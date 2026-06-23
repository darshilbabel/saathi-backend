import base64
import io
import re
import wave

from chatbot.constants.voice_provider_defaults import get_provider_defaults
from chatbot.models import VoiceProvider, LanguageMapping, Voice, VoiceType, CompanyBot
from chatbot.translate.ai4Bharat.speech_to_text import transcribe_ai4bharat_multiple_chunks
from chatbot.translate.ai4Bharat.text_to_speech import ai4bharat_text_speech
from chatbot.translate.ai4Bharat.text_to_text import call_ai4bharat_translation_api
from chatbot.translate.custom.custom_llm import handle_custom_translation
from chatbot.translate.google.google_stt import transcribe_multiple_languages_v2
from chatbot.translate.google.google_stt_v1 import transcribe_multiple_languages_v1
from chatbot.translate.google.google_translate import translate_text
from chatbot.translate.google.google_tts import google_text_to_speech
from chatbot.translate.openai.openai_stt import transcribe_audio
from chatbot.translate.sarvam.sarvam import SarvamLanguageService
from chatbot.translate.sarvam.speech_to_text import transcribe_sarvam_multiple_chunks
from chatbot.translate.sarvam.text_to_speech import sarvam_text_to_speech
from django.conf import settings
import logging


logger = logging.getLogger('django')


