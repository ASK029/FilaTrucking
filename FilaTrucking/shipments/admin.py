from django.contrib import admin

from .models import FinancialStatement


@admin.register(FinancialStatement)
class FinancialStatementAdmin(admin.ModelAdmin):
    list_display = ("__str__", "revenue", "total_expenses", "driver_pay_total", "net_profit", "generated_at")
    list_filter = ("period_type", "year")
    readonly_fields = ("generated_at",)
