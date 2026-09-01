import json
import logging
import os
from copy import deepcopy

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from simple_history.models import HistoricalRecords

from chatbot.constants.tool_definitions import SEARCH_KNOWLEDGE_BASE_TOOL, SEARCH_KNOWLEDGE_BASE_TOOL_NAME
from chatbot.constants.voice_provider_defaults import get_provider_defaults, VOICE_PROVIDER_DEFAULTS
from chatbot.constants.provider_slugs import VOICE_PROVIDER_TO_SLUG, SLUG_TO_VOICE_PROVIDER
from chatbot.models.enums import (
    CreateStoryChoices, EntityStatus, LLMModel, GenderChoices, ChatStatus,
    FeedbackChoices, CompanyBotTypeChoices, CompanyBotDynamicContextType, CompanyChatSourceChoices,
    VoiceProvider, VoiceType, LLMProvider, EntityTypeChoices, TextConversionType,
    PreProcessType, PreProcessOutputMode, PostProcessType, PostProcessOutputMode,
    UserTypeChoices, OperationTypeChoices, BotStrategyChoices, WebSearchContextSize
)

S3_BASE_URL = os.getenv('S3_BASE_URL')
logger = logging.getLogger(__name__)


class Company(models.Model):
    """
    Represents a company that owns and manages chatbot configurations.
    Stores company details like name, slug, status, and logo.
    """

    def get_file_upload_path(self, filename):
        folder_name = 'chatbot/company/{}'.format(self.slug)
        upload_path = f"{folder_name}/{filename}"
        return upload_path

    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=EntityStatus.choices)
    url = models.URLField(blank=True, null=True)
    logo = models.ImageField(upload_to=get_file_upload_path, max_length=1000, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        indexes = [
            models.Index(fields=['slug']),
        ]

    def get_public_url(self):
        return f"{S3_BASE_URL}{self.logo.name}"


class CompanyBot(models.Model):
    """
    Defines a chatbot configuration for a specific company.
    Stores LLM settings, prompts, provider details, and behavior controls.
    """

    def get_file_upload_path(self, filename):
        folder_name = self.company.slug+'/'+'static-media'
        upload_path = f"{folder_name}/{filename}"
        return upload_path

    name = models.CharField(max_length=100, help_text="Enter the name of the bot.")
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, help_text="Select the company this bot belongs to."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    context = models.TextField(help_text="Provide the bot's main prompt or description of its purpose.")
    max_token = models.IntegerField(default=2048, validators=[MinValueValidator(1)])
    provider = models.CharField(
        max_length=100, choices=LLMProvider.choices, default=LLMProvider.OPENAI)
    provider_keys = models.TextField(
        default="", max_length=1000, null=False, blank=True)
    bot_temperature = models.FloatField(
        default=0,
        help_text="Set the temperature for controlling response randomness (0-1). Lower values produce more "
                  "deterministic responses."
    )
    top_k = models.IntegerField(
        default=2, validators=[MinValueValidator(1)],
        help_text="Set the top-k value for the bot's response selection. This defines how many top options to consider "
                  "for each response."
    )
    provider = models.CharField(
        max_length=100, choices=LLMProvider.choices, default=LLMProvider.BEDROCK,
        help_text="Select the LLM provider (BEDROCK, ANTHROPIC, or OPENAI)"
    )
    provider_keys = models.TextField(
        default="", max_length=1000, null=False, blank=True,
        help_text="API keys or credentials for the selected LLM provider."
    )
    llm_model = models.CharField(
        max_length=100, choices=LLMModel.choices, default=LLMModel.GPT4_O_MINI,
        help_text="Select the LLM model to be used by the bot (e.g., GPT-4o, GPT-4)."
    )
    gateway_provider = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="Select the LLM provider to use. Choices are fetched live from the LLM gateway."
    )
    gateway_model = models.CharField(
        max_length=150, null=True, blank=True,
        help_text="Select the model for the chosen provider. If you just changed the provider, save "
                  "the bot first — the model list here updates to match the new provider after saving."
    )
    gateway_sub_provider = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="Only used when the gateway provider is 'openrouter'. Select which upstream endpoint "
                  "(e.g. DeepInfra, Google, Anthropic) should serve the chosen model. If you just changed "
                  "the model, save the bot first — the choices here update to match after saving."
    )
    filter_score = models.FloatField(
        default=0.8,
        help_text="Set the filter score for bot response selection (0-1). Responses below this score will be "
                  "filtered out."
    )
    end_context = models.TextField(
        null=True, blank=True,
        help_text="Provide additional prompt or context to append at the end of the main prompt to guide the "
                  "conversation"
    )
    introductory_message = models.CharField(
        max_length=1000, null=True, blank=True,
        help_text="Provide an introductory message that the bot will present when the conversation starts."
    )
    tag_context = models.TextField(
        null=True, blank=True,
        help_text="Provide any information or context related to variables (like Python-bound variables) that will be "
                  "inserted into the prompt."
    )
    route = models.CharField(
        max_length=100, default='/', help_text="Specify the route or API endpoint for interacting with the bot."
    )
    bot_type = models.CharField(max_length=30, choices=CompanyBotTypeChoices.choices,
                                default=CompanyBotTypeChoices.SIMPLE)
    strategy = models.CharField(
        max_length=100,
        choices=BotStrategyChoices.choices,
        null=True,
        blank=True,
        help_text="Select the strategy or approach this bot uses for conversations."
    )
    llm_key = models.CharField(max_length=255, null=True, blank=True)
    dynamic_context = models.TextField(
        null=True, blank=True,
        help_text="Provide dynamic context that can be adjusted during the bot's interactions, such as "
                  "personalized data."
    )
    dynamic_context_type = models.CharField(max_length=20, choices=CompanyBotDynamicContextType.choices,
                                            null=True, blank=True)
    pre_context = models.TextField(
        null=True, blank=True, help_text="Provide pre-context that will be set before the main prompt to shape the "
                                         "conversation."
    )
    tool_context = models.TextField(
        null=True, blank=True,
        help_text="JSON tool definitions for the LLM. For SIMPLE bots, the search_knowledge_base "
                  "entry here is auto-added/removed based on Use vector service."
    )
    other_params = models.JSONField(null=True, blank=True)

    connect_timeout = models.FloatField(default=5.0, help_text="Timeout in seconds for establishing a LLM connection.")
    read_timeout = models.FloatField(default=10.0, help_text="Timeout in seconds for reading a LLM response.")
    chat_history_limit = models.IntegerField(
        default=1000, validators=[MinValueValidator(1)],
        help_text=(
            "Controls how many of the most recent chat messages are included "
            "as conversation history when making an LLM request."
        )
    )
    stream = models.BooleanField(
        default=False,
        help_text="Enable streaming mode for LLM responses."
    )
    use_vector_service = models.BooleanField(
        default=False,
        help_text=(
            "Enable vector knowledge base search. Uses a two-step LLM call: "
            "first to extract the search query, then to answer with retrieved context. "
            "SIMPLE bots only: on save, this adds/removes the search_knowledge_base tool "
            "in Tool context automatically, leaving other tools untouched."
        )
    )
    enable_web_search = models.BooleanField(
        default=False,
        help_text="Enable web search via the LLM gateway."
    )
    web_search_context_size = models.CharField(
        max_length=10,
        choices=WebSearchContextSize.choices,
        default=WebSearchContextSize.MEDIUM,
        help_text="Amount of context the web search retrieves. Only used when enable_web_search is True."
    )
    enable_cache = models.BooleanField(
        default=False,
        help_text="Enable prompt/tool caching via the LLM gateway. When enabled, Cache TTL and "
                  "Cache Targets become required."
    )
    cache_ttl = models.CharField(
        max_length=20, null=True, blank=True,
        help_text="TTL to use for cached content. Choices are fetched live from the LLM gateway. "
                  "Required when Enable Cache is checked."
    )
    cache_targets = models.JSONField(
        null=True, blank=True,
        help_text="List of request parts to cache (e.g. ['prompt', 'tools']). Choices are fetched "
                  "live from the LLM gateway. Required when Enable Cache is checked."
    )

    history = HistoricalRecords()

    def __str__(self):
        return self.name

    def clean(self):
        if self.enable_cache:
            errors = {}
            if not self.cache_ttl:
                errors['cache_ttl'] = "Cache TTL is required when Enable Cache is checked."
            if not self.cache_targets:
                errors['cache_targets'] = "Cache targets are required when Enable Cache is checked."
            if errors:
                raise ValidationError(errors)
        elif self.cache_ttl or self.cache_targets:
            self.cache_ttl = None
            self.cache_targets = None

    @staticmethod
    def _tool_entry_name(entry):
        return (entry.get('function', {}) or {}).get('name') or entry.get('name', '')

    def _sync_vector_search_tool(self):
        """Keep the search_knowledge_base tool entry in tool_context in step with
        use_vector_service, for SIMPLE bots only (tool_context on STATE_MACHINE bots
        lives per-step on CompanyStateMachine and is left untouched here).
        Returns True if self.tool_context was changed."""
        if self.bot_type != CompanyBotTypeChoices.SIMPLE:
            return False

        raw = (self.tool_context or '').strip()
        parsed = None
        if raw:
            try:
                import json_repair
                parsed = json_repair.repair_json(raw, return_objects=True)
            except Exception as e:
                logger.error(f"CompanyBot {self.pk}: failed to parse tool_context while syncing vector search tool: {e}")
                return False

        if isinstance(parsed, dict):
            tools_key = 'tools' if 'tools' in parsed else ('tool' if 'tool' in parsed else 'tools')
            tools = list(parsed.get(tools_key) or [])
        elif isinstance(parsed, list):
            tools_key = None
            tools = list(parsed)
        else:
            tools_key = None
            tools = []

        has_tool = any(self._tool_entry_name(t) == SEARCH_KNOWLEDGE_BASE_TOOL_NAME for t in tools)

        if self.use_vector_service:
            if has_tool:
                return False
            tools = tools + [SEARCH_KNOWLEDGE_BASE_TOOL]
        else:
            if not has_tool:
                return False
            tools = [t for t in tools if self._tool_entry_name(t) != SEARCH_KNOWLEDGE_BASE_TOOL_NAME]

        if not tools:
            self.tool_context = None
        elif tools_key:
            parsed[tools_key] = tools
            self.tool_context = json.dumps(parsed, ensure_ascii=False)
        else:
            self.tool_context = json.dumps(tools, ensure_ascii=False)
        return True

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        reset_fields = set()
        if self._sync_vector_search_tool():
            reset_fields |= {'tool_context'}
        gateway_fields_changing = update_fields is None or {'gateway_provider', 'gateway_model'} & set(update_fields)
        if self.pk and gateway_fields_changing:
            old = CompanyBot.objects.filter(pk=self.pk).only('gateway_provider', 'gateway_model').first()
            if old and old.gateway_provider != self.gateway_provider:
                self.gateway_model = None
                self.gateway_sub_provider = None
                reset_fields |= {'gateway_model', 'gateway_sub_provider'}
            elif old and old.gateway_model != self.gateway_model:
                self.gateway_sub_provider = None
                reset_fields |= {'gateway_sub_provider'}

        had_cache_values = bool(self.cache_ttl or self.cache_targets)
        self.clean()
        if had_cache_values and not self.enable_cache:
            reset_fields |= {'cache_ttl', 'cache_targets'}

        # update_fields only persists what's listed — make sure resets we just made
        # in memory are actually included, otherwise they'd be silently dropped.
        if update_fields is not None and reset_fields:
            kwargs['update_fields'] = list(set(update_fields) | reset_fields)
        super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(fields=['company']),
        ]


