from decimal import Decimal
from datetime import datetime, timedelta, date

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, F, Q, Avg
from django.utils import timezone
from django.core.paginator import Paginator
from django.http import HttpResponse
import json

from accounts.permissions import can_view_reports
from inventory.models import Medicine
from .models import Sale, SaleItem, CashMovement


# ═══════════════════════════════════════════════════════════════════
# UTILITAIRES DE PÉRIODE
# ═══════════════════════════════════════════════════════════════════
def get_period_range(period_type, ref_date=None, custom_start=None, custom_end=None):
    """
    Retourne (start_date, end_date) pour un type de période donné.
    Utilisé aussi pour calculer la période précédente.
    """
    if ref_date is None:
        ref_date = timezone.now().date()

    if period_type == 'day':
        return ref_date, ref_date

    elif period_type == 'week':
        # Lundi au dimanche
        start = ref_date - timedelta(days=ref_date.weekday())
        end = start + timedelta(days=6)
        return start, end

    elif period_type == 'month':
        start = ref_date.replace(day=1)
        # Dernier jour du mois
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1, day=1)
        else:
            next_month = start.replace(month=start.month + 1, day=1)
        end = next_month - timedelta(days=1)
        return start, end

    elif period_type == 'custom':
        if custom_start and custom_end:
            return custom_start, custom_end
        # Fallback : 30 derniers jours
        return ref_date - timedelta(days=30), ref_date

    return ref_date, ref_date


def get_previous_period(period_type, start, end):
    """Retourne la période précédente de même durée."""
    duration = (end - start).days + 1

    if period_type == 'day':
        prev_start = prev_end = start - timedelta(days=1)
    elif period_type == 'week':
        prev_start = start - timedelta(days=7)
        prev_end = end - timedelta(days=7)
    elif period_type == 'month':
        # Mois précédent
        if start.month == 1:
            prev_start = start.replace(year=start.year - 1, month=12, day=1)
        else:
            prev_start = start.replace(month=start.month - 1, day=1)
        # Dernier jour du mois précédent
        if prev_start.month == 12:
            next_m = prev_start.replace(year=prev_start.year + 1, month=1, day=1)
        else:
            next_m = prev_start.replace(month=prev_start.month + 1, day=1)
        prev_end = next_m - timedelta(days=1)
    else:  # custom
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=duration - 1)

    return prev_start, prev_end


