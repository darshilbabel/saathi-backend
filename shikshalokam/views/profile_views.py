import os
import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response
from chatbot.utils.elevate.profile_utils import handle_elevate_profile

ACCESS_TOKEN_COOKIE_KEY = os.getenv('ACCESS_TOKEN_COOKIE_KEY')


@api_view(['GET'])
def read_elevate_profile(request):
    access_token = request.COOKIES.get(ACCESS_TOKEN_COOKIE_KEY) if ACCESS_TOKEN_COOKIE_KEY else None
    if not access_token:
        access_token = request.headers.get('X-auth-token')
    print("Access token: ", access_token)

    try:
        profile_details = handle_elevate_profile(access_token=access_token)
    except requests.exceptions.HTTPError as e:
        upstream_status = e.response.status_code if e.response is not None else 502
        try:
            upstream_body = e.response.json()
        except Exception:
            upstream_body = {'status': 'error', 'message': str(e)}
        return Response(upstream_body, status=upstream_status)

    if not profile_details or not profile_details.get('profileid'):
        return Response({
            'status': 'error',
            'message': 'Failed to fetch or create profile from Elevate.'
        }, status=500)

    return Response({
        'status': 'ok',
        'profile_details': profile_details
    }, status=200)