class CompanyChat(models.Model):
    """
    Represents a chat message exchanged between a user and a company bot.
    Stores message content, session data, metadata, and optional attachments.
    """

    def get_file_upload_path(self, filename):
        folder_name = f'chatbot'
        upload_path = os.path.join(folder_name, filename)
        print("upload_path: ", upload_path)
        return upload_path

    message = models.TextField()
    translated_message = models.TextField(null=True, blank=True)
    chunks = models.TextField(null=True)
    sender = models.ForeignKey('chatbot.Profile', related_name='sender', on_delete=models.SET_NULL, null=True)
    receiver = models.ForeignKey('chatbot.Profile', related_name='receiver', on_delete=models.SET_NULL, null=True)
    session = models.CharField(max_length=255)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=ChatStatus.choices, null=True, blank=True)
    feedback = models.CharField(max_length=20, choices=FeedbackChoices.choices, null=True, blank=True)
    source = models.CharField(max_length=20, choices=CompanyChatSourceChoices.choices,
                              default=CompanyChatSourceChoices.WEB)
    source_msg_id = models.CharField(max_length=256, null=True, blank=True)
    whatsapp_message_id = models.CharField(max_length=255, null=True, blank=True)
    message_type = models.CharField(max_length=20, null=True, blank=True)
    stage = models.CharField(max_length=500, null=True, blank=True)
    other_params = models.JSONField(null=True, blank=True)
    audio_file = models.FileField(upload_to=get_file_upload_path, max_length=1000, null=True, blank=True)
    file_url = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.message

    class Meta:
        indexes = [
            models.Index(fields=['session']),
            models.Index(fields=['created_at']),
            models.Index(fields=['sender']),
            models.Index(fields=['receiver']),
        ]

    def save(self, *args, **kwargs):
        if not self.created_at:
            self.created_at = timezone.now()
        super(CompanyChat, self).save(*args, **kwargs)


