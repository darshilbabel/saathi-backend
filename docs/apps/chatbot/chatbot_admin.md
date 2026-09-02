# Chatbot Admin Module

## Overview

The `admin` module contains Django admin customizations facilitating management of chatbot configurations and content through the Django Admin interface. Story/Media/Theme/I18n admin modules (`story_admin.py`, `media_admin.py`, `theme_admin.py`, `i18n_admin.py`) were removed along with those models — see the repo-root `CODE_CLEANUP_PLAN.md` for the full history.

## Key Admin Modules

- `bot_vernacular_admin.py`: Admin configurations for bot vernacular settings allowing customization of bot messages per bot and locale.
- `company_admin.py`: Admin setup for managing Company, CompanyBot, Flow, and related entities.
- `generic_upload_admin.py`: Provides generic CSV bulk upload functionality for admin models enabling structured batch data ingestion.
- `language_provider_admin.py`: Admin configurations for Language/Provider/LanguageProviderConfig.
- `pdf_template_admin.py`: Admin configurations for PDF templates.
- `profile_admin.py`: Admin configurations for managing user profile data.

## Purpose

These admin customizations provide a user-friendly and efficient UI overlay that simplifies managing core chatbot configurations, content, and metadata by directly manipulating the database through Django's ORM.

They support bulk uploading, content review, and entity configuration essential for daily operational management.