def strip_markdown_for_tts(text: str) -> str:
    """Strip markdown formatting so TTS engines receive clean natural-language text."""
    if text is None:
        return ""
    if not text:
        return text

    # Fenced code blocks — remove entirely (before anything else to avoid inner matches)
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Inline code — remove
    text = re.sub(r'`[^`\n]+`', '', text)
    # Images first — must precede links or [alt](url) gets consumed leaving a stray !
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Links — keep display text, drop URL
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Horizontal rules: standalone line of 3+ hyphens / underscores / asterisks
    text = re.sub(r'^\s*[-_*]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Multiple consecutive hyphens used as em/en dash
    text = re.sub(r'-{2,}', ' ', text)
    # Table separator rows (|---|:---|)
    text = re.sub(r'^\|[\s\-|:]+\|$', '', text, flags=re.MULTILINE)
    # Table cell pipes → space
    text = re.sub(r'\|', ' ', text)
    # Heading markers (# / ## / ### etc.)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Blockquote markers
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # HTML line breaks → period so TTS pauses between items (LLM uses <br> inside table cells)
    text = re.sub(r'\s*<br\s*/?>\s*', '. ', text, flags=re.IGNORECASE)
    # Any remaining HTML tags → remove
    text = re.sub(r'<[^>]+>', '', text)
    # Unicode bullet character • → remove (period from <br> already provides the pause)
    text = re.sub(r'•\s*', '', text)
    # Clean up double periods that arise when text before <br> already ended with punctuation
    text = re.sub(r'([.!?])\s*\.\s*', r'\1 ', text)
    # Bullet list markers BEFORE bold/italic — prevents * bullet + **bold** from being misread as nested *{1,3}
    text = re.sub(r'^[ \t]*[-*]\s+', '', text, flags=re.MULTILINE)
    # Bold + italic with asterisks: ***text*** / **text** / *text*  (single-line, non-nested)
    text = re.sub(r'\*{1,3}([^\n*]*?)\*{1,3}', r'\1', text)
    # Bold + italic with underscores: __text__ / _text_  (single-line, non-nested)
    text = re.sub(r'_{1,2}([^\n_]*?)_{1,2}', r'\1', text)
    # Remaining lone asterisks / underscores not attached to word characters
    text = re.sub(r'(?<!\w)[*_]+(?!\w)', '', text)
    # "1: 30" is read as a time by TTS engines — replace colon with comma
    text = re.sub(r'(?<!\d)(\d+)\s*:\s*(\d+)(?!\d)', r'\1, \2', text)
    # Collapse 3+ consecutive newlines → 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse multiple spaces/tabs → single space
    text = re.sub(r'[ \t]{2,}', ' ', text)

    return text.strip()


def get_voice_provider(company_bot, voice_type, source_language=None, target_language=None):
    """Return appropriate Voice provider preferring non-English language."""

    language = (
        target_language if target_language and target_language.lower() != "en"
        else source_language if source_language and source_language.lower() != "en"
        else "en"
    )

    voice = Voice.objects.filter(
        company_bot=company_bot,
        type=voice_type,
        language=language,
    ).first()

    if not voice and language != "en":
        voice = Voice.objects.filter(
            company_bot=company_bot,
            type=voice_type,
            language="en",
        ).first()

    return voice


def _get_tts_byte_limit(voice_provider) -> int:
    """Return the UTF-8 byte limit for a single TTS request, merged from provider defaults and voice other_params."""
    try:
        if not voice_provider:
            return 4800
        defaults = get_provider_defaults(voice_provider.provider, VoiceType.TextToSpeech)
        merged = {**defaults, **(voice_provider.other_params or {})}
        limit = int(merged.get('tts_byte_limit', 4800))
        return limit if limit > 0 else 4800
    except Exception as e:
        logger.error("Failed to read tts_byte_limit, using default 4800: %s", e)
        return 4800


def _split_on_words(text: str, byte_limit: int) -> list:
    """Split text on word boundaries when a single sentence exceeds byte_limit."""
    try:
        words = text.split()
        chunks, current = [], ""
        for word in words:
            if len(word.encode('utf-8')) > byte_limit:
                if current:
                    chunks.append(current)
                    current = ""
                part = ""
                for ch in word:
                    candidate = f"{part}{ch}"
                    if len(candidate.encode('utf-8')) <= byte_limit:
                        part = candidate
                    else:
                        if part:
                            chunks.append(part)
                        part = ch
                if part:
                    chunks.append(part)
                continue
            candidate = f"{current} {word}".strip() if current else word
            if len(candidate.encode('utf-8')) <= byte_limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = word
        if current:
            chunks.append(current)
        return chunks or [text]
    except Exception as e:
        logger.error("TTS word split failed, returning text as-is: %s", e)
        return [text]


def _split_text_for_tts(text: str, byte_limit: int) -> list:
    """Split text into byte-safe chunks at sentence boundaries, falling back to word boundaries."""
    try:
        if len(text.encode('utf-8')) <= byte_limit:
            return [text]

        # Split on sentence boundaries (।, ., ?, !) and paragraph breaks, keeping delimiters
        sentences = re.split(r'(?<=[।.?!])\s+|\n\n+', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        chunks, current = [], ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate.encode('utf-8')) <= byte_limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                if len(sentence.encode('utf-8')) > byte_limit:
                    word_chunks = _split_on_words(sentence, byte_limit)
                    chunks.extend(word_chunks[:-1])
                    current = word_chunks[-1] if word_chunks else ""
                else:
                    current = sentence

        if current:
            chunks.append(current)

        return chunks or [text]
    except Exception as e:
        logger.error("TTS text split failed, returning text as single chunk: %s", e)
        return [text]


def _detect_audio_format(audio_bytes: bytes) -> str:
    """Detect audio format from magic bytes — 'wav' for RIFF, 'mp3' otherwise."""
    try:
        return 'wav' if audio_bytes[:4] == b'RIFF' else 'mp3'
    except Exception as e:
        logger.error("Audio format detection failed, defaulting to mp3: %s", e)
        return 'mp3'


def _merge_audio_base64(chunks_b64: list, audio_format: str) -> str:
    """Merge base64-encoded audio chunks into one. WAV uses stdlib wave; MP3 uses raw byte concat."""
    if len(chunks_b64) == 1:
        return chunks_b64[0]

    try:
        raw_chunks = [base64.b64decode(c) for c in chunks_b64]

        if audio_format == 'wav':
            output = io.BytesIO()
            with wave.open(output, 'wb') as out_wav:
                params_set = False
                for chunk in raw_chunks:
                    with wave.open(io.BytesIO(chunk), 'rb') as in_wav:
                        if not params_set:
                            out_wav.setparams(in_wav.getparams())
                            params_set = True
                        out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))
            return base64.b64encode(output.getvalue()).decode('utf-8')

        # MP3: frames are independently decodable, raw concatenation works
        return base64.b64encode(b''.join(raw_chunks)).decode('utf-8')
    except Exception as e:
        logger.error("TTS audio merge failed: %s", e)
        raise ValueError("Failed to merge TTS audio chunks") from e


def _call_single_tts(text: str, source_language: str, voice_provider) -> dict:
    """Dispatch a single TTS request to the configured provider and return its response dict."""
    if voice_provider.provider == VoiceProvider.AI4Bharat:
        return ai4bharat_text_speech(
            text=text, gender=voice_provider.gender, source_language=source_language,
            voice_provider=voice_provider
        )
    elif voice_provider.provider == VoiceProvider.GOOGLE:
        return google_text_to_speech(
            message=text, language_code=LanguageMapping.get_mapped_language(source_language),
            voice_provider=voice_provider
        )
    elif voice_provider.provider == VoiceProvider.SARVAM:
        return sarvam_text_to_speech(
            message=text, source_language=source_language, voice_provider=voice_provider
        )
    return {'status': 500, 'content': "No provider found!"}