class CompanyChatFeedback(models.Model):
    """
    A single feedback submission (thumbs up/down + optional comment) for a bot response.
    Rows are append-only — never updated — so the full history is preserved and the
    most recent row (by created_at) represents the current state.
    """
    company_chat = models.ForeignKey(
        CompanyChat, related_name='feedbacks', on_delete=models.CASCADE,
        help_text='The bot response (CompanyChat row) this feedback is about.'
    )
    thumbs_up = models.BooleanField(
        default=False, help_text='True if the user gave a positive rating in this submission.'
    )
    thumbs_down = models.BooleanField(
        default=False,
        help_text='True if the user gave a negative rating in this submission. '
                   'Cannot be True at the same time as thumbs_up (enforced in the serializer).'
    )
    comment = models.TextField(
        null=True, blank=True, help_text='Optional free-text feedback typed by the user.'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text='When this feedback was submitted. Immutable — also used to determine '
                   'the current state (latest row wins) and submission order.'
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company_chat', '-created_at']),
        ]

    def __str__(self):
        return f'Feedback #{self.id} for CompanyChat #{self.company_chat_id}'


class VoiceManager(models.Manager):
    """Excludes Voice rows that only exist as another row's fallback config."""

    def get_queryset(self):
        return super().get_queryset().filter(is_fallback=False)


