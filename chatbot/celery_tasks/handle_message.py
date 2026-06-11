from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from chatbot.models import RouteLanguageChoices, Voice, VoiceType
from chatbot.utils.audio_provider_utils import text_translate_provider
import logging


channel_layer = get_channel_layer()
logger = logging.getLogger('django')


def _translate_chips(extra_content, voice_provider, route):
    if not extra_content or not voice_provider:
        return extra_content
    chips = extra_content.get('quick_reply_chips')
    if not chips:
        return extra_content
    translated = []
    for chip in chips:
        if not isinstance(chip, str):
            translated.append(chip)
            continue
        try:
            resp = text_translate_provider(
                voice_provider=voice_provider, message_body=chip,
                target_language=route, source_language='en'
            )
            if resp.get('status') == 200:
                translated.append(resp.get('content') or chip)
            else:
                logger.error('[_translate_chips] chip translation failed status=%s — using original', resp.get('status'))
                translated.append(chip)
        except Exception as e:
            logger.error('[_translate_chips] chip translation exception: %s — using original', e)
            translated.append(chip)
    return {**extra_content, 'quick_reply_chips': translated}


def translate_and_send_message(
        accumulated_message, current_channel_name, current_step_number, finish_reason, route, company_bot,
        extra_content=None, is_bot_vernacular_message=False
):

    if route != 'en' and accumulated_message and accumulated_message!= '':
        # target_language_code = get_language_code_from_route(route)
        logger.info(f"target_language_code date: %s", route)

        if is_bot_vernacular_message:
            # Message is already in the target language — skip translation entirely.
            translated_messages = accumulated_message
            voice_provider = None
        else:
            voice_provider = Voice.objects.filter(
                company_bot=company_bot, type=VoiceType.TextToText, language=route
            ).first()

            response = text_translate_provider(
                voice_provider=voice_provider, message_body=accumulated_message, target_language=route,
                source_language='en'
            )
            if response.get('status') == 200:
                translated_messages = response.get('content')
            else:
                translated_messages = accumulated_message

        extra_content = _translate_chips(extra_content, voice_provider, route)

        async_to_sync(channel_layer.send)(
            current_channel_name,
            {
                "type": "chat.message",
                "text": {
                    "msg": translated_messages,
                    "source": "bot",
                    "finish_reason": finish_reason,
                    "step": current_step_number,
                    "extra_content": extra_content
                },
            },
        )
        logger.info(f"Translated message: %s", translated_messages)
        return translated_messages
    else:
        logger.info(f"Sending  accumulated_message: %s", accumulated_message)
        async_to_sync(channel_layer.send)(
            current_channel_name,
            {
                "type": "chat.message",
                "text": {
                    "msg": accumulated_message,
                    "source": "bot",
                    "finish_reason": finish_reason,
                    "step": current_step_number,
                    "extra_content": extra_content
                },
            },
        )
        return None

def get_language_code_from_route(route):
    route = route.strip()
    for choice in RouteLanguageChoices:
        if choice.value == route:
            return choice.value
    return RouteLanguageChoices.ENGLISH.value