# ═══════════════════════════════════════════════════════════════════
# AGRÉGATION DES DONNÉES
# ═══════════════════════════════════════════════════════════════════
def aggregate_report(start_date, end_date):
    """
    Retourne un dictionnaire complet de statistiques sur la période.
    """
    # Filtre de base
    sales_qs = Sale.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )

    # Exclure les annulées/remboursées pour le CA
    valid_sales = sales_qs.exclude(status__in=['cancelled', 'refunded'])

    # ─── Indicateurs principaux ───────────────────────────────────
    total_sales = valid_sales.aggregate(
        revenue=Sum('patient_share'),
        total=Sum('total_amount'),
        insurance=Sum('insurance_share'),
    )
    revenue = total_sales['revenue'] or Decimal('0.00')
    total_invoiced = total_sales['total'] or Decimal('0.00')
    insurance_total = total_sales['insurance'] or Decimal('0.00')

    transactions_count = valid_sales.count()

    # Nombre d'articles vendus
    items_qs = SaleItem.objects.filter(
        sale__in=valid_sales
    )
    items_sold = items_qs.aggregate(q=Sum('quantity'))['q'] or 0

    # Panier moyen
    avg_basket = (revenue / transactions_count) if transactions_count else Decimal('0.00')

    # Marge brute (approximation : somme (prix vente - prix achat) × quantité)
    margin_total = Decimal('0.00')
    for item in items_qs.select_related('medicine').iterator():
        # On utilise le prix d'achat actuel du médicament
        margin = (item.unit_price - item.medicine.purchase_price) * item.quantity
        margin_total += margin

    # ─── Par mode de paiement ─────────────────────────────────────
    payments_data = (
        CashMovement.objects
        .filter(
            movement_type='sale_patient',
            date__date__gte=start_date,
            date__date__lte=end_date,
        )
        .values('payment_method')
        .annotate(total=Sum('amount'), count=Count('id'))
        .order_by('-total')
    )
    payment_labels = dict(Sale.PAYMENT_CHOICES)
    by_payment = [
        {
            'code': r['payment_method'],
            'label': payment_labels.get(r['payment_method'], r['payment_method']),
            'total': r['total'],
            'count': r['count'],
        }
        for r in payments_data
    ]

    # ─── Par type de vente ────────────────────────────────────────
    by_type = (
        valid_sales
        .values('sale_type')
        .annotate(total=Sum('patient_share'), count=Count('id'))
    )
    type_labels = dict(Sale.TYPE_CHOICES)
    by_sale_type = [
        {
            'code': r['sale_type'],
            'label': type_labels.get(r['sale_type'], r['sale_type']),
            'total': r['total'] or Decimal('0.00'),
            'count': r['count'],
        }
        for r in by_type
    ]

    # ─── Top 10 produits ──────────────────────────────────────────
    top_products = (
        items_qs
        .values('medicine__name', 'medicine__id')
        .annotate(
            qty=Sum('quantity'),
            revenue=Sum('subtotal'),
        )
        .order_by('-revenue')[:10]
    )

    # ─── Top 10 marges ────────────────────────────────────────────
    margins_by_product = {}
    for item in items_qs.select_related('medicine').iterator():
        med_id = item.medicine_id
        if med_id not in margins_by_product:
            margins_by_product[med_id] = {
                'name': item.medicine.name,
                'qty': 0,
                'margin': Decimal('0.00'),
            }
        margins_by_product[med_id]['qty'] += item.quantity
        margins_by_product[med_id]['margin'] += (
            (item.unit_price - item.medicine.purchase_price) * item.quantity
        )

    top_margins = sorted(
        margins_by_product.values(),
        key=lambda x: x['margin'],
        reverse=True
    )[:10]

    # ─── Performance par caissier ─────────────────────────────────
    by_cashier = (
        valid_sales
        .values('cashier__username')
        .annotate(
            total=Sum('patient_share'),
            count=Count('id'),
        )
        .order_by('-total')
    )
    cashier_data = []
    for r in by_cashier:
        avg = (r['total'] / r['count']) if r['count'] else Decimal('0.00')
        cashier_data.append({
            'username': r['cashier__username'],
            'total': r['total'] or Decimal('0.00'),
            'count': r['count'],
            'avg': avg,
        })

    # ─── Données graphique : évolution jour par jour ──────────────
    daily_data = []
    current = start_date
    while current <= end_date:
        day_total = (
            valid_sales
            .filter(created_at__date=current)
            .aggregate(t=Sum('patient_share'))['t'] or Decimal('0.00')
        )
        daily_data.append({
            'date': current.strftime('%Y-%m-%d'),
            'label': current.strftime('%d/%m'),
            'total': float(day_total),
        })
        current += timedelta(days=1)

    return {
        'revenue': revenue,
        'total_invoiced': total_invoiced,
        'insurance_total': insurance_total,
        'transactions_count': transactions_count,
        'items_sold': items_sold,
        'avg_basket': avg_basket,
        'margin_total': margin_total,
        'by_payment': by_payment,
        'by_sale_type': by_sale_type,
        'top_products': top_products,
        'top_margins': top_margins,
        'by_cashier': cashier_data,
        'daily_data': daily_data,
    }


