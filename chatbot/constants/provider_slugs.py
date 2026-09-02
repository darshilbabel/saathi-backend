from chatbot.models.enums import VoiceProvider

# Slugs for the providers that currently have working dispatch handlers
# (see chatbot/constants/provider_dispatch.py). Kept in this lightweight,
# translate-module-free file (only depends on chatbot.models.enums) so it can
# be safely imported from chatbot/models/company_models.py without risking a
# circular import through chatbot.translate.*, which itself imports chatbot.models.
GOOGLE = "google"
AI4BHARAT = "ai4bharat"
OPENAI_WHISPER = "openai-whisper"
SARVAM = "sarvam"
CUSTOM_LLM = "custom-llm"

# Legacy Voice.provider (VoiceProvider enum value) <-> new Provider.slug.
# Voice.save() uses this to keep the legacy `provider` CharField synced to
# provider_ref, and bot_resource.py's import path uses it in reverse to
# resolve a legacy provider string back to a Provider row.
VOICE_PROVIDER_TO_SLUG = {
    VoiceProvider.GOOGLE: GOOGLE,
    VoiceProvider.AI4Bharat: AI4BHARAT,
    VoiceProvider.OPENAI_WHISPER: OPENAI_WHISPER,
    VoiceProvider.SARVAM: SARVAM,
    VoiceProvider.CUSTOM_LLM: CUSTOM_LLM,
}
SLUG_TO_VOICE_PROVIDER = {slug: provider for provider, slug in VOICE_PROVIDER_TO_SLUG.items()}
