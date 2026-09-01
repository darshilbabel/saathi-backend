import json
import logging
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from chatbot.constants.provider_slugs import VOICE_PROVIDER_TO_SLUG
from chatbot.models import CompanyBot, Voice, CompanyStateMachine, Company, VoiceType, Language, Provider

logger = logging.getLogger('django')


class CompanyBotResource(resources.ModelResource):
    """Resource for exporting/importing CompanyBot with related Voice and StateMachine models"""

    # Use ForeignKeyWidget to handle company relationship
    company = fields.Field(
        column_name='company',
        attribute='company',
        widget=ForeignKeyWidget(Company, field='slug')
    )

    # JSON fields to store related models
    voices = fields.Field(column_name='voices', attribute='voices')
    state_machines = fields.Field(column_name='state_machines', attribute='state_machines')

    class Meta:
        model = CompanyBot
        fields = (
            'id', 'name', 'company', 'context', 'max_token', 'provider', 'provider_keys',
            'bot_temperature', 'top_k', 'llm_model', 'filter_score', 'end_context',
            'introductory_message', 'tag_context', 'route', 'bot_type', 'llm_key',
            'dynamic_context', 'dynamic_context_type', 'pre_context', 'tool_context',
            'other_params', 'connect_timeout', 'read_timeout', 'voices', 'state_machines',
        )
        export_order = fields
        skip_unchanged = True
        report_skipped = True

    def dehydrate_voices(self, bot):
        """Export Voice objects related to this bot"""
        voices = Voice.all_voices.filter(company_bot=bot)
        voice_data = []
        for voice in voices:
            voice_dict = {
                'type': voice.type,
                'provider': voice.provider,
                'name': voice.name,
                'sample_link': voice.sample_link,
                'language': voice.language,
                'provider_code': voice.provider_code,
                'gender': voice.gender,
                'voice_speed': voice.voice_speed,
                'other_params': voice.other_params,
                'is_fallback': voice.is_fallback,
                # Reference the fallback's primary by language (unique within a bot
                # for Text To Text), not by raw pk — meaningless/unsafe across a
                # database boundary. See after_import_instance for resolution.
                'primary_voice_language': voice.primary_voice.language if voice.primary_voice_id else None,
            }
            voice_data.append(voice_dict)
        return json.dumps(voice_data)

    def dehydrate_state_machines(self, bot):
        """Export CompanyStateMachine objects related to this bot"""
        state_machines = CompanyStateMachine.objects.filter(company_bot=bot).order_by('step')
        sm_data = []
        for sm in state_machines:
            sm_dict = {
                'name': sm.name,
                'step': sm.step,
                'use_stage_chats': sm.use_stage_chats,
                'type': sm.type,
                'text_conversion_type': sm.text_conversion_type,
                'bot_question': sm.bot_question,
                'completion_criteria': sm.completion_criteria,
                'context': sm.context,
                'tool_context': sm.tool_context,
                'preprocess_type': sm.preprocess_type,
                'preprocess_prompt': sm.preprocess_prompt,
                'preprocess_bot_name': sm.preprocess_bot.name if sm.preprocess_bot else None,
                'preprocess_output_mode': sm.preprocess_output_mode,
                'postprocess_type': sm.postprocess_type,
                'postprocess_prompt': sm.postprocess_prompt,
                'postprocess_bot_name': sm.postprocess_bot.name if sm.postprocess_bot else None,
                'postprocess_output_mode': sm.postprocess_output_mode,
                'skip_to_step': sm.skip_to_step,
            }
            sm_data.append(sm_dict)
        return json.dumps(sm_data)

    def before_import_row(self, row, **kwargs):
        """Clean up the row data before import"""
        # Remove empty strings and convert to None
        for field in row:
            if row[field] == '':
                row[field] = None

    def after_import_row(self, row, row_result, **kwargs):
        """Handle related models after the main instance has been imported and saved.

        NOTE: django-import-export 4.x no longer calls the old after_import_instance(instance,
        new, **kwargs) hook that this method used to be named (import_row() only calls the
        deprecated shim if a subclass overrides the OLD name, and that shim itself forwards to
        after_init_instance(instance, new, row=None, ...) — which fires before the instance is
        saved, too early for the FK-to-instance.pk writes this method does below). This
        resource previously still used the old name, so this whole voices/state_machines
        import path was silently never invoked; fixed here (moved to after_import_row, which
        fires post-save) since it's directly adjacent to the FK-resolution change this method
        needed anyway (voices import now requires a resolved Language/Provider).
        """
        if row_result.import_type not in (row_result.IMPORT_TYPE_NEW, row_result.IMPORT_TYPE_UPDATE):
            return
        instance = CompanyBot.objects.filter(pk=row_result.object_id).first()
        if not instance:
            return
        new = row_result.import_type == row_result.IMPORT_TYPE_NEW
        row = row or {}

        # Import Voice objects
        if 'voices' in row and row['voices']:
            try:
                voices_data = json.loads(row['voices'])
                # Delete existing voices if updating
                if not new:
                    Voice.all_voices.filter(company_bot=instance).delete()

                # Skip if essential fields are missing
                voices_data = [
                    v for v in voices_data if v.get('type') and v.get('provider')
                ]
                # Primaries first, then fallbacks — a fallback's primary is
                # resolved by language within this bot, never trusted as a raw
                # pk (not present here anyway; see primary_voice_language).
                primary_voices_data = [v for v in voices_data if not v.get('primary_voice_language')]
                fallback_voices_data = [v for v in voices_data if v.get('primary_voice_language')]

                def _resolve_language(language_code):
                    if not language_code:
                        return None
                    return Language.objects.filter(iso_code=language_code).first()

                def _resolve_provider(provider_enum_value):
                    if not provider_enum_value:
                        return None
                    slug = VOICE_PROVIDER_TO_SLUG.get(provider_enum_value)
                    return Provider.objects.filter(slug=slug).first() if slug else None

                def _create_voice(voice_dict, primary_voice=None):
                    language_ref = _resolve_language(voice_dict.get('language'))
                    provider_ref = _resolve_provider(voice_dict.get('provider'))
                    if not language_ref or not provider_ref:
                        logger.warning(
                            "Skipping voice import for bot %s — could not resolve language %r / provider %r "
                            "to a seeded Language/Provider row in this environment.",
                            instance.pk, voice_dict.get('language'), voice_dict.get('provider')
                        )
                        return None
                    return Voice.objects.create(
                        company_bot=instance,
                        primary_voice=primary_voice,
                        type=voice_dict.get('type'),
                        provider=voice_dict.get('provider'),
                        language_ref=language_ref,
                        provider_ref=provider_ref,
                        name=voice_dict.get('name'),
                        sample_link=voice_dict.get('sample_link'),
                        language=voice_dict.get('language'),
                        provider_code=voice_dict.get('provider_code'),
                        gender=voice_dict.get('gender', 'MALE'),
                        voice_speed=voice_dict.get('voice_speed', 1.0),
                        other_params=voice_dict.get('other_params')
                    )

                for voice_dict in primary_voices_data:
                    _create_voice(voice_dict)

                for voice_dict in fallback_voices_data:
                    primary_language = voice_dict['primary_voice_language']
                    primary_voice = Voice.objects.filter(
                        company_bot=instance, type=VoiceType.TextToText, language=primary_language
                    ).first()
                    if not primary_voice:
                        continue  # No matching primary — skip, don't guess
                    _create_voice(voice_dict, primary_voice=primary_voice)
            except (json.JSONDecodeError, KeyError) as e:
                pass  # Skip if JSON is invalid

        # Import CompanyStateMachine objects
        if 'state_machines' in row and row['state_machines']:
            try:
                sm_data = json.loads(row['state_machines'])
                # Delete existing state machines if updating
                if not new:
                    CompanyStateMachine.objects.filter(company_bot=instance).delete()

                for sm_dict in sm_data:
                    # Skip if essential fields are missing
                    if not sm_dict.get('name') or sm_dict.get('step') is None:
                        continue

                    # Handle preprocess and postprocess bot references
                    preprocess_bot = None
                    if sm_dict.get('preprocess_bot_name'):
                        try:
                            preprocess_bot = CompanyBot.objects.get(
                                name=sm_dict['preprocess_bot_name'],
                                company=instance.company
                            )
                        except CompanyBot.DoesNotExist:
                            pass

                    postprocess_bot = None
                    if sm_dict.get('postprocess_bot_name'):
                        try:
                            postprocess_bot = CompanyBot.objects.get(
                                name=sm_dict['postprocess_bot_name'],
                                company=instance.company
                            )
                        except CompanyBot.DoesNotExist:
                            pass

                    CompanyStateMachine.objects.create(
                        company_bot=instance,
                        name=sm_dict.get('name'),
                        step=sm_dict.get('step'),
                        use_stage_chats=sm_dict.get('use_stage_chats', False),
                        type=sm_dict.get('type', 'mandatory'),
                        text_conversion_type=sm_dict.get('text_conversion_type', 'translate'),
                        bot_question=sm_dict.get('bot_question'),
                        completion_criteria=sm_dict.get('completion_criteria'),
                        context=sm_dict.get('context'),
                        tool_context=sm_dict.get('tool_context'),
                        preprocess_type=sm_dict.get('preprocess_type', 'none'),
                        preprocess_prompt=sm_dict.get('preprocess_prompt'),
                        preprocess_bot=preprocess_bot,
                        preprocess_output_mode=sm_dict.get('preprocess_output_mode', 'none'),
                        postprocess_type=sm_dict.get('postprocess_type', 'none'),
                        postprocess_prompt=sm_dict.get('postprocess_prompt'),
                        postprocess_bot=postprocess_bot,
                        postprocess_output_mode=sm_dict.get('postprocess_output_mode', 'none'),
                        skip_to_step=sm_dict.get('skip_to_step'),
                    )
            except (json.JSONDecodeError, KeyError) as e:
                pass  # Skip if JSON is invalid