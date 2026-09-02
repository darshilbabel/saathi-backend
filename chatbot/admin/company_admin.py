from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import OuterRef, Q, Subquery
from simple_history.admin import SimpleHistoryAdmin
from .generic_upload_admin import BatchUploadMixin
from chatbot.filter.admin_filter import (CompanyChatCompanyFilter, ChatSessionFilter,
                                         ProfileCompanyChatFilter, ProfileEmailFilter)
from chatbot.filter.custom_date_from_filter import CustomAdvanceDateFilter
from chatbot.models import Company, Profile, ProfileType, CompanyBot, CompanyChat, CompanyChatFeedback, \
    ChatSession, CompanyBotTypeChoices, Voice, VoiceProvider, VoiceType, ImageConfiguration, Flow, Language
from chatbot.models.company_models import CompanyStateMachine
from chatbot.resources.resource import CompanyChatResource
from chatbot.resources.company_resource import ChatSessionResource
from django.shortcuts import redirect
from django.contrib import messages
import logging
import secrets
from django.urls import path
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.forms import ModelForm, MultipleChoiceField, ChoiceField, CheckboxSelectMultiple, Select
from ..utils.admin_config.export_mixin import ExportAllFieldsMixin
from chatbot.llm_models.llm_gateway import get_provider_list, get_model_list, get_openrouter_endpoints, \
    get_cache_options


class CompanyStateMachineAdmin(admin.TabularInline):
    model = CompanyStateMachine
    fk_name = 'company_bot'
    extra = 1
    raw_id_fields = ['preprocess_bot', 'postprocess_bot']
    fields = (
        'name', 'step', 'use_stage_chats', 'text_conversion_type',
        'bot_question', 'completion_criteria', 'context', 'tool_context',
        'operation_type', 'skip_if_authenticated',
        'preprocess_type', 'preprocess_prompt', 'preprocess_bot', 'preprocess_output_mode',
        'postprocess_type', 'postprocess_prompt', 'postprocess_bot', 'postprocess_output_mode',
        'skip_to_step',
    )
    exclude = ('type',)  # ✅ hide type

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.order_by('step')


class VoiceProviderAdmin(admin.TabularInline):
    model = Voice
    extra = 1
    fk_name = 'company_bot'
    fields = (
        'type', 'language_ref', 'provider_ref', 'name', 'sample_link',
        'provider_code', 'gender', 'voice_speed', 'other_params',
    )

    def get_queryset(self, request):
        # Explicitly use the filtered manager (not super()/the default manager,
        # which Meta.default_manager_name points at the unfiltered one for
        # Django-internal reasons — see Voice.Meta) so this table only ever
        # shows real/primary provider rows, not fallback configs.
        qs = Voice.objects.get_queryset()
        return qs.order_by('type', 'language_ref__name')

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "other_params":
            kwargs["help_text"] = "Leave empty to auto-load provider defaults."

        return super().formfield_for_dbfield(db_field, request, **kwargs)


