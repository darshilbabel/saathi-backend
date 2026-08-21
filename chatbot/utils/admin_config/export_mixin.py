import tablib
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.http import urlencode
from django.urls import path
from django.utils.timezone import localtime
from django.contrib import admin


class ExportAllFieldsMixin:

    export_filename = "export.xlsx"
    # Extra computed columns appended after the model's own fields, e.g.
    # {'session_type': lambda obj: obj.session_type} for a value pulled in via
    # an annotation on get_queryset().
    extra_export_fields = {}

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                "export_all/",
                self.admin_site.admin_view(self.export_all_view),
                name=f"{self.model._meta.app_label}_{self.model._meta.model_name}_export_all",
            ),
        ]

        return custom_urls + urls

    def export_all_view(self, request):

        ids = request.GET.get("ids", "")
        selected_ids = ids.split(",") if ids else []

        queryset = self.get_queryset(request).filter(id__in=selected_ids)

        dataset = tablib.Dataset()

        fields = [field.name for field in self.model._meta.fields]
        extra_fields = list(self.extra_export_fields)

        dataset.headers = fields + extra_fields

        for obj in queryset:

            row = []

            for field in fields:

                value = getattr(obj, field)

                if hasattr(value, "__str__"):
                    value = str(value)

                # handle datetime timezone
                if hasattr(value, "tzinfo") and value.tzinfo:
                    value = localtime(value).replace(tzinfo=None)

                row.append(value)

            for field in extra_fields:
                row.append(self.extra_export_fields[field](obj))

            dataset.append(row)

        response = HttpResponse(
            dataset.export("xlsx"),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        response["Content-Disposition"] = f'attachment; filename="{self.export_filename}"'

        return response

    @admin.action(description="Export selected records")
    def export_selected_records(self, request, queryset):
        selected = queryset.values_list("pk", flat=True)
        query_string = urlencode({"ids": ",".join(map(str, selected))})
        return HttpResponseRedirect(f"{request.path}export_all/?{query_string}")

    def get_actions(self, request):
        actions = super().get_actions(request)

        actions["export_selected_records"] = (
            self.__class__.export_selected_records,
            "export_selected_records",
            "Export selected records",
        )

        return actions
