from django.contrib import admin
from .models import InsuranceClaim, InsurancePayment


class InsurancePaymentInline(admin.TabularInline):
    model = InsurancePayment
    extra = 0
    fields = ('amount', 'payment_method', 'reference', 'payment_date', 'bank_account')


@admin.register(InsuranceClaim)
class InsuranceClaimAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'insurance', 'patient_name', 'patient_id' , 'amount_claimed',
        'amount_paid', 'balance_due_display', 'status',
        'invoice_number', 'due_date', 'days_overdue_display'
    )
    list_filter = ('insurance', 'status', 'invoice_date', 'due_date')
    search_fields = ('patient_name', 'patient_id', 'invoice_number', 'sale__id')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'
    inlines = [InsurancePaymentInline]

    @admin.display(description="Solde dû")
    def balance_due_display(self, obj):
        return f"{obj.balance_due:.0f} FCFA"

    @admin.display(description="Retard (j)")
    def days_overdue_display(self, obj):
        d = obj.days_overdue
        return f"⚠️ {d}" if d > 0 else "—"


@admin.register(InsurancePayment)
class InsurancePaymentAdmin(admin.ModelAdmin):
    list_display = (
        'payment_date', 'insurance', 'claim', 'amount',
        'payment_method', 'reference', 'recorded_by'
    )
    list_filter = ('insurance', 'payment_method', 'payment_date')
    search_fields = ('reference', 'claim__invoice_number', 'claim__patient_name')
    date_hierarchy = 'payment_date'
    readonly_fields = ('created_at',)
