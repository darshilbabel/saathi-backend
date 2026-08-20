import logging
import os
import traceback
from django.contrib.auth.hashers import check_password
from rest_framework.response import Response
from rest_framework.decorators import api_view, authentication_classes
from chatbot.models import ProfileType
from chatbot.models.geo_models import ProfileAddress
from chatbot.models.auth_models import BlacklistedToken
from chatbot.models.company_models import Company, CompanyBot
from chatbot.models.profile_models import Profile
from chatbot.serializer.profile_serializer import ProfileSerializer
from chatbot.utils.elevate.profile_utils import fetch_elevate_user, update_elevate_profile, logout_elevate_user
from django.http import JsonResponse
from django.contrib.sessions.backends.db import SessionStore
from rest_framework_simplejwt.tokens import RefreshToken
from chatbot.translate.ai4Bharat.transliterate import call_ai4bharat_transliterate_api
from chatbot.models.company_models import Flow

logger = logging.getLogger('django')
ACCESS_TOKEN_COOKIE_KEY = os.getenv('ACCESS_TOKEN_COOKIE_KEY')
REFRESH_TOKEN_COOKIE_KEY = os.getenv('REFRESH_TOKEN_COOKIE_KEY')


def _get_access_token(request):
    access_token = request.COOKIES.get(ACCESS_TOKEN_COOKIE_KEY) if ACCESS_TOKEN_COOKIE_KEY else None
    if not access_token:
        access_token = request.headers.get('X-auth-token')
    return access_token


def generate_session_id(request):
    try:
        session = SessionStore()
        session.create()
        return JsonResponse({'sessionid': session.session_key})
    except Exception as e:
        print('Exception is here')
        print(e)
        traceback.print_exc()


@api_view(['POST'])
def post_profile(request):
    try:
        data = request.data
        if not ('email' in data and 'company' in data):
            return Response({
               'status': 'error',
               'message': 'email and subdomain/company are mandatory'
            }, status=400)
        company_slug = data.get('company')
        company = Company.objects.get(slug=company_slug)

        email = data['email']
        first_name = data.get('first_name', '')
        target_language = data.get('preferred_route', None)
        if first_name and first_name != '' and target_language:
            source_language='en'
            first_names = call_ai4bharat_transliterate_api(
                source_language=source_language, target_language=target_language, message_body=first_name
            )
            if first_names and isinstance(first_names, dict):
                first_names = first_names.get('content', [])
            if first_names and isinstance(first_names, list) and len(first_names) > 0:
                data['first_name'] = first_names[0]

        # handling the latest flow
        flow_route = data.get('latest_flow', None)

        if flow_route:
            flow = Flow.objects.values('id').get(flow_route=flow_route)
            data['latest_flow'] = flow.get('id')

        profile = Profile.objects.filter(email=email, company=company).first()
        if profile:
            serializer = ProfileSerializer(profile, data=data)
        else:
            phone = data.get('phone', None)
            if phone:
                profile = Profile.objects.filter(phone=phone, company=company)
                if len(profile) > 0:
                    serializer = ProfileSerializer(profile[0])
                    return Response(serializer.data)
            serializer = ProfileSerializer(data=data)
        if serializer.is_valid():
            serializer.save(company=company)
            return Response(serializer.data)
        else:
            return Response({
                'status': 'error',
                'message': 'Invalid data',
                'errors': serializer.errors
            }, status=400)

    except Company.DoesNotExist:
        return Response({
            'status': 'error',
            'message': 'Company does not exist'
        }, status=404)

    except Flow.DoesNotExist:
        return Response({
            'status': 'error',
            'message': 'Flow does not exist'
        }, status=404)

    except Exception as e:
        traceback.print_exc()
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)