# ═══════════════════════════════════════════════════════════════════
# VUE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_view_reports
def sales_report(request):
    """
    Rapport multi-jours avec comparaison et graphiques.
    """
    period_type = request.GET.get('period', 'month')

    # Récupération des dates custom
    custom_start_str = request.GET.get('start', '')
    custom_end_str = request.GET.get('end', '')

    custom_start = None
    custom_end = None
    if custom_start_str:
        try:
            custom_start = datetime.strptime(custom_start_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if custom_end_str:
        try:
            custom_end = datetime.strptime(custom_end_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    # Calcul des périodes
    start_date, end_date = get_period_range(period_type, custom_start=custom_start, custom_end=custom_end)
    prev_start, prev_end = get_previous_period(period_type, start_date, end_date)

    # Agrégations
    current = aggregate_report(start_date, end_date)
    previous = aggregate_report(prev_start, prev_end)

    # Calcul des variations %
    def calc_delta(curr, prev):
        if prev == 0:
            return None  # indéfini
        return round(float((curr - prev) / prev * 100), 1)

    comparison = {
        'revenue_delta': calc_delta(current['revenue'], previous['revenue']),
        'transactions_delta': calc_delta(current['transactions_count'], previous['transactions_count']),
        'avg_basket_delta': calc_delta(current['avg_basket'], previous['avg_basket']),
        'margin_delta': calc_delta(current['margin_total'], previous['margin_total']),
        'items_delta': calc_delta(current['items_sold'], previous['items_sold']),
    }

    # Sérialisation JSON pour Chart.js
    chart_data = {
        'daily': current['daily_data'],
        'payments': [
            {'label': p['label'], 'value': float(p['total'])}
            for p in current['by_payment']
        ],
        'top_products': [
            {
                'label': p['medicine__name'][:30],
                'value': float(p['revenue'])
            }
            for p in current['top_products'][:10]
        ],
    }

    context = {
        'period_type': period_type,
        'start_date': start_date,
        'end_date': end_date,
        'prev_start': prev_start,
        'prev_end': prev_end,
        'custom_start': custom_start_str,
        'custom_end': custom_end_str,
        'current': current,
        'previous': previous,
        'comparison': comparison,
        'chart_data_json': json.dumps(chart_data),
    }
    return render(request, 'sales/sales_report.html', context)


# ═══════════════════════════════════════════════════════════════════
# EXPORT CSV
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_view_reports
def sales_report_export_csv(request):
    """Export CSV complet du rapport."""
    import csv

    period_type = request.GET.get('period', 'month')
    custom_start_str = request.GET.get('start', '')
    custom_end_str = request.GET.get('end', '')

    custom_start = custom_end = None
    if custom_start_str:
        try:
            custom_start = datetime.strptime(custom_start_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if custom_end_str:
        try:
            custom_end = datetime.strptime(custom_end_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    start_date, end_date = get_period_range(period_type, custom_start=custom_start, custom_end=custom_end)
    data = aggregate_report(start_date, end_date)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="rapport_{start_date}_{end_date}.csv"'
    )
    response.write('\ufeff')

    writer = csv.writer(response, delimiter=';')

    # En-tête
    writer.writerow([f'RAPPORT DE VENTES — {start_date:%d/%m/%Y} au {end_date:%d/%m/%Y}'])
    writer.writerow([])

    # Synthèse
    writer.writerow(['SYNTHÈSE'])
    writer.writerow(['Chiffre d\'affaires', f"{data['revenue']:.0f}"])
    writer.writerow(['Total facturé (avec assurance)', f"{data['total_invoiced']:.0f}"])
    writer.writerow(['Créances assurances générées', f"{data['insurance_total']:.0f}"])
    writer.writerow(['Nombre de transactions', data['transactions_count']])
    writer.writerow(['Articles vendus', data['items_sold']])
    writer.writerow(['Panier moyen', f"{data['avg_basket']:.0f}"])
    writer.writerow(['Marge brute estimée', f"{data['margin_total']:.0f}"])
    writer.writerow([])

    # Par mode de paiement
    writer.writerow(['PAR MODE DE PAIEMENT'])
    writer.writerow(['Mode', 'Nombre', 'Total (FCFA)'])
    for p in data['by_payment']:
        writer.writerow([p['label'], p['count'], f"{p['total']:.0f}"])
    writer.writerow([])

    # Par caissier
    writer.writerow(['PAR CAISSIER'])
    writer.writerow(['Caissier', 'Nb transactions', 'CA total', 'Panier moyen'])
    for c in data['by_cashier']:
        writer.writerow([
            c['username'], c['count'], f"{c['total']:.0f}", f"{c['avg']:.0f}"
        ])
    writer.writerow([])

    # Top produits
    writer.writerow(['TOP 10 PRODUITS'])
    writer.writerow(['Médicament', 'Quantité', 'CA'])
    for p in data['top_products']:
        writer.writerow([
            p['medicine__name'], p['qty'], f"{p['revenue']:.0f}"
        ])
    writer.writerow([])

    # Évolution journalière
    writer.writerow(['ÉVOLUTION JOURNALIÈRE'])
    writer.writerow(['Date', 'CA'])
    for d in data['daily_data']:
        writer.writerow([d['date'], f"{d['total']:.0f}"])

    return response


# ═══════════════════════════════════════════════════════════════════
# EXPORT PDF
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_view_reports
def sales_report_export_pdf(request):
    """Export PDF du rapport."""
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa

    period_type = request.GET.get('period', 'month')
    custom_start_str = request.GET.get('start', '')
    custom_end_str = request.GET.get('end', '')

    custom_start = custom_end = None
    if custom_start_str:
        try:
            custom_start = datetime.strptime(custom_start_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if custom_end_str:
        try:
            custom_end = datetime.strptime(custom_end_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    start_date, end_date = get_period_range(period_type, custom_start=custom_start, custom_end=custom_end)
    prev_start, prev_end = get_previous_period(period_type, start_date, end_date)

    data = aggregate_report(start_date, end_date)
    previous = aggregate_report(prev_start, prev_end)

    def calc_delta(curr, prev):
        if prev == 0:
            return None
        return round(float((curr - prev) / prev * 100), 1)

    comparison = {
        'revenue_delta': calc_delta(data['revenue'], previous['revenue']),
        'transactions_delta': calc_delta(data['transactions_count'], previous['transactions_count']),
        'avg_basket_delta': calc_delta(data['avg_basket'], previous['avg_basket']),
        'margin_delta': calc_delta(data['margin_total'], previous['margin_total']),
    }

    html = render_to_string('sales/sales_report_pdf.html', {
        'data': data,
        'previous': previous,
        'comparison': comparison,
        'start_date': start_date,
        'end_date': end_date,
        'prev_start': prev_start,
        'prev_end': prev_end,
        'today': timezone.now(),
    })

    result = BytesIO()
    pdf_status = pisa.CreatePDF(src=html, dest=result, encoding='utf-8')

    if pdf_status.err:
        messages.error(request, "Erreur lors de la génération du PDF.")
        return redirect('sales:sales_report')

    pdf_bytes = result.getvalue()
    result.close()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'inline; filename="rapport_{start_date}_{end_date}.pdf"'
    )
    return response