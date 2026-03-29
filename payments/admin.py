from django.contrib import admin

from payments.models import Payment

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "booking", "amount", "status", "transaction_id", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["booking__id", "transaction_id"]
    list_display_links = ["id", "booking"]