from chatbot.views.admin.generic_upload_views import GenericBatchUploadView, GenericBatchTemplateView, \
    GenericBatchImportView
from chatbot.views.aws_views import get_presigned_url
from chatbot.views.profile_views import create_profile_views, read_elevate_profile
from django.urls import path, include
from chatbot.views import api_views
from chatbot.views.bhashini_views import text_speech_view, speech_text, text_translation_view, text_transliterate_view
from chatbot.views.chat_view import save_chats_view, create_chatsession
from chatbot.views.drf_views import CompanyChatListCreateView, CompanyChatRetrieveUpdateDestroyView, \
    CompanyChatFeedbackCreateView, \
    CompanyBotListCreateView, CompanyBotRetrieveUpdateDestroyView, \
    ChatSessionListCreateView, ChatSessionRetrieveUpdateDestroyView, \
    ChatSessionRetrieveUpdateDestroyViewSession, BotVernacularListCreateView, BotVernacularRetrieveUpdateDestroyView, \
    FlowLanguagesView, FlowConnectionInfoView

app_name = "chatbot"


urlpatterns = [
    path('api/get-profile/', api_views.get_profile_view, name='get-profile'),
    path('api/accept-tnc/', api_views.accept_tnc_view, name='accept-tnc'),
    path('api/update-profile/', api_views.update_profile_view, name='update-profile'),
    path('api/logout/', api_views.logout_profile, name='logout-profile'),

    path('api/generate-session/', api_views.generate_session_id, name='generate_session_id'),
    path('api/shikshalokam/read-elevate-profile/', read_elevate_profile, name='read-elevate-profile'),

    path('api/text_to_speech/', text_speech_view, name='text_speech_view'),
    path('api/asr/', speech_text, name='speech_text'),
    path('api/text_translate/', text_translation_view, name='text_translation_view'),
    path('api/text_transliterate/', text_transliterate_view, name='text_transliterate_view'),

    path('api/companychat/', CompanyChatListCreateView.as_view(), name='companychat-list-create'),
    path('api/companychat/<int:pk>/', CompanyChatRetrieveUpdateDestroyView.as_view(),
         name='companychat-retrieve-update-destroy'),
    path('api/companychat-feedback/', CompanyChatFeedbackCreateView.as_view(), name='companychat-feedback-create'),

    path('api/companybot/', CompanyBotListCreateView.as_view(), name='companybot-list-create'),
    path('api/companybot/<int:pk>/', CompanyBotRetrieveUpdateDestroyView.as_view(),
         name='companybot-retrieve-update-destroy'),

    path('api/bot_vernacular/', BotVernacularListCreateView.as_view(), name='bot_vernacular-list-create'),
    path('api/bot_vernacular/<int:pk>/', BotVernacularRetrieveUpdateDestroyView.as_view(),
         name='bot_vernacular-retrieve-update-destroy'),

    path('api/chatsession/', ChatSessionListCreateView.as_view(), name='chatsession-list-create'),
    path('api/chatsession/<int:pk>/', ChatSessionRetrieveUpdateDestroyView.as_view(),
         name='chatsession-retrieve-update-destroy'),
    path('api/chatsession/<str:session>/', ChatSessionRetrieveUpdateDestroyViewSession.as_view(),
         name='chatsession-retrieve-update-destroy'),

    # Flow APIs
    path('api/flow-languages/', FlowLanguagesView.as_view(), name='flow-languages'),
    path('api/flow-connection-info/', FlowConnectionInfoView.as_view(), name='flow-connection-info'),

    path('api/save-company-chat/', save_chats_view, name="save-company-chat"),
    path('api/create-chatsession/', create_chatsession, name="create-chatsession"),
    path('api/create-profile/', create_profile_views, name="create-profile"),
    path("api/get-presigned-url/", get_presigned_url,  name='get-presigned-url'),

    # generic batch upload URLs
    path('admin/<str:app_label>/<str:model_name>/batch-upload/', GenericBatchUploadView.as_view(),
         name='generic_batch_upload'),
    path('admin/<str:app_label>/<str:model_name>/batch-template/', GenericBatchTemplateView.as_view(),
         name='generic_batch_template'),
    path('admin/<str:app_label>/<str:model_name>/batch-import/', GenericBatchImportView.as_view(),
         name='generic_batch_import'),
]