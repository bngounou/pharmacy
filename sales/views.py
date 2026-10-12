import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from inventory.models import Medicine, Batch
from .models import Sale, SaleItem, Insurance, CashMovement


@login_required
def pos(request):
    """Interface Point de Vente avec gestion des assurances."""
    medicines = Medicine.objects.filter(is_active=True).select_related('category')
    insurances = Insurance.objects.filter(is_active=True)
    return render(request, 'sales/pos.html', {
        'medicines': medicines,
        'payment_methods': Sale.PAYMENT_CHOICES,
        'insurances': insurances,
    })


@login_required
def sale_detail(request, pk):
    """Reçu d'une vente."""
    sale = get_object_or_404(
        Sale.objects.select_related('cashier').prefetch_related('items__medicine'),
        pk=pk
    )
    return render(request, 'sales/sale_detail.html', {'sale': sale})


def _deduct_stock_fefo(medicine, quantity):
    """
    Déduit `quantity` du stock en respectant FEFO.
    Retourne un tuple (used_batches, quantity_deducted, quantity_backorder).
    - Si stock suffisant : tout est déduit, backorder = 0
    - Si stock insuffisant : le stock disponible est déduit, le reste est backorder
    """
    available = medicine.total_stock
    to_deduct = min(available, quantity)
    backorder = quantity - to_deduct

    remaining = to_deduct
    used_batches = []

    for batch in medicine.batches.filter(quantity__gt=0).order_by('expiry_date'):
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        batch.quantity -= take
        batch.save(update_fields=['quantity'])
        used_batches.append((batch, take))
        remaining -= take

    return used_batches, to_deduct, backorder


