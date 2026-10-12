from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, DeleteView
)
from django.views.decorators.http import require_POST
from .forms import MedicineForm, CategoryForm, SupplierForm, BatchForm
from .models import Medicine, Category, Supplier, Batch

from datetime import timedelta
from django.db.models import Sum, Count
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import transaction as db_transaction
from django.http import HttpResponse
from decimal import Decimal
from accounts.permissions import can_manage_inventory
from .models import StockRemoval


# ═══════════════════════════════════════════════════════════════════
# MIXIN DE PERMISSION
# ═══════════════════════════════════════════════════════════════════
class InventoryPermissionMixin(UserPassesTestMixin):
    """Vérifie que l'utilisateur peut gérer l'inventaire."""
    def test_func(self):
        u = self.request.user
        return u.is_superuser or getattr(u, 'can_manage_inventory', False)

    def handle_no_permission(self):
        messages.error(self.request, "Accès refusé : permission inventaire requise.")
        raise PermissionDenied


# ═══════════════════════════════════════════════════════════════════
# MÉDICAMENTS
# ═══════════════════════════════════════════════════════════════════
class MedicineListView(LoginRequiredMixin, InventoryPermissionMixin, ListView):
    model = Medicine
    template_name = 'inventory/medicine_list.html'
    context_object_name = 'medicines'
    paginate_by = 25

    def get_queryset(self):
        # Annotation du stock total (somme des batches)
        total_subq = Batch.objects.filter(
            medicine=OuterRef('pk')
        ).values('medicine').annotate(t=Sum('quantity')).values('t')

        qs = Medicine.objects.select_related('category', 'supplier').annotate(
            total_stock_annot=Coalesce(
                Subquery(total_subq, output_field=IntegerField()),
                0
            )
        )

        q = self.request.GET.get('q', '').strip()
        category_id = self.request.GET.get('category', '')
        supplier_id = self.request.GET.get('supplier', '')
        stock_filter = self.request.GET.get('stock', '')
        active_filter = self.request.GET.get('active', 'yes')

        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(generic_name__icontains=q) |
                Q(barcode__icontains=q)
            )
        if category_id:
            qs = qs.filter(category_id=category_id)
        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)
        if stock_filter == 'low':
            qs = qs.filter(total_stock_annot__lte=F('reorder_level'))
        elif stock_filter == 'zero':
            qs = qs.filter(total_stock_annot=0)
        if active_filter == 'yes':
            qs = qs.filter(is_active=True)
        elif active_filter == 'no':
            qs = qs.filter(is_active=False)

        return qs.order_by('name')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['categories'] = Category.objects.all().order_by('name')
        ctx['suppliers'] = Supplier.objects.all().order_by('name')
        ctx['q'] = self.request.GET.get('q', '')
        ctx['category_filter'] = self.request.GET.get('category', '')
        ctx['supplier_filter'] = self.request.GET.get('supplier', '')
        ctx['stock_filter'] = self.request.GET.get('stock', '')
        ctx['active_filter'] = self.request.GET.get('active', 'yes')
        ctx['total_count'] = Medicine.objects.count()
        return ctx


class MedicineDetailView(LoginRequiredMixin, InventoryPermissionMixin, DetailView):
    model = Medicine
    template_name = 'inventory/medicine_detail.html'
    context_object_name = 'medicine'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['batches'] = self.object.batches.order_by('expiry_date')
        ctx['recent_sales'] = self.object.saleitem_set.select_related(
            'sale'
        ).order_by('-sale__created_at')[:10]
        return ctx


class MedicineCreateView(LoginRequiredMixin, InventoryPermissionMixin, CreateView):
    model = Medicine
    form_class = MedicineForm
    template_name = 'inventory/medicine_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Médicament « {self.object.name} » créé. "
            f"Ajoutez une entrée de stock pour définir la quantité."
        )
        return response

    def get_success_url(self):
        return reverse_lazy('inventory:medicine_detail', kwargs={'pk': self.object.pk})


class MedicineUpdateView(LoginRequiredMixin, InventoryPermissionMixin, UpdateView):
    model = Medicine
    form_class = MedicineForm
    template_name = 'inventory/medicine_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"« {self.object.name} » mis à jour.")
        return response

    def get_success_url(self):
        return reverse_lazy('inventory:medicine_detail', kwargs={'pk': self.object.pk})


