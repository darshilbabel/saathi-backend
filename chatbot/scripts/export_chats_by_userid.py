# Standalone shell_plus script.
#
# Usage: open `python manage.py shell_plus`, paste this ENTIRE file, then run:
#
#     export_chats_by_userid(
#         input_csv='/path/to/phnNumberData.csv',
#         output_xlsx='/path/to/output.xlsx',
#     )
#
# It's wrapped in exec("""...""") on purpose — some shell_plus setups (plain
# Python REPL inside tmux/screen, no bracketed paste) mis-parse a pasted
# multi-line script because blank lines inside a function body look like
# "end of block" to the incremental parser. Wrapping the whole body in a
# single string literal sidesteps that: the REPL just buffers lines until
# the closing triple-quote, then exec() runs it as one unit.
#
# Input CSV must have columns: Name, Phone Number, User Id
# (header names are matched case-insensitively, spaces/underscores ignored).
#
# For every input row, this fetches chatbot_profile by userid, then every
# CompanyChat where that profile is sender or receiver, and writes one xlsx
# row per chat message (all CompanyChat fields except other_params), with
# the original Name/Phone Number/User Id repeated on every row so it can be
# filtered/pivoted in Google Sheets. Rows whose userid doesn't map to a
# profile, or has no chats, still get exactly one output row with the chat
# columns left blank — a mapping_status column says why.

exec("""
import csv
import pandas as pd
from django.db.models import Q
from chatbot.models import Profile
from chatbot.models.company_models import CompanyChat

COMPANY_CHAT_FIELDS = [
    'id', 'session', 'message', 'translated_message', 'chunks',
    'sender_id', 'receiver_id', 'created_at', 'updated_at', 'status',
    'feedback', 'source', 'source_msg_id', 'whatsapp_message_id',
    'message_type', 'stage', 'file_url', 'audio_file',
]  # deliberately excludes other_params

EMPTY_CHAT_ROW = {f'chat_{f}': '' for f in COMPANY_CHAT_FIELDS}
EMPTY_CHAT_ROW['chat_file_url_https'] = ''


def _s3_to_https(value):
    if value and value.startswith('s3://'):
        return 'https://' + value[len('s3://'):]
    return ''


def _read_input_rows(input_csv):
    with open(input_csv, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        header_map = {}
        for raw_name in reader.fieldnames or []:
            key = raw_name.strip().lower().replace(' ', '').replace('_', '')
            header_map[key] = raw_name

        name_col = header_map.get('name')
        phone_col = header_map.get('phonenumber') or header_map.get('phone')
        userid_col = header_map.get('userid')

        if not (name_col and phone_col and userid_col):
            raise ValueError(
                f"Could not find Name/Phone Number/User Id columns. "
                f"Found headers: {reader.fieldnames}"
            )

        rows = []
        for row in reader:
            rows.append({
                'csv_name': (row.get(name_col) or '').strip(),
                'csv_phone_number': (row.get(phone_col) or '').strip(),
                'csv_user_id': (row.get(userid_col) or '').strip(),
            })
        return rows


def _chat_row(chat):
    out = {}
    for field in COMPANY_CHAT_FIELDS:
        value = getattr(chat, field)
        out[f'chat_{field}'] = str(value) if value not in (None, '') else ''
    out['chat_file_url_https'] = _s3_to_https(out['chat_file_url'])
    return out


def export_chats_by_userid(input_csv, output_xlsx):
    input_rows = _read_input_rows(input_csv)
    print(f"[export_chats_by_userid] read {len(input_rows)} input rows from {input_csv}")

    output_rows = []
    stats = {'no_userid_in_csv': 0, 'profile_not_found': 0, 'profile_found_no_chats': 0, 'profile_found': 0}

    for i, row in enumerate(input_rows, start=1):
        userid = row['csv_user_id']
        base = {
            'name': row['csv_name'],
            'phone_number': row['csv_phone_number'],
            'user_id': userid,
        }

        if not userid:
            stats['no_userid_in_csv'] += 1
            output_rows.append({**base, 'mapping_status': 'no_userid_in_csv',
                                 'profile_id': '', 'profile_first_name': '', 'profile_last_name': '',
                                 'profile_phone': '', 'profile_email': '', 'profile_status': '',
                                 **EMPTY_CHAT_ROW})
            continue

        profile = Profile.objects.filter(userid=userid).first()

        if not profile:
            stats['profile_not_found'] += 1
            output_rows.append({**base, 'mapping_status': 'profile_not_found',
                                 'profile_id': '', 'profile_first_name': '', 'profile_last_name': '',
                                 'profile_phone': '', 'profile_email': '', 'profile_status': '',
                                 **EMPTY_CHAT_ROW})
            continue

        profile_cols = {
            'profile_id': profile.id,
            'profile_first_name': profile.first_name or '',
            'profile_last_name': profile.last_name or '',
            'profile_phone': profile.phone or '',
            'profile_email': profile.email or '',
            'profile_status': profile.status or '',
        }

        chats = CompanyChat.objects.filter(
            Q(sender_id=profile.id) | Q(receiver_id=profile.id)
        ).order_by('session', 'created_at')

        if not chats.exists():
            stats['profile_found_no_chats'] += 1
            output_rows.append({**base, 'mapping_status': 'profile_found_no_chats',
                                 **profile_cols, **EMPTY_CHAT_ROW})
            continue

        stats['profile_found'] += 1
        for chat in chats:
            output_rows.append({**base, 'mapping_status': 'profile_found',
                                 **profile_cols, **_chat_row(chat)})

        if i % 50 == 0:
            print(f"[export_chats_by_userid] processed {i}/{len(input_rows)} input rows")

    df = pd.DataFrame(output_rows)
    df.to_excel(output_xlsx, index=False, engine='openpyxl')

    print("[export_chats_by_userid] done")
    print(f"  input rows           : {len(input_rows)}")
    print(f"  no_userid_in_csv      : {stats['no_userid_in_csv']}")
    print(f"  profile_not_found     : {stats['profile_not_found']}")
    print(f"  profile_found_no_chats: {stats['profile_found_no_chats']}")
    print(f"  profile_found         : {stats['profile_found']}")
    print(f"  output rows (xlsx)    : {len(output_rows)}")
    print(f"  written to            : {output_xlsx}")

    return df
""")

# ============================================================================
# Run — edit the paths below before pasting into shell_plus
# ============================================================================
# export_chats_by_userid(
#     input_csv='/home/kunal/user_sample_data.csv',
#     output_xlsx='/home/kunal/sample_output.xlsx',
# )
