from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from simple_history.models import HistoricalRecords

from chatbot.utils.pycountry_utils import resolve_iso_language


class Language(models.Model):
    iso_code = models.CharField(
        max_length=10, unique=True,
        help_text="ISO 639 code, selected from the pycountry library dropdown (admin-form validated)."
    )
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['iso_code'])]

    def __str__(self):
        return f"{self.name} ({self.iso_code})"

    def clean(self):
        super().clean()
        if self.iso_code and not resolve_iso_language(self.iso_code):
            raise ValidationError(f"{self.iso_code!r} is not a known ISO 639 language code.")


class Provider(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(
        max_length=100, unique=True, blank=True,
        help_text="Auto-generated from name. Must match a key in chatbot/constants/provider_dispatch.py "
                   "for TTS/STT/translate calls to actually work for this provider."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class LanguageProviderConfig(models.Model):
    language = models.ForeignKey(Language, on_delete=models.CASCADE, related_name='provider_configs')
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name='language_configs')
    custom_code = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Overrides the language's iso_code for this provider's outbound API calls. "
                   "Leave blank to use iso_code as-is."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        # At most one config per (language, provider) — Voice has no FK of its own to a
        # specific config row; the override is looked up live by (language_ref, provider_ref)
        # in get_voice_provider(), so a pair must resolve to a single unambiguous row.
        constraints = [
            models.UniqueConstraint(fields=['language', 'provider'], name='unique_language_provider'),
        ]

    def __str__(self):
        label = f"{self.language.iso_code} / {self.provider.slug}"
        return f"{label} -> {self.custom_code}" if self.custom_code else label
