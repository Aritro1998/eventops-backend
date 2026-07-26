from django.contrib import admin
from django.db.models import Sum, Count
from django.db.models.functions import TruncDate
from knowledge.services import KnowledgeService
from ai_assistant.models import (
    PendingBookingThread,
    PendingBookingCancellation,
    UsageLog,
)


# Mostly for debugging a specific user's stuck/confusing draft state — in
# normal operation these rows are created/deleted by the AI tools and
# action views (see ai_assistant/models.py's module docstring), not admin.
@admin.register(PendingBookingThread)
class PendingBookingThreadAdmin(admin.ModelAdmin):
    """The graph-based flow's version of PendingBookingAdmin above - a
    marker pointing at a paused booking_graph thread, not the draft
    content itself (that lives in the checkpoint tables)."""
    list_display = ["id", "user", "conversation_id", "created_at", "expires_at"]
    search_fields = ["user__username", "conversation_id"]
    list_display_links = ["id", "user"]


@admin.register(PendingBookingCancellation)
class PendingBookingCancellationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "booking", "created_at", "expires_at"]
    search_fields = ["user__username", "booking__id"]
    list_display_links = ["id", "user", "booking"]


@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    """
    Read-only by design (see has_add_permission/has_change_permission
    below) — unlike the Pending* models above, this is a permanent audit
    record of what was actually spent, not a draft. Editing a token count
    after the fact would corrupt that record, so admin can view/search/
    filter it but never hand-edit or hand-create a row. Deletion is left
    at Django's normal permission default (superusers only) rather than
    blocked outright, in case old rows ever need pruning under a
    retention policy - there isn't one yet.
    """
    list_display = [
        "id", "conversation_id", "user", "call_type", "model", "system_prompt_tokens",
        "prompt_tokens", "completion_tokens", "total_tokens", "tool_called", "created_at",
    ]
    list_filter = ["model", "user", "tool_called"]
    search_fields = ["conversation_id", "user__username"]
    list_display_links = ["id", "conversation_id"]
    # Adds the year/month/day drill-down at the top of the list view -
    # exactly the kind of time-based browsing the upcoming Conversation
    # analytics milestone will also need, so this is free groundwork.
    date_hierarchy = "created_at"

    @admin.display(description="Call Type")
    def call_type(self, obj):
        # Distinguishes embedding calls (search_knowledge_base's RAG
        # search - real OpenAI spend, but no completion/system-prompt
        # tokens the way a chat turn has) from ordinary chat completions,
        # without needing a schema change: model name is what actually
        # differs, so that's what this reads instead of tool_called
        # (which stays accurate even if another tool starts calling
        # embeddings later).
        return "Embedding" if obj.model == KnowledgeService.EMBEDDING_MODEL else "Chat"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
    
    def changelist_view(self, request, extra_context=None):
        """
        Injects an aggregate summary above the row list - totals,
        per-model breakdown, per-day trend, and the system-prompt-overhead
        percentage that motivated tracking system_prompt_tokens in the
        first place. Runs against whatever's currently filtered/searched
        (cl.queryset, pulled off the response super() already built),
        not UsageLog.objects.all() - so drilling into one model or one
        day via the admin's own filters updates the summary too, instead
        of it always showing the global picture regardless of what's on
        screen.
        """
        response = super().changelist_view(request, extra_context=extra_context)
        
        if not hasattr(response, "context_data"):
            return response
        
        queryset = response.context_data["cl"].queryset
        
        totals = queryset.aggregate(
            call_count=Count("id"),
            conversation_count=Count("conversation_id", distinct=True),
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens"),
            total_tokens=Sum("total_tokens"),
            system_prompt_tokens=Sum("system_prompt_tokens"),
        )
        
        # Guard against dividing by zero when the current filter matches
        # no rows at all (e.g. a date range with no activity yet).
        system_prompt_pct = None
        if totals["prompt_tokens"]:
            system_prompt_pct = round(
                100 * (totals["system_prompt_tokens"] or 0) / totals["prompt_tokens"], 1
            )
            
        per_model = (
            queryset.values("model")
            .annotate(total_tokens=Sum("total_tokens"), call_count=Count("id"))
            .order_by("-total_tokens")
        )

        per_day = (
            queryset.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(total_tokens=Sum("total_tokens"))
            .order_by("day")
        )

        response.context_data["usage_summary"] = {
            "totals": totals,
            "system_prompt_pct": system_prompt_pct,
            "per_model": per_model,
            "per_day": per_day,
        }
        
        return response
    
    