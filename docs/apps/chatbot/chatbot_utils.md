# Chatbot Utilities

## Overview

The utilities module contains helper functions and classes that support various chatbot operations ranging from audio processing, translation, and profile handling to LLM integrations and PDF/DOCX generation. Story/Media-specific helpers (project formatting, image-converter, database/vector utilities, knowledge-service extraction, and the per-flow tool-call helpers for the removed consumers) were removed — see the repo-root `CODE_CLEANUP_PLAN.md` for the full history.

## Key Utility Files and Functions

- `chat_utils.py`: Utilities related to chatbot message processing and guided chat generation.
- `audio_provider_utils.py`: Functions for handling audio provider integrations including text-to-text translation.
- `audio_converter_utils.py`: Audio format conversion helpers.
- `transliterate_utils.py`: Supports text transliteration used in chat translations.
- `profile_utils.py`: Helpers to deal with user profile data extraction and formatting.
- `llm.py`: Instruments interactions with Large Language Model providers.
- `sql_utils.py`: Small SQL helpers used by prompt building and other utils.
- `gotenberg_utils.py`: HTML-to-PDF conversion via the Gotenberg service.
- `chat_query_handler.py`, `pycountry_utils.py`: Supporting helpers for chat querying and country/language code lookups.

## Subdirectories

- `S3/`: `s3_service.py` — `upload_file_to_s3`, used by the document-generation path in `media_preview/`.
- `media_preview/`: `media_creation.py` and supporting modules — generates PDF/DOCX documents on request during a live chat (reachable from `common_handler.py`).
- `admin_config/`: `export_mixin.py` — shared admin export behavior, used by `chatbot/admin/company_admin.py`.
- `shiksha_chaupal/`: `date_utils.py`, `base_utils.py` — date/prompt helpers used by `common_handler.py`.
- `elevate/`: `profile_utils.py` — Elevate UMS profile integration, used by the profile/auth views.

## Usage

- These utilities are imported and used strategically across services, consumers, and celery tasks for streamlined logic and code reuse.

---