@api_view(['POST'])
def login(request):
    try:
        if 'email' in request.data and 'password' in request.data:
            email = request.data['email']
            password = request.data['password']
            print("Login User Email: ", email)
            print("Login User Password: ", password)

            p = Profile.objects.filter(email=email)
            if len(p) > 0:
                p = p[0]
                if check_password(password, p.password):
                    profile_address = ProfileAddress.objects.filter(profile=p)
                    if len(profile_address) > 0:
                        state = profile_address[0].state
                    else:
                        state = ''
                    token = RefreshToken.for_user(p)
                    access_token = str(token.access_token)
                    request.session['is_authenticated'] = True
                    request.session['profileid'] = p.id
                    return Response({
                        'status': 'ok',
                        'id': p.id,
                        'first_name': p.first_name,
                        'email': p.email,
                        'access_token': access_token,
                        'company': p.company.slug,
                        'state': state
                    }, status=200)
                else:
                    logger.error('Password incorrect')
                    return Response({
                        'status': 'error',
                        'message': 'Password is incorrect'
                    }, status=401)
            else:
                logger.error('Profile does not exist')
                return Response({
                    'status': 'error',
                    'message': 'Profile does not exist'
                }, status=400)
        else:
            logger.error('Email and Password are mandatory')
            return Response({
                'status': 'error',
                'message': 'Email and Password are mandatory'
            }, status=400)
    except Exception as e:
        traceback.print_exc()
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)


@api_view(['POST'])
def logout(request):
    try:
        # Blacklist the token
        token = request.headers.get('Authorization', '').split(' ')[1]
        if token:
            BlacklistedToken.objects.create(token=token)

        # Clear the session data to log the user out
        request.session.clear()

        response = Response({
            'status': 'ok',
            'message': 'Logout successful'
        }, status=200)

        response.delete_cookie('sessionid')
        return response
    except Exception as e:
        traceback.print_exc()
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)


@api_view(['GET'])
def get_profile_view(request):
    try:
        profile_id = request.query_params.get('profile_id')
        email = request.query_params.get('email')
        company_slug = request.query_params.get('company_slug')

        if not profile_id and not email:
            return Response({
                'status': 'error',
                'message': 'profile_id or email is required'
            }, status=400)

        if profile_id:
            profile = Profile.objects.get(pk=profile_id)
        else:
            if company_slug:
                company = Company.objects.get(slug=company_slug)
            else:
                company = Company.objects.order_by('id').first()
                if not company:
                    return Response({
                        'status': 'error',
                        'message': 'No company found'
                    }, status=404)
            profile = Profile.objects.get(email=email, company=company)

        access_token = _get_access_token(request)
        elevate_user_data = fetch_elevate_user(access_token)
        if elevate_user_data.get('error') == 'unauthorized':
            logger.error('[get_profile_view] Elevate auth failure')
            return Response({
                'status': 'error',
                'message': 'Unauthorized.'
            }, status=elevate_user_data.get('status_code'))

        if elevate_user_data.get('error') == 'elevate_server_error':
            logger.error('[get_profile_view] Elevate server error')
            return Response({
                'status': 'error',
                'message': 'Elevate service unavailable.'
            }, status=elevate_user_data.get('status_code') or 502)

        is_tnc_accepted = elevate_user_data.get('has_accepted_tnc', False)

        is_onboarding_completed = bool(
            profile.other_params and profile.other_params.get('is_onboarding_completed', False)
        )

        return Response({
            'id': profile.id,
            'is_tnc_accepted': is_tnc_accepted,
            'is_profile_complete': is_onboarding_completed,
        }, status=200)

    except Profile.DoesNotExist:
        return Response({
            'status': 'error',
            'message': 'Profile not found'
        }, status=404)

    except Company.DoesNotExist:
        return Response({
            'status': 'error',
            'message': 'Company not found'
        }, status=404)

    except Exception as e:
        traceback.print_exc()
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)