class FallbackVoiceProviderAdmin(admin.TabularInline):
    """
    A separate section from VoiceProviderAdmin above: each row here is a fallback
    provider config, explicitly mapped (via the "Primary Voice" dropdown, labeled
    "provider - type - language" so it's clear which row you're picking) to one
    of the Text To Text rows in the section above. Retried once if that primary
    row's provider errors. type/company_bot are mirrored automatically from the
    chosen primary row (see Voice.save()); language must be entered explicitly
    and is validated (see Voice.clean()) to match the primary row's language.
    """
    model = Voice
    fk_name = 'company_bot'
    extra = 1
    verbose_name = "Fallback Voice Provider"
    verbose_name_plural = "Fallback Voice Providers (mapped to a Text To Text row above)"
    fields = (
        'primary_voice', 'language_ref', 'provider_ref', 'name', 'sample_link',
        'provider_code', 'gender', 'voice_speed', 'other_params',
    )

    def get_queryset(self, request):
        # Fallback rows are excluded from the default manager (so the many other
        # Voice lookups across the app never resolve to one) — use the unfiltered
        # manager here so this section can find/display its own rows.
        qs = Voice.all_voices.filter(is_fallback=True)
        return qs.order_by('language_ref__name')

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "other_params":
            kwargs["help_text"] = "Leave empty to auto-load provider defaults."

        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "primary_voice":
            object_id = request.resolver_match.kwargs.get('object_id')
            if object_id:
                # Default manager already excludes fallback rows, so this only
                # ever offers real Text To Text provider rows as valid targets.
                kwargs["queryset"] = Voice.objects.filter(
                    company_bot_id=object_id, type=VoiceType.TextToText,
                )
            else:
                kwargs["queryset"] = Voice.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'status')
    list_filter = (
        CustomAdvanceDateFilter,
    )
    search_fields = ('name',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user_email = request.user.email
        profile = Profile.objects.filter(email=user_email)
        if request.user.is_superuser:
            return qs
        elif len(profile) > 0 and profile[0].profile_type == ProfileType.MODERATOR:
            return qs.filter(id=profile[0].company.id)
        else:
            return qs.none()


class CompanyBotAdminForm(ModelForm):
    cache_targets = MultipleChoiceField(
        required=False,
        widget=CheckboxSelectMultiple,
        help_text="Select one or more cache targets. Choices are fetched live from the LLM gateway. "
                  "Required when Enable Cache is checked."
    )

    class Meta:
        model = CompanyBot
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        saved_targets = self.instance.cache_targets if self.instance and self.instance.pk else None
        cache_options = get_cache_options() or {}
        target_values = cache_options.get('target_values') or []
        choices = [(t, t) for t in target_values]
        # DB value is the source of truth — never drop it silently if the gateway is
        # down or the target list no longer includes it.
        if saved_targets:
            known = {t for t, _ in choices}
            choices += [(t, t) for t in saved_targets if t not in known]
        self.fields['cache_targets'].choices = choices
        self.fields['cache_targets'].initial = saved_targets or cache_options.get('target_default') or []

    def clean_cache_targets(self):
        return list(self.cleaned_data.get('cache_targets') or [])


@admin.register(CompanyBot)
class CompanyBotAdmin(BatchUploadMixin, SimpleHistoryAdmin):

    form = CompanyBotAdminForm
    list_display = ('name', 'company', 'created_at')
    list_filter = (
        'company',
        'name',
        'provider',
        'llm_model',
        CustomAdvanceDateFilter,
    )
    search_fields = ('name', 'company__name')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    inlines = [VoiceProviderAdmin, FallbackVoiceProviderAdmin]
    actions = ['duplicate_bot', 'export_selected_bots']

    # Hidden from the add/change form in favor of gateway_provider/gateway_model — the
    # underlying fields and migrations are unchanged, this is UI-only.
    exclude = ('provider', 'llm_model')

    enable_batch_upload = True
    batch_load_foreign_keys = True
    batch_upload_fields = ['name', 'company', 'provider', 'llm_model', 'context', 'max_token', 'route']

    import_template_name = 'admin/import_export/import.html'
    export_template_name = 'admin/import_export/export.html'

    def render_change_form(self, request, context, add=False, change=False, form_url='', obj=None):
        # Surface inline validation errors (e.g. Voice.clean() messages) as
        # top-of-page messages too — the jazzmin theme doesn't reliably render
        # inline formset non-field/non-form errors down at the form itself,
        # so without this an admin only sees a generic "correct the error
        # below" banner with no indication of what actually went wrong.
        seen = set()
        for inline_admin_formset in context.get('inline_admin_formsets', []):
            formset = inline_admin_formset.formset
            label = inline_admin_formset.opts.verbose_name
            for error in formset.non_form_errors():
                if (label, str(error)) not in seen:
                    seen.add((label, str(error)))
                    messages.error(request, f"{label}: {error}")
            for form in formset.forms:
                for field, field_errors in form.errors.items():
                    for error in field_errors:
                        if (label, str(error)) not in seen:
                            seen.add((label, str(error)))
                            messages.error(request, f"{label}: {error}")
        return super().render_change_form(request, context, add=add, change=change, form_url=form_url, obj=obj)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'export/',
                self.admin_site.admin_view(self.export_view),
                name='chatbot_companybot_export',
            ),
            path(
                'import/',
                self.admin_site.admin_view(self.import_view),
                name='chatbot_companybot_import',
            ),
        ]
        # Important: custom URLs must come before the default admin URLs
        return custom_urls + urls

    def export_view(self, request):
        """Handle export requests"""
        from chatbot.views.admin.bot_admin_views import export_bots
        return export_bots(request)

    def import_view(self, request):
        """Handle import requests"""
        from chatbot.views.admin.bot_admin_views import import_bots
        return import_bots(request)

    def get_import_formats(self):
        """Define allowed import formats"""
        from import_export.formats import base_formats
        return [base_formats.CSV, base_formats.XLSX, base_formats.JSON]

    def get_export_formats(self):
        """Define allowed export formats"""
        from import_export.formats import base_formats
        return [base_formats.CSV, base_formats.XLSX, base_formats.JSON]

    def get_export_filename(self, request, queryset, file_format):
        """Generate filename for exports"""
        import datetime
        date_str = datetime.datetime.now().strftime('%Y-%m-%d')
        filename = f"company_bots_{date_str}"
        return f"{filename}.{file_format.get_extension()}"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user_email = request.user.email
        profile = Profile.objects.filter(email=user_email)
        if request.user.is_superuser:
            return qs
        elif len(profile) > 0 and profile[0].profile_type == ProfileType.MODERATOR:
            return qs.filter(company=profile[0].company)
        else:
            return qs.none()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'gateway_provider':
            providers = get_provider_list() or []
            choices = [('', '---------')] + [(p['name'], p['name']) for p in providers if p.get('name')]
            kwargs['widget'] = Select(choices=choices)
        elif db_field.name == 'gateway_model':
            kwargs['widget'] = Select(choices=[('', '---------')])
        elif db_field.name == 'gateway_sub_provider':
            kwargs['widget'] = Select(choices=[('', '---------')])
        elif db_field.name == 'cache_ttl':
            kwargs['widget'] = Select(choices=[('', '---------')])
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        user = request.user
        user_email = request.user.email
        profile = Profile.objects.filter(email=user_email)
        if not user.is_superuser and len(profile) > 0 and profile[0].profile_type == ProfileType.MODERATOR:
            company_field = form.base_fields.get('company')
            if company_field:
                form.base_fields['company'].queryset = form.base_fields['company'].queryset.filter(
                    id=profile[0].company.id)
            form.base_fields = {field_name: form.base_fields[field_name] for field_name in form.base_fields}

        # Make sure the saved gateway_provider always shows up as a selectable option,
        # even if the live provider list is unavailable (gateway down) or no longer
        # includes it — the DB value is the source of truth, never drop it silently.
        provider_field = form.base_fields.get('gateway_provider')
        if provider_field is not None and obj is not None and obj.gateway_provider:
            provider_choices = list(provider_field.widget.choices)
            if obj.gateway_provider not in dict(provider_choices):
                provider_choices.append((obj.gateway_provider, obj.gateway_provider))
                provider_field.widget.choices = provider_choices

        # Populate the gateway model dropdown from the saved gateway_provider. If the
        # provider was just changed but not yet saved, this still reflects the old
        # provider's models — save once to refresh the model choices.
        model_field = form.base_fields.get('gateway_model')
        if model_field is not None and obj is not None and obj.gateway_provider:
            models_data = get_model_list(obj.gateway_provider) or []
            choices = [('', '---------')] + [
                (m['id'], m.get('name') or m['id']) for m in models_data if m.get('id')
            ]
            if obj.gateway_model and obj.gateway_model not in dict(choices):
                choices.append((obj.gateway_model, obj.gateway_model))
            model_field.widget.choices = choices

        # gateway_sub_provider only applies to the 'openrouter' provider (it picks which
        # upstream endpoint should serve the model) — hide it entirely for any other
        # provider, and populate it from the saved gateway_model's endpoint list otherwise.
        # The stored/passed value is 'tag' (e.g. 'google-vertex/europe'), not 'provider_name'
        # (e.g. 'Google') — provider_name isn't unique per endpoint (a provider can have
        # multiple regional/routing variants), tag is the actual routable identifier.
        # Save-then-reload, same pattern as gateway_model.
        sub_provider_field = form.base_fields.get('gateway_sub_provider')
        if sub_provider_field is not None:
            if not obj or obj.gateway_provider != 'openrouter':
                form.base_fields.pop('gateway_sub_provider', None)
            else:
                choices = [('', '---------')]
                if obj.gateway_model:
                    endpoints = get_openrouter_endpoints(obj.gateway_model) or []
                    seen_tags = []
                    labels = {}
                    for endpoint in endpoints:
                        tag = endpoint.get('tag')
                        if not tag or tag in seen_tags:
                            continue
                        seen_tags.append(tag)
                        provider_name = endpoint.get('provider_name')
                        labels[tag] = f'{provider_name} ({tag})' if provider_name else tag
                    choices += [(tag, labels[tag]) for tag in seen_tags]
                # DB value is the source of truth — never drop it silently if the gateway is
                # down or the endpoint list no longer includes it.
                if obj.gateway_sub_provider and obj.gateway_sub_provider not in dict(choices):
                    choices.append((obj.gateway_sub_provider, obj.gateway_sub_provider))
                sub_provider_field.widget.choices = choices

        # Populate the cache TTL dropdown live from the gateway's /v1/cache/options endpoint,
        # and surface the supported-providers list in the help text — dynamic, no migration
        # needed when the gateway's supported provider list changes.
        ttl_field = form.base_fields.get('cache_ttl')
        enable_cache_field = form.base_fields.get('enable_cache')
        if ttl_field is not None or enable_cache_field is not None:
            cache_options = get_cache_options() or {}
            ttl_values = cache_options.get('ttl_values') or []
            providers = cache_options.get('providers') or []
            provider_note = f" Supported providers: {', '.join(providers)}." if providers else ""

            if ttl_field is not None:
                choices = [('', '---------')] + [(t, t) for t in ttl_values]
                if obj is not None and obj.cache_ttl and obj.cache_ttl not in dict(choices):
                    choices.append((obj.cache_ttl, obj.cache_ttl))
                ttl_field.widget.choices = choices
                ttl_field.help_text = f"{ttl_field.help_text}{provider_note}"
                if obj is None and cache_options.get('ttl_default'):
                    ttl_field.initial = cache_options['ttl_default']

            if enable_cache_field is not None and provider_note:
                enable_cache_field.help_text = f"{enable_cache_field.help_text}{provider_note}"

        form.base_fields = {field_name: form.base_fields[field_name] for field_name in form.base_fields}
        return form

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        # This method is called when the admin change form is rendered.
        if object_id:
            obj = self.model.objects.get(pk=object_id)
            if obj.bot_type == CompanyBotTypeChoices.STATE_MACHINE:
                # If the bot_type is 'state machine', include the inline.
                self.inlines = [VoiceProviderAdmin, FallbackVoiceProviderAdmin, CompanyStateMachineAdmin]

            else:
                # Otherwise, no inlines.
                self.inlines = [VoiceProviderAdmin, FallbackVoiceProviderAdmin]
        else:
            # For the add form, decide if you want the inline to be shown or not.
            # This example assumes not.
            self.inlines = [VoiceProviderAdmin, FallbackVoiceProviderAdmin]
        return super().changeform_view(request, object_id, form_url, extra_context)

    # Sync Google glossary for TextToText voice providers after inline save
    def save_formset(self, request, form, formset, change):
        if getattr(formset, "model", None) is not Voice:
            return super().save_formset(request, form, formset, change)

        from chatbot.translate.google import google_glossary

        logger = logging.getLogger("django")

        old_entries_by_pk = {}
        for f in formset.forms:
            if f.instance.pk:
                # all_voices (not objects) — this formset can be the fallback
                # inline too, whose rows are excluded from the default manager.
                v = Voice.all_voices.filter(pk=f.instance.pk).only("other_params").first()
                if v and v.other_params and "glossary_entries" in v.other_params:
                    old_entries_by_pk[v.pk] = google_glossary.normalize_glossary_entries(
                        v.other_params.get("glossary_entries")
                    )

        super().save_formset(request, form, formset, change)

        for f in formset.forms:
            if not f.instance.pk or f.cleaned_data.get("DELETE"):
                continue
            inst = Voice.all_voices.filter(pk=f.instance.pk).first()
            if not inst or inst.provider != VoiceProvider.GOOGLE or inst.type != VoiceType.TextToText:
                continue
            params = inst.other_params or {}
            if "glossary_entries" not in params:
                continue
            new_entries = google_glossary.normalize_glossary_entries(params.get("glossary_entries"))
            if not new_entries:
                continue
            if new_entries == old_entries_by_pk.get(inst.pk):
                continue
            try:
                google_glossary.sync_glossary_for_voice(inst)

                messages.success(
                    request,
                    f"Google glossary synced successfully for Voice id={inst.pk}",
                )
            except Exception as e:
                logger.error(
                    "Glossary sync failed for Voice id=%s",
                    inst.pk,
                    exc_info=True
                )
                messages.error(
                    request,
                    f"Google glossary sync failed for Voice id={inst.pk} (save completed): {e}",
                )


    @staticmethod
    def _unique_bot_route(company, base_route):
        """Route CompanyBot.save() now rejects a same-(company, route) duplicate, which
        `duplicate_bot` below would otherwise always hit since it clones the route
        verbatim. Suffix with a short random token rather than an incrementing counter
        (1, 2, 3, ...) — a random suffix needs only one existence check in the
        overwhelming common case instead of scanning past every prior clone, and the
        collision odds (1 in 16^8) make the retry loop below a safety net, not the
        normal path."""
        if not base_route:
            return base_route
        for _ in range(5):
            candidate = f"{base_route}-copy-{secrets.token_hex(4)}"
            if not CompanyBot.objects.filter(company=company, route=candidate).exists():
                return candidate
        raise ValidationError(
            f"Could not generate a unique route for a duplicate of {base_route!r} "
            f"after 5 attempts — this should be virtually impossible; check for a bug."
        )

    def duplicate_bot(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Please select exactly one bot to duplicate.", level=messages.ERROR)
            return

        original = queryset.first()

        # Duplicate the bot
        new_bot = CompanyBot.objects.get(pk=original.pk)
        new_bot.pk = None
        new_bot.name = f"{original.name} (Copy)"
        new_bot.route = self._unique_bot_route(original.company, original.route)
        new_bot.save()

        # Duplicate VoiceProvider inlines (including fallback config rows), remapping
        # primary_voice to the corresponding clone instead of leaving it pointing
        # at a row that still belongs to the original bot.
        original_voice_providers = list(Voice.all_voices.filter(company_bot=original))
        primary_map = {voice.pk: voice.primary_voice_id for voice in original_voice_providers}
        old_to_new_pk = {}
        for voice in original_voice_providers:
            old_pk = voice.pk
            voice.pk = None
            voice.primary_voice = None
            voice.company_bot = new_bot
            voice.save()
            old_to_new_pk[old_pk] = voice.pk

        for old_pk, old_primary_id in primary_map.items():
            if old_primary_id and old_primary_id in old_to_new_pk:
                # Bypasses save(), so is_fallback (normally auto-derived there) is
                # set explicitly here too.
                Voice.all_voices.filter(pk=old_to_new_pk[old_pk]).update(
                    primary_voice_id=old_to_new_pk[old_primary_id], is_fallback=True,
                )

        # Duplicate StateMachine if present
        if original.bot_type == CompanyBotTypeChoices.STATE_MACHINE:
            original_state_machines = CompanyStateMachine.objects.filter(company_bot=original)
            for sm in original_state_machines:
                sm.pk = None
                sm.company_bot = new_bot
                sm.save()

        self.message_user(request, "Bot duplicated successfully!", level=messages.SUCCESS)
        return redirect(f"/admin/chatbot/companybot/{new_bot.id}/change/")

    def export_selected_bots(self, request, queryset):
        """Custom export action"""
        selected_ids = queryset.values_list('id', flat=True)
        ids_str = ','.join(str(id) for id in selected_ids)

        # Use admin URL reverse with the app label and model name
        info = self.model._meta.app_label, self.model._meta.model_name
        url = reverse('admin:%s_%s_export' % info) + f'?ids={ids_str}'
        return HttpResponseRedirect(url)

    export_selected_bots.short_description = "Export selected bots"

    def changelist_view(self, request, extra_context=None):
        """Add custom buttons to the changelist view"""
        extra_context = extra_context or {}
        extra_context['custom_buttons'] = True
        return super().changelist_view(request, extra_context=extra_context)

    duplicate_bot.short_description = "Duplicate selected bot"


class CompanyChatFeedbackInline(admin.TabularInline):
    """Read-only: feedback rows are created via the feedback API only and are never edited,
    so admins can view the full history here but can't add/change/delete from this screen."""
    model = CompanyChatFeedback
    fk_name = 'company_chat'
    extra = 0
    fields = ('thumbs_up', 'thumbs_down', 'comment', 'created_at')
    readonly_fields = fields
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CompanyChat)
class CompanyChatAdmin(ExportAllFieldsMixin, admin.ModelAdmin):
    list_display = ('session', 'sender', 'receiver', 'message', 'translated_message', 'created_at', 'stage', 'status')
    inlines = [CompanyChatFeedbackInline]
    list_filter = (
        CustomAdvanceDateFilter,
        ProfileCompanyChatFilter,
        ProfileEmailFilter,
        'session',
        CompanyChatCompanyFilter,
        'stage'
    )
    search_fields = ('session', 'message__icontains', 'translated_message__icontains')
    list_per_page = 20
    raw_id_fields = ('sender', 'receiver')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    export_filename = "company_chats.xlsx"
    resource_class = CompanyChatResource
    extra_export_fields = {
        'session_type': lambda obj: obj.session_type,
        # chat_import_views.py reads this exact column name back on import (falling
        # back to the ChatSession model default - English - when absent), so this must
        # stay in lockstep with that column name. The actual "Export selected records"
        # button uses ExportAllFieldsMixin.export_all_view (this class isn't an
        # ImportExportModelAdmin, so resource_class/CompanyChatResource above is unused
        # by that button) - it reads columns from model fields + extra_export_fields,
        # not from CompanyChatResource.Meta.fields.
        'chat_session_language': lambda obj: obj.chat_session_language or '',
    }

    def get_queryset(self, request):
        qs = super().get_queryset(request).prefetch_related('sender__company', 'receiver__company')
        qs = qs.annotate(
            session_type=Subquery(
                ChatSession.objects.filter(session=OuterRef('session')).values('session_type')[:1]
            ),
            chat_session_language=Subquery(
                ChatSession.objects.filter(session=OuterRef('session')).values('language')[:1]
            ),
        )
        user_email = request.user.email
        profile = Profile.objects.filter(email=user_email).select_related('company').first()
        if request.user.is_superuser:
            return qs
        elif profile and profile.profile_type == ProfileType.MODERATOR:
            return qs.filter(Q(sender__company=profile.company) | Q(receiver__company=profile.company))
        else:
            return qs.none()


