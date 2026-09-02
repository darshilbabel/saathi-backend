"""
Provider-slug -> dispatch-handler registries for TTS / STT / translation calls.

Decouples "a Provider row exists" (freely admin-nameable) from "dispatch code
knows how to call it" (only providers with a registered adapter here actually
work). Adding a Provider row for a brand-new vendor does NOT make calls to it
work — a handler must be added to the relevant registry below first.

Each adapter wraps one of the existing chatbot/translate/*/*.py functions with
a uniform per-operation signature; those underlying functions are untouched.
"""
from django.conf import settings

from chatbot.constants.provider_slugs import AI4BHARAT, GOOGLE, OPENAI_WHISPER, SARVAM, CUSTOM_LLM
from chatbot.models import LanguageMapping
from chatbot.translate.ai4Bharat.speech_to_text import transcribe_ai4bharat_multiple_chunks
from chatbot.translate.ai4Bharat.text_to_speech import ai4bharat_text_speech
from chatbot.translate.ai4Bharat.text_to_text import call_ai4bharat_translation_api
from chatbot.translate.ai4Bharat.transliterate import call_ai4bharat_transliterate_api
from chatbot.translate.custom.custom_llm import handle_custom_translation
from chatbot.translate.google.google_stt import transcribe_multiple_languages_v2
from chatbot.translate.google.google_translate import translate_text
from chatbot.translate.google.google_tts import google_text_to_speech
from chatbot.translate.openai.openai_stt import transcribe_audio
from chatbot.translate.sarvam.sarvam import SarvamLanguageService
from chatbot.translate.sarvam.speech_to_text import transcribe_sarvam_multiple_chunks
from chatbot.translate.sarvam.text_to_speech import sarvam_text_to_speech


class NoDispatchHandlerError(Exception):
    """Raised when a Provider row has no registered handler for the requested operation."""


def _resolve_custom_llm_bot(voice_provider):
    from chatbot.models import CompanyBot
    other = getattr(voice_provider, "other_params", {}) or {}
    route = other.get('route', "/transliterate_text")
    return CompanyBot.objects.filter(route=route).first()


TTS_DISPATCH = {
    AI4BHARAT: lambda text, source_language, voice_provider: ai4bharat_text_speech(
        text=text, gender=voice_provider.gender, source_language=source_language, voice_provider=voice_provider
    ),
    GOOGLE: lambda text, source_language, voice_provider: google_text_to_speech(
        message=text, language_code=LanguageMapping.get_mapped_language(source_language), voice_provider=voice_provider
    ),
    SARVAM: lambda text, source_language, voice_provider: sarvam_text_to_speech(
        message=text, source_language=source_language, voice_provider=voice_provider
    ),
}

STT_DISPATCH = {
    AI4BHARAT: lambda base64_audio, audio_format, source_language, voice_provider: transcribe_ai4bharat_multiple_chunks(
        base64_audio_file=base64_audio, source_language=source_language, audio_format=audio_format,
        voice_provider=voice_provider
    ),
    GOOGLE: lambda base64_audio, audio_format, source_language, voice_provider: transcribe_multiple_languages_v2(
        project_id=settings.SECRETS.get('project_id'), audio_file=base64_audio,
        language_codes=[LanguageMapping.get_mapped_language(source_language, "US" if source_language == "en" else "IN")],
        voice_provider=voice_provider
    ),
    OPENAI_WHISPER: lambda base64_audio, audio_format, source_language, voice_provider: transcribe_audio(
        base64_audio=base64_audio, audio_format=audio_format, source_language=source_language,
        voice_provider=voice_provider
    ),
    SARVAM: lambda base64_audio, audio_format, source_language, voice_provider: transcribe_sarvam_multiple_chunks(
        base64_audio_file=base64_audio, audio_format=audio_format,
        source_language=LanguageMapping.get_sarvam_language(source_language), voice_provider=voice_provider
    ),
}

TRANSLATE_DISPATCH = {
    AI4BHARAT: lambda message_body, source_language, target_language, voice_provider, company_bot: call_ai4bharat_translation_api(
        source_language=source_language, target_language=target_language, message_body=message_body,
        voice_provider=voice_provider
    ),
    GOOGLE: lambda message_body, source_language, target_language, voice_provider, company_bot: translate_text(
        project_id=settings.SECRETS.get('project_id'), text=message_body,
        source_language_code=LanguageMapping.get_google_translate_language(source_language),
        target_language_code=LanguageMapping.get_google_translate_language(target_language),
        voice_provider=voice_provider
    ),
    SARVAM: lambda message_body, source_language, target_language, voice_provider, company_bot: SarvamLanguageService().translate(
        input_text=message_body, source_lang=LanguageMapping.get_sarvam_language(source_language),
        target_lang=LanguageMapping.get_sarvam_language(target_language), voice_provider=voice_provider
    ),
    CUSTOM_LLM: lambda message_body, source_language, target_language, voice_provider, company_bot: handle_custom_translation(
        message_body=message_body, source_language=LanguageMapping.get_mapped_language(source_language),
        target_language=LanguageMapping.get_mapped_language(target_language),
        company_bot=_resolve_custom_llm_bot(voice_provider)
    ),
}

TRANSLITERATE_DISPATCH = {
    AI4BHARAT: lambda message_body, source_language, target_language, voice_provider, company_bot, is_sentence: call_ai4bharat_transliterate_api(
        source_language=source_language, target_language=target_language, message_body=message_body,
        is_sentence=is_sentence
    ),
    SARVAM: lambda message_body, source_language, target_language, voice_provider, company_bot, is_sentence: SarvamLanguageService().transliterate(
        input_text=message_body, source_lang=LanguageMapping.get_mapped_language(source_language),
        target_lang=LanguageMapping.get_mapped_language(target_language), voice_provider=voice_provider
    ),
    CUSTOM_LLM: lambda message_body, source_language, target_language, voice_provider, company_bot, is_sentence: handle_custom_translation(
        message_body=message_body, source_language=LanguageMapping.get_mapped_language(source_language),
        target_language=LanguageMapping.get_mapped_language(target_language),
        company_bot=_resolve_custom_llm_bot(voice_provider)
    ),
}


def get_handler(registry, provider_slug, operation_label):
    handler = registry.get(provider_slug)
    if handler is None:
        raise NoDispatchHandlerError(
            f"No {operation_label} dispatch handler registered for provider slug {provider_slug!r}. "
            f"Registered slugs: {sorted(registry.keys())}. Add one in chatbot/constants/provider_dispatch.py."
        )
    return handler
