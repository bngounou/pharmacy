from django.contrib import admin
from .models import Category, Supplier, Medicine, Batch


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_person', 'phone', 'email')
    search_fields = ('name', 'contact_person')


class BatchInline(admin.TabularInline):
    model = Batch
    extra = 1
    fields = ('batch_number', 'quantity', 'expiry_date', 'received_date')
    verbose_name = "Entrée de stock"
    verbose_name_plural = "Entrées de stock"


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = (
        'medicine', 'quantity', 'expiry_date',
        'days_until_expiry_display', 'is_expired_display',
        'received_date', 'batch_number'
    )
    list_filter = ('expiry_date', 'received_date')
    search_fields = ('medicine__name', 'batch_number')
    date_hierarchy = 'expiry_date'
    autocomplete_fields = ('medicine',)
    list_per_page = 30

    @admin.display(description="Périmé", boolean=True, ordering='expiry_date')
    def is_expired_display(self, obj):
        return obj.is_expired

    @admin.display(description="Jours restants", ordering='expiry_date')
    def days_until_expiry_display(self, obj):
        days = obj.days_until_expiry
        if days < 0:
            return f"❌ Expiré depuis {-days}j"
        if days < 30:
            return f"⚠️ {days}j"
        if days < 90:
            return f"{days}j"
        return f"✅ {days}j"


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'category', 'purchase_price', 'cash_price',
        'total_stock', 'is_low_stock', 'reorder_quantity_display',
        'insurance_eligible', 'is_active',
    )
    list_filter = ('category', 'requires_prescription', 'insurance_eligible', 'is_active')
    search_fields = ('name', 'generic_name', 'barcode')
    inlines = [BatchInline]
    list_per_page = 25

    fieldsets = (
        ("Informations", {
            'fields': ('name', 'generic_name', 'category', 'supplier',
                       'barcode', 'description', 'image')
        }),
        ("Prix", {
            'fields': ('purchase_price', 'cash_price', 'insurance_price'),
            'description': "Laisser 'Prix assurance' vide pour calcul automatique "
                           "(prix comptant × 1.15)."
        }),
        ("Gestion du stock", {
            'fields': ('reorder_level', 'recommended_stock'),
        }),
        ("Divers", {
            'fields': ('requires_prescription', 'is_active'),
        }),
    )

    @admin.display(description="Stock total", ordering='name')
    def total_stock(self, obj):
        return obj.total_stock

    @admin.display(description="Stock faible", boolean=True)
    def is_low_stock(self, obj):
        return obj.is_low_stock

    @admin.display(description="Marge")
    def margin_display(self, obj):
        return f"{obj.gross_margin:.0f} FCFA ({obj.gross_margin_percent}%)"

    @admin.display(description="À commander")
    def reorder_quantity_display(self, obj):
        qty = obj.reorder_quantity
        if qty > 0:
            return f"⚠️ {qty}"
        return "—"


