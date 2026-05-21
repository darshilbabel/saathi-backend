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
        metadata = other.get('metadata')
        use_stream = self.should_use_streaming(company_bot) and channel_name

        if use_stream:
            result = self._handle_gateway_stream(
                gateway_messages=gateway_messages, company_bot=company_bot,
                session_id=session_id, profile_id=profile_id, tools=tools,
                tool_choice=tool_choice, channel_name=channel_name,
                cache_policy=cache_policy, metadata=metadata,
            )
        else:
            result = self._call_gateway_non_stream(
                gateway_messages=gateway_messages, company_bot=company_bot,
                session_id=session_id, profile_id=profile_id,
                tools=tools, tool_choice=tool_choice,
            )

        print("Result found: ", result)

        if not result or (isinstance(result, tuple) and result[0] is None):
            return None, None, None

        response_data, extra, finish = result

        if (getattr(company_bot, 'use_vector_service', False) and isinstance(response_data, dict) and
                response_data.get('function_call', {}).get('name') == 'search_knowledge_base'):
            query = response_data['function_call'].get('arguments', {}).get('query', '')
            return self._handle_vector_search(
                query=query, system_prompt=system_prompt, messages=messages,
                company_bot=company_bot, tools=tools, session_id=session_id,
                profile_id=profile_id, channel_name=channel_name,
                cache_policy=cache_policy, metadata=metadata, use_stream=use_stream,
            )

        return response_data, extra, finish

    def _call_gateway_non_stream(
        self, gateway_messages, company_bot, session_id, profile_id, tools, tool_choice,
        retrieved_chunks=None,
    ):
        """Single non-streaming LLM call, returns (response, extra, finish_reason)."""
        import json
        try:
            data = call_llm_gateway(
                messages=gateway_messages, provider=company_bot.provider, model=company_bot.llm_model,
                params=build_gateway_params(company_bot), tools=tools, tool_choice=tool_choice,
            )
            if not data:
                return None, None, None

            choice = data.get('choices', [{}])[0]
            message = choice.get('message', {})
            finish_reason = choice.get('finish_reason')
            tool_calls = message.get('tool_calls') or []

            if tool_calls:
                tc = tool_calls[0]
                raw_args = tc.get('function', {}).get('arguments', '{}')
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                return {'function_call': {'name': tc['function']['name'], 'arguments': arguments}}, None, 'function_call'

            content = message.get('content', '')
            print(f'[non_stream_response] finish_reason={finish_reason} content={repr(content[:200])}')
            if content:
                save_in_company_db(
                    session_id=session_id, profile_id=profile_id, initiated_by='AI',
                    message=content, chunks=retrieved_chunks, status=ChatStatus.IN_PROGRESS, stage=None,
                )
            return content, None, finish_reason

        except Exception as e:
            logger.error(f'Error in non-stream gateway call: {e}', exc_info=True)
            return None, None, None

    def _handle_vector_search(
        self, query, system_prompt, messages, company_bot, tools,
        session_id, profile_id, channel_name, cache_policy, metadata, use_stream,
    ):
        """Fetch vector context, then make the final LLM call (stream or non-stream)."""
        from chatbot.services.vector.vector_service import fetch_context_for_query

        context, retrieved_chunks = fetch_context_for_query(query=query, company_bot=company_bot)
        enriched_prompt = system_prompt + context if context else system_prompt
        print(f'[vector_search] enriched system prompt:\n{enriched_prompt}...')

        enriched_messages = []
        if enriched_prompt:
            enriched_messages.append({'role': 'system', 'content': enriched_prompt})
        enriched_messages += messages

        remaining_tools = [
            t for t in (tools or [])
            if t.get('function', {}).get('name') != 'search_knowledge_base'
        ] or None
        remaining_tool_choice = 'auto' if remaining_tools else None

        if use_stream and channel_name:
            return self._handle_gateway_stream(
                gateway_messages=enriched_messages, company_bot=company_bot,
                session_id=session_id, profile_id=profile_id, tools=remaining_tools,
                tool_choice=remaining_tool_choice, channel_name=channel_name,
                cache_policy=cache_policy, metadata=metadata, retrieved_chunks=retrieved_chunks,
            )

        return self._call_gateway_non_stream(
            gateway_messages=enriched_messages, company_bot=company_bot,
            session_id=session_id, profile_id=profile_id,
            tools=remaining_tools, tool_choice=remaining_tool_choice,
            retrieved_chunks=retrieved_chunks,
        )

    def _handle_gateway_stream(
        self, gateway_messages, company_bot, session_id, profile_id, tools, tool_choice,
        channel_name, cache_policy=None, metadata=None, retrieved_chunks=None,
    ):
        import json
        accumulated_content = []
        tool_calls_buffer = {}  # index -> {id, name, arguments}
        finish_reason = None
        try:
            for delta_content, tool_use_delta, chunk_finish_reason in call_llm_gateway_stream(
                messages=gateway_messages, provider=company_bot.provider, model=company_bot.llm_model,
                params=build_gateway_params(company_bot), tools=tools, tool_choice=tool_choice,
                cache_policy=cache_policy, metadata=metadata,
            ):
                if delta_content:
                    accumulated_content.append(delta_content)
                    self._send_chunk(channel_name, delta_content, finish_reason=None)
                if tool_use_delta:
                    idx = tool_use_delta.get('index', 0)
                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {'id': '', 'name': '', 'arguments': ''}
                    if tool_use_delta.get('id'):
                        tool_calls_buffer[idx]['id'] = tool_use_delta['id']
                    if tool_use_delta.get('name'):
                        tool_calls_buffer[idx]['name'] = tool_use_delta['name']
                    tool_calls_buffer[idx]['arguments'] += tool_use_delta.get('arguments_delta') or ''
                if chunk_finish_reason:
                    finish_reason = chunk_finish_reason

            if tool_calls_buffer:
                tc = tool_calls_buffer[0]
                raw_args = tc['arguments']
                try:
                    arguments = json.loads(raw_args)
                except json.JSONDecodeError:
                    import json_repair
                    arguments = json_repair.repair_json(raw_args, return_objects=True)
                return {'function_call': {'name': tc['name'], 'arguments': arguments}}, None, 'function_call'

            content = ''.join(accumulated_content)
            print(f'[stream_response] finish_reason={finish_reason} content={repr(content[:200])}')
            if content:
                save_in_company_db(
                    session_id=session_id, profile_id=profile_id, initiated_by='AI',
                    message=content, chunks=retrieved_chunks, status=ChatStatus.IN_PROGRESS, stage=None,
                )
            self._send_chunk(channel_name, '', finish_reason=finish_reason or 'stop')
            return content, None, finish_reason or 'stop'

        except Exception as e:
            logger.error(f'Error in gateway stream handling: {e}', exc_info=True)
            return None, None, None

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
