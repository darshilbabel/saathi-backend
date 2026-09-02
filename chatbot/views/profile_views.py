import logging
import os

from rest_framework.decorators import api_view
from rest_framework.response import Response

from chatbot.utils.elevate.profile_utils import handle_elevate_profile
from chatbot.utils.profile_utils import create_profile_utils

logger = logging.getLogger('django')
ACCESS_TOKEN_COOKIE_KEY = os.getenv('ACCESS_TOKEN_COOKIE_KEY')


@api_view(['GET'])
def read_elevate_profile(request):
    try:
        access_token = request.COOKIES.get(ACCESS_TOKEN_COOKIE_KEY) if ACCESS_TOKEN_COOKIE_KEY else None
        if not access_token:
            access_token = request.headers.get('X-auth-token')

        company_slug = os.getenv('DEFAULT_COMPANY_SLUG')
        if not company_slug:
            logger.error('[read_elevate_profile] DEFAULT_COMPANY_SLUG is not set')
            return Response({'status': 'error', 'message': 'Server misconfiguration.'}, status=500)

        profile_details = handle_elevate_profile(access_token=access_token)

        if profile_details.get('error') == 'unauthorized':
            logger.error('[read_elevate_profile] Elevate auth failure')
            return Response({
                'status': 'error',
                'message': profile_details.get('message') or 'Unauthorized.',
                'errors': profile_details.get('errors'),
            }, status=profile_details.get('status_code'))

        if profile_details.get('error') == 'elevate_server_error':
            logger.error('[read_elevate_profile] Elevate server error')
            return Response({
                'status': 'error',
                'message': profile_details.get('message') or 'Elevate service unavailable.',
                'errors': profile_details.get('errors'),
            }, status=profile_details.get('status_code') or 502)

        if profile_details.get('error') == 'internal_error':
            logger.error('[read_elevate_profile] internal error while upserting profile')
            return Response({
                'status': 'error',
                'message': 'Internal server error.'
            }, status=profile_details.get('status_code') or 500)

        if not profile_details.get('profileid'):
            logger.error('[read_elevate_profile] no profileid in response')
            return Response({
                'status': 'error',
                'message': 'Failed to fetch or create profile from Elevate.'
            }, status=500)

        ums_profile = profile_details.get('ums_profile') or {}

        logger.info('[read_elevate_profile] profile=%s', profile_details.get('profileid'))
        return Response({
            'status': 'ok',
            'profile_details': {
                'profileid': profile_details.get('profileid'),
                'company': company_slug,
                'has_accepted_tnc': profile_details.get('has_accepted_tnc', False),
                'route': profile_details.get('route'),
                'reroute_url': profile_details.get('reroute_url'),
                'name': profile_details.get('name'),
                'role': ums_profile.get('designation'),
                'school_name': ums_profile.get('org_associated'),
                'district': ums_profile.get('district'),
                'state': ums_profile.get('state'),
            }
        }, status=200)

    except Exception:
        logger.error('[read_elevate_profile] unexpected error', exc_info=True)
        return Response({
            'status': 'error',
            'message': 'Internal server error.'
        }, status=500)


@api_view(['POST'])
def create_profile_views(request):
    body = request.data
    access_token = body.get('access_token')
    print("Access token: ", access_token)

    if not access_token:
        return Response({
            'status': 'error',
            'message': 'Access token is required.'
        }, status=400)

    profile_details = create_profile_utils(access_token=access_token)

    if not profile_details.get('success'):
        return Response({
            'status': 'error',
            'message': profile_details.get('message', 'Failed to fetch or create profile details.')
        }, status=profile_details.get('status_code', 500))

    return Response({
        'status': 'ok',
        'profile_details': profile_details.get('data')
    }, status=200)

