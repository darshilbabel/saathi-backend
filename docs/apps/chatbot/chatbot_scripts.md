# Chatbot Scripts

## Overview

This documents utility scripts located in the `chatbot/scripts/` directory. This directory was pruned to only the scripts still in active use — everything Story/Media/Theme-related, plus several unmaintained one-off scripts, was removed. See the repo-root `CODE_CLEANUP_PLAN.md` for the full history.

## Key Scripts

All three are standalone `shell_plus`-paste scripts: open `python manage.py shell_plus`, paste the file's contents, then call the documented entry-point function.

- `check_kb_web_search_scores.py`: Scans `CompanyChat.chunks` and splits chats into KB+web-search vs. KB-only groups, writing per-chat scores to an output file. Read-only.
- `export_chats_by_userid.py`: Given a CSV of Name/Phone Number/User Id rows, exports every `CompanyChat` for each mapped `Profile` to an xlsx workbook (one row per chat message).
- `lang_detect_eval.py`: Runs AI4Bharat/Bhashini language-detection service IDs against a sample-sentence set and writes an accuracy report (one sheet per service ID plus a summary). **Note:** this script imports `chatbot.scripts.lang_detect_sample_texts.SAMPLE_TEXTS`, which is currently missing from the repo (pre-existing gap, not caused by the code cleanup) — the script will not run until that file is restored.

## Usage

These scripts serve as manual data-export/diagnostic tools, not part of any automated pipeline. They are not imported by any other part of the application.
