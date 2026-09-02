# Shikshalokam Backend

## What This System Does

This backend powers a conversational AI platform (Saathi).

It enables:

### 1. Conversational AI Workflows
- Real-time chat via a single WebSocket flow (`ws/common/`), shared by every bot/session type
- Session persistence
- Multi-language support
- Voice (STT/TTS), translation, and transliteration
- On-demand PDF/DOCX document generation during a chat (e.g. action-plan reports)

### 2. Company & Bot Configuration
- Company, CompanyBot, Flow, and state-machine-driven conversation configuration via Django Admin
- Bot vernacular (per-language introductory/error messages)
- Language/provider configuration for translation and speech services

> **Note:** Story generation, project creation, knowledge-base document ingestion/search
> (Media), and the `observability` LLM-evaluation app were removed as not part of
> Saathi's current scope — see the repo-root `CODE_CLEANUP_PLAN.md` for the full
> history of what was removed and why.