class ChatSessionAdminForm(ModelForm):
    language = ChoiceField(
        help_text="Pick from languages defined under Chatbot > Languages. To add one not "
                  "listed here, create it there first.",
    )

    class Meta:
        model = ChatSession
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built fresh per request (not a module-level constant) so a Language added via its
        # own admin page shows up here immediately, with no code change.
        self.fields["language"].choices = list(Language.objects.values_list('iso_code', 'name'))


@admin.register(ChatSession)
class ChatSessionAdmin(ExportAllFieldsMixin, admin.ModelAdmin):
    form = ChatSessionAdminForm
    list_display = (
        'session', 'get_first_name', 'session_status', 'session_type', 'current_question', 'total_steps',
        'created_at', 'updated_at'
    )
    list_filter = (
        'session',
        'title',
        ChatSessionFilter,
        'project_id',
        'session_status',
        'session_type',
        CustomAdvanceDateFilter,
    )
    search_fields = ('session', 'title', 'profile__first_name')
    raw_id_fields = ('profile',)
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    resource_class = ChatSessionResource

    def get_urls(self):
        urls = super().get_urls()
        from chatbot.views.admin.chat_import_views import chat_import_tool_enabled
        if not chat_import_tool_enabled():
            return urls
        custom_urls = [
            path(
                'import-company-chats/',
                self.admin_site.admin_view(self.import_company_chats_view),
                name='chatbot_chatsession_import_company_chats',
            ),
        ]
        return custom_urls + urls

    def import_company_chats_view(self, request):
        from chatbot.views.admin.chat_import_views import CompanyChatImportView
        return CompanyChatImportView.as_view()(request)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        from chatbot.views.admin.chat_import_views import chat_import_tool_enabled
        if chat_import_tool_enabled():
            profile = Profile.objects.filter(email=request.user.email).first()
            is_moderator = bool(profile and profile.profile_type == ProfileType.MODERATOR)
            extra_context['show_chat_import_tool'] = request.user.is_superuser or is_moderator
        return super().changelist_view(request, extra_context=extra_context)

    def current_question(self, obj):
        return obj.current_step

    current_question.short_description = 'Current Question'

    def total_steps(self, obj):
        if obj.company_bot and CompanyStateMachine.objects.filter(company_bot=obj.company_bot).exists():
            return CompanyStateMachine.objects.filter(company_bot=obj.company_bot).count()
        return 0

    total_steps.short_description = 'Total Questions'

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('profile', 'company_bot')
        user_email = request.user.email
        profile = Profile.objects.filter(email=user_email)
        if request.user.is_superuser:
            return qs
        elif len(profile) > 0 and profile[0].profile_type == ProfileType.MODERATOR:
            return qs.filter(profile__company=profile[0].company).prefetch_related('profile__company')
        else:
            return qs.none()

    def get_list_display(self, request):
        user = request.user
        user_email = request.user.email
        profile = Profile.objects.filter(email=user_email)
        if not user.is_superuser and len(profile) > 0 and profile[0].profile_type == ProfileType.MODERATOR:
            return 'session', 'get_first_name', 'current_question', 'total_steps', 'session_status', 'created_at', 'updated_at'
        return 'session', 'get_first_name', 'current_question', 'total_steps', 'session_status', 'created_at', 'updated_at'

    def get_first_name(self, obj):
        return obj.profile.first_name if obj.profile else None

    get_first_name.short_description = 'First Name'

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        user = request.user
        user_email = request.user.email
        profile = Profile.objects.filter(email=user_email)
        # Check if the user is a moderator
        if not user.is_superuser and len(profile) > 0 and profile[0].profile_type == ProfileType.MODERATOR:
            # Exclude the fields for moderators
            form.base_fields = {field_name: form.base_fields[field_name] for field_name in form.base_fields
                                if field_name not in ['current_step']}
        return form