class MedicineDeleteView(LoginRequiredMixin, InventoryPermissionMixin, DeleteView):
    """Soft delete : désactive le médicament au lieu de le supprimer."""
    model = Medicine
    template_name = 'inventory/medicine_confirm_delete.html'
    success_url = reverse_lazy('inventory:medicine_list')

    def form_valid(self, form):
        self.object = self.get_object()
        self.object.is_active = False
        self.object.save(update_fields=['is_active'])
        messages.warning(
            self.request,
            f"« {self.object.name} » a été désactivé. "
            f"Réactivez-le depuis sa fiche si nécessaire."
        )
        return HttpResponseRedirect(self.get_success_url())


class MedicineReactivateView(LoginRequiredMixin, InventoryPermissionMixin, UpdateView):
    """Réactive un médicament désactivé."""
    model = Medicine
    fields = []
    template_name = 'inventory/medicine_confirm_reactivate.html'

    def form_valid(self, form):
        self.object = self.get_object()
        self.object.is_active = True
        self.object.save(update_fields=['is_active'])
        messages.success(self.request, f"« {self.object.name} » réactivé.")
        return HttpResponseRedirect(
            reverse_lazy('inventory:medicine_detail', kwargs={'pk': self.object.pk})
        )


# ═══════════════════════════════════════════════════════════════════
# BATCHES (entrées de stock)
# ═══════════════════════════════════════════════════════════════════
class BatchCreateView(LoginRequiredMixin, InventoryPermissionMixin, CreateView):
    model = Batch
    form_class = BatchForm
    template_name = 'inventory/batch_form.html'

    def get_medicine(self):
        return get_object_or_404(Medicine, pk=self.kwargs['medicine_pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['medicine'] = self.get_medicine()
        return ctx

    def form_valid(self, form):
        form.instance.medicine = self.get_medicine()
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Entrée de stock ajoutée : {self.object.quantity} unités "
            f"(péremption {self.object.expiry_date:%d/%m/%Y})."
        )
        return response

    def get_success_url(self):
        return reverse_lazy('inventory:medicine_detail', kwargs={'pk': self.kwargs['medicine_pk']})


class BatchUpdateView(LoginRequiredMixin, InventoryPermissionMixin, UpdateView):
    model = Batch
    form_class = BatchForm
    template_name = 'inventory/batch_form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['medicine'] = self.object.medicine
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Entrée de stock mise à jour.")
        return response

    def get_success_url(self):
        return reverse_lazy('inventory:medicine_detail', kwargs={'pk': self.object.medicine.pk})


class BatchDeleteView(LoginRequiredMixin, InventoryPermissionMixin, DeleteView):
    model = Batch
    template_name = 'inventory/batch_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('inventory:medicine_detail', kwargs={'pk': self.object.medicine.pk})

    def form_valid(self, form):
        medicine = self.get_object().medicine
        response = super().form_valid(form)
        messages.warning(self.request, f"Entrée de stock supprimée pour {medicine.name}.")
        return response


# ═══════════════════════════════════════════════════════════════════
# CATÉGORIES
# ═══════════════════════════════════════════════════════════════════
class CategoryListView(LoginRequiredMixin, InventoryPermissionMixin, ListView):
    model = Category
    template_name = 'inventory/category_list.html'
    context_object_name = 'categories'
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Count
        return Category.objects.annotate(
            medicine_count=Count('medicines')
        ).order_by('name')


class CategoryCreateView(LoginRequiredMixin, InventoryPermissionMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = 'inventory/category_form.html'
    success_url = reverse_lazy('inventory:category_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Catégorie « {self.object.name} » créée.")
        return response


class CategoryUpdateView(LoginRequiredMixin, InventoryPermissionMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = 'inventory/category_form.html'
    success_url = reverse_lazy('inventory:category_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Catégorie « {self.object.name} » mise à jour.")
        return response


class CategoryDeleteView(LoginRequiredMixin, InventoryPermissionMixin, DeleteView):
    model = Category
    template_name = 'inventory/category_confirm_delete.html'
    success_url = reverse_lazy('inventory:category_list')

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
            messages.success(self.request, "Catégorie supprimée.")
            return response
        except Exception as e:
            messages.error(
                self.request,
                "Impossible de supprimer : des médicaments utilisent cette catégorie."
            )
            return HttpResponseRedirect(self.success_url)


# ═══════════════════════════════════════════════════════════════════
# FOURNISSEURS
# ═══════════════════════════════════════════════════════════════════
class SupplierListView(LoginRequiredMixin, InventoryPermissionMixin, ListView):
    model = Supplier
    template_name = 'inventory/supplier_list.html'
    context_object_name = 'suppliers'
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Count
        return Supplier.objects.annotate(
            medicine_count=Count('medicines')
        ).order_by('name')


class SupplierCreateView(LoginRequiredMixin, InventoryPermissionMixin, CreateView):
    model = Supplier
    form_class = SupplierForm
    template_name = 'inventory/supplier_form.html'
    success_url = reverse_lazy('inventory:supplier_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Fournisseur « {self.object.name} » créé.")
        return response


class SupplierUpdateView(LoginRequiredMixin, InventoryPermissionMixin, UpdateView):
    model = Supplier
    form_class = SupplierForm
    template_name = 'inventory/supplier_form.html'
    success_url = reverse_lazy('inventory:supplier_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Fournisseur « {self.object.name} » mis à jour.")
        return response


class SupplierDeleteView(LoginRequiredMixin, InventoryPermissionMixin, DeleteView):
    model = Supplier
    template_name = 'inventory/supplier_confirm_delete.html'
    success_url = reverse_lazy('inventory:supplier_list')


@login_required
@can_manage_inventory
def expiring_dashboard(request):
    """
    Tableau de bord des alertes péremption.
    Affiche les entrées de stock par tranche d'expiration.
    """
    today = timezone.now().date()

    # Toutes les entrées non vides et non retirées
    base_qs = Batch.objects.filter(quantity__gt=0).select_related(
        'medicine', 'medicine__category', 'medicine__supplier'
    )

    # Calcul des tranches
    expired = base_qs.filter(expiry_date__lt=today)

    days_30 = today + timedelta(days=30)
    days_60 = today + timedelta(days=60)
    days_90 = today + timedelta(days=90)

    soon_30 = base_qs.filter(expiry_date__gte=today, expiry_date__lte=days_30)
    soon_60 = base_qs.filter(expiry_date__gt=days_30, expiry_date__lte=days_60)
    soon_90 = base_qs.filter(expiry_date__gt=days_60, expiry_date__lte=days_90)

    def compute_value(qs):
        """Calcule la valeur d'achat totale du stock concerné."""
        total = Decimal('0.00')
        for b in qs:
            total += b.quantity * b.medicine.purchase_price
        return total

    context = {
        'expired': {
            'batches': expired.order_by('expiry_date'),
            'count': expired.count(),
            'total_qty': expired.aggregate(t=Sum('quantity'))['t'] or 0,
            'value': compute_value(expired),
        },
        'soon_30': {
            'batches': soon_30.order_by('expiry_date'),
            'count': soon_30.count(),
            'total_qty': soon_30.aggregate(t=Sum('quantity'))['t'] or 0,
            'value': compute_value(soon_30),
        },
        'soon_60': {
            'batches': soon_60.order_by('expiry_date'),
            'count': soon_60.count(),
            'total_qty': soon_60.aggregate(t=Sum('quantity'))['t'] or 0,
            'value': compute_value(soon_60),
        },
        'soon_90': {
            'batches': soon_90.order_by('expiry_date'),
            'count': soon_90.count(),
            'total_qty': soon_90.aggregate(t=Sum('quantity'))['t'] or 0,
            'value': compute_value(soon_90),
        },
    }
    return render(request, 'inventory/expiring_dashboard.html', context)


@login_required
@can_manage_inventory
def expiring_list(request):
    """Liste filtrable de toutes les entrées expirant (avec tranche)."""
    today = timezone.now().date()
    days = int(request.GET.get('days', '90'))

    max_date = today + timedelta(days=days)
    batches = Batch.objects.filter(
        quantity__gt=0,
        expiry_date__lte=max_date
    ).select_related('medicine', 'medicine__category').order_by('expiry_date')

    # Filtres
    category_filter = request.GET.get('category', '')
    supplier_filter = request.GET.get('supplier', '')
    if category_filter:
        batches = batches.filter(medicine__category_id=category_filter)
    if supplier_filter:
        batches = batches.filter(medicine__supplier_id=supplier_filter)

    # Calcul valeur globale
    total_value = sum(
        (b.quantity * b.medicine.purchase_price for b in batches),
        Decimal('0.00')
    )

    from .models import Category, Supplier
    context = {
        'batches': batches,
        'days': days,
        'total_value': total_value,
        'categories': Category.objects.order_by('name'),
        'suppliers': Supplier.objects.order_by('name'),
        'category_filter': category_filter,
        'supplier_filter': supplier_filter,
    }
    return render(request, 'inventory/expiring_list.html', context)


@login_required
@can_manage_inventory
@require_POST
def remove_from_stock(request, batch_pk):
    """Retire une quantité d'un lot avec motif."""
    batch = get_object_or_404(Batch.objects.select_related('medicine'), pk=batch_pk)

    try:
        qty = int(request.POST.get('quantity', 0))
    except (ValueError, TypeError):
        qty = 0

    reason = request.POST.get('reason', 'other')
    notes = (request.POST.get('notes') or '').strip()

    if qty <= 0:
        messages.error(request, "Quantité invalide.")
        return redirect('inventory:expiring_dashboard')

    if qty > batch.quantity:
        messages.error(
            request,
            f"Impossible : quantité demandée ({qty}) supérieure au stock du lot ({batch.quantity})."
        )
        return redirect('inventory:expiring_dashboard')

    with db_transaction.atomic():
        # Créer le retrait (snapshot du prix d'achat actuel)
        removal = StockRemoval.objects.create(
            batch=batch,
            quantity=qty,
            reason=reason,
            unit_cost=batch.medicine.purchase_price,
            notes=notes,
            removed_by=request.user,
        )
        # Décrémenter le stock
        batch.quantity -= qty
        batch.save(update_fields=['quantity'])

    messages.success(
        request,
        f"Retrait enregistré : {qty} unité(s) de {batch.medicine.name} "
        f"({removal.get_reason_display()}) — perte : {removal.total_loss:.0f} FCFA."
    )
    return redirect('inventory:expiring_dashboard')


@login_required
@can_manage_inventory
@require_POST
def toggle_promotion(request, medicine_pk):
    """Active/désactive la promotion d'un médicament."""
    medicine = get_object_or_404(Medicine, pk=medicine_pk)

    if medicine.on_promotion:
        medicine.on_promotion = False
        medicine.promotion_discount_pct = 0
        medicine.save(update_fields=['on_promotion', 'promotion_discount_pct'])
        messages.info(request, f"Promotion retirée pour « {medicine.name} ».")
    else:
        try:
            pct = Decimal(str(request.POST.get('discount_pct', '10')).replace(',', '.'))
        except Exception:
            pct = Decimal('10')

        if pct <= 0 or pct > 90:
            messages.error(request, "Le taux de remise doit être entre 1 et 90 %.")
            return redirect(request.META.get('HTTP_REFERER', 'inventory:expiring_dashboard'))

        medicine.on_promotion = True
        medicine.promotion_discount_pct = pct
        medicine.save(update_fields=['on_promotion', 'promotion_discount_pct'])
        messages.success(
            request,
            f"Promotion activée : « {medicine.name} » à -{pct}% "
            f"(nouveau prix : {medicine.promotion_price:.0f} FCFA)."
        )

    return redirect(request.META.get('HTTP_REFERER', 'inventory:expiring_dashboard'))


@login_required
@can_manage_inventory
def expiring_export_csv(request):
    """Export CSV des entrées expirant."""
    import csv
    today = timezone.now().date()
    days = int(request.GET.get('days', '90'))
    max_date = today + timedelta(days=days)

    batches = Batch.objects.filter(
        quantity__gt=0,
        expiry_date__lte=max_date
    ).select_related('medicine', 'medicine__category').order_by('expiry_date')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="peremption_{today.isoformat()}.csv"'
    )
    response.write('\ufeff')

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Médicament', 'Catégorie', 'N° lot', 'Quantité',
        'Péremption', 'Jours restants', 'Prix achat unitaire',
        'Valeur totale', 'Statut'
    ])

    for b in batches:
        days_left = b.days_until_expiry
        status = (
            'PÉRIMÉ' if days_left < 0 else
            'URGENT' if days_left <= 30 else
            'BIENTÔT' if days_left <= 60 else
            'À SURVEILLER'
        )
        value = b.quantity * b.medicine.purchase_price
        writer.writerow([
            b.medicine.name,
            b.medicine.category.name,
            b.batch_number,
            b.quantity,
            b.expiry_date.strftime('%d/%m/%Y'),
            days_left,
            f"{b.medicine.purchase_price:.0f}",
            f"{value:.0f}",
            status,
        ])

    return response
