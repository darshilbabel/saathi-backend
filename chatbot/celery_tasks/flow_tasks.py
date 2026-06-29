from celery import shared_task

from chatbot.services.core.bot_service_factory import BotServiceFactory
from chatbot.services.core.orchestrator import ChatOrchestrator
import logging

logger = logging.getLogger('django')


@shared_task
def get_flow_response(channel_name, session_id, profile_id, route, bot_type, bot_route, access_token=None,
                      ums_profile=None):
    logger.info('[get_flow_response] bot_type=%s bot_route=%s', bot_type, bot_route)
    bot_strategy = BotServiceFactory.create_bot_service(
        bot_type=bot_type, route=bot_route
    )
    orchestrator = ChatOrchestrator(bot_strategy=bot_strategy)
    return orchestrator.process_chat_request(
        channel_name=channel_name, session_id=session_id, profile_id=profile_id,
        language=route, access_token=access_token, ums_profile=ums_profile
    )
