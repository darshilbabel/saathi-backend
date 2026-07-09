from rest_framework import serializers
from chatbot.models import ChatSession
from chatbot.models.profile_models import Profile
from chatbot.models.company_models import Voice, CompanyChat


class VoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Voice
        fields = '__all__'


class ChatSessionSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(write_only=True)
    company_slug = serializers.CharField(write_only=True)

    class Meta:
        model = ChatSession
        fields = '__all__'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not instance.title:
            fallback = getattr(instance, 'first_user_message', None)
            if fallback is None:
                fallback = (
                    CompanyChat.objects
                    .filter(session=instance.session)
                    .exclude(sender_id=1)
                    .order_by('created_at')
                    .values_list('message', flat=True)
                    .first()
                )
            data['title'] = fallback
        return data

    def create(self, validated_data):
        phone = validated_data.pop('phone', None)
        company_slug = validated_data.pop('company_slug', None)
        if phone and company_slug:
            try:
                profile = Profile.objects.get(phone=phone, company__slug=company_slug)
            except Profile.DoesNotExist:
                raise serializers.ValidationError("Profile does not exist.")
            validated_data['profile'] = profile
        return super().create(validated_data)
