from django.db import transaction
from rest_framework import serializers
from chatbot.models.profile_models import Profile
from chatbot.models.company_models import CompanyChat, CompanyChatFeedback
from chatbot.models.geo_models import ProfileAddress
from chatbot.serializer.company_serializer import CompanySerializer


class ProfileAddressSerializer(serializers.ModelSerializer):

    class Meta:
        model = ProfileAddress
        fields = '__all__'
        extra_kwargs = {'profile': {'required': False}}


class ProfileSerializer(serializers.ModelSerializer):
    company = CompanySerializer(read_only=True)
    profile_address = ProfileAddressSerializer(many=True, required=False)

    class Meta:
        model = Profile
        fields = '__all__'
        extra_kwargs = {'password': {'write_only': True, 'required': False}}

    def list(self, request, *args, **kwargs):
        print("GET request received")
        return super().list(request, *args, **kwargs)

    def create(self, validated_data):
        profile_address_data = validated_data.pop('profile_address', None)

        profile = Profile.objects.create(**validated_data)
        if profile_address_data:
            for address_data in profile_address_data:
                ProfileAddress.objects.create(profile=profile, **address_data)

        return profile

    def update(self, instance, validated_data):
        profile_address_data = validated_data.pop('profile_address', [])

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
        # The view's queryset annotates latest_thumbs_up/latest_thumbs_down via a Subquery
        # so the full feedback history is never loaded. Fall back to a direct query for
        # instances not fetched through that queryset (e.g. a freshly created row on POST).
        if hasattr(obj, 'latest_thumbs_up'):
            return obj.latest_thumbs_up, obj.latest_thumbs_down
        latest = obj.feedbacks.order_by('-created_at').first()
        return (latest.thumbs_up, latest.thumbs_down) if latest else (None, None)

    def get_thumbs_up(self, obj):
        thumbs_up, _ = self._latest_feedback(obj)
        return bool(thumbs_up)

    def get_thumbs_down(self, obj):
        _, thumbs_down = self._latest_feedback(obj)
        return bool(thumbs_down)


class CompanyChatFeedbackSerializer(serializers.ModelSerializer):
    """Creates a new feedback row. Never updates an existing one — every submission
    (including switching thumbs up <-> down) is stored as its own history entry."""

    class Meta:
        model = CompanyChatFeedback
        fields = ['id', 'company_chat', 'thumbs_up', 'thumbs_down', 'comment', 'created_at']
        read_only_fields = ['id', 'created_at']

    def to_internal_value(self, data):
        # ModelSerializer silently drops unknown keys by default; reject them instead so
        # the request body is required to strictly match the schema.
        if hasattr(data, 'keys'):
            unknown_fields = set(data.keys()) - set(self.fields.keys())
            if unknown_fields:
                raise serializers.ValidationError(
                    {field: 'This field is not allowed.' for field in unknown_fields}
                )
        return super().to_internal_value(data)

    def validate(self, attrs):
        thumbs_up = attrs.get('thumbs_up', False)
        thumbs_down = attrs.get('thumbs_down', False)
        has_comment = bool((attrs.get('comment') or '').strip())
        has_thumbs_key = 'thumbs_up' in self.initial_data or 'thumbs_down' in self.initial_data

        # Explicit thumbs decisions are validated here; the comment-only carry-forward
        # case is resolved atomically in create() to avoid a read-then-insert race with
        # a concurrent feedback submission for the same company_chat.
        if thumbs_up and thumbs_down:
            raise serializers.ValidationError('thumbs_up and thumbs_down cannot both be true.')

        # An explicit false/false is a valid decision (the user deselected their
        # feedback) — only reject when neither thumbs key nor a comment was sent at
        # all, since that request wouldn't carry forward or record anything.
        if not has_thumbs_key and not has_comment:
            raise serializers.ValidationError(
                'At least one of thumbs_up, thumbs_down must be provided, or a comment must be provided.'
            )
        return attrs

    def create(self, validated_data):
        has_thumbs_key = 'thumbs_up' in self.initial_data or 'thumbs_down' in self.initial_data
        company_chat = validated_data['company_chat']

        with transaction.atomic():
            # Lock the parent row so ALL feedback submissions for this company_chat —
            # explicit thumbs decisions and comment-only carry-forwards alike — serialize
            # here. Without locking on the explicit-thumbs path too, a concurrent
            # comment-only request could still read a stale "latest" and, since it's
            # inserted later, overwrite a newer explicit decision.
            CompanyChat.objects.select_for_update().get(pk=company_chat.pk)

            if not has_thumbs_key:
                latest = CompanyChatFeedback.objects.filter(
                    company_chat=company_chat
                ).order_by('-created_at').first()
                if latest:
                    validated_data['thumbs_up'] = latest.thumbs_up
                    validated_data['thumbs_down'] = latest.thumbs_down

            return super().create(validated_data)
