from django.contrib import admin
from .models import Sale, SaleItem, Insurance, CashMovement


@admin.register(Insurance)
class InsuranceAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'coverage_rate_display', 'payment_delay_days', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')
    list_editable = ('is_active',)

    @admin.display(description="Couverture")
    def coverage_rate_display(self, obj):
        return f"{obj.coverage_rate * 100:.0f}%"


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ('subtotal',)
    fields = (
        'medicine', 'quantity', 'quantity_in_stock', 'quantity_backorder',
        'is_backorder', 'delivered', 'unit_price', 'subtotal'
    )


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'customer_name', 'sale_type', 'insurance_display',
        'total_amount', 'patient_share', 'insurance_share',
        'payment_method', 'status', 'created_at'
    )
    list_filter = (
        'status', 'sale_type', 'payment_method',
        'insurance', 'created_at'
    )
    search_fields = ('customer_name', 'customer_phone', 'patient_id', 'id')
    readonly_fields = ('created_at', 'delivered_at')
    inlines = [SaleItemInline]
    date_hierarchy = 'created_at'

    @admin.display(description="Assurance")
    def insurance_display(self, obj):
        if obj.insurance:
            return f"{obj.insurance.name} ({obj.insurance_coverage_rate * 100:.0f}%)"
        return "—"



@admin.register(CashMovement)
class CashMovementAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'movement_type', 'amount', 'payment_method',
        'sale_link', 'description', 'created_by'
    )
    list_filter = ('movement_type', 'payment_method', 'date')
    search_fields = ('description', 'reference')
    date_hierarchy = 'date'
    readonly_fields = ('created_at',)

    @admin.display(description="Vente")
    def sale_link(self, obj):
        return f"#{obj.sale_id}" if obj.sale_id else "—"
