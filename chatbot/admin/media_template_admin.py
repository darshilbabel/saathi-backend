from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from chatbot.filter.custom_date_from_filter import CustomAdvanceDateFilter
from chatbot.models import MediaTemplate
from chatbot.models.enums import MediaTemplateType


@admin.register(MediaTemplate)
class MediaTemplateAdmin(SimpleHistoryAdmin):
    """Admin interface for MediaTemplate. The type-specific field (`template` for
    PDF, `template_file` for DOCX) only appears once the row already exists —
    pick `flow`/`type`/`template_name` first, save, then the right field shows up."""
    list_display = ('template_name', 'type', 'flow', 'created_at', 'updated_at')
    list_filter = (
        'type',
        'flow',
        CustomAdvanceDateFilter,
    )
    search_fields = ('template_name',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')

    def get_fields(self, request, obj=None):
        fields = ['flow', 'type', 'template_name']
        if obj is None:
            return fields

        if obj.type == MediaTemplateType.PDF:
            fields.append('template')
        elif obj.type == MediaTemplateType.DOCX:
            fields.append('template_file')

        fields += ['constants_json', 'created_at', 'updated_at']
        return fields

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'template':
            kwargs['widget'] = admin.widgets.AdminTextareaWidget(attrs={'rows': 20, 'cols': 100})
        elif db_field.name == 'constants_json':
            kwargs['help_text'] = 'Enter constants as JSON object, e.g., {"key1": "value1", "key2": "value2"}'
        return super().formfield_for_dbfield(db_field, request, **kwargs)
