from django.contrib import admin

from knowledge.models import KnowledgeDocument


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'venue', 'event', 'updated_at']
    list_filter = ['venue', 'event']
    search_fields = ['title', 'content']