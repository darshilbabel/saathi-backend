from django.urls import re_path
from .consumers.Reflection_bedrock_consumer import ReflectionBedrockConsumer
from .consumers.async_chaupal_consumer import AsyncShikshalokamChaupalConsumer
from .consumers.async_consumer import AsyncSocketConsumer
from .consumers.free_flow_consumer import FreeFlowConsumer
from .consumers.guided_guest_consumer import GuidedGuestConsumer
from .consumers.mitra_bedrock_consumer import MitraBedrockConsumer
from .consumers.one_shot_bedrock_consumer import OneShotBedrockConsumer
from .consumers.oneshot_guest_consumer import OneShotGuestConsumer
from .consumers.shikshalokam_bedrock_consumer import ShikshalokamBedrockConsumer


websocket_urlpatterns = [
    re_path(r"ws/common/$", AsyncSocketConsumer.as_asgi()),
]
