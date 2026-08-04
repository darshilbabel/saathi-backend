import django_filters
from django.db.models import OuterRef, Subquery
from rest_framework import generics
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework import status
from chatbot.filter.drf_filter import ChatSessionProfileFilter
from chatbot.models import ChatSession, BotVernacular, SessionFlowName, ChatType
from chatbot.models.company_models import CompanyChat, CompanyChatFeedback, CompanyBot, Flow
from chatbot.models.profile_models import Profile
from chatbot.serializer.base_serializer import ChatSessionSerializer
from chatbot.serializer.company_serializer import (
    CompanyBotSerializer, BotVernacularSerializer, ImageConfigurationSerializer,
    FlowLanguagesSerializer, FlowConnectionInfoSerializer
)
from chatbot.serializer.profile_serializer import (
    ProfileSerializer, CompanyChatSerializer, CompanyChatFeedbackSerializer
)


def _with_latest_feedback(queryset):
    """Annotate each row with its latest feedback's thumbs_up/thumbs_down instead of
    prefetching the full (potentially unbounded, append-only) feedback history."""
    latest_feedback = CompanyChatFeedback.objects.filter(
        company_chat=OuterRef('pk')
    ).order_by('-created_at')
    return queryset.annotate(
        latest_thumbs_up=Subquery(latest_feedback.values('thumbs_up')[:1]),
        latest_thumbs_down=Subquery(latest_feedback.values('thumbs_down')[:1]),
    )


class CompanyChatListCreateView(generics.ListCreateAPIView):
    queryset = _with_latest_feedback(CompanyChat.objects.all().order_by('created_at'))
    serializer_class = CompanyChatSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend]
    filterset_fields = ['message', 'sender', 'receiver', 'session', 'status']


class CompanyChatRetrieveUpdateDestroyView(generics.RetrieveUpdateAPIView):
    queryset = _with_latest_feedback(CompanyChat.objects.all())
    serializer_class = CompanyChatSerializer


class CompanyChatFeedbackCreateView(generics.CreateAPIView):
    """Create-only: FE always POSTs a new feedback row, never PATCH/PUT an existing one."""
    queryset = CompanyChatFeedback.objects.all()
    serializer_class = CompanyChatFeedbackSerializer


class CompanyBotListCreateView(generics.ListCreateAPIView):
    queryset = CompanyBot.objects.all()
    serializer_class = CompanyBotSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend]
    filterset_fields = ['name', 'company__name', 'llm_model', 'company__slug', 'route']


class CompanyBotRetrieveUpdateDestroyView(generics.RetrieveUpdateAPIView):
    queryset = CompanyBot.objects.all()
    serializer_class = CompanyBotSerializer


class BotVernacularListCreateView(generics.ListCreateAPIView):
    queryset = BotVernacular.objects.all()
    serializer_class = BotVernacularSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend]
    filterset_fields = ['company_bot', 'language', 'company_bot__route']


class BotVernacularRetrieveUpdateDestroyView(generics.RetrieveUpdateAPIView):
    queryset = BotVernacular.objects.all()
    serializer_class = BotVernacularSerializer


class ProfileListCreateView(generics.ListCreateAPIView):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend]
    filterset_fields = ['first_name', 'email', 'company__name', 'phone', 'company__slug']


class ProfileRetrieveUpdateDestroyView(generics.RetrieveUpdateAPIView):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer


class ChatSessionListCreateView(generics.ListCreateAPIView):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, ChatSessionProfileFilter, OrderingFilter]
    filterset_fields = ['session', 'project_id', 'user_id', 'profile', 'session_type']
    ordering_fields = ['updated_at', 'created_at', 'id']
    ordering = ['-updated_at']


class ChatSessionRetrieveUpdateDestroyView(generics.RetrieveUpdateAPIView):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend]
    filterset_fields = ['session']


class ChatSessionRetrieveUpdateDestroyViewSession(generics.RetrieveUpdateAPIView):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer
    lookup_field = 'session'


class FlowImageConfigView(generics.GenericAPIView):
    """
    API endpoint to get image configuration for a specific flow route.
    Query param: flow_route (required)
    Returns: ImageConfiguration object or 404
    """
    serializer_class = ImageConfigurationSerializer

    def get(self, request, *args, **kwargs):
        flow_route = request.query_params.get('flow_route')
        
        if not flow_route:
            return Response(
                {'error': 'flow_route query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            flow = Flow.objects.select_related('image_config_id').get(
                flow_route=flow_route,
                active=True
            )
            
            if not flow.image_config_id:
                return Response(
                    {'error': 'No image configuration found for this flow'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            serializer = self.get_serializer(flow.image_config_id)
            return Response(serializer.data)
            
        except Flow.DoesNotExist:
            return Response(
                {'error': 'Flow not found or inactive'},
                status=status.HTTP_404_NOT_FOUND
            )


class FlowLanguagesView(generics.GenericAPIView):
    """
    API endpoint to get supported languages for a specific flow route.
    Query param: flow_route (required)
    Returns: List of language codes
    """
    serializer_class = FlowLanguagesSerializer
    
    def get(self, request, *args, **kwargs):
        flow_route = request.query_params.get('flow_route')
        
        if not flow_route:
            return Response(
                {'error': 'flow_route query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            flow = Flow.objects.get(
                flow_route=flow_route,
                active=True
            )
            
            serializer = self.get_serializer(flow)
            return Response(serializer.data)
            
        except Flow.DoesNotExist:
            return Response(
                {'error': 'Flow not found or inactive'},
                status=status.HTTP_404_NOT_FOUND
            )


class FlowConnectionInfoView(generics.GenericAPIView):
    """
    API endpoint to get websocket URL and bot route for a flow.
    Query param: flow_route (required)
    Returns: websocket_url, bot route, isParentFlow flag, children flows, and image configuration
    """
    serializer_class = FlowConnectionInfoSerializer
    
    def get(self, request, *args, **kwargs):
        flow_route = request.query_params.get('flow_route')
        
        if not flow_route:
            return Response(
                {'error': 'flow_route query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            flow = Flow.objects.select_related('bot', 'image_config').prefetch_related('child_flows').get(
                flow_route=flow_route
            )
            
            # Check if flow is active
            if not flow.active:
                return Response(
                    {'error': 'Flow is inactive'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            serializer = self.get_serializer(flow)
            return Response(serializer.data)
            
        except Flow.DoesNotExist:
            return Response(
                {'error': 'Flow not found'},
                status=status.HTTP_404_NOT_FOUND
            )
