import asyncio
import json
import os
from django.conf import settings
from django.db import transaction
from chatbot.celery_tasks.common_chat_tasks import save_in_company_db
from chatbot.consumers.async_base_consumer import AsyncBaseConsumer
from chatbot.models import ChatStatus, ChatSession, Profile, CompanyBot, Voice, VoiceType, ChatType, CompanyChat, \
    TextConversionType, CompanyBotTypeChoices
from chatbot.celery_tasks.flow_tasks import get_flow_response
from chatbot.models.company_models import CompanyStateMachine
from chatbot.utils.audio_provider_utils import text_translate_provider
from asgiref.sync import sync_to_async
from chatbot.utils.elevate.profile_utils import fetch_elevate_user, upsert_elevate_profile
import logging
from channels.db import database_sync_to_async
from chatbot.utils.transliterate_utils import transliterate_text
import jwt

logger = logging.getLogger('django')
PUBLIC_KEY = os.getenv("JWT_PUBLIC_KEY")


class AsyncSocketConsumer(AsyncBaseConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = None
        self.profile_id = None
        self.route = None
        self.bot_route = None
        self.company_bot = None
        self.flow_name = None
        self.ip_address = None
        self.access_token = None
        self.ums_profile = None
        self.background_tasks = set()

    async def disconnect(self, code):
        try:
            logger.info(f"Websocket closed with code: %s", code)
        except Exception as e:
            logger.error('Disconnect Error: %s', e, exc_info=True)
        finally:
            # Don't call self.close() here - let the parent handle that
            await super().disconnect(code)

    async def profile_update(self, event):
        """Merge freshly-submitted profile values (e.g. from submit_user_context) into the live session's
        ums_profile, prioritizing them over the stale snapshot fetched at authenticate time."""
        updates = event.get('ums_profile_updates') or {}
        if not updates:
            return
        self.ums_profile = {**(self.ums_profile or {}), **updates}
        logger.info('[profile_update] merged into live ums_profile=%s', self.ums_profile)

    async def receive(self, text_data):
        self.last_activity = asyncio.get_running_loop().time()
        try:
            logger.info(f"Received text data via common websocket: {text_data}")
            text_data_json = json.loads(text_data)
            message_type = text_data_json.get('type', None)
            company_chat_status = None
            if message_type == 'authenticate':
                self.session_id = text_data_json.get('sessionid')
                self.profile_id = text_data_json.get('profileid')
                self.route = text_data_json.get('route')
                self.bot_route = text_data_json.get('bot_route')
                self.flow_name = text_data_json.get('flow_name')
                self.ip_address = text_data_json.get('address')
                self.access_token = text_data_json.get('access_token')

                user_id = await self.handle_access_token(self.access_token)

                elevate_user_data = await sync_to_async(fetch_elevate_user, thread_sensitive=False)(self.access_token)
                elevate_result = await self.upsert_elevate_profile(elevate_user_data)
                if elevate_result.get('error') == 'unauthorized':
                    logger.error('[authenticate] Elevate auth failure — closing connection')
                    await self.send(text_data=json.dumps({
                        "msg": "Authentication failed. Invalid or expired token.", 
                        "source": "system", 
                        "error": True, 
                        "event": "auth_error"
                    }))
                    await self.close()
                    return
                if elevate_result.get('error') == 'elevate_server_error':
                    logger.error('[authenticate] Elevate server error — closing connection')
                    await self.send(text_data=json.dumps({
                        "msg": "Service unavailable. Please try again later.", 
                        "source": "system", 
                        "error": True, 
                        "event": "service_error"
                    }))
                    await self.close()
                    return
                if not elevate_result.get('profileid'):
                    logger.error('[authenticate] Elevate returned no profileid — closing connection')
                    await self.send(text_data=json.dumps(
                        {"msg": "Authentication failed. Please try again.", 
                         "source": "system", 
                         "error": True, 
                         "event": "auth_error"
                         }
                    ))
                    await self.close()
                    return
                self.profile_id = elevate_result['profileid']
                self.ums_profile = elevate_result.get('ums_profile')

                profile = await self.get_profile(self.profile_id)
                logger.info(
                    'channel_name: %s, session_id: %s, profile_id: %s, route: %s',
                    self.channel_name, self.session_id, self.profile_id, self.route
                )

                self.company_bot = await self.get_company_bot(profile, self.bot_route)

                # Create chat session asynchronously
                _, session_created = await self.create_chat_session(
                    self.session_id, profile, self.company_bot, self.ip_address, user_id
                )

                if not session_created:
                    company_chat_status = await self.determine_company_chat_status_async(
                        session_id=self.session_id, profile_id=self.profile_id, route=self.bot_route
                    )
                    await self.update_session_status(chat_status=company_chat_status)
            else:
                # Validate that user is authenticated before processing messages
                if not self.session_id or not self.bot_route:
                    error_msg = "Authentication required. Please send authentication message first with type='authenticate', sessionid, profileid, route, and bot_route."
                    logger.error(f"Unauthenticated message attempt: {error_msg}")
                    await self.channel_layer.send(
                        self.channel_name,
                        {
                            "type": "chat_message",
                            "text": {
                                "msg": error_msg,
                                "source": "system",
                                "error": True,
                                "event": "auth_required"
                            },
                        },
                    )
                    return
                company_chat_status = await self.determine_company_chat_status_async(
                    session_id=self.session_id, profile_id=self.profile_id, route=self.bot_route
                )
                await self.channel_layer.send(
                    self.channel_name,
                    {
                        "type": "chat_message",
                        "text": {"msg": text_data_json["text"], "source": "user"},
                    },
                )

            translated_message = None
            if self.route != 'en' and text_data_json and text_data_json.get('text'):
                translated_message = await self.translate_message(text_data_json['text'])

            if message_type != 'authenticate' and text_data_json and text_data_json.get('text'):
                chat_session = await database_sync_to_async(
                    lambda: ChatSession.objects.filter(session=self.session_id).order_by('-created_at').first()
                )()

                current_stage = None
                if chat_session and self.company_bot and self.company_bot.bot_type == CompanyBotTypeChoices.STATE_MACHINE:
                    try:
                        state_machine = await database_sync_to_async(
                            lambda: CompanyStateMachine.objects.get(
                                company_bot=self.company_bot, step=chat_session.current_step
                            )
                        )()
                        if state_machine:
                            current_stage = state_machine.name
                    except CompanyStateMachine.DoesNotExist:
                        logger.error(
                            f"CompanyStateMachine not found for bot_id={self.company_bot.id}, "
                            f"step={chat_session.current_step}. "
                            f"Please create state machines in admin panel."
                        )
                # Use a task for database operations
                await database_sync_to_async(save_in_company_db)(
                    session_id=self.session_id, profile_id=self.profile_id, initiated_by='User',
                    message=text_data_json['text'], chunks=None, status=company_chat_status,
                    translated_message=translated_message, audio_base64=text_data_json.get('asr_audio'),
                    stage=current_stage
                )
                if company_chat_status == ChatStatus.IN_PROGRESS:
                    await self.update_session_status(chat_status=company_chat_status)

            logger.info(
                f"channel_name: %s, session_id: %s, profile_id: %s, route: %s",
                self.channel_name, self.session_id, self.profile_id, self.route
            )

            if message_type != 'authenticate':
                # Start the Celery task but don't wait for it
                get_flow_response.delay(
                    self.channel_name, self.session_id, self.profile_id, self.route,
                    'common', self.bot_route, access_token=self.access_token,
                    ums_profile=self.ums_profile
                )

        except Exception as e:
            logger.error('Receive Error: %s', e, exc_info=True)

    async def connect(self):
        try:
            logger.info(f"Attempting to connect to websocket")
            await super().connect()
        except Exception as e:
            logger.error('Connect Error: %s', e, exc_info=True)

    @database_sync_to_async
    def get_profile(self, profile_id):
        if not profile_id:
            return None
        return Profile.objects.filter(id=profile_id).first()

    @database_sync_to_async
    def handle_access_token(self, access_token):
        user_id = None

        if access_token and PUBLIC_KEY:
            try:
                decoded = jwt.decode(
                    access_token,
                    PUBLIC_KEY,
                    algorithms=["HS256"]
                )
                if decoded:
                    user_id = decoded.get("data", {}).get("id")
            except Exception as e:
                logger.error('[handle_access_token] JWT decode error: %s', e, exc_info=True)
        elif access_token and not PUBLIC_KEY:
            logger.error('[handle_access_token] JWT_PUBLIC_KEY is not set — skipping decode')

        logger.info('[handle_access_token] user_id=%s', user_id)
        return user_id

    @database_sync_to_async
    def upsert_elevate_profile(self, user_data):
        return upsert_elevate_profile(user_data)

    @database_sync_to_async
    def get_company_bot(self, profile, route):
        if profile and profile.company_id:
            return CompanyBot.objects.get(company=profile.company, route=route)
        else:
            return CompanyBot.objects.get(route=route)

    @database_sync_to_async
    def create_chat_session(self, session_id, profile, company_bot, ip_address, user_id):
        step_number = 1
        if profile and profile.first_name and profile.first_name != '':
            try:
                challenges_step = CompanyStateMachine.objects.get(
                    company_bot=self.company_bot, name="CHALLENGES"
                )
                step_number = challenges_step.step
            except CompanyStateMachine.DoesNotExist:
                step_number = 1
        cs, cs_created = ChatSession.objects.get_or_create(
            session=session_id,
            defaults={
                'profile': profile,
                'current_step': step_number,
                'language': self.route,
                'company_bot': company_bot,
                'session_status': ChatStatus.IN_PROGRESS,
                'user_id': user_id,
                'session_type': self.flow_name
            }
        )
        logger.info(f"Chatsession: %s %s", cs, cs_created)

        if not cs_created:
            # Locked so this read-modify-write of other_params (on a reconnect) can't
            # race with a still-in-flight celery task's other_params writes for a
            # previous turn on this same session (usage, finalized_sources, the
            # pending text-conversion override).
            with transaction.atomic():
                cs = ChatSession.objects.select_for_update().get(pk=cs.pk)
                if cs.language != self.route:
                    cs.language = self.route

                other_params = cs.other_params or {}
                other_params["ip_address"] = ip_address

                cs.other_params = other_params

                cs.save(update_fields=["language", "other_params"])
        else:
            cs.other_params = {"ip_address": ip_address}
            cs.save(update_fields=["other_params"])


        return cs, cs_created

    @database_sync_to_async
    def translate_message(self, message):
        try:
            # One-shot override: respond_to_user.next_reply_conversion from the previous bot
            # turn (persisted in ChatSession.other_params by
            # BaseResponseHandler._save_pending_text_conversion) takes priority over the
            # state's static text_conversion_type. Consumed and cleared here, before any
            # early return below, so it's removed exactly once regardless of whether a
            # voice provider is actually configured for this message. Locked so this
            # read-modify-write can't race with a concurrent _save_pending_text_conversion
            # write for the next turn (e.g. the user sends a new message before the
            # previous turn's celery task has finished writing other_params).
            pending_conversion = None
            with transaction.atomic():
                chat_session = ChatSession.objects.select_for_update().filter(session=self.session_id).first()
                if chat_session:
                    other_params = chat_session.other_params or {}
                    pending_conversion = other_params.pop('pending_text_conversion_type', None)
                    if pending_conversion is not None:
                        chat_session.other_params = other_params
                        chat_session.save(update_fields=['other_params'])

            if not self.company_bot:
                return message

            voice_provider = Voice.objects.filter(
                company_bot=self.company_bot,
                type=VoiceType.TextToText,
                language=self.route
            ).first()

            if not voice_provider:
                return message

            if not chat_session:
                return message

            if pending_conversion is not None:
                use_transliterate = str(pending_conversion).strip().upper() == TextConversionType.TRANSLITERATE
            else:
                state_machine = CompanyStateMachine.objects.filter(
                    company_bot=self.company_bot, step=chat_session.current_step
                ).first()
                use_transliterate = bool(
                    state_machine and state_machine.text_conversion_type == TextConversionType.TRANSLITERATE
                )

            if use_transliterate:
                transliterate_voice_provider = Voice.objects.filter(
                    company_bot=self.company_bot,
                    type=VoiceType.Transliterate,
                    language=self.route
                ).first()
                response =  transliterate_text(
                    voice_provider=transliterate_voice_provider, source_language=self.route, target_language='en',
                    message_body=message, is_sentence=True
                )
                logger.info('[translate_message] transliterate response=%s', response)
                if response and response.get('content'):
                    content = response.get('content')
                    if content and isinstance(content, list) and len(content)>0:
                        content = content[0]
                    return content
            else:
                response = text_translate_provider(
                    voice_provider=voice_provider, message_body=message,
                    target_language='en', source_language=self.route
                )

                if response.get('status') == 200:
                    return response.get('content')
                else:
                    return message

        except Exception as e:
            logger.error('Translation Error: %s', e, exc_info=True)
            return message
