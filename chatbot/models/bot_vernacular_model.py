from django.db import models
from simple_history.models import HistoricalRecords
from chatbot.models import CompanyBot


class BotVernacular(models.Model):
    """
    Stores language-specific (vernacular) configurations for a company bot.
    Allows customized introductory and error messages per language.
    """

    company_bot = models.ForeignKey(CompanyBot, on_delete=models.SET_NULL, related_name='bot_vernacular', null=True)

    language = models.CharField(max_length=250, help_text="Language code, Example for English use en.")
    language_ref = models.ForeignKey(
        'chatbot.Language', on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name='bot_vernaculars',
        help_text="Structured language, auto-derived from `language` on save(). Not load-bearing — "
                   "purely a derived convenience field; `language` remains the source of truth.",
    )
    introductory_message = models.TextField(
        null=True, blank=True, help_text="Provide an introductory message that the bot will present when the "
                                         "conversation starts."
    )
    alt_introductory_message = models.TextField(
        null=True, blank=True, help_text="Provide an alternate introductory message that the bot will present when the "
                                         "conversation starts."
    )
    name = models.CharField(max_length=100,null=True, blank=True, help_text="Enter the name of the bot.")
    error_message = models.TextField(
        null=True, blank=True, help_text="Provide an error message that the bot will display."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        # Deferred import: chatbot/models/__init__.py imports this module before
        # language_provider_models, so a module-level import here would be circular.
        from chatbot.models.language_provider_models import Language
        self.language_ref = Language.objects.filter(iso_code=self.language).first()
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'shikshalokam"."bot_vernacular'
        indexes = [
            models.Index(fields=['language']),
            models.Index(fields=['created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['company_bot', 'language'],
                name='uniq_bot_vernacular_bot_language',
            ),
        ]