class Voice(models.Model):
    """
    Defines a text-to-speech voice configuration for a company bot.
    Stores provider details, language, gender, and playback settings.

    A row can optionally be a fallback config for another (primary) Voice row,
    via `primary_voice` — used to retry translation with a different provider
    if the primary one errors. `is_fallback` is derived automatically from
    `primary_voice` (see save()) and is the authoritative DB-level marker of
    which rows are fallback configs vs. real/primary provider rows. Type and
    company_bot are mirrored from the primary row automatically; language must
    be entered explicitly and is validated to match the primary row's language.
    """

    company_bot = models.ForeignKey(CompanyBot, on_delete=models.SET_NULL, null=True, blank=True)
    type = models.CharField(max_length=300, choices=VoiceType.choices, null=True, blank=True)
    provider = models.CharField(
        max_length=300, null=True, blank=True, choices=VoiceProvider.choices, default=VoiceProvider.AI4Bharat,
        help_text="Legacy — frozen, hidden from admin, auto-synced from provider_ref on save(). "
                   "Kept only for backward compatibility with existing read call sites."
    )
    name = models.CharField(max_length=100, null=True, blank=True)
    sample_link = models.URLField(null=True, blank=True)
    language = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="Legacy — frozen, hidden from admin, auto-synced from language_ref on save(). "
                   "Kept only for backward compatibility with existing read call sites."
    )
    provider_code = models.CharField(max_length=100, null=True, blank=True)
    language_ref = models.ForeignKey(
        'chatbot.Language', on_delete=models.PROTECT, related_name='voices',
        help_text="Structured language for this voice config."
    )
    provider_ref = models.ForeignKey(
        'chatbot.Provider', on_delete=models.PROTECT, related_name='voices',
        help_text="Structured provider for this voice config."
    )
    gender = models.CharField(max_length=100, choices=GenderChoices.choices, default=GenderChoices.MALE)
    voice_speed = models.FloatField(
        null=True, blank=True, default=1.0,
        validators=[MinValueValidator(0.25), MaxValueValidator(4.0)]
    )
    primary_voice = models.OneToOneField(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='fallback_config',
        help_text="Set only on a row that is a fallback config for a Text To Text primary row."
    )
    is_fallback = models.BooleanField(
        default=False, editable=False,
        help_text="Auto-derived from primary_voice — True for a row that is a fallback config."
    )

    other_params = models.JSONField(null=True, blank=True)
    history = HistoricalRecords()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = VoiceManager()
    all_voices = models.Manager()

    def __str__(self):
        return f"{self.get_provider_display()} - {self.get_type_display()} - {self.language}"

    @property
    def provider_slug(self):
        """Slug used to key into the provider_dispatch registries — reads provider_ref when set,
        falling back to mapping the legacy provider enum value for rows not yet backfilled."""
        if self.provider_ref_id:
            return self.provider_ref.slug
        return VOICE_PROVIDER_TO_SLUG.get(self.provider)

    def _sync_legacy_fields_from_refs(self):
        """Sync legacy language/provider CharFields from language_ref/provider_ref.

        Called from both clean() and save() — clean()'s uniqueness/fallback-match
        checks below read the legacy `language` field, and clean() runs (via
        full_clean(), e.g. from admin forms) before save(), so the sync can't only
        live in save() or those checks would see a stale/blank legacy value for a
        row created via the FK fields alone.
        """
        if self.language_ref_id:
            self.language = self.language_ref.iso_code
        if self.provider_ref_id:
            self.provider = SLUG_TO_VOICE_PROVIDER.get(self.provider_ref.slug, self.provider)

    def clean(self):
        super().clean()
        self._sync_legacy_fields_from_refs()

        if not self.primary_voice_id:
            # A primary/non-fallback row — at most one per (company_bot, type,
            # language), regardless of provider. Matches the single-row lookup
            # get_voice_provider() (and the ~40 similar lookups across the app)
            # has always assumed but never enforced.
            duplicate = Voice.objects.filter(
                company_bot=self.company_bot, type=self.type, language=self.language,
            ).exclude(pk=self.pk).first()
            if duplicate:
                raise ValidationError(
                    f"A {self.get_type_display()} voice for language {self.language!r} already exists "
                    f"({duplicate}). Only one entry per type and language is allowed per bot, "
                    f"regardless of provider."
                )
            return

        if self.primary_voice_id == self.pk:
            raise ValidationError("A fallback voice cannot reference itself as its primary voice.")

        if self.primary_voice.type != VoiceType.TextToText:
            raise ValidationError("A fallback voice can only be set for a Text To Text (translation) row.")

        if self.primary_voice.is_fallback:
            raise ValidationError("Cannot set a fallback under another fallback row (only one level is supported).")

        other_fallback = Voice.all_voices.filter(
            primary_voice_id=self.primary_voice_id
        ).exclude(pk=self.pk).first()
        if other_fallback:
            raise ValidationError(
                f"'{self.primary_voice}' already has a fallback voice configured ({other_fallback}). "
                f"Edit or delete that row instead of adding another — only one fallback per primary voice "
                f"is supported."
            )

        if self.primary_voice.provider == self.provider:
            raise ValidationError("Fallback voice must use a different provider than the primary voice.")

        if self.language != self.primary_voice.language:
            raise ValidationError(
                f"Fallback voice language ({self.language!r}) must match the primary voice's "
                f"language ({self.primary_voice.language!r})."
            )

    def save(self, *args, **kwargs):

        if self.other_params == "null":
            self.other_params = None

        self._sync_legacy_fields_from_refs()

        self.is_fallback = bool(self.primary_voice_id)

        # A fallback config row mirrors its primary row's type/bot — language is
        # entered explicitly and validated (see clean()) to match the primary's.
        if self.primary_voice_id:
            primary_company_bot_id = self.primary_voice.company_bot_id
            # Guard against a caller (e.g. bulk import) explicitly setting
            # company_bot to something other than the primary voice's own bot —
            # silently trusting primary_voice's company here would let an
            # imported row get cross-tenant reattached to another company's bot.
            if self.company_bot_id and self.company_bot_id != primary_company_bot_id:
                raise ValueError(
                    "primary_voice must belong to the same company_bot as this Voice row."
                )
            self.type = self.primary_voice.type
            self.company_bot_id = primary_company_bot_id

        defaults = VOICE_PROVIDER_DEFAULTS.get(self.provider, {}).get(self.type, {})

        if self.pk:
            old = Voice.all_voices.filter(pk=self.pk).first()

            # If provider or type changed → reset config
            if old and (old.provider != self.provider or old.type != self.type):
                self.other_params = deepcopy(defaults)

        # If new object and params empty → load defaults
        if self.other_params in (None, {}, "null") and defaults:
            self.other_params = deepcopy(defaults)

        super().save(*args, **kwargs)

    class Meta:
        # validate_unique() (and other Django internals) use _default_manager, not
        # _base_manager, to exclude "self" from conflict checks — if that manager
        # is the filtered one, a fallback row (excluded from it) can never exclude
        # itself, producing bogus "not a valid choice" errors on an untouched save.
        # Both must point at the unfiltered manager for Voice's internals to work
        # correctly; application code should still use the explicit `objects` /
        # `all_voices` manager names, which are unaffected by these Meta options.
        default_manager_name = 'all_voices'
        base_manager_name = 'all_voices'
        indexes = [
            models.Index(fields=['company_bot']),
            models.Index(fields=['created_at']),
            models.Index(fields=['type']),
            models.Index(fields=['provider']),
        ]
        constraints = [
            # DB-level backstop for the same rule enforced in clean() — catches
            # raw .create()/bulk_create() paths (e.g. bulk import) that bypass
            # form validation. Fallback rows (is_fallback=True) are excluded
            # since they're expected to share (type, language) with their primary.
            models.UniqueConstraint(
                fields=['company_bot', 'type', 'language'],
                condition=models.Q(is_fallback=False),
                name='unique_primary_voice_per_bot_type_language',
            ),
        ]