@login_required
@require_POST
def create_sale(request):
    """
    Crée une vente. Gère :
    - Vente comptoir simple (comptant, Orange Money, etc.)
    - Vente avec assurance (tiers payant)
    - Précommande si stock insuffisant
    - Génération automatique des transactions au journal
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'Le panier est vide'}, status=400)

    # ─── Extraction des entrées ───────────────────────────────────
    payment_method = data.get('payment_method', 'cash')
    customer_name = (data.get('customer_name') or '').strip()
    customer_phone = (data.get('customer_phone') or '').strip()
    patient_id = (data.get('patient_id') or '').strip()
    amount_paid_input = data.get('amount_paid')
    expected_delivery = data.get('expected_delivery_date')
    notes = (data.get('notes') or '').strip()
    insurance_id = data.get('insurance_id')  # None, '' ou int

    # ─── Récupération de l'assurance si fournie ──────────────────
    insurance = None
    coverage_rate = Decimal('0.00')
    if insurance_id:
        try:
            insurance = Insurance.objects.get(pk=insurance_id, is_active=True)
            coverage_rate = insurance.coverage_rate
        except Insurance.DoesNotExist:
            return JsonResponse({'error': 'Assurance introuvable ou inactive'}, status=400)

    # Validation : matricule obligatoire si assurance
    if insurance_id and not patient_id:
        return JsonResponse(
            {'error': "Le numéro d'assuré est obligatoire pour une vente avec assurance."},
            status=400
        )

    try:
        with transaction.atomic():
            sale = Sale.objects.create(
                cashier=request.user,
                customer_name=customer_name,
                customer_phone=customer_phone,
                patient_id=patient_id,
                payment_method=payment_method,
                status='completed',
                sale_type='counter',
                expected_delivery_date=expected_delivery or None,
                notes=notes,
                insurance=insurance,
                insurance_coverage_rate=coverage_rate if insurance else None,
                amount_paid=Decimal('0.00'),
                total_amount=Decimal('0.00'),
            )

            total = Decimal('0.00')
            any_backorder = False
            all_backorder = True

            for item in items:
                medicine_id = item.get('medicine_id')
                quantity = int(item.get('quantity', 0))

                if quantity <= 0:
                    raise ValueError("Quantité invalide")

                medicine = Medicine.objects.select_for_update().get(pk=medicine_id)

                # Prix : insurance_price si assurance ET médicament éligible
                if insurance and medicine.insurance_eligible:
                    unit_price = medicine.insurance_price or medicine.cash_price
                elif medicine.on_promotion and medicine.promotion_discount_pct > 0:
                    unit_price = medicine.promotion_price
                else:
                    unit_price = medicine.cash_price

                used_batches, qty_in_stock, qty_backorder = _deduct_stock_fefo(
                    medicine, quantity
                )

                if qty_backorder > 0:
                    any_backorder = True
                if qty_in_stock > 0:
                    all_backorder = False

                first_batch = used_batches[0][0] if used_batches else None

                SaleItem.objects.create(
                    sale=sale,
                    medicine=medicine,
                    batch=first_batch,
                    quantity=quantity,
                    quantity_in_stock=qty_in_stock,
                    quantity_backorder=qty_backorder,
                    is_backorder=qty_backorder > 0,
                    delivered=(qty_backorder == 0),
                    unit_price=unit_price,
                    subtotal=unit_price * quantity,
                )

                total += unit_price * quantity

            # ─── Calcul des parts patient / assurance ─────────────
            if insurance:
                insurance_share = (total * coverage_rate).quantize(Decimal('0.01'))
                patient_share = total - insurance_share
            else:
                insurance_share = Decimal('0.00')
                patient_share = total

            sale.total_amount = total
            sale.patient_share = patient_share
            sale.insurance_share = insurance_share

            # Statut précommande
            if any_backorder:
                sale.sale_type = 'preorder'
                sale.status = 'partial' if not all_backorder else 'pending'
            else:
                sale.sale_type = 'counter'
                sale.status = 'completed'

            # Montant payé par le patient
            if amount_paid_input is not None and amount_paid_input != '':
                sale.amount_paid = Decimal(str(amount_paid_input))
            else:
                sale.amount_paid = patient_share

            sale.save(update_fields=[
                'total_amount', 'patient_share', 'insurance_share',
                'sale_type', 'status', 'amount_paid',
            ])

            # ─── Création du mouvement de caisse (part patient uniquement) ───
            now = timezone.now()

            if patient_share > 0:
                CashMovement.objects.create(
                    movement_type='sale_patient',
                    amount=patient_share,
                    payment_method=payment_method,
                    sale=sale,
                    description=f"Vente #{sale.pk} — part patient",
                    created_by=request.user,
                    date=now,
                )

            # ─── Création de la créance assurance (dans son propre module) ──
            if insurance and insurance_share > 0:
                from insurance.models import InsuranceClaim
                due = now.date() + timezone.timedelta(days=insurance.payment_delay_days)
                InsuranceClaim.objects.create(
                    insurance=insurance,
                    sale=sale,
                    patient_name=customer_name or "Non renseigné",
                    patient_id=patient_id,
                    amount_claimed=insurance_share,
                    due_date=due,
                    status='pending',
                )


        return JsonResponse({
            'success': True,
            'sale_id': sale.pk,
            'total': str(sale.total_amount),
            'patient_share': str(sale.patient_share),
            'insurance_share': str(sale.insurance_share),
            'status': sale.status,
            'sale_type': sale.sale_type,
            'has_backorder': any_backorder,
            'has_insurance': insurance is not None,
            'redirect': f'/sales/{sale.pk}/',
        })

    except Medicine.DoesNotExist:
        return JsonResponse({'error': 'Médicament introuvable'}, status=404)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'Erreur serveur : {e}'}, status=500)


@login_required
@require_POST
def deliver_backorder(request, pk):
    """
    Marque une ligne de précommande comme livrée et déduit le stock.
    """
    sale = get_object_or_404(Sale, pk=pk)

    try:
        with transaction.atomic():
            pending_items = sale.items.filter(is_backorder=True, delivered=False)

            if not pending_items.exists():
                return JsonResponse({'error': 'Aucune ligne en attente'}, status=400)

            for item in pending_items:
                medicine = Medicine.objects.select_for_update().get(pk=item.medicine_id)

                # On ne peut livrer que la quantité disponible maintenant
                available = medicine.total_stock
                if available < item.quantity_backorder:
                    return JsonResponse({
                        'error': f"Stock insuffisant pour {medicine.name} "
                                 f"(disponible : {available}, "
                                 f"requis : {item.quantity_backorder})"
                    }, status=400)

                _deduct_stock_fefo(medicine, item.quantity_backorder)
                item.delivered = True
                item.save(update_fields=['delivered'])

            # Mettre à jour le statut de la vente
            remaining = sale.items.filter(is_backorder=True, delivered=False).count()
            if remaining == 0:
                sale.status = 'completed'
                sale.delivered_at = timezone.now()
                sale.save(update_fields=['status', 'delivered_at'])
            else:
                sale.status = 'partial'
                sale.save(update_fields=['status'])

        return JsonResponse({'success': True, 'status': sale.status})

    except Exception as e:
        return JsonResponse({'error': f'Erreur : {e}'}, status=500)

    " ========================================================="

from datetime import datetime, timedelta
from django.db.models import Sum, Count, Q
from accounts.permissions import can_view_reports


@login_required
@can_view_reports
def cash_report(request):
    """
    Rapport journalier de caisse.
    Filtre par date, groupe par mode de paiement, affiche le détail.
    """
    # ─── Récupération de la date ──────────────────────────────────
    date_param = request.GET.get('date')
    if date_param:
        try:
            report_date = datetime.strptime(date_param, '%Y-%m-%d').date()
        except ValueError:
            report_date = timezone.now().date()
    else:
        report_date = timezone.now().date()

    # ─── Mouvements du jour ───────────────────────────────────────
    movements = CashMovement.objects.filter(
        date__date=report_date
    ).select_related('sale', 'created_by').order_by('date')

    # ─── Totaux par mode de paiement ──────────────────────────────
    by_payment = (
        movements
        .filter(movement_type='sale_patient')
        .values('payment_method')
        .annotate(
            total=Sum('amount'),
            count=Count('id'),
        )
        .order_by('payment_method')
    )

    # Construire un dict plus lisible {code: {label, total, count}}
    payment_labels = dict(Sale.PAYMENT_CHOICES)
    payments_summary = []
    for row in by_payment:
        payments_summary.append({
            'code': row['payment_method'],
            'label': payment_labels.get(row['payment_method'], row['payment_method']),
            'total': row['total'],
            'count': row['count'],
        })

    # ─── Totaux par type de mouvement ─────────────────────────────
    by_type = (
        movements
        .values('movement_type')
        .annotate(total=Sum('amount'), count=Count('id'))
        .order_by('movement_type')
    )
    type_labels = dict(CashMovement.TYPE_CHOICES)
    types_summary = [
        {
            'code': r['movement_type'],
            'label': type_labels.get(r['movement_type'], r['movement_type']),
            'total': r['total'],
            'count': r['count'],
            'is_inflow': r['movement_type'] in ('sale_patient', 'cash_in'),
        }
        for r in by_type
    ]

    # ─── Synthèse ─────────────────────────────────────────────────
    inflow = movements.filter(
        movement_type__in=['sale_patient', 'cash_in']
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    outflow = movements.filter(
        movement_type__in=['refund', 'purchase', 'expense', 'cash_out']
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    net_cash = inflow - outflow

    sales_qs = movements.filter(movement_type='sale_patient')
    sale_count = sales_qs.count()
    average_sale = (inflow / sale_count) if sale_count else Decimal('0.00')

    # ─── Ventes avec assurance du jour (informatif) ──────────────
    insurance_sales = Sale.objects.filter(
        created_at__date=report_date,
        insurance__isnull=False
    ).select_related('insurance')
    insurance_total = insurance_sales.aggregate(
        total=Sum('insurance_share')
    )['total'] or Decimal('0.00')

    context = {
        'report_date': report_date,
        'movements': movements,
        'payments_summary': payments_summary,
        'types_summary': types_summary,
        'inflow': inflow,
        'outflow': outflow,
        'net_cash': net_cash,
        'sale_count': sale_count,
        'average_sale': average_sale,
        'insurance_sales': insurance_sales,
        'insurance_total': insurance_total,
    }
    return render(request, 'sales/cash_report.html', context)

import csv
from django.http import HttpResponse


@login_required
@can_view_reports
def cash_report_export(request):
    """Export CSV du rapport de caisse."""
    date_param = request.GET.get('date')
    try:
        report_date = datetime.strptime(date_param, '%Y-%m-%d').date() if date_param else timezone.now().date()
    except ValueError:
        report_date = timezone.now().date()

    movements = CashMovement.objects.filter(
        date__date=report_date
    ).select_related('sale', 'created_by').order_by('date')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="caisse_{report_date.isoformat()}.csv"'
    )
    # BOM UTF-8 pour Excel
    response.write('\ufeff')

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Heure', 'Type', 'Description', 'Mode de paiement',
        'Montant', 'Sens', 'Vente', 'Caissier'
    ])

    for m in movements:
        writer.writerow([
            m.date.strftime('%H:%M:%S'),
            m.get_movement_type_display(),
            m.description,
            m.get_payment_method_display() if m.payment_method else '',
            f"{m.amount:.2f}".replace('.', ','),
            'Entrée' if m.is_inflow else 'Sortie',
            f"#{m.sale.pk}" if m.sale else '',
            m.created_by.username if m.created_by else '',
        ])

    # Ligne de total
    writer.writerow([])
    writer.writerow(['', '', '', 'SOLDE NET', '', '', '', ''])
    net = movements.filter(
        movement_type__in=['sale_patient', 'cash_in']
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    net -= movements.filter(
        movement_type__in=['refund', 'purchase', 'expense', 'cash_out']
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    writer.writerow(['', '', '', '', f"{net:.2f}".replace('.', ','), '', '', ''])

    return response

# ===================================================

from accounts.permissions import can_access_cash_closure
from .models import CashClosure


@login_required
@can_access_cash_closure
def cash_closure_list(request):
    """Historique des clôtures."""
    closures = CashClosure.objects.select_related(
        'cashier', 'validated_by'
    ).order_by('-period_end')[:100]

    today_closures = CashClosure.objects.filter(
        closure_date=timezone.now().date()
    ).count()

    context = {
        'closures': closures,
        'today_closures': today_closures,
    }
    return render(request, 'sales/cash_closure_list.html', context)


@login_required
@can_access_cash_closure
def cash_closure_new(request):
    """
    Nouvelle clôture — pré-remplie avec les mouvements du caissier
    sur la période définie.
    """
    now = timezone.now()
    default_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ─── Période ──────────────────────────────────────────────────
    period_start_str = request.GET.get('start') or request.POST.get('period_start')
    period_end_str = request.GET.get('end') or request.POST.get('period_end')

    try:
        period_start = (
            datetime.fromisoformat(period_start_str)
            if period_start_str else default_start
        )
    except ValueError:
        period_start = default_start

    try:
        period_end = (
            datetime.fromisoformat(period_end_str)
            if period_end_str else now
        )
    except ValueError:
        period_end = now

    # ─── Création d'une instance temporaire pour calculer le théorique
    if request.method == 'POST':
        # Le formulaire renvoie les montants comptés
        closure = CashClosure(
            cashier=request.user,
            closure_date=period_end.date(),
            period_start=period_start,
            period_end=period_end,
            counted_cash=Decimal(request.POST.get('counted_cash') or '0'),
            counted_om=Decimal(request.POST.get('counted_om') or '0'),
            counted_momo=Decimal(request.POST.get('counted_momo') or '0'),
            counted_card=Decimal(request.POST.get('counted_card') or '0'),
            notes=request.POST.get('notes', '').strip(),
        )
        closure.compute_totals()
        closure.save()

        messages.success(
            request,
            f"Clôture enregistrée — Écart : {closure.discrepancy:+.0f} FCFA"
        )
        return redirect('sales:cash_closure_detail', pk=closure.pk)

    # ─── GET : preview avec les totaux théoriques ─────────────────
    preview = CashClosure(
        cashier=request.user,
        closure_date=period_end.date(),
        period_start=period_start,
        period_end=period_end,
    )
    preview.compute_totals()

    # Détail des mouvements de la période
    movements = CashMovement.objects.filter(
        created_by=request.user,
        date__gte=period_start,
        date__lte=period_end,
    ).order_by('date')

    # Ventes du jour pour affichage
    sales = Sale.objects.filter(
        cashier=request.user,
        created_at__gte=period_start,
        created_at__lte=period_end,
    ).order_by('created_at')

    context = {
        'preview': preview,
        'movements': movements,
        'sales': sales,
        'period_start': period_start,
        'period_end': period_end,
        'payment_methods': CashMovement.PAYMENT_CHOICES if hasattr(CashMovement, 'PAYMENT_CHOICES') else Sale.PAYMENT_CHOICES,
    }
    return render(request, 'sales/cash_closure_form.html', context)


@login_required
@can_access_cash_closure
def cash_closure_detail(request, pk):
    """Détail d'une clôture."""
    closure = get_object_or_404(
        CashClosure.objects.select_related('cashier', 'validated_by'),
        pk=pk
    )
    movements = CashMovement.objects.filter(
        created_by=closure.cashier,
        date__gte=closure.period_start,
        date__lte=closure.period_end,
    ).order_by('date')

    context = {
        'closure': closure,
        'movements': movements,
    }
    return render(request, 'sales/cash_closure_detail.html', context)


@login_required
@can_access_cash_closure
@require_POST
def cash_closure_validate(request, pk):
    """Valider une clôture (par un responsable)."""
    closure = get_object_or_404(CashClosure, pk=pk)
    if closure.is_validated:
        messages.warning(request, "Cette clôture est déjà validée.")
    else:
        closure.is_validated = True
        closure.validated_by = request.user
        closure.validated_at = timezone.now()
        closure.save(update_fields=['is_validated', 'validated_by', 'validated_at'])
        messages.success(request, "Clôture validée.")
    return redirect('sales:cash_closure_detail', pk=closure.pk)