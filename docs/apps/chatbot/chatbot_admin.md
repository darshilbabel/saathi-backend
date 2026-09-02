# Chatbot Admin Module

## Overview

The `admin` module contains Django admin customizations facilitating management of chatbot configurations and content through the Django Admin interface. Story/Media/Theme/I18n admin modules (`story_admin.py`, `media_admin.py`, `theme_admin.py`, `i18n_admin.py`) were removed along with those models — see the repo-root `CODE_CLEANUP_PLAN.md` for the full history.

## Key Admin Modules

- `bot_vernacular_admin.py`: Admin configurations for bot vernacular settings allowing customization of bot messages per bot and locale.
- `company_admin.py`: Admin setup for managing Company, CompanyBot, Flow, and related entities.
- `generic_upload_admin.py`: Provides generic CSV bulk upload functionality for admin models enabling structured batch data ingestion.
- `language_provider_admin.py`: Admin configurations for Language/Provider/LanguageProviderConfig.
- `media_template_admin.py`: Admin configurations for `MediaTemplate` (downloadable-document templates — PDF and DOCX today). Superseded `pdf_template_admin.py`, which has been removed from the admin site (unregistered, not deleted — `PDFTemplates`' model, table, and data are untouched; anyone who used to edit PDF templates there now uses this page with `type=PDF`).
- `profile_admin.py`: Admin configurations for managing user profile data.

### MediaTemplate admin

`MediaTemplateAdmin` shows a different set of fields depending on whether the
row is being created or already exists, and on its `type`:

- **Add form**: only `flow`, `type`, `template_name` — deliberately minimal,
  so the admin commits to a format before anything format-specific is shown.
- **Change form** (after the first save): the same three fields, plus
  `constants_json`, plus exactly one of:
  - `template` (a large textarea) — shown when `type=PDF`. Inline HTML/Jinja2,
    same convention as the old `PDFTemplatesAdmin`.
  - `template_file` (a file upload) — shown when `type=DOCX`. An uploaded
    `.docx` file containing `docxtpl`/Jinja2 tags (e.g. `{{ args.goal }}`,
    `{%tr for item in args.action_plan %}` for a table-row loop). Stored via
    Django's default storage backend (S3 in production, same mechanism as
    `Company.logo`/`Voice.audio_file`) at
    `media_template/{type}/{id}/{filename}` — the `{id}` only exists once the
    row has already been saved once, which is exactly why the file field is
    withheld from the add form.

This is implemented via `MediaTemplateAdmin.get_fields(request, obj)` reading
`obj.type` fresh on each request — it is not live JavaScript toggling as the
`type` dropdown changes before saving; the field set only updates on the next
page load after a save.

`flow` is optional (not required) — a `MediaTemplate` can exist unattached to
a flow, same as `PDFTemplates` allowed.

See [Utils](chatbot_utils.md#media-templates-and-document-generation) for how
these templates are actually rendered, and what's involved in adding a new
document type beyond PDF/DOCX.

## Purpose

These admin customizations provide a user-friendly and efficient UI overlay that simplifies managing core chatbot configurations, content, and metadata by directly manipulating the database through Django's ORM.

They support bulk uploading, content review, and entity configuration essential for daily operational management.
