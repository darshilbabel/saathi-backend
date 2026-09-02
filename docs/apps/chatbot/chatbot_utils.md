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
- `media_preview/`: `media_creation.py` and supporting modules — generates PDF/DOCX documents on request during a live chat (reachable from `common_handler.py`). See [Media Templates and Document Generation](#media-templates-and-document-generation) below.
- `admin_config/`: `export_mixin.py` — shared admin export behavior, used by `chatbot/admin/company_admin.py`.
- `shiksha_chaupal/`: `date_utils.py`, `base_utils.py` — date/prompt helpers used by `common_handler.py`.
- `elevate/`: `profile_utils.py` — Elevate UMS profile integration, used by the profile/auth views.

## Usage

- These utilities are imported and used strategically across services, consumers, and celery tasks for streamlined logic and code reuse.

---

## Media Templates and Document Generation

`chatbot/utils/media_preview/media_creation.py` generates the downloadable
documents behind the `download_file` tool call (triggered mid-chat, handled in
`chatbot/services/response_handlers/common_handler.py`'s
`_handle_freeflow_function_call`). It always tries to produce **both** a PDF
and a DOCX for the same request — either can succeed or fail independently;
`download_file`'s response only includes whichever URLs actually came back.

### The `MediaTemplate` model

`chatbot/models/company_models.py` — one row per `(flow, type)`. Replaces the
older `PDFTemplates` model (still in the codebase and still populated, but no
longer read by any render path — `PDFTemplates`' admin page has been
unregistered; see [Admin](chatbot_admin.md#mediatemplate-admin)). Fields:

- `type` — `MediaTemplateType` enum (`PDF` or `DOCX` today). Decides which
  render function handles the row and which of the two template fields below
  is actually used.
- `flow` — optional FK to `Flow`. `render_*_from_template()` looks a template
  up by `(flow, type)`.
- `template_name` — unique, admin-facing label only.
- `constants_json` — `{language_code: {label_key: label_value}}`, e.g.
  `{"en": {"goal_label": "Goal"}, "hi": {"goal_label": "लक्ष्य"}}`. Looked up
  by the current chat's language, falling back to `"en"`. Per-row, not shared
  across a flow's PDF and DOCX templates — a deliberate choice (over e.g.
  moving labels onto `Flow` itself) so creating a PDF `MediaTemplate` needs no
  code changes, matching how `PDFTemplates.constants_json` already worked.
- `template` — used when `type=PDF`. Inline HTML with Jinja2 tags, rendered by
  `jinja2.Template(...).render(**context)` then converted to PDF via
  Gotenberg (`generate_pdf_with_gotenberg`, `gotenberg_utils.py`).
- `template_file` — used when `type=DOCX`. An uploaded `.docx` file containing
  `docxtpl`/Jinja2 tags, rendered by the `docxtpl` library (Jinja2 embedded
  directly inside the Word document's XML, rather than in an HTML string).

Both `template` and `template_file` are rendered with the **same context
shape**, so template authors work with one consistent convention regardless
of output format:

```python
context = {
    'args': template_args,      # the download_file tool call's arguments, e.g. args.goal, args.action_plan
    'constants': lang_constants,  # constants_json[language] (or ["en"] as fallback)
    'language': language,
    'profile': profile,          # the chat session's Profile, or None
    'sources': sources,          # finalized_sources accumulated during the chat
}
```

A real example from the production `MIP` template:
`{{ args.goal }}`, `{{ constants.goal_label }}`,
`{% for item in args.action_plan %}{{ item.action }} — {{ item.week }}{% endfor %}`.

### DOCX-specific: `docxtpl` row/paragraph loops

Word documents wrap text in XML elements (`<w:p>` for a paragraph, `<w:tr>`
for a table row) that plain Jinja2 doesn't understand how to loop over.
`docxtpl` adds two special tag forms for this — **the `for`/`endfor` markers
must sit in their own row/paragraph, separate from the content being looped**,
or the tag-stripping step silently corrupts the template:

- **Table row loop**: a row containing only `{%tr for item in args.action_plan %}`,
  then a separate content row with `{{ item.action }}` etc. (this row repeats
  once per iteration, unmodified from the author's perspective), then a
  separate row containing only `{%tr endfor %}`.
- **Paragraph loop**: same shape with `{%p for ... %}` / `{%p endfor %}`, each
  the sole content of their own paragraph, bracketing a content paragraph.

Mixing the `for`/`endfor` marker into the same row/paragraph as the loop
content is the most common authoring mistake — it raises a Jinja2
`TemplateSyntaxError` at render time (encountered this exact failure while
prototyping; the fix was splitting the combined row into three).

### `render_template_to_pdf()` and `render_docx_from_template()`

Both live in `media_creation.py`, both take the same argument shape
(`flow_name`, `arguments`, `company_bot_id`, `session_id`, `sources`,
`language`, `display_filename`) and return `{'success': bool, 'media_url': str, 'file_name': str}`
or `{'success': False, 'error': str}`. Neither call is source-of-truth for
whether a document exists — `_handle_freeflow_function_call` calls both and
tolerates either one failing.

They differ in fallback behavior when no `MediaTemplate` is configured for the
flow:

- `render_template_to_pdf()` **falls back** to `create_and_upload_file()` — a
  generic text-dump PDF — so a flow always produces *some* PDF.
- `render_docx_from_template()` has **no fallback** — if no
  `MediaTemplate(type=DOCX, flow=X)` exists, it returns
  `{'success': False, 'error': 'No DOCX template configured for this flow'}`
  and no DOCX is produced for that request. This was an explicit decision,
  not an oversight — DOCX generation is meant to always go through an
  admin-authored template now, not silently fall back to hardcoded structure.

### `create_docx_from_args()` — superseded, kept for reference

The original DOCX generator, entirely hardcoded in Python (`python-docx`,
manually building headings/tables per an `is_mip` branch, no admin template
involved at all). Still defined in `media_creation.py` but **no longer called
anywhere** — `common_handler.py`'s import of it is commented out with an
explanation, not deleted. Do not resurrect the call site; if DOCX generation
for a flow needs to work again, add a `MediaTemplate(type=DOCX)` row for it
instead.

### Adding a future document type (e.g. PPTX, XLSX)

`MediaTemplateType` is a plain Python enum (`chatbot/models/enums.py`), not a
DB-driven table like `Language`/`Provider` — deliberately, since a new output
*format* always needs new rendering code (there's no such thing as a
code-free new file format), so a DB table would only add indirection without
removing the need for a deploy. To add one:

1. Add the new value to `MediaTemplateType` (`chatbot/models/enums.py`).
2. Decide which existing `MediaTemplate` field shape it needs — inline text
   (like `template`) or an uploaded file (like `template_file`) — per the
   discussion that shaped this design, most future formats will fit one of
   these two; a genuinely different shape (e.g. structured JSON config
   instead of a file or freeform text) would need a new field on the model.
3. Write a new `render_<format>_from_template()` function in
   `media_creation.py`, following `render_docx_from_template()`'s shape:
   look up `MediaTemplate.objects.filter(flow=flow, type=<NEW_TYPE>).first()`,
   build the same `args`/`constants`/`language`/`profile`/`sources` context,
   render, upload via `upload_file_to_s3()`, return the same
   `{'success', 'media_url', 'file_name'}` / `{'success': False, 'error'}` shape.
4. Wire it into `_handle_freeflow_function_call()` in `common_handler.py`
   alongside the existing `pdf_result`/`docx_result` calls, and add its URL
   to the `download` dict the same way `docx_url` was added.
5. Update `MediaTemplateAdmin.get_fields()` (`chatbot/admin/media_template_admin.py`)
   to show the right field for the new type on the change form.
6. Decide the fallback behavior deliberately (PDF falls back to a generic
   document, DOCX does not) — don't default to either without a decision.

---
