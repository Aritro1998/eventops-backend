from django import forms
from django.contrib import admin

from events.models import Event
from knowledge.models import KnowledgeDocument
from core.admin_widgets import ChainedSelect


class EventSelect(ChainedSelect):
    """Only offer Events belonging to whichever Venue is currently
    selected — events with no venue at all are correctly hidden once a
    venue is chosen, and everything shows again if venue is cleared."""
    parent_field_id = "id_venue"

    def get_parent_map(self):
        return dict(Event.objects.values_list("id", "venue_id"))


class KnowledgeDocumentAdminForm(forms.ModelForm):
    class Meta:
        model = KnowledgeDocument
        fields = "__all__"
        widgets = {"event": EventSelect}


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    form = KnowledgeDocumentAdminForm
    list_display = ['id', 'title', 'venue', 'event', 'updated_at']
    list_filter = ['venue', 'event']
    search_fields = ['title', 'content']
    list_display_links = ['id', 'title']

    class Media:
        js = ["admin/chained_select.js"]