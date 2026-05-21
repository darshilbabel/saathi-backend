import logging
import os

import requests

logger = logging.getLogger('django')

_BASE_URL = os.getenv('LLM_GATEWAY_BASE_URL', 'http://localhost:8000')
_API_KEY = os.getenv('LLM_GATEWAY_API_KEY', '')
_TENANT_ID = os.getenv('LLM_GATEWAY_TENANT_ID', '')


def build_gateway_params(company_bot) -> dict:
    params = {}
    if company_bot.max_token is not None:
        params['max_tokens'] = company_bot.max_token
    if company_bot.bot_temperature is not None:
        params['temperature'] = company_bot.bot_temperature
    if company_bot.connect_timeout is not None:
        params['connect_timeout'] = company_bot.connect_timeout
    if company_bot.read_timeout is not None:
        params['read_timeout'] = company_bot.read_timeout
    other = company_bot.other_params or {}
    if other.get('stop') is not None:
        params['stop'] = other['stop']
    if other.get('seed') is not None:
        params['seed'] = other['seed']
    return params


def call_llm_gateway(
    messages: list, provider: str, model: str, params: dict = None, tools: list = None,
    tool_choice=None,
) -> dict | None:
    """
    POST to /v1/chat/ on the LLM gateway service.
    """
    url = f"{_BASE_URL.rstrip('/')}/v1/chat/"
    headers = {
        'Authorization': f'Bearer {_API_KEY}',
        'X-Tenant-Id': _TENANT_ID,
        'Content-Type': 'application/json',
    }

    payload = {
        'provider': provider,
        'model': model,
        'messages': messages,
        'params': params or {},
    }

    if tools is not None:
        payload['tools'] = tools

    if tool_choice is not None:
        payload['tool_choice'] = tool_choice

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        logger.error('LLM gateway request timed out for model %s', model)
    except requests.exceptions.HTTPError as e:
        logger.error('LLM gateway HTTP error %s: %s', e.response.status_code, e.response.text)
    except requests.exceptions.RequestException as e:
        logger.error('LLM gateway request failed: %s', e)
    except Exception as e:
        logger.error('Unexpected error calling LLM gateway: %s', e, exc_info=True)

    return None


def call_llm_gateway_stream(
    messages: list, provider: str, model: str, params: dict = None, tools: list = None,
    tool_choice=None, cache_policy: str = None, metadata: dict = None,
):
    """
    POST to /v1/chat/stream and yield (delta_content, delta_tool_calls, finish_reason) tuples via SSE.
    """
    import json as _json

    url = f"{_BASE_URL.rstrip('/')}/v1/chat/stream"
    headers = {
        'Authorization': f'Bearer {_API_KEY}',
        'X-Tenant-Id': _TENANT_ID,
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
    }

    payload = {
        'provider': provider,
        'model': model,
        'messages': messages,
        'params': params or {},
    }

    if tools is not None:
        payload['tools'] = tools
    if tool_choice is not None:
        payload['tool_choice'] = tool_choice
    if cache_policy is not None:
        payload['cache_policy'] = cache_policy
    if metadata is not None:
        payload['metadata'] = metadata

    try:
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=120) as response:
            response.raise_for_status()
            current_event = None
            for raw_line in response.iter_lines():
                line = raw_line.decode('utf-8') if isinstance(raw_line, bytes) else raw_line
                if not line:
                    current_event = None
                    continue
                if line.startswith('event:'):
                    current_event = line[len('event:'):].strip()
                    continue
                if not line.startswith('data:'):
                    continue
                data_str = line[len('data:'):].strip()
                try:
                    chunk = _json.loads(data_str)
                except _json.JSONDecodeError:
                    continue
                if current_event == 'token':
                    yield chunk.get('delta') or '', None, None
                elif current_event == 'tool_use':
                    yield '', chunk, None
                elif current_event == 'finish':
                    yield '', None, chunk.get('finish_reason')

    except requests.exceptions.Timeout:
        logger.error('LLM gateway stream request timed out for model %s', model)
    except requests.exceptions.HTTPError as e:
        logger.error('LLM gateway stream HTTP error %s: %s', e.response.status_code, e.response.text)
    except requests.exceptions.RequestException as e:
        logger.error('LLM gateway stream request failed: %s', e)
    except Exception as e:
        logger.error('Unexpected error in LLM gateway stream: %s', e, exc_info=True)