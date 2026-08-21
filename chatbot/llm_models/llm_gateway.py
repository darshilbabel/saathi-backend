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
    provider_options = other.get('provider_options')
    if provider_options is None and company_bot.gateway_provider == 'openrouter' and company_bot.gateway_sub_provider:
        # No explicit provider_options override — derive one from gateway_sub_provider so
        # openrouter routes to the endpoint picked in the admin (e.g. a specific region/tag).
        provider_options = {
            'provider': {
                'only': [company_bot.gateway_sub_provider],
                'allow_fallbacks': False,
            }
        }
    if provider_options is not None:
        params['provider_options'] = provider_options
    if getattr(company_bot, 'enable_web_search', False):
        params['web_search_options'] = {
            'search_context_size': company_bot.web_search_context_size or 'medium'
        }
    if getattr(company_bot, 'enable_cache', False):
        params['cache_options'] = {
            'enabled': True,
            'ttl': company_bot.cache_ttl,
            'targets': company_bot.cache_targets,
        }
    return params


def get_effective_provider_model(company_bot) -> tuple:
    """
    Resolve the (provider, model) pair to use for a gateway call, sourced from
    gateway_provider/gateway_model — no fallback to the legacy provider/llm_model
    fields. other_params.custom_model, when present, overrides gateway_model (e.g.
    for a model ID not surfaced by the gateway's catalog for the selected provider).
    """
    custom_model = (company_bot.other_params or {}).get('custom_model')
    model = custom_model.strip() if isinstance(custom_model, str) and custom_model.strip() else company_bot.gateway_model
    return company_bot.gateway_provider, model


def get_provider_list() -> list | None:
    """
    GET /v1/providers on the LLM gateway service. Returns a list of
    {'name': ..., 'source': ...} dicts, or None on failure.
    """
    url = f"{_BASE_URL.rstrip('/')}/v1/providers"
    headers = {
        'Authorization': f'Bearer {_API_KEY}',
        'X-Tenant-Id': _TENANT_ID,
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get('data')
    except requests.exceptions.Timeout:
        logger.error('LLM gateway provider list request timed out')
    except requests.exceptions.HTTPError as e:
        logger.error('LLM gateway provider list HTTP error %s: %s', e.response.status_code, e.response.text)
    except requests.exceptions.RequestException as e:
        logger.error('LLM gateway provider list request failed: %s', e)
    except Exception as e:
        logger.error('Unexpected error fetching LLM gateway provider list: %s', e, exc_info=True)

    return None


def get_model_list(provider: str) -> list | None:
    """
    GET /v1/models on the LLM gateway service for the given provider. Returns a
    list of model dicts (with at least 'id' and 'name' keys), or None on failure.
    """
    url = f"{_BASE_URL.rstrip('/')}/v1/models"
    headers = {
        'Authorization': f'Bearer {_API_KEY}',
        'X-Tenant-Id': _TENANT_ID,
    }
    query_params = {'provider': provider}

    print('[get_model_list] GET', url, 'params:', query_params)
    try:
        response = requests.get(url, headers=headers, params=query_params, timeout=10)
        print('[get_model_list] response status:', response.status_code)
        response.raise_for_status()
        data = response.json().get('data')
        print('[get_model_list] parsed', len(data) if data is not None else 0, 'models')
        return data
    except requests.exceptions.Timeout:
        print('[get_model_list] TIMEOUT for provider', provider)
        logger.error('LLM gateway model list request timed out for provider %s', provider)
    except requests.exceptions.HTTPError as e:
        print('[get_model_list] HTTP ERROR', e.response.status_code, e.response.text)
        logger.error('LLM gateway model list HTTP error %s: %s', e.response.status_code, e.response.text)
    except requests.exceptions.RequestException as e:
        print('[get_model_list] REQUEST EXCEPTION', e)
        logger.error('LLM gateway model list request failed: %s', e)
    except Exception as e:
        print('[get_model_list] UNEXPECTED ERROR', e)
        logger.error('Unexpected error fetching LLM gateway model list: %s', e, exc_info=True)

    return None


def get_openrouter_endpoints(model: str) -> list | None:
    """
    GET /v1/models/endpoints on the LLM gateway service for a given openrouter model.
    Returns the list of endpoint dicts (each with at least 'provider_name' and 'tag'
    keys), or None on failure.
    """
    url = f"{_BASE_URL.rstrip('/')}/v1/models/endpoints"
    headers = {
        'Authorization': f'Bearer {_API_KEY}',
        'X-Tenant-Id': _TENANT_ID,
    }
    query_params = {'provider': 'openrouter', 'model': model}

    try:
        response = requests.get(url, headers=headers, params=query_params, timeout=10)
        response.raise_for_status()
        data = response.json().get('data') or {}
        return data.get('endpoints')
    except requests.exceptions.Timeout:
        logger.error('LLM gateway openrouter endpoints request timed out for model %s', model)
    except requests.exceptions.HTTPError as e:
        logger.error('LLM gateway openrouter endpoints HTTP error %s: %s', e.response.status_code, e.response.text)
    except requests.exceptions.RequestException as e:
        logger.error('LLM gateway openrouter endpoints request failed: %s', e)
    except Exception as e:
        logger.error('Unexpected error fetching LLM gateway openrouter endpoints: %s', e, exc_info=True)

    return None


def get_cache_options() -> dict | None:
    """
    GET /v1/cache/options on the LLM gateway service. Returns a dict with
    'ttl_values', 'target_values', and 'providers' keys, or None on failure.
    """
    url = f"{_BASE_URL.rstrip('/')}/v1/cache/options"
    headers = {
        'Authorization': f'Bearer {_API_KEY}',
        'X-Tenant-Id': _TENANT_ID,
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get('data')
    except requests.exceptions.Timeout:
        logger.error('LLM gateway cache options request timed out')
    except requests.exceptions.HTTPError as e:
        logger.error('LLM gateway cache options HTTP error %s: %s', e.response.status_code, e.response.text)
    except requests.exceptions.RequestException as e:
        logger.error('LLM gateway cache options request failed: %s', e)
    except Exception as e:
        logger.error('Unexpected error fetching LLM gateway cache options: %s', e, exc_info=True)

    return None


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

    params = dict(params or {})
    provider_options = params.pop('provider_options', None)

    payload = {
        'provider': provider,
        'model': model,
        'messages': messages,
        'params': params,
    }

    if provider_options is not None:
        payload['provider_options'] = provider_options

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

    params = dict(params or {})
    provider_options = params.pop('provider_options', None)

    payload = {
        'provider': provider,
        'model': model,
        'messages': messages,
        'params': params,
    }

    if provider_options is not None:
        payload['provider_options'] = provider_options

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
            collected_citations = []
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
                    yield chunk.get('delta') or '', None, None, None, None
                elif current_event == 'tool_use':
                    yield '', chunk, None, None, None
                elif current_event == 'citation':
                    collected_citations.append(chunk)
                elif current_event == 'finish':
                    yield '', None, chunk.get('finish_reason'), collected_citations or None, chunk

    except requests.exceptions.Timeout:
        logger.error('LLM gateway stream request timed out for model %s', model)
    except requests.exceptions.HTTPError as e:
        logger.error('LLM gateway stream HTTP error %s: %s', e.response.status_code, e.response.text)
    except requests.exceptions.RequestException as e:
        logger.error('LLM gateway stream request failed: %s', e)
    except Exception as e:
        logger.error('Unexpected error in LLM gateway stream: %s', e, exc_info=True)