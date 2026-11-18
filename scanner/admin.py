from django.contrib import admin # imports administration tools.
from .models import Scan # imports models


@admin.register(Scan)
class ScanAdmin(admin.ModelAdmin):
    list_display = ("url", "scanned_at", "status")  # specifies columns in scan list.
    list_filter = ("status", "scanned_at")  # adds filters for scan status and date
    search_fields = ("url",)
    ordering = ("-scanned_at",)  # displays newest records first.