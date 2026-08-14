# Standalone shell_plus script.
#
# Usage: open `python manage.py shell_plus`, paste this ENTIRE file, then run:
#
#     check_kb_web_search_scores(output_path='/path/to/kb_web_search_scores.txt')
#
# It's wrapped in exec("""...""") on purpose — some shell_plus setups (plain
# Python REPL inside tmux/screen, no bracketed paste) mis-parse a pasted
# multi-line script because blank lines inside a function body look like
# "end of block" to the incremental parser. Wrapping the whole body in a
# single string literal sidesteps that: the REPL just buffers lines until
# the closing triple-quote, then exec() runs it as one unit.
#
# Scans CompanyChat.chunks and splits chats into two groups, written as two
# clearly separate sections in the output file:
#   - KB + WEB SEARCH: both a kb_search and a web_search chunk are present
#     (KB had a result, but web search still got pulled in)
#   - KB ONLY: kb_search chunks present, no web_search chunk at all
#     (KB result was accepted on its own)
# For each chat, writes every score found in its chunks, keyed by chat id.
# No DB writes — read-only.

exec("""
import json
from chatbot.models import CompanyChat


def _parse_chunks(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, list) else None


def _split_kb_and_web_search_scores():
    \"\"\"Return (kb_and_web, kb_only) — each a list of (chat_id, scores).\"\"\"
    kb_and_web = []
    kb_only = []

    qs = CompanyChat.objects.exclude(chunks__isnull=True).exclude(chunks='').order_by('id')
    for chat in qs.iterator():
        chunks = _parse_chunks(chat.chunks)
        if not chunks:
            continue

        sources = {c.get('source') for c in chunks if isinstance(c, dict)}
        if 'kb_search' not in sources:
            continue

        scores = [c.get('score') for c in chunks if isinstance(c, dict) and 'score' in c]

        if 'web_search' in sources:
            kb_and_web.append((chat.id, scores))
        else:
            kb_only.append((chat.id, scores))

    return kb_and_web, kb_only


def _write_section(f, title, results):
    f.write(f'{title}\\n')
    f.write('=' * len(title) + '\\n\\n')
    for chat_id, scores in results:
        f.write(f'Chat ID: {chat_id}\\n')
        f.write(f'Scores: {scores}\\n\\n')
    f.write('\\n')


def check_kb_web_search_scores(output_path='kb_web_search_scores.txt'):
    kb_and_web, kb_only = _split_kb_and_web_search_scores()

    with open(output_path, 'w') as f:
        _write_section(f, 'KB + WEB SEARCH (KB had a result, web search still triggered)', kb_and_web)
        _write_section(f, 'KB ONLY (no web search — KB result accepted alone)', kb_only)

    print(f'[check_kb_web_search_scores] KB + web search: {len(kb_and_web)} chats')
    print(f'[check_kb_web_search_scores] KB only        : {len(kb_only)} chats')
    print(f'[check_kb_web_search_scores] written to {output_path}')

    return kb_and_web, kb_only
""")

# ============================================================================
# Run — edit the path below before pasting into shell_plus
# ============================================================================
# check_kb_web_search_scores(
#     output_path='chatbot/kb_web_search_scores.txt',
# )