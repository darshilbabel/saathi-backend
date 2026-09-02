# Chatbot Celery Tasks

## Overview

Celery tasks support asynchronous execution of chatbot workloads allowing responsive UX and offloading heavy operations. `chatbot/celery_tasks/` now only holds tasks reachable from the live `ws/common/` flow (see [Consumers](chatbot_consumers.md)); the per-flow task modules for consumers that were confirmed unrouted (chaupal, free-flow, guided-guest, oneshot-guest, and the Bedrock-provider variants) were removed, along with `post_processing_tasks.py` and `ptm_report_tasks.py` (both only served already-removed features).

## Key Celery Task Modules

### handle_message.py

- Utility methods for sending and translating messages on websocket channels.
- Utilizes Django Channels for WebSocket integration.

### common_chat_tasks.py

- Contains common chatbot task logic such as saving chat messages to DB.

### flow_tasks.py

- Manages chatbot flow processing tasks; entry point for `AsyncSocketConsumer`'s `get_flow_response` call into `BotServiceFactory`/`ChatOrchestrator`.

### title_tasks.py

- Handles chat/session title generation tasks.

`shikshalokam_mohini/celery_config.py`'s `autodiscover_tasks()` list is the authoritative registry of what's actually active — it currently only lists `common_chat_tasks`, `flow_tasks`, and `title_tasks`.

## Interaction

- Celery tasks are invoked primarily by websocket consumers and service layers.
- They ensure non-blocking operations and scalability.

---
