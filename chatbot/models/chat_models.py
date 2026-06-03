from django.db import models
from chatbot.models import Profile, CompanyBot, ChatStatus, StoryLanguageChoices


class ChatSession(models.Model):
    """
    Represents an active chat session between a user profile and a company bot.
    """

    session = models.CharField(max_length=255, unique=True)
    profile = models.ForeignKey(Profile, on_delete=models.DO_NOTHING, null=True, blank=True)
    company_bot = models.ForeignKey(CompanyBot, on_delete=models.SET_NULL, null=True, blank=True)
    language = models.CharField(max_length=1000, choices=StoryLanguageChoices.choices,
                                default=StoryLanguageChoices.ENGLISH)
    title = models.CharField(max_length=255, null=True, blank=True)
    summary = models.TextField(null=True, blank=True)
    current_step = models.IntegerField(null=True, blank=True)
    session_context = models.JSONField(null=True, blank=True)
    session_status = models.CharField(max_length=20, choices=ChatStatus.choices, null=True, blank=True)
    project_id = models.CharField(max_length=400, null=True, blank=True)
    user_id = models.CharField(max_length=400, null=True, blank=True)
    session_type = models.CharField(max_length=255, null=True, blank=True)
    other_params = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save_title(self, title):
        if not self.title:
            self.title = title
            self.save(update_fields=['title'])