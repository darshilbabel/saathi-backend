# Standalone shell_plus script.
#
# Usage: open `python manage.py shell_plus`, paste this ENTIRE file, then run:
#
#     run_lang_detect_eval(
#         output_xlsx='/path/to/lang_detect_eval.xlsx',
#     )
#
# It's wrapped in exec("""...""") on purpose — some shell_plus setups (plain
# Python REPL inside tmux/screen, no bracketed paste) mis-parse a pasted
# multi-line script because blank lines inside a function body look like
# "end of block" to the incremental parser. Wrapping the whole body in a
# single string literal sidesteps that: the REPL just buffers lines until
# the closing triple-quote, then exec() runs it as one unit.
#
# What this does: for each AI4Bharat/Bhashini txt-lang-detection serviceId in
# SERVICE_IDS, runs all 600 sample sentences (100 each for hi/en/kn/ta/te/or —
# 50 short/medium/long sentences plus 50 big-paragraph inputs — from
# lang_detect_sample_texts.SAMPLE_TEXTS) through the API, records the
# predicted langCode/scriptCode/langScore, and flags whether it matches the
# known-correct language for that sentence. Output is one xlsx workbook with
# one tab per serviceId (all languages in that tab, filterable via the
# `expected_lang` column) plus a `summary` tab with per serviceId/language
# accuracy. Needs BHASHANI_BASE_URL / BHASHANI_AUTHORIZATION env vars set,
# same as chatbot/translate/ai4Bharat/text_lang_detect.py.

