from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "location", "extraction_status", "created_at")
    list_filter = ("extraction_status", "source")
    search_fields = ("title", "company", "url")
