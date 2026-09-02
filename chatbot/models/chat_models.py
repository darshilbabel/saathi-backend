from django.db import models
from chatbot.models import Profile, CompanyBot, ChatStatus


class ChatSession(models.Model):
    """
    Represents an active chat session between a user profile and a company bot.
    """

    session = models.CharField(max_length=255, unique=True)
    profile = models.ForeignKey(Profile, on_delete=models.DO_NOTHING, null=True, blank=True)
    company_bot = models.ForeignKey(CompanyBot, on_delete=models.SET_NULL, null=True, blank=True)
    language = models.CharField(
        max_length=1000, default='en',
        help_text="Language code — controlled at the admin form layer via a Language-table-sourced "
                  "dropdown (ChatSessionAdminForm), not by a fixed model-level choice list.",
    )
    language_ref = models.ForeignKey(
        'chatbot.Language', on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name='chat_sessions',
        help_text="Structured language, auto-derived from `language` on save(). Not load-bearing — "
                   "purely a derived convenience field; `language` remains the source of truth.",
    )
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

    def save(self, *args, **kwargs):
        # Deferred import: chatbot/models/__init__.py imports this module before
        # language_provider_models, so a module-level import here would be circular.
        from chatbot.models.language_provider_models import Language
        self.language_ref = Language.objects.filter(iso_code=self.language).first()
        super().save(*args, **kwargs)

    def save_title(self, title):
        if not self.title:
            self.title = title
            self.save(update_fields=['title'])