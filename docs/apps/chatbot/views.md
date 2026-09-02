# View Layer

The View layer exposes HTTP endpoints and acts as the execution boundary between client requests and backend workflows.

Responsibilities of this layer:

- Parse and validate incoming requests
- Handle authentication (JWT where applicable)
- Resolve contextual entities (User, Company, CompanyBot)
- Trigger business workflows
- Initiate asynchronous tasks when required
- Return structured JSON responses

Views coordinate execution but do not contain heavy domain logic.

> **Note:** Story/Media/recommendation/location-specific view modules (`views/story_views.py`, `views/Media/*`, `views/recommendation.py`, `views/location_views.py`, `views/kafka_views.py`, `views/mitra_views.py`, `views/gotenberg_view.py`, `views/admin/post_processing_views.py`) were removed as not part of Saathi's scope — see the repo-root `CODE_CLEANUP_PLAN.md` for the full history.

---

## 1. Chat APIs

### `chatbot/views/chat_view.py`

#### Purpose
Implements conversational session lifecycle and message persistence.

#### Responsibilities

- Create chat sessions
- Persist user messages
- Persist bot messages
- Associate chat sessions with CompanyBot
- Resolve authenticated user context
- Maintain chronological conversation ordering
- Structure response payload for frontend
- Ensure conversation continuity across requests

This is the primary entry point for conversational workflows.

---

## 2. Authentication, Profile & Session APIs

### `chatbot/views/api_views.py`

#### Purpose

Handles session generation, profile synchronization, authentication, and token management.

This module initializes and maintains authenticated user context before domain workflows begin.

---

#### Responsibilities

##### 1. Session Initialization

- Generate Django session ID using `SessionStore`
- Return session key to client
- Establish session-based tracking

---

##### 2. Profile Creation & Synchronization (`post_profile`)

- Validate required fields (email + company/subdomain)
- Resolve Company using slug
- Create or update Profile record
- Handle phone-based fallback lookup
- Serialize and persist profile data
- Perform first-name transliteration using AI4Bharat API (when preferred language is provided)
- Support demo / development company slugs

Note: this endpoint's URL route (`api/profile/`) is currently commented out/disabled in `chatbot/urls.py`.

---

##### 3. Login (`login`)

- Validate email and password
- Verify hashed password using `check_password`
- Fetch associated ProfileAddress
- Issue JWT access token via `RefreshToken`
- Store session authentication state
- Return authenticated profile metadata

---

##### 4. Logout (`logout`)

- Extract token from Authorization header
- Blacklist JWT token via `BlacklistedToken`
- Clear Django session
- Remove session cookie

---

#### Architectural Role

This module:

- Establishes authenticated user identity
- Resolves company context
- Issues JWT credentials
- Synchronizes profile state
- Manages session lifecycle 

It is the authentication boundary layer for the application.

---

### `chatbot/views/profile_views.py`

#### Responsibilities

- Create profile records (`create_profile_views`)
- Read the caller's Elevate UMS profile (`read_elevate_profile`, relocated here from the removed `shikshalokam` app — served at `api/shikshalokam/read-elevate-profile/`)

---

## 3. Translation, Voice & Transliteration APIs

### `chatbot/views/bhashini_views.py`

#### Purpose

Provides multilingual processing and voice transformation endpoints.

This module dynamically selects language providers based on `CompanyBot` configuration and `VoiceType`.

---

#### Responsibilities

##### 1. Text-to-Speech (`text_speech_view`)

- Validate required route
- Resolve `CompanyBot` using route
- Select configured `Voice` provider (TextToSpeech)
- Generate audio from text via `text_speech_provider`
- Return encoded audio content

---

##### 2. Speech-to-Text (`speech_text`)

- Fetch audio from S3 URL
- Convert audio to WAV base64 format
- Resolve `CompanyBot` and fallback to `/common_bot` if needed
- Select SpeechToText voice provider
- Generate transcript via `speech_text_provider`
- Return transcription output

---

##### 3. Text Translation (`text_translation_view`)

- Resolve `CompanyBot` using route
- Select TextToText voice provider
- Translate message via `text_translate_provider`
- Return translated transcript

---

##### 4. Transliteration (`text_transliterate_view`)

- Resolve `CompanyBot` using route
- Select Transliterate voice provider
- Optionally detect source language using AI4Bharat API
- Perform script-level transliteration via `transliterate_text`
- Return transliterated output

