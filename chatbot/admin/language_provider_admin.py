from django import forms
from django.contrib import admin

from chatbot.models import Language, Provider, LanguageProviderConfig
from chatbot.utils.pycountry_utils import get_iso_language_choices


class LanguageAdminForm(forms.ModelForm):
    iso_code = forms.ChoiceField(choices=get_iso_language_choices)

    class Meta:
        model = Language
        fields = '__all__'


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    form = LanguageAdminForm
    list_display = ('name', 'iso_code', 'created_at')
    search_fields = ('name', 'iso_code')
    ordering = ('name',)


class LanguageProviderConfigInline(admin.TabularInline):
    model = LanguageProviderConfig
    extra = 1
    fields = ('language', 'custom_code')


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'created_at')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [LanguageProviderConfigInline]