exec("""
import os
import time
import traceback

import pandas as pd
import requests

from chatbot.scripts.lang_detect_sample_texts import SAMPLE_TEXTS

ai4bharat_base_url = os.getenv("BHASHANI_BASE_URL")
ai4bharat_authorization = os.getenv("BHASHANI_AUTHORIZATION")

SERVICE_IDS = [
    "bhashini/indic-lang-detection-all",
    "bhashini/iiiith/indic-lang-detection-all",
    "bhashini/indic/tld",
]


def _sheet_name(service_id, used_names):
    name = service_id.replace('/', '_')[:31]
    base, i = name, 2
    while name in used_names:
        suffix = f'_{i}'
        name = base[:31 - len(suffix)] + suffix
        i += 1
    used_names.add(name)
    return name


def _length_bucket(text):
    n = len(text)
    if n < 20:
        return 'short'
    if n < 80:
        return 'medium'
    if n < 250:
        return 'long'
    return 'big_paragraph'


def _call_lang_detect(service_id, text):
    payload = {
        "pipelineTasks": [
            {
                "taskType": "txt-lang-detection",
                "config": {
                    "serviceId": service_id,
                }
            }
        ],
        "inputData": {
            "input": [
                {
                    "source": text
                }
            ]
        }
    }
    headers = {
        'accept': '*/*',
        'content-type': 'application/json',
        'Authorization': ai4bharat_authorization,
    }
    response = requests.post(ai4bharat_base_url, json=payload, headers=headers, timeout=15)
    result = {
        'http_status': response.status_code,
        'detected_lang_code': '',
        'detected_script_code': '',
        'lang_score': '',
        'error': '',
    }
    if response.status_code != 200:
        result['error'] = response.text[:300]
        return result

    data = response.json()
    if not (isinstance(data, dict) and 'pipelineResponse' in data):
        result['error'] = 'Unexpected response format'
        return result

    try:
        prediction = data['pipelineResponse'][0]['output'][0]['langPrediction'][0]
        result['detected_lang_code'] = prediction.get('langCode', '')
        result['detected_script_code'] = prediction.get('scriptCode', '')
        result['lang_score'] = prediction.get('langScore', '')
    except (KeyError, IndexError):
        result['error'] = 'Could not parse langPrediction from response'

    return result


def run_lang_detect_eval(output_xlsx, service_ids=None, sleep_seconds=0.25):
    service_ids = service_ids or SERVICE_IDS

    total_calls = len(service_ids) * sum(len(v) for v in SAMPLE_TEXTS.values())
    print(f"[lang_detect_eval] {len(service_ids)} service id(s) x "
          f"{sum(len(v) for v in SAMPLE_TEXTS.values())} sentences = {total_calls} calls total")

    # Per-service sheet column layout is: A=service_id, B=expected_lang,
    # C=detected_lang_code, D=detected_script_code, E=is_correct, F=text,
    # G=char_count, H=length_bucket, I=http_status, J=lang_score, K=error.
    # expected_lang/detected_lang_code/detected_script_code are kept adjacent
    # so they're easy to eyeball side by side. Keep this in sync with the
    # `row = {...}` keys below — the summary sheet's formulas reference these
    # columns by letter.
    EXPECTED_LANG_COL = 'B'
    IS_CORRECT_COL = 'E'

    used_names = {'summary'}
    sheet_name_map = {sid: _sheet_name(sid, used_names) for sid in service_ids}

    per_service_rows = {}
    summary_rows = []
    call_count = 0

    for service_id in service_ids:
        sheet_name = sheet_name_map[service_id]
        rows = []
        for expected_lang, texts in SAMPLE_TEXTS.items():
            correct = 0
            for text in texts:
                call_count += 1
                try:
                    result = _call_lang_detect(service_id, text)
                except Exception as e:
                    traceback.print_exc()
                    result = {
                        'http_status': 0,
                        'detected_lang_code': '',
                        'detected_script_code': '',
                        'lang_score': '',
                        'error': str(e),
                    }
                is_correct = (result['detected_lang_code'] == expected_lang)
                if is_correct:
                    correct += 1
                row = {
                    'service_id': service_id,
                    'expected_lang': expected_lang,
                    'detected_lang_code': result['detected_lang_code'],
                    'detected_script_code': result['detected_script_code'],
                    'is_correct': is_correct,
                    'text': text,
                    'char_count': len(text),
                    'length_bucket': _length_bucket(text),
                    'http_status': result['http_status'],
                    'lang_score': result['lang_score'],
                    'error': result['error'],
                }
                rows.append(row)

                if call_count % 25 == 0:
                    print(f"[lang_detect_eval] {call_count}/{total_calls} calls done")

                time.sleep(sleep_seconds)

            summary_row_num = len(summary_rows) + 2  # +2: header is row 1, rows are 1-indexed
            summary_rows.append({
                'service_id': service_id,
                'language': expected_lang,
                'total': (
                    f"=COUNTIF('{sheet_name}'!{EXPECTED_LANG_COL}:{EXPECTED_LANG_COL},"
                    f'"{expected_lang}")'
                ),
                'correct': (
                    f"=COUNTIFS('{sheet_name}'!{EXPECTED_LANG_COL}:{EXPECTED_LANG_COL},"
                    f'"{expected_lang}",'
                    f"'{sheet_name}'!{IS_CORRECT_COL}:{IS_CORRECT_COL},TRUE)"
                ),
                'accuracy_pct': f"=IFERROR(ROUND(100*D{summary_row_num}/C{summary_row_num},1),0)",
                '_correct_count': correct,  # for the console printout only, not written to xlsx
                '_total_count': len(texts),
            })

        per_service_rows[service_id] = rows

    with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
        summary_df = pd.DataFrame(summary_rows).drop(columns=['_correct_count', '_total_count'])
        summary_df.to_excel(writer, sheet_name='summary', index=False)
        for service_id, rows in per_service_rows.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name_map[service_id], index=False)

    print("[lang_detect_eval] done")
    for row in summary_rows:
        acc = round(100 * row['_correct_count'] / row['_total_count'], 1)
        print(f"  {row['service_id']:<45} {row['language']}: "
              f"{row['_correct_count']}/{row['_total_count']} ({acc}%)")
    print(f"  written to: {output_xlsx}")

    return summary_rows
""")

# ============================================================================
# Run — edit the path below before pasting into shell_plus
# ============================================================================
run_lang_detect_eval(
    output_xlsx='/Users/kunalpratapsingh/PycharmProjects/saathi-backend/lang_detect_eval.xlsx',
)