def text_speech_provider(company_bot, text, source_language):
    text = strip_markdown_for_tts(text)
    logger.info("TTS strip text: %s", text)
    voice_provider = get_voice_provider(
        company_bot=company_bot, voice_type=VoiceType.TextToSpeech, source_language=source_language
    )
    if not voice_provider:
        return {
            'status': 500,
            'content': "No voice configuration found!"
        }

    byte_limit = _get_tts_byte_limit(voice_provider)

    if len(text.encode('utf-8')) <= byte_limit:
        return _call_single_tts(text, source_language, voice_provider)

    logger.info("TTS text exceeds %d bytes (%d bytes), splitting into chunks", byte_limit, len(text.encode('utf-8')))
    chunks = _split_text_for_tts(text, byte_limit)
    logger.info("TTS split into %d chunks", len(chunks))

    audio_parts = []
    audio_format = None

    for i, chunk in enumerate(chunks):
        response = _call_single_tts(chunk, source_language, voice_provider)
        if response['status'] != 200:
            logger.error("TTS chunk %d/%d failed: %s", i + 1, len(chunks), response['content'])
            return response
        try:
            chunk_bytes = base64.b64decode(response['content'], validate=True)
        except Exception as e:
            logger.error("TTS chunk %d/%d returned invalid base64: %s", i + 1, len(chunks), e)
            return {'status': 500, 'content': "Invalid audio payload from TTS provider"}
        if audio_format is None:
            audio_format = _detect_audio_format(chunk_bytes)
        audio_parts.append(response['content'])

    try:
        merged_audio = _merge_audio_base64(audio_parts, audio_format)
    except ValueError:
        return {'status': 500, 'content': "Failed to merge audio chunks"}

    return {'status': 200, 'content': merged_audio}


def speech_text_provider(company_bot, base64, audio_format, source_language):
    voice_provider = get_voice_provider(
        company_bot=company_bot, voice_type=VoiceType.SpeechToText, source_language=source_language
    )
    if not voice_provider:
        return {
            'status': 500,
            'content': "No voice configuration found!"
        }

    if voice_provider.provider == VoiceProvider.AI4Bharat:
        response = transcribe_ai4bharat_multiple_chunks(
            base64_audio_file=base64, source_language=source_language, audio_format=audio_format,
            voice_provider=voice_provider
        )
    elif voice_provider.provider == VoiceProvider.GOOGLE:
        if source_language == 'en':
            region = "US"
        else:
            region = "IN"

        secret = settings.SECRETS
        response = transcribe_multiple_languages_v2(
            project_id=secret.get('project_id'), audio_file=base64,
            language_codes=[LanguageMapping.get_mapped_language(source_language, region)],
            voice_provider=voice_provider
        )
    elif voice_provider.provider == VoiceProvider.OPENAI_WHISPER:
        response = transcribe_audio(
            base64_audio=base64, audio_format=audio_format, source_language=source_language,
            voice_provider=voice_provider
        )
    elif voice_provider.provider == VoiceProvider.SARVAM:
        response = transcribe_sarvam_multiple_chunks(
            base64_audio_file=base64, audio_format=audio_format,
            source_language=LanguageMapping.get_sarvam_language(source_language),
            voice_provider=voice_provider
        )

    else:
        return {
            'status': 500,
            'content': "No provider found!"
        }
    return response


def text_translate_provider(message_body, target_language, source_language, voice_provider=None, company_bot=None):
    try:
        if not voice_provider and company_bot:
            voice_provider = get_voice_provider(
                company_bot=company_bot, voice_type=VoiceType.TextToText, source_language=source_language,
                target_language=target_language
            )
        if voice_provider.provider == VoiceProvider.AI4Bharat:
            response = call_ai4bharat_translation_api(
                source_language=source_language, target_language=target_language, message_body=message_body,
                voice_provider=voice_provider
            )
        elif voice_provider.provider == VoiceProvider.GOOGLE:
            secret = settings.SECRETS
            response = translate_text(
                project_id=secret.get('project_id'), text=message_body,
                source_language_code=LanguageMapping.get_google_translate_language(source_language),
                target_language_code=LanguageMapping.get_google_translate_language(target_language),
                voice_provider=voice_provider
            )
        elif voice_provider.provider == VoiceProvider.SARVAM:
            service = SarvamLanguageService()
            response = service.translate(
                input_text=message_body,
                source_lang=LanguageMapping.get_sarvam_language(source_language),
                target_lang=LanguageMapping.get_sarvam_language(target_language),
                voice_provider=voice_provider
            )
        elif voice_provider.provider == VoiceProvider.CUSTOM_LLM:
            other = getattr(voice_provider, "other_params", {}) or {}
            route = other.get('route', "/transliterate_text")
            company_bot = CompanyBot.objects.filter(route=route).first()
            response = handle_custom_translation(
                message_body=message_body, source_language=LanguageMapping.get_mapped_language(source_language),
                target_language=LanguageMapping.get_mapped_language(target_language), company_bot=company_bot
            )
        else:
            return {
                'status': 500,
                'content': "No provider found!"
            }
        return response
    except Exception as e:
        return {
            'status': 500,
            'content': str(e)
        }