admin.site.register(Company, CompanyAdmin)


@admin.register(ImageConfiguration)
class ImageConfigurationAdmin(admin.ModelAdmin):
    """Admin interface for Image Configuration model."""
    list_display = ('name', 'max_images', 'get_image_size_mb', 'created_at')
    list_filter = ('created_at', 'max_images')
    search_fields = ('name',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name',)
        }),
        ('Image Constraints', {
            'fields': ('max_images', 'image_size'),
            'description': 'Configure image upload limits for this configuration.'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at')

    def get_image_size_mb(self, obj):
        """Display image size in MB."""
        return f"{obj.image_size / 1048576:.2f} MB"
    get_image_size_mb.short_description = 'Max Image Size'

class FlowAdminForm(ModelForm):
    languages = MultipleChoiceField(
        required=False,
        widget=CheckboxSelectMultiple,
        help_text="Select one or more supported languages. To add a language not listed here, "
                   "create it under Chatbot > Languages first.",
    )

    class Meta:
        model = Flow
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Built fresh per request (not a module-level constant) so a Language added via its
        # own admin page shows up here immediately, with no code change.
        self.fields["languages"].choices = list(Language.objects.values_list('iso_code', 'name'))

        value = self.instance.languages if self.instance and self.instance.pk else None
        self.fields["languages"].initial = value or ["en", "hi", "kn", "te"]

    def clean_languages(self):
        value = self.cleaned_data.get("languages", [])

        if not isinstance(value, list):
            raise ValidationError("Languages must be a list of language codes.")

        if len(value) != len(set(value)):
            raise ValidationError("Language codes must be unique.")

        allowed = set(Language.objects.values_list('iso_code', flat=True))
        invalid = [code for code in value if code not in allowed]
        if invalid:
            raise ValidationError(f"Invalid language codes: {', '.join(invalid)}")

        return value


@admin.register(Flow)
class FlowAdmin(SimpleHistoryAdmin):
    """Admin interface for Flow model."""
    form = FlowAdminForm

    list_display = (
        'flow_name', 'flow_route', 'bot', 'active', 'hidden', 
        'user_type', 'created_at'
    )
    list_filter = (
        'active', 'hidden', 'user_type',
        'bot__company', CustomAdvanceDateFilter
    )
    search_fields = ('flow_name', 'flow_route', 'bot__name')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    raw_id_fields = ('bot', 'title_bot', 'parent_flow', 'image_config')

    fieldsets = (
        ('Basic Information', {
            'fields': ('flow_name', 'flow_route', 'languages')
        }),
        ('Bot Configuration', {
            'fields': ('bot', 'title_bot'),
            'description': 'Configure the bots associated with this flow.'
        }),
        ('Flow Settings', {
            'fields': ('active', 'hidden', 'user_type', 'parent_flow', 'image_config'),
        }),
        ('Advanced Settings', {
            'fields': ('websocket_url',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at')
    
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Customize form field for languages JSONField."""
        if db_field.name == 'languages':
            kwargs['help_text'] = 'Enter languages as JSON array, e.g., ["en", "hi", "kn"]'
        elif db_field.name == 'websocket_url':
            kwargs['help_text'] = 'Enter WebSocket route only (e.g., "ws/common/"). Do not include the full URL.'
        return super().formfield_for_dbfield(db_field, request, **kwargs)
