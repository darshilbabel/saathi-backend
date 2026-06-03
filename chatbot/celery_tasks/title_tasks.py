from celery import shared_task
from chatbot.models import ChatSession, CompanyChat, Voice, VoiceType
from chatbot.models.company_models import Flow
from chatbot.llm_models.llm_gateway import call_llm_gateway, build_gateway_params
from chatbot.utils.chat_utils import get_guided_chat
from chatbot.utils.audio_provider_utils import text_translate_provider
import json_repair
import logging

logger = logging.getLogger('django')


def _track_usage(session_id, response):
    try:
        usage = response.get('usage', {}) or {}
        cost = response.get('cost', {}) or {}
        usage_cost = {
            'input_tokens': usage.get('input_tokens', 0) or 0,
            'output_tokens': usage.get('output_tokens', 0) or 0,
            'total_tokens': usage.get('total_tokens', 0) or 0,
            'cost_usd': cost.get('computed_usd', 0) or 0,
        }
        if not any(usage_cost.values()):
            return
        session = ChatSession.objects.get(session=session_id)
        other_params = session.other_params or {}
        totals = other_params.get('usage', {})
        logger.info("[usage] title call session %s before update: %s | this call: %s", session_id, totals, usage_cost)
        totals['total_input_tokens'] = totals.get('total_input_tokens', 0) + usage_cost['input_tokens']
        totals['total_output_tokens'] = totals.get('total_output_tokens', 0) + usage_cost['output_tokens']
        totals['total_tokens'] = totals.get('total_tokens', 0) + usage_cost['total_tokens']
        totals['total_cost_usd'] = round(totals.get('total_cost_usd', 0) + usage_cost['cost_usd'], 6)
        other_params['usage'] = totals
        session.other_params = other_params
        session.save(update_fields=['other_params'])
        logger.info("[usage] title call session %s after update: %s", session_id, totals)
    except Exception as e:
        logger.error("[usage] failed to track title usage for session %s: %s", session_id, e)


@shared_task
def generate_session_title(session_id, language='en'):
    session = ChatSession.objects.filter(session=session_id).first()
    if not session or session.title:
        return

    flow = Flow.objects.filter(bot=session.company_bot).first()
    if not flow or not flow.title_bot:
        logger.info("No title bot configured for session %s", session_id)
        return

    company_bot = flow.title_bot
    logger.info("Generating title for session %s using bot %s", session_id, company_bot.id)

    company_chats = (
        CompanyChat.objects
        .select_related('sender', 'receiver')
        .filter(session=session_id)
        .order_by('created_at')
        .values("receiver", "receiver__id", "translated_message", "message", "status", "created_at")
    )
    messages = get_guided_chat(company_bot=company_bot, company_chats=company_chats)

    tools = company_bot.tool_context
    if tools and isinstance(tools, str):
        tools = json_repair.repair_json(tools, return_objects=True)

    tool_choice = None
    if isinstance(tools, dict):
        tool_choice = tools.get('tool_choice', 'auto')
        tools = tools.get('tools') or tools.get('tool')
    elif isinstance(tools, list):
        tool_choice = 'auto'

    system_msg = {'role': 'system', 'content': company_bot.context}
    response = call_llm_gateway(
        messages=[system_msg] + list(messages),
        provider=company_bot.provider,
        model=company_bot.llm_model,
        params=build_gateway_params(company_bot),
        tools=tools or None,
        tool_choice=tool_choice,
    )

    if not response:
        logger.error("LLM gateway returned no response for title generation, session %s", session_id)
        return

    _track_usage(session_id, response)

    try:
        import json as _json
        choice = response.get('choices', [{}])[0]
        message = choice.get('message', {})
        tool_calls = message.get('tool_calls') or []
        title_tc = next(
            (tc for tc in tool_calls if tc.get('function', {}).get('name') == 'generate_title'),
            None,
        )
        if not title_tc:
            logger.error("generate_title tool call missing in response for session %s", session_id)
            return
        raw_args = title_tc.get('function', {}).get('arguments', '{}')
        arguments = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        output_title = arguments.get('title')
    except Exception as e:
        logger.error("Error extracting title for session %s: %s", session_id, e)
        return

    if not output_title:
        logger.error("No title value in generate_title tool call for session %s", session_id)
        return

    if language != 'en':
        voice_provider = Voice.objects.filter(
            company_bot=company_bot, type=VoiceType.TextToText, language=language
        ).first()
        translated = text_translate_provider(
            voice_provider=voice_provider, message_body=output_title, target_language=language,
            source_language='en'
        )
        if translated.get('status') == 200:
            output_title = translated.get('content')

    session.save_title(output_title)
    logger.info("Title saved for session %s: %s", session_id, output_title)