from django.contrib import admin
from .models import InsuranceClaim, InsurancePayment, InsuranceInvoice


class InsurancePaymentInline(admin.TabularInline):
    model = InsurancePayment
    extra = 0
    fields = (
        'payment_date', 'amount', 'payment_method',
        'reference', 'bank_account', 'recorded_by'
    )


class InsuranceClaimInline(admin.TabularInline):
    model = InsuranceClaim
    extra = 0
    fields = ('sale', 'patient_name', 'patient_id', 'amount_claimed', 'amount_paid', 'status')
    readonly_fields = ('sale', 'patient_name', 'patient_id', 'amount_claimed', 'amount_paid', 'status')
    can_delete = False
    show_change_link = True


@admin.register(InsuranceInvoice)
class InsuranceInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number', 'insurance', 'period_display',
        'total_claimed', 'total_paid', 'balance_display',
        'status', 'invoice_date', 'due_date', 'days_overdue_display'
    )
    list_filter = ('insurance', 'status', 'period_type', 'invoice_date')
    search_fields = ('invoice_number', 'external_reference')
    readonly_fields = ('created_at', 'updated_at', 'total_claimed', 'total_paid')
    inlines = [InsuranceClaimInline]     # ← UNIQUEMENT le claim inline
    date_hierarchy = 'invoice_date'

    @admin.display(description="Période")
    def period_display(self, obj):
        return f"{obj.period_start:%d/%m/%Y} → {obj.period_end:%d/%m/%Y}"

    @admin.display(description="Solde dû")
    def balance_display(self, obj):
        return f"{obj.balance_due:.0f} FCFA"

    @admin.display(description="Retard (j)")
    def days_overdue_display(self, obj):
        d = obj.days_overdue
        return f"⚠️ {d}" if d > 0 else "—"


@admin.register(InsuranceClaim)
class InsuranceClaimAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'insurance', 'patient_name', 'patient_id',
        'amount_claimed', 'amount_paid', 'balance_due_display',
        'status', 'invoice', 'due_date', 'days_overdue_display'
    )
    list_filter = ('insurance', 'status', 'invoice', 'invoice_date', 'due_date')
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