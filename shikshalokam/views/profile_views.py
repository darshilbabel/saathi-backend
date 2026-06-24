import logging
import os
from rest_framework.decorators import api_view
from rest_framework.response import Response
from chatbot.utils.elevate.profile_utils import handle_elevate_profile

logger = logging.getLogger('django')
ACCESS_TOKEN_COOKIE_KEY = os.getenv('ACCESS_TOKEN_COOKIE_KEY')


@api_view(['GET'])
def read_elevate_profile(request):
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
            'message': 'Unauthorized.'
        }, status=profile_details.get('status_code'))

    if profile_details.get('error') == 'elevate_server_error':
        logger.error('[read_elevate_profile] Elevate server error')
        return Response({
            'status': 'error',
            'message': 'Elevate service unavailable.'
        }, status=profile_details.get('status_code') or 502)

    if not profile_details.get('profileid'):
        logger.error('[read_elevate_profile] no profileid in response')
        return Response({
            'status': 'error',
            'message': 'Failed to fetch or create profile from Elevate.'
        }, status=500)

    logger.info('[read_elevate_profile] profile=%s', profile_details.get('profileid'))
    return Response({
        'status': 'ok',
        'profile_details': {
            'profileid': profile_details.get('profileid'),
            'company': company_slug,
            'has_accepted_tnc': "ONGOING",
            'route': profile_details.get('route'),
            'reroute_url': profile_details.get('reroute_url'),
        }
    }, status=200)
