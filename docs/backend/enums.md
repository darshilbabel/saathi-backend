# Django Enums

`chatbot/models/enums.py`

This document defines all enumeration classes used across the platform.

Enums ensure consistency, validation, and type safety for status fields, providers, configuration types, and workflow definitions.

---

> **Note:** `ThemeType`, `TagChoices`, `TagSourceChoices`, `MediaTemplateChoices`, `StorySourceChoices`, `StoryStatusChoices`, and `CreateStoryChoices` were removed as unused (Story/Media/Theme cleanup). `ChatStageChoices`, `ChatType`, `FileDisplayMode`, `FileTypeChoices`, `LanguageChoices`, `PDFStrategyChoices`, `StoryLanguageChoices`, and `VoiceProviderChoices` were later removed as unused too (usage audit) — `ChatType` was an unused import in two files, the rest had zero references anywhere. `StoryLanguageChoices` specifically was replaced by a `Language`-table-sourced admin dropdown for `ChatSession.language` rather than being kept as a fixed choice list. `MediaTypeChoices` remains despite the naming — still used by kept code (`media_creation.py`'s PDF/DOCX generation). `MediaTemplateType` (new) is unrelated to the removed `MediaTemplateChoices` — it backs the current `MediaTemplate` model (see [Admin](../apps/chatbot/chatbot_admin.md)), not a leftover from the Story/Media cleanup.

## 1. ChatStatus

### Purpose

Represents the lifecycle status of a chat session.
    Used to track conversation progress and state transitions.

### Values

| Name | Value |
|------|-------|
| STARTED | STARTED |
| IN_PROGRESS | IN_PROGRESS |
| COMPLETED | COMPLETED |
| PAUSED | PAUSED |
| RESUME | RESUME |

---

## 2. CompanyBotDynamicContextType

### Purpose

Specifies dynamic context generation mechanism.
    Supports SQL queries or Python scripts.

### Values

| Name | Value |
|------|-------|
| SQL_QUERY | SQL_QUERY |
| PYTHON_SCRIPT | PYTHON_SCRIPT |

---

## 3. CompanyBotTypeChoices

### Purpose

Defines architecture type of company bots.
    Determines conversation execution strategy.

### Values

| Name | Value |
|------|-------|
| SIMPLE | SIMPLE |
| STATE_MACHINE | STATE_MACHINE |
| DATABASE_SIMPLE | DATABASE_SIMPLE |
| INTERVIEW_STATE_MACHINE | INTERVIEW_STATE_MACHINE |

---

## 4. CompanyChatSourceChoices

### Purpose

Identifies source platform of a chat session.
    Used for analytics and usage tracking.

### Values

| Name | Value |
|------|-------|
| WEB | WEB |
| PHONE | PHONE |

---

## 5. EntityStatus

### Purpose

Indicates whether an entity is active or inactive.
    Supports soft-deletion and visibility control.

### Values

| Name | Value |
|------|-------|
| ACTIVE | ACTIVE |
| INACTIVE | INACTIVE |

---

## 6. EntityTypeChoices

### Purpose

Marks whether an entity is mandatory or optional.
    Used in dynamic validation and schema enforcement.

### Values

| Name | Value |
|------|-------|
| MANDATORY | MANDATORY |
| OPTIONAL | OPTIONAL |

---

## 7. FeedbackChoices

### Purpose

Captures feedback sentiment classification.
    Used for analytics and rating systems.

### Values

| Name | Value |
|------|-------|
| POSITIVE | POSITIVE |
| NEGATIVE | NEGATIVE |

---

## 8. GenderChoices

### Purpose

Stores supported gender options.
    Used in user demographic information.

### Values

| Name | Value |
|------|-------|
| MALE | Male |
| FEMALE | Female |

---

## 9. LLMModel

### Purpose

Enumerates all supported AI model identifiers.
    Used for dynamic model configuration.

### Values

| Name | Value |
|------|-------|
| GPT4 | gpt-4 |
| GPT4_1 | gpt-4.1 |
| GPT4_1_MINI | gpt-4.1-mini |
| GPT4_128K | gpt-4-1106-preview |
| GPT4_TURBO | gpt-4-turbo |
| LLAMA_3_8B_8192 | llama3-8b-8192 |
| LLAMA_3_70B_8192 | llama3-70b-8192 |
| LLAMA_3_1_70B_VERSATILE | llama-3.1-70b-versatile |
| LLAMA_3_1_8B_INSTANT | llama-3.1-8b-instant |
| LLAMA_3_1_70B_INSTRUCT | meta.llama3-1-70b-instruct-v1:0 |
| LLAMA_3_1_8B_INSTRUCT | meta.llama3-1-8b-instruct-v1:0 |
| LLAMA_3_3_70B_INSTRUCT | us.meta.llama3-3-70b-instruct-v1:0 |
| LLAMA_3_3_8B_INSTRUCT | us.meta.llama3-3-8b-instruct-v1:0 |
| MIXTRAL_8X70B_32768 | mixtral-8x7b-32768 |
| GPT4_O | gpt-4o |
| GPT4_O_MINI | gpt-4o-mini |
| LLAMA_3_1_8B_OPS | meta-llama/Meta-Llama-3.1-8B-Instruct |
| GPT5_2 | gpt-5.2 |
| GPT5_2_PRO | gpt-5.2-pro |
| GPT5_MINI | gpt-5-mini |

---

## 10. LLMProvider

### Purpose

Lists supported Large Language Model providers.
    Determines which AI backend service is used.

### Values

| Name | Value |
|------|-------|
| BEDROCK | bedrock |
| BEDROCK_CONVERSE | bedrock/converse |
| OPENAI | openai |

---

## 11. MediaTemplateType

### Purpose

Output format a `MediaTemplate` row produces — decides which of that model's
type-specific fields is used (`template` for PDF, `template_file` for DOCX).
Deliberately a plain enum, not a DB-driven table like `Language`/`Provider` —
unlike adding a language, adding a new output format always requires new
rendering code (a new `render_*_from_template()` function in
`media_creation.py`), so DB-configurability would add indirection without
removing the need for a deploy. See [Admin](../apps/chatbot/chatbot_admin.md)
for the `MediaTemplate` model and admin behavior, and
[Utils](../apps/chatbot/chatbot_utils.md) for the render functions.

### Values

| Name | Value |
|------|-------|
| PDF | PDF |
| DOCX | DOCX |

---

## 12. MediaTypeChoices

### Purpose

Supported MIME types for uploaded media.
    Used for validation and content handling.

### Values

| Name | Value |
|------|-------|
| PDF | application/pdf |
| TXT | text/plain |
| CSV | text/csv |
| JPEG | image/jpeg |
| PNG | image/png |
| SVG | image/svg+xml |
| WEBP | image/webp |
| HEIF | image/heif |
| HEIC | image/heic |
| XLSX | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet |

---

## 13. PostProcessOutputMode

### Purpose

Controls workflow behavior after postprocessing.
    Can skip execution of the next stage.

### Values

| Name | Value |
|------|-------|
| NONE | NONE |
| SKIP | SKIP |

---

## 14. PostProcessType

### Purpose

Defines postprocessing strategy after LLM response.
    Used for response refinement and enhancement.

### Values

| Name | Value |
|------|-------|
| NONE | NONE |
| SIMPLE | SIMPLE |
| COMPLEX | COMPLEX |

---

## 15. PreProcessOutputMode

### Purpose

Controls behavior after preprocessing stage.
    Can skip execution of the current stage.

### Values

| Name | Value |
|------|-------|
| NONE | NONE |
| SKIP | SKIP |

---

## 16. PreProcessType

### Purpose

Defines preprocessing strategy before LLM execution.
    Controls prompt transformation complexity.

### Values

| Name | Value |
|------|-------|
| NONE | NONE |
| SIMPLE | SIMPLE |
| COMPLEX | COMPLEX |

---

## 17. ProfileType

### Purpose

Defines different user profile roles.
    Used for access control and permissions.

### Values

| Name | Value |
|------|-------|
| USER | USER |
| MODERATOR | MODERATOR |
| PROSPECT | PROSPECT |

---

## 18. RouteLanguageChoices

### Purpose

Maps URL route prefixes to language codes.
    Used for multilingual routing configuration.

### Values

| Name | Value |
|------|-------|
| ENGLISH | en |
| HINDI | hi |
| KANNADA | kn |
| TELUGU | te |

---

## 19. SessionFlowName

### Purpose

Represents predefined session flow identifiers.
    Used to control guest, login, and special flows.

### Values

| Name | Value |
|------|-------|
| GuestDiscussion | guest-discussion |
| LoginDiscussion | login-discussion |
| GuestMiStory | guest-mi-story |
| ListeningActivity | listening-activity |
| LoginMiStory | login |
| SsoFlow | sso |
| Reflection | reflection |
| megaPTM | megaPTM |
| YLC | YLC |
| ParentPerceptionSurvey | parent_perception_survey |
| creation | creation |

---

## 20. TextConversionType

### Purpose

Specifies text transformation operation type.
    Supports translation and transliteration modes.

### Values

| Name | Value |
|------|-------|
| TRANSLATE | TRANSLATE |
| TRANSLITERATE | TRANSLITERATE |

---

## 21. VoiceProvider

### Purpose

Lists supported speech processing providers.
    Used for transcription and voice synthesis services.

### Values

| Name | Value |
|------|-------|
| GOOGLE | GOOGLE |
| GOOGLE_V1 | GOOGLE_V1 |
| AI4Bharat | AI4Bharat |
| OPENAI_WHISPER | OPENAI_WHISPER |
| SARVAM | Sarvam |

---

## 22. VoiceType

### Purpose

Defines type of voice processing operation.
    Covers STT, TTS, and transliteration modes.

### Values

| Name | Value |
|------|-------|
| SpeechToText | SpeechToText |
| TextToText | TextToText |
| TextToSpeech | TextToSpeech |
| Transliterate | Transliterate |

---
