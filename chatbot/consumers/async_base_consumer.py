import asyncio
import json
import os
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import Max
from chatbot.models import ChatSession, CompanyChat, ChatStatus, Profile, CompanyBot, CompanyBotTypeChoices
from chatbot.models.company_models import CompanyStateMachine
import logging
import traceback

logger = logging.getLogger('django')


class AsyncBaseConsumer(AsyncWebsocketConsumer):
    IDLE_TIMEOUT_SECONDS = int(os.getenv('WEBSOCKET_IDLE_TIMEOUT', '60'))
    IDLE_POLL_INTERVAL = int(os.getenv('WEBSOCKET_IDLE_POLL_INTERVAL', '10'))

    async def connect(self):
        await self.accept()
        self.last_activity = asyncio.get_running_loop().time()
        self._idle_task = asyncio.create_task(self._idle_timeout_monitor())

    async def _idle_timeout_monitor(self):
        try:
            while True:
                await asyncio.sleep(self.IDLE_POLL_INTERVAL)
                idle_seconds = asyncio.get_running_loop().time() - self.last_activity
                if idle_seconds >= self.IDLE_TIMEOUT_SECONDS:
                    logger.info('Idle timeout on channel %s after %.1fs', self.channel_name, idle_seconds)
                    await self.send(text_data=json.dumps({
                        "msg": "Connection closed due to inactivity.",
                        "source": "system",
                        "error": False,
                        "event": "idle_timeout"
                    }))
                    await self.close()
                    return
        except asyncio.CancelledError:
            pass

    async def disconnect(self, code):
        if hasattr(self, '_idle_task') and self._idle_task:
            self._idle_task.cancel()
        try:
            if hasattr(self, 'session_id') and self.session_id:
                session_id = self.session_id
            else:
                session_id = self.scope.get('cookies', {}).get('sessionid')

            if session_id:
                await self.save_chat_session(session_id)
                if getattr(self, 'profile_id', None) and getattr(self, 'bot_route', None):
                    company_chat_status = await self.determine_company_chat_status_async(
                        session_id=session_id,
                        profile_id=self.profile_id,
                        is_disconnected=True,
                        route=self.bot_route
                    )
                    await self.update_last_chat_status_async(chat_status=company_chat_status)
        except Exception as e:
            traceback.print_exc()
            logger.error('Receive Error: %s', e, exc_info=True)

        finally:
            try:
                await self.close()
            except Exception:
                pass

    async def receive(self, text_data):
        raise NotImplementedError("receive method must be implemented in subclass")

    async def chat_message(self, event):
        self.last_activity = asyncio.get_running_loop().time()
        text = event["text"]
        await self.send(text_data=json.dumps({"text": text}))

    @database_sync_to_async
    def save_chat_session(self, session_id):
        from chatbot.celery_tasks.title_tasks import generate_session_title
        session = ChatSession.objects.filter(session=session_id).first()
        if session and not session.title:
            language = getattr(self, 'route', 'en') or 'en'
            generate_session_title.delay(session_id, language)

    @database_sync_to_async
    def determine_company_chat_status(self, session_id, profile_id, route, is_disconnected=False):
        if not session_id:
            return None
        chat_session = ChatSession.objects.filter(session=session_id).first()
        if not chat_session:
            return None

        profile = Profile.objects.filter(id=profile_id).first()
        try:
            if profile and profile.company:
                company_bot = CompanyBot.objects.get(company=profile.company, route=route)
            else:
                company_bot = CompanyBot.objects.get(route=route)

            existing_chats = CompanyChat.objects.filter(session=session_id)

            if company_bot.bot_type == CompanyBotTypeChoices.SIMPLE:
                if existing_chats.exclude(sender_id=1).count() == 0:
                    return ChatStatus.STARTED
                if is_disconnected:
                    return ChatStatus.PAUSED
                last_chat = existing_chats.last()
                if last_chat and last_chat.status == ChatStatus.PAUSED:
                    return ChatStatus.RESUME
                return ChatStatus.IN_PROGRESS

            state_machine = CompanyStateMachine.objects.filter(
                company_bot=company_bot, step=chat_session.current_step
            ).first()

            max_step = CompanyStateMachine.objects.filter(
                company_bot=company_bot
            ).aggregate(Max('step'))['step__max']
            is_last_step = state_machine and state_machine.step == max_step

            if existing_chats.exclude(sender_id=1).count() == 0:
                return ChatStatus.STARTED
            elif state_machine and not is_last_step and is_disconnected:
                return ChatStatus.PAUSED
            elif existing_chats.exists():
                last_chat = existing_chats.last()
                if last_chat and last_chat.status == ChatStatus.PAUSED:
                    return ChatStatus.RESUME
            elif chat_session and chat_session.session_status == ChatStatus.COMPLETED:
                return ChatStatus.COMPLETED

            return ChatStatus.IN_PROGRESS
        except Exception as e:
            logger.info('Error in determine_company_chat_status: %s', e, exc_info=True)

            return ChatStatus.PAUSED  # Default safe value

    async def determine_company_chat_status_async(self, session_id, profile_id, route, is_disconnected=False):
        return await self.determine_company_chat_status(session_id, profile_id, route, is_disconnected)

    @database_sync_to_async
    def update_last_chat_status(self, chat_status):
        if not hasattr(self, 'session_id') or not self.session_id:
            return

        try:
            existing_chat = CompanyChat.objects.filter(session=self.session_id).last()
            if not existing_chat:
                return

            if existing_chat.status != ChatStatus.COMPLETED:
                existing_chat.status = chat_status
                existing_chat.save()

            chat_session = ChatSession.objects.filter(session=self.session_id).first()
            if chat_session and chat_session.session_status != ChatStatus.COMPLETED:
                chat_session.session_status = chat_status
                chat_session.save(update_fields=['session_status', 'updated_at'])
        except Exception as e:
            logger.info('Error in update_last_chat_status: %s', e, exc_info=True)

    async def update_last_chat_status_async(self, chat_status):
        await self.update_last_chat_status(chat_status)

    @database_sync_to_async
    def update_session_status(self, chat_status):
        if not hasattr(self, 'session_id') or not self.session_id:
            return
        try:
            chat_session = ChatSession.objects.filter(session=self.session_id).first()
            if chat_session and chat_session.session_status != ChatStatus.COMPLETED:
                chat_session.session_status = chat_status
                chat_session.save(update_fields=['session_status', 'updated_at'])
        except Exception as e:
            logger.info('Error in update_session_status: %s', e, exc_info=True)