---

#### Architectural Role

This module:

- Acts as multilingual abstraction layer
- Dynamically selects providers per bot configuration
- Integrates external language APIs
- Supports STT, TTS, Translation, and Transliteration
- Maintains consistent JSON response structure

It centralizes all language transformation workflows behind route-based configuration.

---

## 4. Infrastructure Integration APIs

### `chatbot/views/aws_views.py`

#### Responsibilities

- Generate S3 presigned URLs (`get_presigned_url`)
- Handle local-dev-mode direct file upload (`upload_media_local`) — the counterpart used when `STORAGE_CLOUD_PROVIDER=LOCAL`
- Validate upload-related parameters
- Return signed access credentials

---

## 5. DRF-Based Generic APIs

### `chatbot/views/drf_views.py`

#### Purpose

Provides Django REST Framework–based generic CRUD endpoints for core models.

#### Responsibilities

- Implement ListCreateAPIView and RetrieveUpdateAPIView patterns
- Expose model-level CRUD operations for CompanyChat, CompanyBot, BotVernacular, ChatSession, and Flow-related lookups
- Apply serializer-based validation
- Integrate Django Filter backend for query filtering
- Support pagination and query parameter filtering
- Return standardized DRF response formats

This module centralizes DRF-based CRUD patterns instead of writing custom views for each model.

---

## 6. Admin & Configuration Views

These views are accessible through Django Admin and are restricted to authenticated staff users.

They enable configuration management, bulk operations, and admin-triggered processing workflows that are not exposed to public APIs.

---

### `chatbot/views/admin/bot_admin_views.py`

#### Purpose

Implements import and export workflows for `CompanyBot` along with its related configuration models.

#### Detailed Responsibilities

- Export a CompanyBot configuration into structured JSON
- Include related inline models during export:
  - Voices
  - State Machines
  - Bot Vernacular
- Generate JSON templates to guide correct import format
- Import CompanyBot configuration from JSON payload
- Reconstruct related inline objects during import
- Detect whether to update an existing bot (route-based matching) or create a new one
- Execute the entire import inside a database transaction to ensure atomicity
- Enforce permission-based restrictions (e.g., superuser/moderator controls)

#### What This Enables

- Safe migration of bot configurations between environments
- Backup and restore of complex bot setups
- Replication of conversational configurations across tenants
- Structured configuration management without manual DB manipulation

This view effectively serializes and reconstructs bot-level conversational configuration.

---

### `chatbot/views/admin/generic_upload_views.py`

#### Purpose

Provides a reusable bulk upload engine for arbitrary Django models via CSV.

#### Detailed Responsibilities

- Dynamically inspect model fields using Django model metadata
- Generate downloadable CSV templates reflecting model structure
- Parse uploaded CSV files row-by-row
- Perform type conversion for fields (Integer, Boolean, Date, etc.)
- Resolve ForeignKey references using lookup logic
- Resolve ManyToMany relationships
- Validate required fields and field constraints
- Collect row-level validation errors
- Execute bulk inserts/updates within database transactions
- Return structured success/error summaries

#### What This Enables

- Admin-level batch data ingestion without writing custom import scripts
- Controlled mass updates for structured models
- Reduced risk of manual data-entry inconsistencies
- Transaction-safe bulk operations with validation feedback

This acts as a generic data ingestion utility within the admin layer.

---

### `chatbot/views/admin/chat_import_views.py`

#### Purpose

Supports the company-chat import tool (see `chatbot/templates/admin/company_chat_import.html`).

---

## Admin Layer Characteristics

- Restricted to authenticated staff users
- Transaction-safe configuration changes
- Async-aware processing for heavy workflows
- Structured validation and error reporting
- Designed for configuration management and controlled bulk operations

---

## View Layer Execution Characteristics

### 1. Thin Request Boundary
Views manage request lifecycle and workflow initiation.

### 2. Async-Aware Architecture
Heavy workflows rely on Celery.

### 3. Structured Persistence Before Async Execution
Data is persisted before triggering asynchronous workflows.

### 4. Context Resolution
User, Company, and CompanyBot context are resolved early.

### 5. Deterministic Response Contracts
All endpoints return predictable JSON schemas.

### 6. Separation of Runtime & Admin Flows
Runtime APIs and admin workflows are clearly separated.
