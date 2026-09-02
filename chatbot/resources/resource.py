from import_export.resources import ModelResource
from import_export.fields import Field
from import_export.widgets import ForeignKeyWidget
from chatbot.models import CompanyChat, Profile, Company, ChatSession
from chatbot.models.geo_models import ProfileAddress


class CompanyChatResource(ModelResource):
    sender_name = Field(attribute='sender__first_name', column_name='Sender Name')
    receiver_name = Field(attribute='receiver__first_name', column_name='Receiver Name')
    sender_phone = Field(attribute='sender__phone', column_name='Sender Phone')
    receiver_phone = Field(attribute='receiver__phone', column_name='Receiver Phone')
    # chat_import_views.py reads this exact column name back on import (falling back to
    # the ChatSession model default - English - when the column is absent), so this must
    # stay in lockstep with that column name.
    chat_session_language = Field(column_name='chat_session_language')

    class Meta:
        model = CompanyChat
        fields = ('message', 'sender_name', 'receiver_name', 'sender_phone', 'receiver_phone',
                  'session', 'feedback', 'chat_session_language')

    def dehydrate_chat_session_language(self, obj):
        # CompanyChat.session is a plain CharField matching ChatSession.session, not an
        # FK, so this can't be a plain `attribute=` lookup. Cached per distinct session
        # value (not per row) since many messages share one session.
        if not hasattr(self, '_session_language_cache'):
            self._session_language_cache = {}
        if obj.session not in self._session_language_cache:
            chat_session = ChatSession.objects.filter(session=obj.session).only('language').first()
            self._session_language_cache[obj.session] = chat_session.language if chat_session else ''
        return self._session_language_cache[obj.session]


class ProfileResource(ModelResource):
    id = Field(attribute='id', column_name='ID')
    company = Field(attribute='company', column_name='company_id', widget=ForeignKeyWidget(Company, 'id'))
    customer_name = Field(attribute='first_name', column_name='Customer Name')
    org_associated = Field(attribute='org_associated', column_name='Organization Associated')
    contact_number = Field(attribute='phone', column_name='Contact Number')
    password = Field(attribute='password', column_name='Password')
    profile_code = Field(attribute='profile_code', column_name='Profile Code')
    email = Field(attribute='email', column_name='Email')
    city = Field(attribute='get_city', column_name='City')
    pin_code = Field(attribute='get_pin_code', column_name='PIN Code')
    state = Field(attribute='get_state', column_name='State')
    discussion_details = Field(attribute='get_discussion_details', column_name='Discussion Details')
    company_spoc = Field(attribute='company_spoc', column_name='Company SPOC')

    class Meta:
        model = Profile
        fields = ('id', 'customer_name', 'org_associated', 'contact_number', 'email', 'city', 'pin_code',
                  'state', 'enquiry_status', 'discussion_details', 'other_parameters',
                  'company_spoc', 'company', 'password', 'profile_code')
        import_id_fields = ('email',)  # Use email as a unique identifier


    def dehydrate_model_name(self, profile):
        if not profile.pk:
            return ''
        return profile.other_params.get('model_name', '') if profile.other_params else ''

    def dehydrate_discussion_details(self, profile):
        if not profile.pk:
            return ''
        return profile.other_params.get('discussion_details', '') if profile.other_params else ''


    def dehydrate_state(self, profile):
        if not profile.pk:
            return ''
        profile_address = ProfileAddress.objects.filter(profile=profile)
        if len(profile_address) > 0 and profile_address[0].state:
            return profile_address[0].state
        else:
            return ''

    def dehydrate_city(self, profile):
        if not profile.pk:
            return ''
        profile_address = ProfileAddress.objects.filter(profile=profile)
        if len(profile_address) > 0 and profile_address[0].city:
            return profile_address[0].city
        else:
            return ''

    def dehydrate_pin_code(self, profile):
        if not profile.pk:
            return ''
        profile_address = ProfileAddress.objects.filter(profile=profile)
        if len(profile_address) > 0 and profile_address[0].pincode:
            return profile_address[0].pincode
        else:
            return ''


    def get_model_name(self, profile):
        return profile.other_params.get('model_name', '') if profile.other_params else ''

    def get_discussion_details(self, profile):
        return profile.other_params.get('discussion_details', '') if profile.other_params else ''

    def get_state(self, obj):
        profile_address = ProfileAddress.objects.filter(profile=obj)
        if len(profile_address) > 0 and profile_address[0].state:
            return profile_address[0].state
        else:
            return ''

    def get_city(self, obj):
        profile_address = ProfileAddress.objects.filter(profile=obj)
        if len(profile_address) > 0 and profile_address[0].city:
            return profile_address[0].city
        else:
            return ''

    def get_pin_code(self, profile):
        profile_address = ProfileAddress.objects.filter(profile=profile)
        if len(profile_address) > 0 and profile_address[0].pincode:
            return profile_address[0].pincode
        else:
            return ''
