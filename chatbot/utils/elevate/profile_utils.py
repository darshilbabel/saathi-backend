import os
import logging
import requests
from chatbot.models import Profile

logger = logging.getLogger('django')
elevate_base_url = os.getenv('ELEVATE_BASE_URL')


def _safe_body(response):
    try:
        return response.text
    except Exception:
        return '<unreadable>'


def fetch_elevate_user(access_token):
    """HTTP-only fetch from Elevate. No DB access."""
    try:
        if not elevate_base_url:
            logger.error('[fetch_elevate_user] ELEVATE_BASE_URL is not configured')
            return {'error': 'elevate_server_error', 'status_code': 502}

        url = f"{elevate_base_url}/user/v1/user/read"
        headers = {'X-auth-token': access_token}
        response = requests.get(url=url, headers=headers, timeout=30)
        logger.info('[fetch_elevate_user] status=%s', response.status_code)

        if response.status_code == 401:
            logger.error('[fetch_elevate_user] unauthorized — token invalid or expired body=%s', _safe_body(response))
            return {'error': 'unauthorized', 'status_code': 401}

        if response.status_code >= 500:
            logger.error('[fetch_elevate_user] Elevate server error status=%s body=%s', response.status_code, _safe_body(response))
            return {'error': 'elevate_server_error', 'status_code': response.status_code}

        response.raise_for_status()

        json_data = response.json()

        if json_data.get('responseCode', '').lower() != 'ok':
            logger.error('[fetch_elevate_user] unexpected responseCode=%s', json_data.get('responseCode'))
            return {}

        user_data = json_data.get('result', {})
        userid = user_data.get('id')

        if not userid:
            logger.error('[fetch_elevate_user] no userid in Elevate response')
            return {}

        language = user_data.get('preferred_language')
        if isinstance(language, dict):
            language = language.get('value', 'en')
        elif not language:
            language = 'en'

        raw_designation = user_data.get('userRole')
        if isinstance(raw_designation, dict):
            designation_value = raw_designation.get('label')
        elif isinstance(raw_designation, str):
            designation_value = raw_designation
        else:
            designation_value = None

        raw_school = user_data.get('userSchool')
        school_name = raw_school.get('label') if isinstance(raw_school, dict) else raw_school

        state = user_data.get('profileState') or {}
        district = user_data.get('userDistrict') or {}

        return {
            'userid': userid,
            'language': language,
            'designation': designation_value,
            'school_name': school_name,
            'district': district.get('label'),
            'state': state.get('label'),
            'has_accepted_tnc': bool(user_data.get('has_accepted_terms_and_conditions', False)),
        }

    except requests.exceptions.HTTPError as e:
        upstream_status = e.response.status_code if e.response is not None else None
        logger.error('[fetch_elevate_user] HTTP error status=%s body=%s', upstream_status, _safe_body(e.response) if e.response is not None else '')
        return {'error': 'elevate_server_error', 'status_code': upstream_status}
    except requests.exceptions.RequestException as e:
        logger.error('[fetch_elevate_user] request failed: %s', e, exc_info=True)
        return {'error': 'elevate_server_error'}
    except Exception as e:
        logger.error('[fetch_elevate_user] unexpected error: %s', e, exc_info=True)

    return {}


def upsert_elevate_profile(user_data):
    """DB-only upsert. Expects the dict returned by fetch_elevate_user."""
    if not user_data or user_data.get('error') or not user_data.get('userid'):
        return user_data or {}

    userid = user_data['userid']
    language = user_data['language']

    profile, created = Profile.objects.update_or_create(
        userid=userid,
        defaults={'source': 'elevate'}
    )
    logger.info('[upsert_elevate_profile] profile %s userid=%s', 'created' if created else 'updated', userid)

    return {
        "profileid": profile.id,
        "has_accepted_tnc": user_data.get('has_accepted_tnc', False),
        "route": language,
        "reroute_url": os.getenv('SSO_REROUTE_URL'),
        "ums_profile": {
            "designation": user_data['designation'],
            "org_associated": user_data['school_name'],
            "district": user_data['district'],
            "state": user_data['state'],
            "preferred_route": language,
        }
    }


def handle_elevate_profile(access_token):
    user_data = fetch_elevate_user(access_token)
    return upsert_elevate_profile(user_data)


def logout_elevate_user(access_token, refresh_token):
    """HTTP-only logout call to Elevate. No DB access."""
    try:
        if not elevate_base_url:
            logger.error('[logout_elevate_user] ELEVATE_BASE_URL is not configured')
            return {'error': 'elevate_server_error', 'status_code': 502}

        url = f"{elevate_base_url}/user/v1/account/logout"
        headers = {'X-auth-token': access_token}
        response = requests.post(url, headers=headers, data={'refresh_token': refresh_token}, timeout=30)
        logger.info('[logout_elevate_user] status=%s', response.status_code)

        if response.status_code == 401:
            logger.error('[logout_elevate_user] unauthorized — token invalid or expired body=%s', _safe_body(response))
            return {'error': 'unauthorized', 'status_code': 401}

        if response.status_code >= 500:
            logger.error('[logout_elevate_user] Elevate server error status=%s body=%s', response.status_code, _safe_body(response))
            return {'error': 'elevate_server_error', 'status_code': response.status_code}

        response.raise_for_status()

        json_data = response.json()

        if json_data.get('responseCode', '').lower() != 'ok':
            logger.error('[logout_elevate_user] unexpected responseCode=%s', json_data.get('responseCode'))
            return {'error': 'elevate_server_error', 'status_code': response.status_code}

        return {'success': True}

    except requests.exceptions.HTTPError as e:
        upstream_status = e.response.status_code if e.response is not None else None
        logger.error('[logout_elevate_user] HTTP error status=%s body=%s', upstream_status, _safe_body(e.response) if e.response is not None else '')
        return {'error': 'elevate_server_error', 'status_code': upstream_status}
    except requests.exceptions.RequestException as e:
        logger.error('[logout_elevate_user] request failed: %s', e, exc_info=True)
        return {'error': 'elevate_server_error'}
    except Exception as e:
        logger.error('[logout_elevate_user] unexpected error: %s', e, exc_info=True)

    return {'error': 'elevate_server_error'}


def update_elevate_profile(access_token, name=None, role=None, school_name=None, district=None, state=None,
                            has_accepted_terms_and_conditions=None):
    try:
        url = f"{elevate_base_url}/user/v1/user/update"
        headers = {'X-auth-token': access_token}
        body = {'about': 'please get hardcode the about'}  # hardcoded for now
        if name:
            body['name'] = name
        if role:
            body['userRole'] = role
        if school_name:
            body['userSchool'] = school_name
        if district:
            body['userDistrict'] = district
        if state:
            body['profileState'] = state
        if has_accepted_terms_and_conditions is not None:
            body['has_accepted_terms_and_conditions'] = has_accepted_terms_and_conditions

        logger.info(f'[update_elevate_profile] sending body={body}')
        response = requests.patch(url, headers=headers, json=body, timeout=30)
        logger.info(f'[update_elevate_profile] status={response.status_code} body={response.text}')
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'[update_elevate_profile] request failed: {e}', exc_info=True)
    except Exception as e:
        logger.error(f'[update_elevate_profile] unexpected error: {e}', exc_info=True)
    return {}
