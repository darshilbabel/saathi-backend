from rest_framework import serializers
from chatbot.models.media_models import ProfileMedia
from chatbot.models.profile_models import Profile
from chatbot.models.company_models import CompanyChat, CompanyChatFeedback
from chatbot.models.geo_models import ProfileAddress
from chatbot.serializer.company_serializer import CompanySerializer


class ProfileAddressSerializer(serializers.ModelSerializer):

    class Meta:
        model = ProfileAddress
        fields = '__all__'
        extra_kwargs = {'profile': {'required': False}}


class ProfileMediaSerializer(serializers.ModelSerializer):
    public_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProfileMedia
        fields = '__all__'

    def get_public_url(self, obj):
        return obj.get_public_url()


class ProfileSerializer(serializers.ModelSerializer):
    company = CompanySerializer(read_only=True)
    profile_address = ProfileAddressSerializer(many=True, required=False)
    profile_media = ProfileMediaSerializer(many=True, required=False)

    class Meta:
        model = Profile
        fields = '__all__'
        extra_kwargs = {'password': {'write_only': True, 'required': False}}

    def list(self, request, *args, **kwargs):
        print("GET request received")
        return super().list(request, *args, **kwargs)

    def create(self, validated_data):
        profile_address_data = validated_data.pop('profile_address', None)
        profile_media_data = validated_data.pop('profile_media', None)

        profile = Profile.objects.create(**validated_data)
        if profile_address_data:
            for address_data in profile_address_data:
                ProfileAddress.objects.create(profile=profile, **address_data)

        if profile_media_data:
            for profile_media in profile_media_data:
                ProfileMedia.objects.create(profile=profile, **profile_media)

        return profile

    def update(self, instance, validated_data):
        profile_address_data = validated_data.pop('profile_address', [])
        profile_media_data = validated_data.pop('profile_media', [])

        for field_name, value in validated_data.items():
            setattr(instance, field_name, value)

        # Update or create ProfileAddress instances
        for address_data in profile_address_data:
            profile_address = ProfileAddress.objects.filter(profile=instance)
            if len(profile_address) > 0:
                profile_address = profile_address[0]
                for field_name, value in address_data.items():
                    setattr(profile_address, field_name, value)
                profile_address.save()
            else:
                ProfileAddress.objects.create(profile=instance, **address_data)

        # Update or create ProfileMedia instances
        for media_data in profile_media_data:
            media_instance, _ = ProfileMedia.objects.update_or_create(
                profile=instance, id=media_data.get('id'), defaults=media_data
            )

        instance.save()
        return instance

class CompanyChatSerializer(serializers.ModelSerializer):
    """Note: thumbs_up/thumbs_down reflect only the latest CompanyChatFeedback row for this
    message (comment text and older feedback history are intentionally not exposed here)."""
    sender = ProfileSerializer(read_only=True)
    receiver = ProfileSerializer(read_only=True)
    thumbs_up = serializers.SerializerMethodField()
    thumbs_down = serializers.SerializerMethodField()

    class Meta:
        model = CompanyChat
        fields = '__all__'

    def _latest_feedback(self, obj):
        # list() reuses the prefetch_related('feedbacks') cache set up by the view's
        # queryset, so this does not issue an extra query per row.
        feedbacks = list(obj.feedbacks.all())
        return feedbacks[0] if feedbacks else None

    def get_thumbs_up(self, obj):
        latest = self._latest_feedback(obj)
        return bool(latest.thumbs_up) if latest else False

    def get_thumbs_down(self, obj):
        latest = self._latest_feedback(obj)
        return bool(latest.thumbs_down) if latest else False


class CompanyChatFeedbackSerializer(serializers.ModelSerializer):
    """Creates a new feedback row. Never updates an existing one — every submission
    (including switching thumbs up <-> down) is stored as its own history entry."""

    class Meta:
        model = CompanyChatFeedback
        fields = ['id', 'company_chat', 'thumbs_up', 'thumbs_down', 'comment', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        has_thumbs_key = 'thumbs_up' in self.initial_data or 'thumbs_down' in self.initial_data
        has_comment = bool((attrs.get('comment') or '').strip())

        if not has_thumbs_key and not has_comment:
            raise serializers.ValidationError(
                'At least one of thumbs_up, thumbs_down, or comment is required.'
            )

        # Safety net: if the request carries no thumbs info at all (e.g. a comment-only
        # submission), carry forward the current thumbs state instead of resetting it to
        # False/False. If either key is present, it's an explicit thumbs decision — use it as-is.
        if not has_thumbs_key:
            latest = CompanyChatFeedback.objects.filter(
                company_chat=attrs.get('company_chat')
            ).order_by('-created_at').first()
            if latest:
                attrs['thumbs_up'] = latest.thumbs_up
                attrs['thumbs_down'] = latest.thumbs_down

        if attrs.get('thumbs_up', False) and attrs.get('thumbs_down', False):
            raise serializers.ValidationError('thumbs_up and thumbs_down cannot both be true.')
        return attrs
