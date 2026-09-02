import os
from celery import Celery


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shikshalokam_mohini.settings')

REDIS_HOST = os.environ.get('REDIS_HOST', "localhost")
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB = os.environ.get("REDIS_DB", 0)

app = Celery(
    'shikshalokam_mohini',
    backend=f'redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}',
    broker=f'redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'
)

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks([
    'chatbot.celery_tasks.common_chat_tasks',
    'chatbot.celery_tasks.flow_tasks',
    'chatbot.celery_tasks.title_tasks'
])
