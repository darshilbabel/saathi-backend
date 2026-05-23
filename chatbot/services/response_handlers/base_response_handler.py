from abc import ABC, abstractmethod
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from chatbot.celery_tasks.common_chat_tasks import save_in_company_db
from chatbot.celery_tasks.handle_message import translate_and_send_message
from chatbot.llm_models.llm_gateway import build_gateway_params, call_llm_gateway, call_llm_gateway_stream
from chatbot.models import ChatSession, ChatStatus, CompanyBotTypeChoices
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
                    response = self.default_error_message

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

        return self.process_response(
            response, chat_session, chunks, streaming_completed=streaming_completed, **kwargs
        )

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
        )

        if result is None or (isinstance(result, tuple) and result[0] is None):
            logger.error("LLM gateway response returned None")
            return None, None, None

        response, extra_content, finish_reason = result
        return response, extra_content, finish_reason


    def _handle_gateway_response(
        self, system_prompt, messages, company_bot, session_id, profile_id, tools=None, channel_name=None,
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

        # If the KB tool is present, web search is held back until KB returns nothing (fallback).
        # If there is no KB tool, respect enable_web_search from the bot config immediately.
        has_kb_tool = any(
            (t.get('function', {}).get('name') or t.get('name', '')) == 'search_knowledge_base'
            for t in (tools or [])
        )
        use_web_search = False if has_kb_tool else getattr(company_bot, 'enable_web_search', False)

        for iteration in range(max_iterations):
            if use_stream:
                result = self._handle_gateway_stream(
                    gateway_messages=current_messages, company_bot=company_bot,
                    session_id=session_id, profile_id=profile_id, tools=current_tools,
                    tool_choice=current_tool_choice, channel_name=channel_name,
                    cache_policy=cache_policy, metadata=metadata_param,
                    retrieved_chunks=retrieved_chunks, append_to_last=append_to_last,
                    use_web_search=use_web_search,
                )
            else:
                result = self._call_gateway_non_stream(
                    gateway_messages=current_messages, company_bot=company_bot,
                    session_id=session_id, profile_id=profile_id,
                    tools=current_tools, tool_choice=current_tool_choice,
                    retrieved_chunks=retrieved_chunks, append_to_last=append_to_last,
                    use_web_search=use_web_search,
                )

            print(f'[tool_loop] iteration={iteration} result={str(result)[:200]}')

            print("="*50)
            print("Result: ", result)
            print("="*50)

            if not result or (isinstance(result, tuple) and result[0] is None):
                return None, None, None

            response_data, extra, finish = result

            if not (isinstance(response_data, dict) and 'function_call' in response_data):
                # Web search held back for KB fallback, but LLM returned plain text without calling KB.
                # Retry once with web search so the LLM can pull live results.
                if not use_web_search and getattr(company_bot, 'enable_web_search', False) and iteration == 0:
                    use_web_search = True
                    logger.info('[tool_loop] LLM did not call KB — retrying with web search enabled')
                    continue
                return result

            tool_name = response_data['function_call'].get('name', '')
            arguments = response_data['function_call'].get('arguments', {})

            if tool_name not in self._executable_tools:
                logger.info(f'[tool_loop] passing through tool={tool_name} to process_response')
                if retrieved_chunks:
                    response_data, extra, finish = result
                    extra = extra or {}
                    extra['_retrieved_chunks'] = retrieved_chunks
                    return response_data, extra, finish
                return result

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

            # Remove executed tool so the LLM cannot call it again
            current_tools = [
                t for t in (current_tools or [])
                if (t.get('function', {}).get('name') or t.get('name', '')) != tool_name
            ] or None
            current_tool_choice = 'auto' if current_tools else None
            append_to_last = True

        logger.error('[tool_loop] max tool iterations reached')
        return self.default_error_message, None, 'stop'

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
                chunks_text = '\n\n---\n\n'.join(parts)
            else:
                chunks_text = 'No relevant results found in the knowledge base.'
            logger.info(f'[tool_loop] search_knowledge_base: {len(retrieved_chunks)} chunks for query: {query}')
            return chunks_text, retrieved_chunks

        logger.error(f'[tool_loop] unknown tool: {tool_name}')
        return f'Tool "{tool_name}" is not available.', []

    def _call_gateway_non_stream(
        self, gateway_messages, company_bot, session_id, profile_id, tools, tool_choice,
        retrieved_chunks=None, append_to_last=False, use_web_search=False,
    ):
        """Single non-streaming LLM call, returns (response, extra, finish_reason)."""
        import json
        try:
            params = build_gateway_params(company_bot)
            if not use_web_search:
                params.pop('web_search_options', None)
            print(f'[non_stream] use_web_search={use_web_search} bot.enable_web_search={getattr(company_bot, "enable_web_search", "N/A")} web_search_in_params={"web_search_options" in params}')
            data = call_llm_gateway(
                messages=gateway_messages, provider=company_bot.provider, model=company_bot.llm_model,
                params=params, tools=tools, tool_choice=tool_choice,
            )
            print("Data from llm gateway: ", data)
            if not data:
                return None, None, None

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

            if content:
                save_in_company_db(
                    session_id=session_id, profile_id=profile_id, initiated_by='AI',
                    message=content, chunks=all_chunks, status=ChatStatus.IN_PROGRESS, stage=None,
                    append_to_last=append_to_last,
                )
            sources = self._prepare_sources(all_chunks)
            extra = {'sources': sources} if sources else None
            return content, extra, finish_reason

        except Exception as e:
            logger.error(f'Error in non-stream gateway call: {e}', exc_info=True)
            return None, None, None


    def _handle_gateway_stream(
        self, gateway_messages, company_bot, session_id, profile_id, tools, tool_choice,
        channel_name, cache_policy=None, metadata=None, retrieved_chunks=None, append_to_last=False,
        use_web_search=False,
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
            for delta_content, tool_use_delta, chunk_finish_reason, chunk_citations in call_llm_gateway_stream(
                messages=gateway_messages, provider=company_bot.provider, model=company_bot.llm_model,
                params=stream_params, tools=tools, tool_choice=tool_choice,
                cache_policy=cache_policy, metadata=metadata,
            ):
                if delta_content:
                    # Buffer content — do not stream to FE yet; we only flush if there is no tool call
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

            if tool_calls_buffer:
                # Tool call detected — if LLM generated preamble text before the tool call, send it to FE and save
                if accumulated_content:
                    preamble = ''.join(accumulated_content)
                    for chunk in accumulated_content:
                        self._send_chunk(channel_name, chunk, finish_reason=None)
                    save_in_company_db(
                        session_id=session_id, profile_id=profile_id, initiated_by='AI',
                        message=preamble, chunks=None, status=ChatStatus.IN_PROGRESS, stage=None,
                    )
                tc = tool_calls_buffer[0]
                raw_args = tc['arguments']
                print(f'[stream_tool_call] name={tc["name"]} args={raw_args[:200]}')
                try:
                    arguments = json.loads(raw_args)
                except json.JSONDecodeError:
                    import json_repair
                    arguments = json_repair.repair_json(raw_args, return_objects=True)
                all_chunks_for_tool = list(retrieved_chunks or []) + citation_chunks
                extra_for_tool = {'_retrieved_chunks': all_chunks_for_tool} if all_chunks_for_tool else None
                return {'function_call': {'name': tc['name'], 'arguments': arguments}}, extra_for_tool, 'function_call'

            # No tool call — flush buffered content to FE now
            content = ''.join(accumulated_content)
            all_chunks = list(retrieved_chunks or []) + citation_chunks
            if content:
                for chunk in accumulated_content:
                    self._send_chunk(channel_name, chunk, finish_reason=None)
                save_in_company_db(
                    session_id=session_id, profile_id=profile_id, initiated_by='AI',
                    message=content, chunks=all_chunks, status=ChatStatus.IN_PROGRESS, stage=None,
                    append_to_last=append_to_last,
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
        citations_raw = message.get('citations') or []
        chunks = []
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

    def translate_message(self, message, channel_name, step_number, language, company_bot, extra_content=None):
        """Translate and send message"""
        return translate_and_send_message(
            accumulated_message=message,
            current_channel_name=channel_name,
            current_step_number=step_number,
            finish_reason="stop",
            route=language,
            company_bot=company_bot,
            extra_content=extra_content
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