class CompanyStateMachine(models.Model):
    """
    Represents a step in a structured conversational workflow for a company bot.
    Defines stage logic, prompts, and optional pre/post processing rules.
    """

    company_bot = models.ForeignKey(CompanyBot, on_delete=models.CASCADE)
    name = models.CharField(max_length=100, help_text="Enter the name of the state.")
    step = models.IntegerField(
        help_text="Integer representing the order in which state function calling happens. Lower values are "
                  "called first."
    )
    use_stage_chats = models.BooleanField(
        default=False,
        verbose_name="Use Stage Chats",
        help_text="If True, only chats from this stage will be included and passed to the LLM."
    )
    type = models.CharField(
        max_length=10, choices=EntityTypeChoices.choices, default=EntityTypeChoices.MANDATORY,
        help_text="Specify whether the state is mandatory or optional."
    )
    text_conversion_type = models.CharField(
        max_length=15, choices=TextConversionType.choices, default=TextConversionType.TRANSLATE,
        help_text="Choose how to process this field's text: "
                  "'Translation' converts meaning into another language, "
                  "'Transliteration' preserves sound using another script."
    )
    bot_question = models.TextField(
        null=True, blank=True, help_text="Provide the first question that the bot will ask when the state is triggered."
    )
    completion_criteria = models.TextField(
        null=True, blank=True,
        help_text="Define the criteria required to move from this state to the next state."
    )
    context = models.TextField(
        null=True, blank=True, help_text="Provide the main prompt or description of the state, explaining its purpose."
    )
    tool_context = models.TextField(null=True, blank=True)
    preprocess_type = models.CharField(
        max_length=100, choices=PreProcessType.choices, default=PreProcessType.NONE,
        help_text="Choose how this stage should be preprocessed: "
        "'Simple Prompt' lets you define a direct prompt, "
        "'Use Preprocess Bot' lets you select a separate bot to handle complex logic."
    )

    preprocess_prompt = models.TextField(
        blank=True, null=True,
        help_text="Define the skip logic prompt if Preprocess Type is SIMPLE. "
    )

    preprocess_bot = models.ForeignKey(
        CompanyBot,
        on_delete=models.SET_NULL, null=True, blank=True, related_name='preprocess_bots',
        help_text="Select which Bot to use for preprocessing for complex logic."
    )

    preprocess_output_mode = models.CharField(
        max_length=100, choices=PreProcessOutputMode.choices, default=PreProcessOutputMode.NONE,
        help_text="Define how to use the preprocess output: "
        "'Skip' means use output to decide if stage should be skipped; "
        "'Enrich' means use output in this stage's prompt; "
        "'Custom' means run custom logic on the output."
    )
    postprocess_type = models.CharField(
        max_length=100, choices=PostProcessType.choices, default=PostProcessType.NONE,
        help_text="Choose how this stage should be postprocessed: "
                  "'Simple Prompt' lets you define a direct prompt, "
                  "'Use Postprocess Bot' lets you select a separate bot to handle complex logic."
    )

    postprocess_prompt = models.TextField(
        blank=True, null=True,
        help_text="Define the postprocess prompt if Postprocess Type is SIMPLE."
    )

    postprocess_bot = models.ForeignKey(
        CompanyBot,
        on_delete=models.SET_NULL, null=True, blank=True, related_name='postprocess_bots',
        help_text="Select which Bot to use for postprocessing for complex logic."
    )

    postprocess_output_mode = models.CharField(
        max_length=100, choices=PostProcessOutputMode.choices, default=PostProcessOutputMode.NONE,
        help_text="Define how to use the postprocess output."
    )

    skip_to_step = models.IntegerField(
        null=True, blank=True,
        help_text="If set, the flow will skip directly to this step number when skip conditions are met."
    )
    
    operation_type = models.CharField(
        max_length=20,
        choices=OperationTypeChoices.choices,
        default=OperationTypeChoices.LLM,
        help_text="Choose whether this state uses LLM or non-LLM processing."
    )
    
    skip_if_authenticated = models.BooleanField(
        default=False,
        help_text="If True, this state will be skipped for authenticated users."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    def clean(self):
        # --- Preprocess validation ---
        if self.preprocess_type == PreProcessType.SIMPLE:
            if not self.preprocess_prompt or self.preprocess_prompt.strip() == '':
                raise ValidationError({
                    'preprocess_prompt': "Preprocess prompt is required when Preprocess Type is SIMPLE."
                })
            if self.preprocess_output_mode == PreProcessOutputMode.NONE:
                raise ValidationError({
                    'output_mode': "Output mode cannot be NONE when Preprocess Type is SIMPLE."
                })

        elif self.preprocess_type == PreProcessType.COMPLEX:
            if not self.preprocess_bot:
                raise ValidationError({
                    'preprocess_bot': "Preprocess bot must be selected for the selected preprocess type."
                })
            if self.preprocess_output_mode == PreProcessOutputMode.NONE:
                raise ValidationError({
                    'output_mode': "Output mode cannot be NONE for the selected preprocess type."
                })
        else:
            self.preprocess_prompt = None
            self.preprocess_bot = None
            self.preprocess_output_mode = PreProcessOutputMode.NONE

        # --- Postprocess validation ---
        if self.postprocess_type == PostProcessType.SIMPLE:
            if not self.postprocess_prompt or self.postprocess_prompt.strip() == '':
                raise ValidationError({
                    'postprocess_prompt': "Postprocess prompt is required when Postprocess Type is SIMPLE."
                })
            if self.postprocess_output_mode == PostProcessOutputMode.NONE:
                raise ValidationError({
                    'postprocess_output_mode': "Output mode cannot be NONE when Postprocess Type is SIMPLE."
                })
        elif self.postprocess_type == PostProcessType.COMPLEX:
            if not self.postprocess_bot:
                raise ValidationError({
                    'postprocess_bot': "Postprocess bot must be selected for the selected postprocess type."
                })
            if self.postprocess_output_mode == PostProcessOutputMode.NONE:
                raise ValidationError({
                    'postprocess_output_mode': "Output mode cannot be NONE for the selected postprocess type."
                })
        else:
            self.postprocess_prompt = None
            self.postprocess_bot = None
            self.postprocess_output_mode = PostProcessOutputMode.NONE

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.company_bot.name} - {self.name}"