@api_view(['PATCH'])
def accept_tnc_view(request):
    try:
        profile_id = request.data.get('profile_id')
        email = request.data.get('email')
        company_slug = request.data.get('company_slug')

        if not profile_id and not email:
            return Response({
                'status': 'error',
                'message': 'profile_id or email is required'
            }, status=400)

        if profile_id:
            profile = Profile.objects.get(pk=profile_id)
        else:
            if company_slug:
                company = Company.objects.get(slug=company_slug)
            else:
                company = Company.objects.order_by('id').first()
                if not company:
                    return Response({
                        'status': 'error',
                        'message': 'No company found'
                    }, status=404)
            profile = Profile.objects.get(email=email, company=company)

        access_token = _get_access_token(request)
        result = update_elevate_profile(access_token, has_accepted_terms_and_conditions=True)

        if result.get('error') == 'unauthorized':
            logger.error('[accept_tnc_view] Elevate auth failure')
            return Response({
                'status': 'error',
                'message': 'Unauthorized.'
            }, status=result.get('status_code'))

        if result.get('error') == 'elevate_client_error':
            logger.error('[accept_tnc_view] Elevate client error message=%s', result.get('message'))
            return Response({
                'status': 'error',
                'message': result.get('message'),
                'errors': result.get('errors'),
            }, status=result.get('status_code') or 422)

        if result.get('error') == 'elevate_server_error':
            logger.error('[accept_tnc_view] Elevate server error')
            return Response({
                'status': 'error',
                'message': 'Elevate service unavailable.'
            }, status=result.get('status_code') or 502)

        return Response({
            'status': 'ok',
            'is_tnc_accepted': True,
        }, status=200)

    except Profile.DoesNotExist:
        return Response({
            'status': 'error',
            'message': 'Profile not found'
        }, status=404)

    except Company.DoesNotExist:
        return Response({
            'status': 'error',
            'message': 'Company not found'
        }, status=404)

    except Exception as e:
        traceback.print_exc()
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)


@api_view(['PATCH'])
def update_profile_view(request):
    try:
        update_fields = {}
        for field in ('name', 'role', 'school_name', 'district', 'state'):
            if field in request.data:
                update_fields[field] = request.data.get(field)

        access_token = _get_access_token(request)
        result = update_elevate_profile(access_token, **update_fields)

        if result.get('error') == 'unauthorized':
            logger.error('[update_profile_view] Elevate auth failure')
            return Response({
                'status': 'error',
                'message': 'Unauthorized.'
            }, status=result.get('status_code'))

        if result.get('error') == 'elevate_client_error':
            logger.error('[update_profile_view] Elevate client error message=%s', result.get('message'))
            return Response({
                'status': 'error',
                'message': result.get('message'),
                'errors': result.get('errors'),
            }, status=result.get('status_code') or 422)

        if result.get('error') == 'elevate_server_error':
            logger.error('[update_profile_view] Elevate server error')
            return Response({
                'status': 'error',
                'message': 'Elevate service unavailable.'
            }, status=result.get('status_code') or 502)

        logger.info('[update_profile_view] updated fields=%s', list(update_fields))
        return Response({
            'status': 'ok',
            'updated_fields': list(update_fields),
        }, status=200)

    except Exception:
        logger.error('[update_profile_view] unexpected error', exc_info=True)
        return Response({
            'status': 'error',
            'message': 'Internal server error.'
        }, status=500)


@api_view(['POST'])
def logout_profile(request):
    access_token = request.COOKIES.get(ACCESS_TOKEN_COOKIE_KEY) if ACCESS_TOKEN_COOKIE_KEY else None
    if not access_token:
        access_token = request.headers.get('X-auth-token')

    refresh_token = request.COOKIES.get(REFRESH_TOKEN_COOKIE_KEY) if REFRESH_TOKEN_COOKIE_KEY else None
    if not refresh_token:
        refresh_token = request.headers.get('X-refresh-token')

    logout_result = logout_elevate_user(access_token=access_token, refresh_token=refresh_token)

    if logout_result.get('error') == 'unauthorized':
        logger.error('[logout_elevate_profile] Elevate auth failure')
        return Response({
            'status': 'error',
            'message': 'Unauthorized.'
        }, status=logout_result.get('status_code'))

    if logout_result.get('error') == 'elevate_server_error':
        logger.error('[logout_elevate_profile] Elevate server error')
        return Response({
            'status': 'error',
            'message': 'Elevate service unavailable.'
        }, status=logout_result.get('status_code') or 502)

    logger.info('[logout_elevate_profile] logout successful')
    response = Response({'status': 'ok'}, status=200)
    if ACCESS_TOKEN_COOKIE_KEY:
        response.delete_cookie(ACCESS_TOKEN_COOKIE_KEY)
    if REFRESH_TOKEN_COOKIE_KEY:
        response.delete_cookie(REFRESH_TOKEN_COOKIE_KEY)
    return response
