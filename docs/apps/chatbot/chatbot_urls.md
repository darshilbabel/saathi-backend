# Chatbot URLs and Routing

## Overview

The chatbot application exposes HTTP API endpoints (`chatbot/urls.py`, mounted at the root by `shikshalokam_mohini/urls.py`) and a single WebSocket route (`chatbot/routing.py`) for chatbot functionality. Story/Media-specific routes (story management, media batch upload/tracking/document-search, PDF generation, PTM question-save, location lookups) were removed as not part of Saathi's scope — see the repo-root `CODE_CLEANUP_PLAN.md` for the full history.

## HTTP URL Routing

- Defined in `chatbot/urls.py`.
- Uses Django REST Framework views for API endpoints.
- Endpoints support authentication/profile management, chat sessions, company chat/bot CRUD, bot vernacular, speech/translation, flow lookups, and file uploads (via presigned URL).
- `shikshalokam_mohini/urls.py` also directly registers `/health/`, `/admin/`, `/docs/`, and `/api/storage/upload-local/<object_key>` (the local-dev-mode counterpart of the presigned-upload flow).

### Example Endpoints

- `/api/login/`, `/api/logout/`, `/api/generate-session/`: Auth/session lifecycle.
- `/api/get-profile/`, `/api/update-profile/`, `/api/accept-tnc/`, `/api/create-profile/`: Profile management.
- `/api/shikshalokam/read-elevate-profile/`: Elevate UMS profile lookup.
- `/api/save-company-chat/`, `/api/create-chatsession/`, `/api/chatsession/`: Chat session management.
- `/api/companychat/`, `/api/companychat-feedback/`, `/api/companybot/`, `/api/bot_vernacular/`: Company chat/bot CRUD.
- `/api/text_to_speech/`, `/api/asr/`, `/api/text_translate/`, `/api/text_transliterate/`: Speech/translation.
- `/api/flow-languages/`, `/api/flow-connection-info/`: Flow lookups.
- `/api/get-presigned-url/`: S3 (or local-dev) file upload.
- `/admin/<app_label>/<model_name>/batch-upload/`, `batch-template/`, `batch-import/`: Generic Django-admin batch tooling.

## WebSocket Routing

- Defined in `chatbot/routing.py`.
- Only one route is registered: `ws/common/`, mapped to `AsyncSocketConsumer`.
- Every chatbot flow (regardless of bot type) connects through this single route — see [Consumers](chatbot_consumers.md) and [Strategies](chatbot_strategies.md) for how dispatch to different bot behaviors happens internally rather than via separate routes.

## Summary

This routing setup provides HTTP access to chatbot data/session management and a single real-time WebSocket channel for chat.

---
