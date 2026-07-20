from abc import ABC, abstractmethod
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from chatbot.celery_tasks.common_chat_tasks import save_in_company_db
from chatbot.celery_tasks.handle_message import translate_and_send_message
from chatbot.llm_models.llm_gateway import build_gateway_params, call_llm_gateway, call_llm_gateway_stream
from chatbot.models import ChatSession, ChatStatus, CompanyBotTypeChoices
from chatbot.models.bot_vernacular_model import BotVernacular
from chatbot.models.company_models import CompanyStateMachine
from chatbot.models.enums import OperationTypeChoices, PreProcessOutputMode
from chatbot.services.postprocessing.postprocessing_service import PostprocessingService
from chatbot.services.preprocessing.preprocessing_service import PreprocessingService
import logging

logger = logging.getLogger('django')
channel_layer = get_channel_layer()


class BaseResponseHandler(ABC):
    """Base class for handling LLM responses with common functionality"""

    def __init__(self):
        self.default_error_message = 'I am sorry, I could not understood completely. Could you rephrase this please?'
        self.preprocessing_service = PreprocessingService()
        self.postprocessing_service = PostprocessingService()
        self.min_word_count = 3
        self.max_retry_attempts = 2
        self._executable_tools = {'search_knowledge_base'}
        self._gateway_handled_tools = {'web_search'}
        self._metadata_tools = {'respond_to_user'}

    def get_error_message(self, company_bot, language):
        """Return (message, is_vernacular) — is_vernacular=True means message is already in target language."""
        try:
            vernacular = BotVernacular.objects.filter(company_bot=company_bot, language=language).first()
            if vernacular and vernacular.error_message:
                return vernacular.error_message, True
        except Exception as e:
            logger.error("Failed to fetch BotVernacular for language=%s: %s", language, e)
        return self.default_error_message, False

    def _is_response_too_short(self, response):
        """
        Check if response is too short (less than 3 words).
        Returns True if response needs to be regenerated.
        """
        try:
            if not response or response == '':
                return False

            if isinstance(response, dict):
                response_text = response.get('response', '')
                if not response_text:
                    return False
            else:
                response_text = str(response)

            response_text = response_text.strip()
            if not response_text:
                return False

            word_count = len(response_text.split())

            logger.info(f"Response word count: {word_count}, text: '{response_text[:100]}...'")

            if word_count < self.min_word_count:
                logger.info(f"Response too short: {word_count} words (minimum: {self.min_word_count})")
                return True

            return False

        except Exception as e:
            logger.error(f"Error checking response length: {e}")
            return False

    def is_non_llm_state(self, state_machine):
        return (
                state_machine
                and hasattr(state_machine, 'operation_type')
                and state_machine.operation_type == OperationTypeChoices.NON_LLM
        )

    def build_non_llm_function_call(self, state_machine):
        return {
            "toolUseId": "non_llm_auto",
            "name": "get_state_information",
            "input": {
                "next_state_name": state_machine.name,
                "reason": "NON_LLM auto transition"
            }
        }

    def handle_response(self, **kwargs):
        """Main response handling method"""
        session_id = kwargs['session_id']
        chat_session = ChatSession.objects.get(session=session_id)
        chunks = []
        is_function_call = False
        early_return = self.check_early_return(chat_session, **kwargs)
        if early_return is not None:
            if isinstance(early_return, str):
                return early_return
            elif isinstance(early_return, dict):
                if early_return.get('skip_llm', False):
                    kwargs['skip_llm'] = True
                else:
                    is_function_call = self.is_function_call(response=early_return)
            else:
                return early_return

        company_bot = kwargs.get('company_bot')
        try:
            state_machine = CompanyStateMachine.objects.filter(
                company_bot=company_bot, step=chat_session.current_step
            ).first()
        except Exception as e:
            logger.error(f"Error getting state machine: {e}")
            state_machine = None

        if self.is_non_llm_state(state_machine):
            from chatbot.models import CompanyChat

            user_messages_for_state = CompanyChat.objects.filter(
                session=session_id,
                stage=state_machine.name
            ).exclude(message=state_machine.bot_question).exists()

            kwargs['skip_llm'] = True
            kwargs['skip_reason'] = 'non_llm_operation_type'

            if not user_messages_for_state:
                kwargs['send_bot_question'] = True
                kwargs['bot_question_from_db'] = state_machine.bot_question or None
                logger.info(f"NON_LLM state {state_machine.name}: Asking question")

            else:
                kwargs['send_bot_question'] = False
                kwargs['force_function_call'] = True
                logger.info(f"NON_LLM state {state_machine.name}: Advancing to next state")

        original_prompt = kwargs.get('system_prompt', [])

        preprocessing_result = {'action': 'continue', 'prompt': original_prompt}
        if state_machine and state_machine.preprocess_output_mode not in [
            PreProcessOutputMode.NONE, PreProcessOutputMode.SKIP, PreProcessOutputMode.MODIFY_QUESTION
        ]:
            preprocessing_result = self.preprocessing_service.execute_preprocessing(
                state_machine, original_prompt, **kwargs
            )
            if preprocessing_result['action'] == 'skip':
                kwargs['skip_llm'] = True
                kwargs['skip_reason'] = 'preprocessing'
            elif preprocessing_result['action'] == 'modify_question':
                kwargs['modified_bot_question'] = preprocessing_result.get('modified_bot_question')
                kwargs['system_prompt'] = preprocessing_result.get('prompt', original_prompt)
                logger.info(f"Preprocessing modified bot_question: {kwargs['modified_bot_question']}")
            elif preprocessing_result['action'] == 'continue':
                kwargs['system_prompt'] = preprocessing_result.get('prompt', original_prompt)

        response = None
        streaming_completed = False
        if not is_function_call and not kwargs.get('skip_llm', False):
            result = self.get_llm_response(**kwargs)

            if isinstance(result, tuple):
                response, extra_content, finish_reason = result

                # Pull out chunks accumulated during tool loop (KB search before pass-through tool)
                if isinstance(extra_content, dict) and '_retrieved_chunks' in extra_content:
                    chunks = extra_content.pop('_retrieved_chunks') or []

                if isinstance(extra_content, dict) and '_usage_cost' in extra_content:
                    kwargs['_usage_cost'] = extra_content.pop('_usage_cost')

                if isinstance(extra_content, dict) and extra_content.pop('_is_vernacular_error', False):
                    kwargs['is_bot_vernacular_message'] = True

                # Store extra_content if present for later use
                if extra_content:
                    kwargs['llm_extra_content'] = extra_content
            else:
                response = result
                finish_reason = None

            use_streaming = self.should_use_streaming(company_bot)

            streaming_completed = finish_reason == "stop" and use_streaming

            # Only treat None as error
            if response is None:
                if company_bot.bot_type == CompanyBotTypeChoices.STATE_MACHINE:
                    response = {
                        "toolUseId": "tooluse_fallback", "name": "get_state_information",
                        "input": {
                            "next_state_name": "SAMPLE",
                            "reason": "LLM returned no response"
                        }
                    }
                else:
                    response, is_vernacular = self.get_error_message(company_bot, kwargs.get('language'))
                    if is_vernacular:
                        kwargs['is_bot_vernacular_message'] = True

        if is_function_call and response is None:
            response = early_return
        if kwargs.get('force_function_call') and state_machine:
            is_function_call = True
            response = self.build_non_llm_function_call(state_machine)

        if not is_function_call:
            is_function_call = self.is_function_call(response=response) if state_machine else False
        if is_function_call and state_machine and response:
            postprocessing_result = self.postprocessing_service.execute_postprocessing(
                state_machine, response, **kwargs
            )

            if postprocessing_result.get('skip_next_stage', False):
                kwargs['skip_next_stage'] = True
                kwargs['target_stage'] = state_machine.skip_to_step
                logger.info("Postprocessing will skip next stage")

                next_stage_number = kwargs['target_stage']
                try:
                    next_state_machine = CompanyStateMachine.objects.get(
                        company_bot=company_bot, step=next_stage_number
                    )

                    next_stage_preprocessing_result = self.preprocessing_service.execute_preprocessing(
                        next_state_machine, kwargs.get('system_prompt', []), **kwargs
                    )

                    if next_stage_preprocessing_result['action'] == 'skip':
                        kwargs['skip_next_stage_preprocessing'] = True
                    elif next_stage_preprocessing_result['action'] == 'modify_question':
                        kwargs['modified_bot_question'] = next_stage_preprocessing_result.get('modified_bot_question')
                        kwargs['system_prompt'] = next_stage_preprocessing_result.get('prompt', original_prompt)
                        logger.info(f"Preprocessing modified bot_question: {kwargs['modified_bot_question']}")

                except CompanyStateMachine.DoesNotExist:
                    logger.error(f"Next state machine {next_stage_number} not found for preprocessing")
            else:
                next_stage_number = chat_session.current_step + 1
                try:
                    next_state_machine = CompanyStateMachine.objects.get(
                        company_bot=company_bot, step=next_stage_number
                    )

                    next_stage_preprocessing_result = self.preprocessing_service.execute_preprocessing(
                        next_state_machine, kwargs.get('system_prompt', []), **kwargs
                    )

                    if next_stage_preprocessing_result['action'] == 'skip':
                        kwargs['skip_next_stage_preprocessing'] = True
                    elif next_stage_preprocessing_result['action'] == 'modify_question':
                        kwargs['modified_bot_question'] = next_stage_preprocessing_result.get('modified_bot_question')
                        kwargs['system_prompt'] = next_stage_preprocessing_result.get('prompt', original_prompt)
                        logger.info(f"Preprocessing modified bot_question: {kwargs['modified_bot_question']}")

                except CompanyStateMachine.DoesNotExist:
                    logger.info(f"Next state machine {next_stage_number} not found, likely at end of flow")

        process_result = self.process_response(
            response, chat_session, chunks, streaming_completed=streaming_completed, **kwargs
        )
        _usage_cost = kwargs.get('_usage_cost')
        if _usage_cost:
            self._update_last_chat_usage(session_id, _usage_cost)
        return process_result

    def analyze_response_for_postprocessing(self, response):
        """Analyze if response needs postprocessing - can be overridden by subclasses"""
        return self.is_function_call(response)

    def should_use_streaming(self, company_bot):
        """
        Determine if streaming should be used for this bot.
        """
        try:
            if hasattr(company_bot, 'stream'):
                return bool(company_bot.stream)

            return False

        except Exception as e:
            logger.error(f"Error determining streaming mode: {e}")
            return False

    def get_llm_response(self, **kwargs):
        """Get response from LLM provider"""
        company_bot = kwargs['company_bot']
        system_prompt = kwargs['system_prompt']
        response = None
        message_to_send = self.get_messages_for_llm(**kwargs)

        session_id = kwargs['session_id']
        profile_id = kwargs.get('profile_id')
        channel_name = kwargs['channel_name']
        chat_session = ChatSession.objects.get(session=session_id)
        state_machine = None
        try:
            state_machine = CompanyStateMachine.objects.get(
                company_bot=company_bot, step=chat_session.current_step
            )
        except Exception as e:
            logger.error(f"Error getting state machine for tools: {e}")

        tools = None
        has_state_machine_tool_context = (
                state_machine
                and hasattr(state_machine, 'tool_context')
                and state_machine.tool_context
                and state_machine.tool_context.strip()
        )

        has_company_bot_tool_context = (
                company_bot
                and hasattr(company_bot, 'tool_context')
                and company_bot.tool_context
                and company_bot.tool_context.strip()
        )

        if has_state_machine_tool_context or (
                company_bot.bot_type == CompanyBotTypeChoices.SIMPLE and has_company_bot_tool_context
        ):
            tool_context = (
                state_machine.tool_context.strip()
                if has_state_machine_tool_context
                else company_bot.tool_context.strip()
            )

            try:
                import json_repair
                tools = json_repair.repair_json(tool_context, return_objects=True)
                logger.info("Using state machine tool_context")
            except Exception as e:
                logger.error(f"Failed to parse state machine tool_context: {e}")
                tools = None

        result = self._handle_gateway_response(
            system_prompt=system_prompt, messages=message_to_send, company_bot=company_bot,
            session_id=session_id, profile_id=profile_id, tools=tools, channel_name=channel_name,
            language=kwargs.get('language'),
        )

        if result is None or (isinstance(result, tuple) and result[0] is None):
            logger.error("LLM gateway response returned None")
            return None, None, None

        response, extra_content, finish_reason = result
        return response, extra_content, finish_reason


    def _handle_gateway_response(
        self, system_prompt, messages, company_bot, session_id, profile_id, tools=None, channel_name=None,
        language=None,
    ):
        import json as _json

        tool_choice = None
        if isinstance(tools, dict):
            tool_choice = tools.get('tool_choice', 'auto')
            tools = tools.get('tools') or tools.get('tool')
        elif isinstance(tools, list):
            tool_choice = 'auto'

        gateway_messages = []
        if system_prompt:
            gateway_messages.append({'role': 'system', 'content': system_prompt})
        gateway_messages += messages

        other = company_bot.other_params or {}
        cache_policy = other.get('cache_policy')
        metadata_param = other.get('metadata')
        use_stream = self.should_use_streaming(company_bot) and channel_name

        current_messages = gateway_messages
        current_tools = tools
        current_tool_choice = tool_choice
        retrieved_chunks = []
        append_to_last = False
        max_iterations = 5
        turn_usage = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'cost_usd': 0.0}

        # If the KB tool is present, web search is held back until KB returns nothing (fallback).
        # If there is no KB tool, respect enable_web_search from the bot config immediately.
        has_kb_tool = any(
            (t.get('function', {}).get('name') or t.get('name', '')) == 'search_knowledge_base'
            for t in (tools or [])
        )
        use_web_search = False if has_kb_tool else getattr(company_bot, 'enable_web_search', False)
        empty_respond_to_user_retried = False

        for iteration in range(max_iterations):
            if use_stream:
                result = self._handle_gateway_stream(
                    gateway_messages=current_messages, company_bot=company_bot,
                    session_id=session_id, profile_id=profile_id, tools=current_tools,
                    tool_choice=current_tool_choice, channel_name=channel_name,
                    cache_policy=cache_policy, metadata=metadata_param,
                    retrieved_chunks=retrieved_chunks, append_to_last=append_to_last,
                    use_web_search=use_web_search, turn_usage=turn_usage,
                )
            else:
                result = self._call_gateway_non_stream(
                    gateway_messages=current_messages, company_bot=company_bot,
                    session_id=session_id, profile_id=profile_id,
                    tools=current_tools, tool_choice=current_tool_choice,
                    retrieved_chunks=retrieved_chunks, append_to_last=append_to_last,
                    use_web_search=use_web_search, turn_usage=turn_usage,
                )

            print(f'[tool_loop] iteration={iteration} result={str(result)[:200]}')

            print("="*50)
            print("Result: ", result)
            print("="*50)

            if not result or (isinstance(result, tuple) and result[0] is None):
                return None, None, None

            response_data, extra, finish = result

            if not (isinstance(response_data, dict) and 'function_call' in response_data):
                if isinstance(extra, dict) and extra.pop('_respond_to_user_handled', False):
                    if not response_data and not empty_respond_to_user_retried:
                        empty_respond_to_user_retried = True
                        logger.info('[tool_loop] respond_to_user called with no text — retrying with correction')
                        current_messages = list(current_messages) + [
                            {
                                'role': 'assistant', 'content': None,
                                'tool_calls': [{'id': 'tu_empty_retry', 'type': 'function',
                                                'function': {'name': 'respond_to_user', 'arguments': '{}'}}],
                            },
                            {
                                'role': 'tool', 'tool_call_id': 'tu_empty_retry',
                                'name': 'respond_to_user',
                                'content': 'Error: Response text is required. Write your full reply as plain text first, then call respond_to_user.',
                            },
                        ]
                        continue
                    if not response_data:
                        logger.info('[tool_loop] respond_to_user still empty after retry — sending default error')
                        err_msg, is_vernacular = self.get_error_message(company_bot, language)
                        return err_msg, {'_is_vernacular_error': is_vernacular} if is_vernacular else None, None
                    return self._with_turn_usage(response_data, extra, finish, turn_usage)
                # Web search held back for KB fallback. Only retry if LLM gave NO response at all —
                # a non-empty answer is a deliberate choice (and streaming already sent those tokens).
                if not use_web_search and getattr(company_bot, 'enable_web_search', False) and iteration == 0 and not response_data:
                    use_web_search = True
                    logger.info('[tool_loop] LLM returned empty response — retrying with web search enabled')
                    continue
                response_data, extra, finish = result
                return self._with_turn_usage(response_data, extra, finish, turn_usage)

            tool_name = response_data['function_call'].get('name', '')
            arguments = response_data['function_call'].get('arguments', {})

            if tool_name not in self._executable_tools:
                logger.info(f'[tool_loop] passing through tool={tool_name} to process_response')
                response_data, extra, finish = result
                if retrieved_chunks:
                    extra = extra or {}
                    extra['_retrieved_chunks'] = retrieved_chunks
                return self._with_turn_usage(response_data, extra, finish, turn_usage)

            logger.info(f'[tool_loop] iteration={iteration} executing tool={tool_name}')

            tool_result_text, new_chunks = self._execute_tool(
                tool_name=tool_name, arguments=arguments, company_bot=company_bot,
            )
            retrieved_chunks.extend(new_chunks or [])

            # If KB search found nothing and web search is configured, activate it for the next call
            if tool_name == 'search_knowledge_base':
                if not new_chunks:
                    use_web_search = getattr(company_bot, 'enable_web_search', False)
                    logger.info('[tool_loop] KB search empty — enabling web search for next iteration')
                else:
                    use_web_search = False

            tool_call_id = f'tool_{iteration}'
            args_str = _json.dumps(arguments) if isinstance(arguments, dict) else (arguments or '{}')
            current_messages = list(current_messages) + [
                {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [{
                        'id': tool_call_id,
                        'type': 'function',
                        'function': {'name': tool_name, 'arguments': args_str},
                    }],
                },
                {
                    'role': 'tool',
                    'tool_call_id': tool_call_id,
                    'name': tool_name,
                    'content': tool_result_text,
                },
            ]

            if tool_name == 'search_knowledge_base' and use_web_search and not new_chunks:
                # Not shown to the user — just nudges the gateway/LLM to actually invoke web_search
                # instead of answering from internal knowledge, whenever our fallback logic enables it.
                current_messages = current_messages + [
                    {
                        'role': 'assistant',
                        'content': "I couldn't find this in our knowledge base. Let me search the web for this.",
                    },
                ]

            # Remove executed tool so the LLM cannot call it again
            current_tools = [
                t for t in (current_tools or [])
                if (t.get('function', {}).get('name') or t.get('name', '')) != tool_name
            ] or None
            current_tool_choice = 'auto' if current_tools else None
            append_to_last = True

        logger.error('[tool_loop] max tool iterations reached')
        err_msg, is_vernacular = self.get_error_message(company_bot, language)
        return err_msg, {'_is_vernacular_error': is_vernacular} if is_vernacular else None, 'stop'

    def _execute_tool(self, tool_name, arguments, company_bot):
        """Execute a tool call and return (result_text_for_llm, retrieved_chunks)."""
        if tool_name == 'search_knowledge_base':
            from chatbot.services.vector.vector_service import fetch_context_for_query
            query = arguments.get('query', '') if isinstance(arguments, dict) else ''
            _, retrieved_chunks = fetch_context_for_query(query=query, company_bot=company_bot)
            if retrieved_chunks:
                parts = []
                for c in retrieved_chunks:
                    title = c.get('title', '')
                    url = c.get('url', '')
                    text = c.get('text', '')
                    header = f'[{title}]({url})' if url else title
                    parts.append(f'Source: {header}\n{text}')
                chunks_text = self._wrap_retrieved_content('\n\n---\n\n'.join(parts), source='repository')
            else:
                if getattr(company_bot, 'enable_web_search', False):
                    no_result_message = (
                        'No repository result found for this query. Web search is enabled for this bot — '
                        'call the web_search tool now to answer this query before responding. '
                        'Do not answer from general/internal knowledge first without doing web search.'
                    )
                else:
                    no_result_message = (
                        '(no repository result found — respond from general knowledge if appropriate, '
                        'per no-hallucination rules)'
                    )
                chunks_text = self._wrap_retrieved_content(no_result_message, source='none')
            logger.info(f'[tool_loop] search_knowledge_base: {len(retrieved_chunks)} chunks for query: {query}')
            return chunks_text, retrieved_chunks

        logger.error(f'[tool_loop] unknown tool: {tool_name}')
        return f'Tool "{tool_name}" is not available.', []

    def _wrap_retrieved_content(self, text, source):
        """Wrap tool-retrieved text in an explicit provenance marker before it enters the transcript."""
        return f'<retrieved_content source="{source}">\n{text}\n</retrieved_content>'

    def _call_gateway_non_stream(
        self, gateway_messages, company_bot, session_id, profile_id, tools, tool_choice,
        retrieved_chunks=None, append_to_last=False, use_web_search=False, turn_usage=None,
    ):
        """Single non-streaming LLM call, returns (response, extra, finish_reason)."""
        import json
        try:
            params = build_gateway_params(company_bot)
            if not use_web_search:
                params.pop('web_search_options', None)
            print(f'[non_stream] use_web_search={use_web_search} bot.enable_web_search={getattr(company_bot, "enable_web_search", "N/A")} web_search_in_params={"web_search_options" in params}')
            data = call_llm_gateway(
                messages=gateway_messages, provider=company_bot.provider, model=self._get_effective_model(company_bot),
                params=params, tools=tools, tool_choice=tool_choice,
            )
            logger.info(f"[gateway] raw response: {data}")
            if not data:
                return None, None, None

            usage_cost = self._extract_usage_cost(data)
            if usage_cost:
                self._update_session_usage(session_id, usage_cost)
                if turn_usage is not None:
                    self._accumulate_usage(turn_usage, usage_cost)

            choice = data.get('choices', [{}])[0]
            message = choice.get('message', {})
            finish_reason = choice.get('finish_reason')
            tool_calls = message.get('tool_calls') or []

            executable_tc = next(
                (tc for tc in tool_calls
                 if (tc.get('function', {}).get('name') or tc.get('name', '')) in self._executable_tools),
                None,
            )
            if executable_tc:
                raw_args = executable_tc.get('function', {}).get('arguments', '{}')
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                return {'function_call': {'name': executable_tc['function']['name'], 'arguments': arguments}}, None, 'function_call'

            # respond_to_user new format: LLM response text is in message.content; tool args are metadata only
            respond_to_user_tc = next(
                (tc for tc in tool_calls
                 if (tc.get('function', {}).get('name') or tc.get('name', '')) == 'respond_to_user'),
                None,
            )
            if respond_to_user_tc:
                raw_args = respond_to_user_tc.get('function', {}).get('arguments', '{}')
                ru_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                if 'response' not in ru_args:
                    content = message.get('content', '') or ''
                    quick_reply_chips = self._parse_if_string(ru_args.get('quick_reply_chips'), [])
                    finalized_sources = self._parse_if_string(ru_args.get('finalized_sources'), [])

                    if finalized_sources is not None:
                        self._save_finalized_sources(session_id, finalized_sources)

                    citation_chunks = self._extract_citation_chunks(message)
                    all_chunks = list(retrieved_chunks or []) + citation_chunks
                    sources = self._prepare_sources(all_chunks)
                    extra_content = {}
                    if sources:
                        extra_content['sources'] = sources
                    if quick_reply_chips is not None:
                        extra_content['quick_reply_chips'] = quick_reply_chips

                    if all_chunks:
                        extra_content['_retrieved_chunks'] = all_chunks

                    extra_content['_respond_to_user_handled'] = True
                    return content, extra_content or None, finish_reason
                # old format (has 'response' key) — falls through to passthrough_tc below

            # Pass-through tools: not executable by us and not handled by the gateway → return to process_response
            passthrough_tc = next(
                (tc for tc in tool_calls
                 if (tc.get('function', {}).get('name') or tc.get('name', '')) not in self._gateway_handled_tools),
                None,
            )
            if passthrough_tc:
                tc_name = passthrough_tc.get('function', {}).get('name') or passthrough_tc.get('name', '')
                raw_args = passthrough_tc.get('function', {}).get('arguments', '{}')
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                return {'function_call': {'name': tc_name, 'arguments': arguments}}, None, 'function_call'

            content = message.get('content', '')
            print(f'[non_stream_response] finish_reason={finish_reason} content={repr(content[:200])}')

            citation_chunks = self._extract_citation_chunks(message)
            all_chunks = list(retrieved_chunks or []) + citation_chunks

            # No save here: content still flows to _handle_regular_response, which does the canonical save.
            sources = self._prepare_sources(all_chunks)
            extra = {'sources': sources} if sources else None
            if all_chunks:
                extra = extra or {}
                extra['_retrieved_chunks'] = all_chunks
            return content, extra, finish_reason

        except Exception as e:
            logger.error(f'Error in non-stream gateway call: {e}', exc_info=True)
            return None, None, None


    def _handle_gateway_stream(
        self, gateway_messages, company_bot, session_id, profile_id, tools, tool_choice,
        channel_name, cache_policy=None, metadata=None, retrieved_chunks=None, append_to_last=False,
        use_web_search=False, turn_usage=None,
    ):
        import json
        accumulated_content = []
        tool_calls_buffer = {}  # index -> {id, name, arguments}
        finish_reason = None
        try:
            stream_params = build_gateway_params(company_bot)
            if not use_web_search:
                stream_params.pop('web_search_options', None)
            print(f'[stream] use_web_search={use_web_search} bot.enable_web_search={getattr(company_bot, "enable_web_search", "N/A")} web_search_in_params={"web_search_options" in stream_params}')
            citation_chunks = []
            finish_chunk = None
            for delta_content, tool_use_delta, chunk_finish_reason, chunk_citations, chunk_finish_data in call_llm_gateway_stream(
                messages=gateway_messages, provider=company_bot.provider, model=self._get_effective_model(company_bot),
                params=stream_params, tools=tools, tool_choice=tool_choice,
                cache_policy=cache_policy, metadata=metadata,
            ):
                if delta_content:
                    self._send_chunk(channel_name, delta_content, finish_reason=None)
                    accumulated_content.append(delta_content)
                if tool_use_delta:
                    idx = tool_use_delta.get('index', 0)
                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {'id': '', 'name': '', 'arguments': ''}
                    if tool_use_delta.get('id'):
                        tool_calls_buffer[idx]['id'] = tool_use_delta['id']
                    if tool_use_delta.get('name'):
                        tool_calls_buffer[idx]['name'] = tool_use_delta['name']
                    tool_calls_buffer[idx]['arguments'] += tool_use_delta.get('arguments_delta') or ''
                if chunk_citations:
                    citation_chunks.extend(self._extract_citation_chunks_from_stream(chunk_citations))
                if chunk_finish_reason:
                    finish_reason = chunk_finish_reason
                if chunk_finish_data:
                    finish_chunk = chunk_finish_data

            usage_cost = self._extract_usage_cost(finish_chunk)
            if usage_cost:
                self._update_session_usage(session_id, usage_cost)
                if turn_usage is not None:
                    self._accumulate_usage(turn_usage, usage_cost)

            if tool_calls_buffer:
                tc = tool_calls_buffer[0]
                raw_args = tc['arguments']
                print(f'[stream_tool_call] name={tc["name"]} args={raw_args[:200]}')
                try:
                    tc_arguments = json.loads(raw_args)
                except json.JSONDecodeError:
                    import json_repair
                    tc_arguments = json_repair.repair_json(raw_args, return_objects=True)
                if not isinstance(tc_arguments, dict):
                    tc_arguments = {}

                if tc['name'] == 'respond_to_user' and 'response' not in tc_arguments:
                    # New format: text was already streamed token-by-token; tool call carries metadata only
                    content = ''.join(accumulated_content)
                    quick_reply_chips = self._parse_if_string(tc_arguments.get('quick_reply_chips'), [])
                    finalized_sources = self._parse_if_string(tc_arguments.get('finalized_sources'), [])

                    if finalized_sources is not None:
                        self._save_finalized_sources(session_id, finalized_sources)

                    all_chunks = list(retrieved_chunks or []) + citation_chunks
                    sources = self._prepare_sources(all_chunks)
                    extra_content = {}
                    if sources:
                        extra_content['sources'] = sources
                    if quick_reply_chips is not None:
                        extra_content['quick_reply_chips'] = quick_reply_chips
                    # Marker consumed by _handle_gateway_response to skip web-search retry
                    extra_content['_respond_to_user_handled'] = True

                    if content:
                        # Text was streamed token-by-token; save to DB and send stop chunk with chips
                        save_in_company_db(
                            session_id=session_id, profile_id=profile_id, initiated_by='AI',
                            message=content, chunks=all_chunks, status=ChatStatus.IN_PROGRESS,
                            stage=None, append_to_last=append_to_last,
                            other_params={'usage': usage_cost} if usage_cost else None,
                        )
                        self._send_chunk(channel_name, '', finish_reason='stop',
                                         extra_content={k: v for k, v in extra_content.items()
                                                        if k != '_respond_to_user_handled'} or None)
                        return content, extra_content, 'stop'
                    else:
                        # LLM generated no text — signal _handle_gateway_response to retry with correction
                        return content, extra_content, None

                # Any other tool call — preamble was already sent to WS token-by-token; only save to DB
                preamble_streamed = bool(accumulated_content)
                if accumulated_content:
                    preamble = ''.join(accumulated_content)
                    save_in_company_db(
                        session_id=session_id, profile_id=profile_id, initiated_by='AI',
                        message=preamble, chunks=None, status=ChatStatus.IN_PROGRESS, stage=None,
                    )
                all_chunks_for_tool = list(retrieved_chunks or []) + citation_chunks
                extra_for_tool = {}
                if all_chunks_for_tool:
                    extra_for_tool['_retrieved_chunks'] = all_chunks_for_tool
                if preamble_streamed:
                    # Signal to process_response handlers that text was already sent to WS
                    extra_for_tool['_text_streamed_to_ws'] = True
                extra_for_tool = extra_for_tool or None
                return {'function_call': {'name': tc['name'], 'arguments': tc_arguments}}, extra_for_tool, 'function_call'

            # No tool call — content was already streamed token-by-token; save to DB
            content = ''.join(accumulated_content)
            logger.info(f"[gateway] raw stream response: {content}")
            all_chunks = list(retrieved_chunks or []) + citation_chunks
            if content:
                save_in_company_db(
                    session_id=session_id, profile_id=profile_id, initiated_by='AI',
                    message=content, chunks=all_chunks, status=ChatStatus.IN_PROGRESS, stage=None,
                    append_to_last=append_to_last,
                    other_params={'usage': usage_cost} if usage_cost else None,
                )
            sources = self._prepare_sources(all_chunks)
            extra_content = {'sources': sources} if sources else None
            self._send_chunk(channel_name, '', finish_reason=finish_reason or 'stop', extra_content=extra_content)
            return content, extra_content, finish_reason or 'stop'

        except Exception as e:
            logger.error(f'Error in gateway stream handling: {e}', exc_info=True)
            return None, None, None

    def _prepare_sources(self, chunks):
        """Build deduplicated sources list from retrieved chunks for extra_content."""
        print("="*100)
        print("Chunks: ", chunks)
        print("="*100)
        if not chunks:
            return []
        seen = set()
        sources = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            title = chunk.get('title', '')
            url = chunk.get('url', '')
            key = url or title
            if not key or key in seen:
                continue
            seen.add(key)
            if url:
                sources.append({'title': title, 'url': url})
            else:
                sources.append({'title': f'Referred: {title}'})
        return sources

    def _extract_citation_chunks(self, message):
        """Extract web search citations from a non-stream gateway message and return as chunk dicts."""
        chunks = []

        def _collect_from_tool_results(tool_results):
            for result in (tool_results or []):
                if not isinstance(result, dict):
                    continue
                for item in result.get('content') or []:
                    if not isinstance(item, dict):
                        continue
                    url = item.get('url', '')
                    title = item.get('title', '')
                    if url or title:
                        chunks.append({'text': item.get('cited_text', ''), 'title': title, 'url': url})

        citations_raw = message.get('citations') or []
        if citations_raw and isinstance(citations_raw[0], dict) and 'content' in citations_raw[0]:
            # Anthropic (via litellm): list of web_search_tool_result objects, each with a
            # nested content[] of {title, url, ...} — not a flat {url, title, cited_text} dict.
            _collect_from_tool_results(citations_raw)
        else:
            for group in citations_raw:
                if not isinstance(group, list):
                    continue
                for citation in group:
                    if not isinstance(citation, dict):
                        continue
                    url = citation.get('url', '')
                    title = citation.get('title', '')
                    text = citation.get('cited_text', '')
                    if url or title:
                        chunks.append({'text': text, 'title': title, 'url': url})

        if not chunks:
            # 'citations' can be null even when a web search happened — the raw provider
            # payload nests results here instead.
            web_search_results = (message.get('provider_specific_fields') or {}).get('web_search_results') or []
            _collect_from_tool_results(web_search_results)

        return chunks

    def _extract_citation_chunks_from_stream(self, citation_events):
        """Extract web search citations from stream citation events and return as chunk dicts."""
        chunks = []
        for event in (citation_events or []):
            if not isinstance(event, dict):
                continue
            # Gateway may send each citation event directly as a citation dict,
            # or as a list of citation dicts under a key
            candidates = event.get('citations') or event.get('results') or [event]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                url = item.get('url', '')
                title = item.get('title', '')
                text = item.get('cited_text', '') or item.get('text', '')
                if url or title:
                    chunks.append({'text': text, 'title': title, 'url': url})
        return chunks

    def _parse_if_string(self, value, fallback):
        """Some models (e.g. Llama) return arrays/objects as JSON strings inside tool args. Parse them."""
        if not isinstance(value, str):
            return value
        try:
            import json as _json
            parsed = _json.loads(value)
            return parsed if isinstance(parsed, type(fallback)) else fallback
        except Exception:
            return fallback

    def _get_effective_model(self, company_bot):
        """Return the model name to use for gateway calls.

        If other_params contains a 'custom_model' key, that value takes precedence
        over the llm_model enum field — useful for OpenRouter or any provider that
        uses model IDs not listed in LLMModel.
        """
        custom = (company_bot.other_params or {}).get('custom_model')
        if isinstance(custom, str):
            custom = custom.strip()
            if custom:
                return custom
        return company_bot.llm_model

    def _with_turn_usage(self, response_data, extra, finish, turn_usage):
        """Return a (response, extra, finish) tuple with turn_usage injected into extra."""
        if any(turn_usage.values()):
            extra = dict(extra) if isinstance(extra, dict) else {}
            extra['_usage_cost'] = dict(turn_usage)
        return response_data, extra or None, finish

    def _accumulate_usage(self, accumulator, usage_cost):
        """Add a single call's usage_cost into a running accumulator dict."""
        for k in ('input_tokens', 'output_tokens', 'total_tokens'):
            accumulator[k] = accumulator.get(k, 0) + (usage_cost.get(k) or 0)
        accumulator['cost_usd'] = round(accumulator.get('cost_usd', 0) + (usage_cost.get('cost_usd') or 0), 6)

    def _update_last_chat_usage(self, session_id, usage_cost):
        """Merge usage cost into the most recently saved AI CompanyChat for this session."""
        from chatbot.models import CompanyChat
        try:
            chat = CompanyChat.objects.filter(
                session=session_id, sender__id=1
            ).order_by('-id').first()
            if chat:
                other_params = chat.other_params or {}
                other_params['usage'] = usage_cost
                chat.other_params = other_params
                chat.save(update_fields=['other_params'])
                logger.info(f"[usage] saved turn usage to CompanyChat id={chat.id} usage={usage_cost}")
        except Exception as e:
            logger.error(f'[_update_last_chat_usage] failed: {e}')

    def _extract_usage_cost(self, data):
        """Extract token usage and cost from a raw gateway response dict."""
        if not data:
            return None
        usage = data.get('usage', {}) or {}
        cost = data.get('cost', {}) or {}
        result = {
            'input_tokens': usage.get('input_tokens', 0) or 0,
            'output_tokens': usage.get('output_tokens', 0) or 0,
            'total_tokens': usage.get('total_tokens', 0) or 0,
            'cost_usd': cost.get('computed_usd', 0) or 0,
        }
        return result if any(result.values()) else None

    def _update_session_usage(self, session_id, usage_cost):
        """Accumulate token usage and cost into ChatSession.other_params['usage']."""
        try:
            session = ChatSession.objects.get(session=session_id)
            other_params = session.other_params or {}
            usage = other_params.get('usage', {})
            usage['total_input_tokens'] = usage.get('total_input_tokens', 0) + usage_cost['input_tokens']
            usage['total_output_tokens'] = usage.get('total_output_tokens', 0) + usage_cost['output_tokens']
            usage['total_tokens'] = usage.get('total_tokens', 0) + usage_cost['total_tokens']
            usage['total_cost_usd'] = round(usage.get('total_cost_usd', 0) + usage_cost['cost_usd'], 6)
            other_params['usage'] = usage
            session.other_params = other_params
            session.save(update_fields=['other_params'])
            logger.info(f"[usage] session {session_id} totals updated: {usage}")
        except Exception as e:
            logger.error(f'[_update_session_usage] failed: {e}')

    def _save_finalized_sources(self, session_id, finalized_sources):
        """Persist finalized_sources from respond_to_user into ChatSession.other_params."""
        try:
            session = ChatSession.objects.get(session=session_id)
            other_params = session.other_params or {}
            other_params['finalized_sources'] = finalized_sources
            session.other_params = other_params
            session.save(update_fields=['other_params'])
        except Exception as e:
            logger.error(f'[_save_finalized_sources] failed: {e}')

    def _send_chunk(self, channel_name, content, finish_reason, extra_content=None):
        """Send a chunk via channel layer to the WebSocket."""
        try:
            message_data = {
                "type": "chat.message",
                "text": {
                    "msg": content,
                    "source": "bot",
                    "type": "chunk",
                    "finish_reason": finish_reason
                },
            }

            if extra_content:
                message_data["text"]["extra_content"] = extra_content

            async_to_sync(channel_layer.send)(channel_name, message_data)

        except Exception as e:
            logger.error(f"Failed to send chunk to channel {channel_name}: {e}", exc_info=True)

    def _send_error_chunk(self, channel_name, error_msg):
        """Send error message via channel layer to the WebSocket."""
        try:
            async_to_sync(channel_layer.send)(
                channel_name,
                {
                    "type": "chat.message",
                    "text": {
                        "msg": error_msg,
                        "source": "bot",
                        "type": "error",
                        "finish_reason": "error"
                    },
                },
            )
        except Exception as e:
            logger.error(f"Failed to send error to channel {channel_name}: {e}")

    def get_default_tools_config(self):
        """Get default tools configuration - fallback for when no tool_context is available"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_state_information",
                    "description": "Get the information of the state you want to be in.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "state_name": {
                                "type": "string",
                                "description": "Name of the next state provided in the context."
                            }
                        },
                        "required": ["state_name"]
                    }
                }
            }
        ]

    def get_tools_config(self):
        """Deprecated - use get_default_tools_config() or PromptBuilder.get_tools_from_state_machine()"""
        logger.info("get_tools_config() is deprecated, use get_default_tools_config() instead")
        return self.get_default_tools_config()

    def is_function_call(self, response):
        """Check if response is a function call"""
        if isinstance(response, dict):
            if 'toolUseId' in response and 'name' in response:
                return response.get('name') == 'get_state_information'

            elif 'name' in response and 'parameters' in response:
                return response.get('name') == 'get_state_information'

            elif 'function_call' in response:
                function_call = response.get('function_call', {})
                return function_call.get('name') == 'get_state_information'

            elif 'tool_calls' in response:
                tool_calls = response.get('tool_calls', [])
                for tool_call in tool_calls:
                    if 'function' in tool_call:
                        function = tool_call.get('function', {})
                        if function.get('name') == 'get_state_information':
                            return True
                return False

            elif 'output' in response and 'message' in response.get('output', {}):
                content = response['output']['message'].get('content', [])
                for item in content:
                    if 'toolUse' in item:
                        tool_use = item.get('toolUse', {})
                        if tool_use.get('name') == 'get_state_information':
                            return True
                return False

            elif 'parameters' in response or 'input' in response:
                nested_data = response.get('parameters') or response.get('input')
                if isinstance(nested_data, dict):
                    if 'next_state_name' in nested_data:
                        return True
                    elif 'response' in nested_data:
                        return False
                    else:
                        return 'get_state_information' in str(nested_data)
                return 'get_state_information' in str(nested_data)

            elif any(key in response for key in ['toolUseId', 'tool_calls', 'function_call']):
                return 'get_state_information' in str(response)

            return False

        elif isinstance(response, str):
            return 'get_state_information' in response

        return False

    def save_message(self, session_id, profile_id, message, chunks,
                     status, translated_message, stage=None, other_params=None):
        """Save message to database"""
        save_in_company_db(
            session_id=session_id,
            profile_id=profile_id,
            initiated_by='AI',
            message=message,
            chunks=chunks,
            status=status,
            translated_message=translated_message,
            stage=stage,
            other_params=other_params
        )

    def translate_message(self, message, channel_name, step_number, language, company_bot, extra_content=None,
                          is_bot_vernacular_message=False):
        """Translate and send message"""
        return translate_and_send_message(
            accumulated_message=message,
            current_channel_name=channel_name,
            current_step_number=step_number,
            finish_reason="stop",
            route=language,
            company_bot=company_bot,
            extra_content=extra_content,
            is_bot_vernacular_message=is_bot_vernacular_message,
        )

    def get_chat_status(self, state_machine, company_bot):
        """Determine chat status based on state"""
        last_state = CompanyStateMachine.objects.filter(company_bot=company_bot).order_by('step').last()
        max_step = last_state.step if last_state else None

        if state_machine.step == max_step:
            return ChatStatus.COMPLETED
        else:
            return ChatStatus.IN_PROGRESS

    @abstractmethod
    def check_early_return(self, chat_session, **kwargs):
        """Check if we should return early (bot-specific logic)"""
        pass

    @abstractmethod
    def get_messages_for_llm(self, **kwargs):
        """Get appropriate messages for LLM"""
        pass

    @abstractmethod
    def process_response(self, response, chat_session, chunks, **kwargs):
        """Process the LLM response (bot-specific logic)"""
        pass
