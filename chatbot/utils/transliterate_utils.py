from chatbot.constants.provider_dispatch import TRANSLITERATE_DISPATCH, get_handler, NoDispatchHandlerError
from chatbot.models import VoiceType
from chatbot.utils.audio_provider_utils import get_voice_provider


def transliterate_text(
        source_language, target_language, message_body, is_sentence=False, voice_provider=None, company_bot=None
):
    try:
        effective_source_language, effective_target_language = source_language, target_language

        if not voice_provider and company_bot:
            voice_provider, effective_language = get_voice_provider(
                company_bot=company_bot, voice_type=VoiceType.Transliterate, source_language=source_language,
                target_language=target_language
            )
            if target_language and target_language.lower() != "en":
                effective_target_language = effective_language
            elif source_language and source_language.lower() != "en":
                effective_source_language = effective_language

        try:
            handler = get_handler(TRANSLITERATE_DISPATCH, voice_provider.provider_slug, "transliteration")
        except NoDispatchHandlerError as e:
            return {'status': 500, 'content': str(e)}

        return handler(
            message_body=message_body, source_language=effective_source_language,
            target_language=effective_target_language, voice_provider=voice_provider, company_bot=company_bot,
            is_sentence=is_sentence
        )
    except Exception as e:
        return {
            'status': 500,
            'content': message_body
        }


def get_transliteration_output(data):
    if data and isinstance(data, dict):
        data = data.get('content', [])
    if data and isinstance(data, list) and len(data) > 0:
        return data[0]

    return None
