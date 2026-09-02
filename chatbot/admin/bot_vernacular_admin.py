from django import forms
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from chatbot.filter.custom_date_from_filter import CustomAdvanceDateFilter
from chatbot.models import BotVernacular, Language


class BotVernacularAdminForm(forms.ModelForm):
    language = forms.ChoiceField(
        help_text="Pick from languages defined under Chatbot > Languages. To add one not "
                  "listed here, create it there first.",
    )

    class Meta:
        model = BotVernacular
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built fresh per request (not a module-level constant) so a Language added via its
        # own admin page shows up here immediately, with no code change.
        self.fields["language"].choices = list(Language.objects.values_list('iso_code', 'name'))


@admin.register(BotVernacular)
class BotVernacularAdmin(SimpleHistoryAdmin):
    form = BotVernacularAdminForm
    list_display = ('company_bot', 'language', 'introductory_message', 'created_at')
    list_filter = (
        'company_bot',
        'language',
        CustomAdvanceDateFilter,
    )
    inlines = []
    raw_id_fields = ('company_bot', )
    search_fields = ('company_bot__name', 'language', 'introductory_message')
    date_hierarchy = 'created_at'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.order_by('company_bot', 'language')