class ImageConfiguration(models.Model):
    """
    Configuration for image handling in flows and bots.
    Defines constraints like max images, size limits, and naming.
    """
    name = models.CharField(
        max_length=100,
        help_text="Name for this image configuration."
    )
    max_images = models.IntegerField(
        default=1,
        validators=[MinValueValidator(0)],
        help_text="Maximum number of images allowed."
    )
    image_size = models.IntegerField(
        default=5242880,  # 5MB in bytes
        validators=[MinValueValidator(1)],
        help_text="Maximum image size in bytes (default: 5MB)."
    )
    history = HistoricalRecords()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} (Max: {self.max_images}, Size: {self.image_size} bytes)"

    class Meta:
        verbose_name = "Image Configuration"
        verbose_name_plural = "Image Configurations"
        indexes = [
            models.Index(fields=['name']),
        ]


class Flow(models.Model):
    """
    Flow model representing a conversation flow configuration.
    Links to CompanyBot, can have associated State Machines, Voice configurations, and Image settings.
    """
    flow_name = models.CharField(
        max_length=255,
        help_text="Name of the flow."
    )
    flow_route = models.CharField(
        max_length=255,
        help_text="Route/path for accessing this flow.",
        unique=True
    )
    languages = models.JSONField(
        default=["en", "hi", "kn", "te"],
        help_text="List of supported language codes (e.g., ['en', 'hi', 'kn'])."
    )
    hidden = models.BooleanField(
        default=False,
        help_text="If True, this flow will be hidden from public listing."
    )
    active = models.BooleanField(
        default=True,
        help_text="If False, this flow will be disabled and not accessible."
    )
    bot = models.ForeignKey(
        CompanyBot,
        on_delete=models.CASCADE,
        related_name='flows',
        help_text="The main bot associated with this flow."
    )
    story_bot = models.ForeignKey(
        CompanyBot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='story_flows',
        help_text="Optional secondary bot for story-related functionality."
    )
    story_validation_bot = models.ForeignKey(
        CompanyBot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='story_validation_flows',
        help_text="Optional secondary bot for story-related functionality."
    )
    title_bot = models.ForeignKey(
        CompanyBot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='title_flows',
        help_text="Optional bot for session title generation."
    )
    websocket_url = models.CharField(
        max_length=500,
        help_text="WebSocket path for real-time communication (e.g., ws/common). Do not include protocol or host.",
        default='ws/common/'
    )
    parent_flow = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_flows',
        help_text="Parent flow if this is a sub-flow."
    )
    user_type = models.CharField(
        max_length=20,
        choices=UserTypeChoices.choices,
        default=UserTypeChoices.ALL,
        help_text="User types allowed to access this flow (guest, auth, or all)."
    )
    image_config = models.ForeignKey(
        ImageConfiguration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='flows',
        help_text="Image configuration settings for this flow."
    )
    create_story = models.CharField(
        max_length=20,
        choices=CreateStoryChoices.choices,
        default=CreateStoryChoices.ALL,
        help_text="Whether to post process the story or not"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    def __str__(self):
        return f"{self.flow_name} ({self.flow_route})"

    class Meta:
        verbose_name = "Flow"
        verbose_name_plural = "Flows"
        indexes = [
            models.Index(fields=['flow_route']),
            models.Index(fields=['bot']),
            models.Index(fields=['active']),
            models.Index(fields=['hidden']),
        ]

    def clean(self):
        """Validate flow configuration."""
        super().clean()
        
        # Validate that languages is a list
        if not isinstance(self.languages, list):
            raise ValidationError({
                'languages': "Languages must be a list of language codes."
            })

        if len(self.languages) != len(list(set(self.languages))):
            raise ValidationError({
                'languages': "Language codes must be unique."
            })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class PDFTemplates(models.Model):
    """
    Model for storing PDF templates used in flows.
    Contains template configurations for generating PDFs with dynamic content.
    """
    template = models.TextField(
        help_text="Template content for PDF generation (e.g., HTML, EJS template)."
    )
    template_name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Unique name identifier for this template."
    )
    user_type = models.CharField(
        max_length=20,
        choices=UserTypeChoices.choices,
        default=UserTypeChoices.ALL,
        help_text="User types that can use this template (guest, auth, or all)."
    )
    constants_json = models.JSONField(
        null=True,
        blank=True,
        help_text="JSON object containing constants/variables used in the template."
    )
    flow = models.ForeignKey(
        Flow,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pdf_templates',
        help_text="Flow associated with this template."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    def __str__(self):
        return f"{self.template_name} ({self.user_type})"

    class Meta:
        verbose_name = "PDF Template"
        verbose_name_plural = "PDF Templates"
        indexes = [
            models.Index(fields=['template_name']),
            models.Index(fields=['user_type']),
        ]
