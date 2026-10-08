from decimal import Decimal
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, Q, F
from django.utils import timezone

from accounts.permissions import can_manage_insurance
from sales.models import Insurance, Sale
from .models import InsuranceClaim, InsurancePayment, InsuranceInvoice


@login_required
@can_manage_insurance
def dashboard(request):
    """Tableau de bord des créances assurances."""
    today = timezone.now().date()

    # ─── Stats globales ──────────────────────────────────────────
    open_claims = InsuranceClaim.objects.exclude(
        status__in=['paid', 'written_off', 'rejected']
    )

    total_outstanding = open_claims.aggregate(
        total=Sum(F('amount_claimed') - F('amount_paid'))
    )['total'] or Decimal('0.00')

    overdue_claims = open_claims.filter(due_date__lt=today)
    total_overdue = overdue_claims.aggregate(
        total=Sum(F('amount_claimed') - F('amount_paid'))
    )['total'] or Decimal('0.00')

    pending_count = open_claims.filter(status='pending').count()

    # ─── Détail par assurance ────────────────────────────────────
    per_insurance = []
    for ins in Insurance.objects.filter(is_active=True):
        claims = open_claims.filter(insurance=ins)
        outstanding = claims.aggregate(
            total=Sum(F('amount_claimed') - F('amount_paid'))
        )['total'] or Decimal('0.00')

        overdue = claims.filter(due_date__lt=today).aggregate(
            total=Sum(F('amount_claimed') - F('amount_paid'))
        )['total'] or Decimal('0.00')

        # Ancienneté max (jours depuis création de la plus vieille créance)
        oldest = claims.order_by('created_at').first()
        max_age_days = (today - oldest.created_at.date()).days if oldest else 0

        per_insurance.append({
            'insurance': ins,
            'count': claims.count(),
            'outstanding': outstanding,
            'overdue': overdue,
            'max_age_days': max_age_days,
        })

    # Trier par montant dû décroissant
    per_insurance.sort(key=lambda x: x['outstanding'], reverse=True)

    # ─── Créances les plus anciennes (top 10) ────────────────────
    oldest_claims = open_claims.select_related(
        'insurance', 'sale'
    ).order_by('created_at')[:10]

    # ─── Vieillissement (aging buckets) ──────────────────────────
    buckets = {
        '0-30': Decimal('0.00'),
        '31-60': Decimal('0.00'),
        '61-90': Decimal('0.00'),
        '90+': Decimal('0.00'),
    }
    for claim in open_claims:
        age = (today - claim.created_at.date()).days
        balance = claim.amount_claimed - claim.amount_paid
        if age <= 30:
            buckets['0-30'] += balance
        elif age <= 60:
            buckets['31-60'] += balance
        elif age <= 90:
            buckets['61-90'] += balance
        else:
            buckets['90+'] += balance

    aging_rows = [
        {'label': '0-30 jours', 'key': '0-30', 'amount': buckets['0-30'], 'class': 'aging-0-30'},
        {'label': '31-60 jours', 'key': '31-60', 'amount': buckets['31-60'], 'class': 'aging-31-60'},
        {'label': '61-90 jours', 'key': '61-90', 'amount': buckets['61-90'], 'class': 'aging-61-90'},
        {'label': '90+ jours', 'key': '90+', 'amount': buckets['90+'], 'class': 'aging-90plus'},
    ]


    context = {
        'total_outstanding': total_outstanding,
        'total_overdue': total_overdue,
        'pending_count': pending_count,
        'per_insurance': per_insurance,
        'oldest_claims': oldest_claims,
        'aging_rows': aging_rows,
    }
    return render(request, 'insurance/dashboard.html', context)


@login_required
@can_manage_insurance
def claim_list(request):
    """Liste filtrable des créances."""
    qs = InsuranceClaim.objects.select_related(
        'insurance', 'sale', 'invoice'
    ).order_by('-created_at')

    # Filtres
    status_filter = request.GET.get('status', '')
    insurance_filter = request.GET.get('insurance', '')
    search = request.GET.get('q', '').strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if insurance_filter:
        qs = qs.filter(insurance_id=insurance_filter)
    if search:
        qs = qs.filter(
            Q(patient_name__icontains=search) |
            Q(patient_id__icontains=search) |
            Q(invoice_number__icontains=search)
        )

    # Pagination simple
    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'insurances': Insurance.objects.filter(is_active=True),
        'status_choices': InsuranceClaim.STATUS_CHOICES,
        'status_filter': status_filter,
        'insurance_filter': insurance_filter,
        'search': search,
    }
    return render(request, 'insurance/claim_list.html', context)


@login_required
@can_manage_insurance
def invoice_list(request):
    """Liste des factures."""
    qs = InsuranceInvoice.objects.select_related('insurance').order_by('-invoice_date')

    status_filter = request.GET.get('status', '')
    insurance_filter = request.GET.get('insurance', '')
    if status_filter:
        qs = qs.filter(status=status_filter)
    if insurance_filter:
        qs = qs.filter(insurance_id=insurance_filter)

    from django.core.paginator import Paginator
    paginator = Paginator(qs, 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'insurances': Insurance.objects.filter(is_active=True),
        'status_choices': InsuranceInvoice.STATUS_CHOICES,
        'status_filter': status_filter,
        'insurance_filter': insurance_filter,
    }
    return render(request, 'insurance/invoice_list.html', context)

from datetime import datetime, timedelta
from django.db import transaction
from django.views.decorators.http import require_POST

@login_required
@can_manage_insurance
def invoice_new(request):
    """
    Création d'une facture groupée.
    Étape 1 (GET) : choisir assureur + période → preview des créances
    Étape 2 (POST) : confirmer → création + rattachement des créances
    """
    insurances = Insurance.objects.filter(is_active=True)
    today = timezone.now().date()

    # ─── Étape 1 : sélection ──────────────────────────────────────
    insurance_id = request.GET.get('insurance') or request.POST.get('insurance')
    period_type = request.GET.get('period_type') or request.POST.get('period_type') or 'monthly'

    # Calcul des dates par défaut selon le type de période
    if period_type == 'weekly':
        default_start = today - timedelta(days=today.weekday())  # lundi
        default_end = today
    elif period_type == 'monthly':
        default_start = today.replace(day=1)
        default_end = today
    else:
        default_start = today - timedelta(days=30)
        default_end = today

    period_start_str = request.GET.get('period_start') or request.POST.get('period_start')
    period_end_str = request.GET.get('period_end') or request.POST.get('period_end')

    try:
        period_start = (
            datetime.strptime(period_start_str, '%Y-%m-%d').date()
            if period_start_str else default_start
        )
    except ValueError:
        period_start = default_start

    try:
        period_end = (
            datetime.strptime(period_end_str, '%Y-%m-%d').date()
            if period_end_str else default_end
        )
    except ValueError:
        period_end = default_end

    insurance = None
    claims_preview = []

    if insurance_id:
        try:
            insurance = Insurance.objects.get(pk=insurance_id, is_active=True)
        except Insurance.DoesNotExist:
            messages.error(request, "Assurance introuvable.")
            return redirect('insurance:invoice_new')

        # Créances éligibles : non facturées, sur la période
        claims_preview = InsuranceClaim.objects.filter(
            insurance=insurance,
            invoice__isnull=True,
            status__in=['pending', 'submitted'],
            sale__created_at__date__gte=period_start,
            sale__created_at__date__lte=period_end,
        ).select_related('sale').order_by('sale__created_at')

    # ─── Étape 2 : création ───────────────────────────────────────
    if request.method == 'POST' and insurance and claims_preview.exists():
        selected_ids = request.POST.getlist('claim_ids')
        if not selected_ids:
            messages.error(request, "Sélectionnez au moins une créance.")
            return redirect(
                f"{request.path}?insurance={insurance_id}&period_start={period_start}&period_end={period_end}"
            )

        with transaction.atomic():
            # Générer le numéro
            invoice_number = InsuranceInvoice.generate_invoice_number(insurance)

            # Créer la facture
            invoice = InsuranceInvoice.objects.create(
                insurance=insurance,
                invoice_number=invoice_number,
                period_type=period_type,
                period_start=period_start,
                period_end=period_end,
                invoice_date=today,
                due_date=today + timedelta(days=insurance.payment_delay_days),
                status='draft',
                created_by=request.user,
            )

            # Rattacher les créances
            claims = InsuranceClaim.objects.filter(
                id__in=selected_ids,
                insurance=insurance,
                invoice__isnull=True,
            )
            claims.update(invoice=invoice, status='submitted')

            # Recalculer les totaux
            invoice.refresh_totals()
            invoice.save()

        messages.success(
            request,
            f"Facture {invoice.invoice_number} créée avec {claims.count()} créances."
        )
        return redirect('insurance:invoice_detail', pk=invoice.pk)

    # ─── Affichage (GET ou POST partiel) ─────────────────────────
    total_preview = sum(c.amount_claimed for c in claims_preview)

    context = {
        'insurances': insurances,
        'selected_insurance': insurance,
        'period_type': period_type,
        'period_start': period_start,
        'period_end': period_end,
        'claims_preview': claims_preview,
        'total_preview': total_preview,
    }
    return render(request, 'insurance/invoice_form.html', context)


@login_required
@can_manage_insurance
def invoice_detail(request, pk):
    """Détail d'une facture."""
    invoice = get_object_or_404(
        InsuranceInvoice.objects.select_related('insurance', 'created_by'),
        pk=pk
    )
    claims = invoice.claims.select_related('sale').order_by('sale__created_at')
    payments = invoice.insurance.payments.filter(claim__invoice=invoice).order_by('-payment_date')

    context = {
        'invoice': invoice,
        'claims': claims,
        'payments': payments,
    }
    return render(request, 'insurance/invoice_detail.html', context)


@login_required
@can_manage_insurance
@require_POST
def invoice_send(request, pk):
    """Marquer une facture comme envoyée."""
    invoice = get_object_or_404(InsuranceInvoice, pk=pk)
    if invoice.status == 'draft':
        invoice.status = 'sent'
        invoice.sent_date = timezone.now().date()
        invoice.save(update_fields=['status', 'sent_date'])
        messages.success(request, "Facture marquée comme envoyée.")
    return redirect('insurance:invoice_detail', pk=pk)


@login_required
@can_manage_insurance
def invoice_pdf(request, pk):
    """Génère le PDF de la facture avec xhtml2pdf."""
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa

    invoice = get_object_or_404(
        InsuranceInvoice.objects.select_related('insurance'),
        pk=pk
    )
    claims = invoice.claims.select_related('sale').order_by('sale__created_at')

    html = render_to_string('insurance/invoice_pdf.html', {
        'invoice': invoice,
        'claims': claims,
        'today': timezone.now(),
    })

    # Convertir en PDF
    result = BytesIO()
    pdf_status = pisa.CreatePDF(
        src=html,
        dest=result,
        encoding='utf-8',
        link_callback=lambda uri, rel: uri,   # ignore les fichiers statiques externes
    )

    if pdf_status.err:
        messages.error(request, "Erreur lors de la génération du PDF.")
        return redirect('insurance:invoice_detail', pk=pk)

    pdf_bytes = result.getvalue()
    result.close()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'inline; filename="{invoice.invoice_number}.pdf"'
    )
    return response

@login_required
@can_manage_insurance
def invoice_excel(request, pk):
    """Génère le fichier Excel de la facture."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter
    from django.http import HttpResponse

    invoice = get_object_or_404(
        InsuranceInvoice.objects.select_related('insurance'),
        pk=pk
    )
    claims = invoice.claims.select_related('sale').order_by('sale__created_at')

    wb = Workbook()
    ws = wb.active
    ws.title = "Facture"

    # ─── Styles ───────────────────────────────────────────────────
    title_font = Font(size=16, bold=True, color="FFFFFF")
    header_font = Font(bold=True, color="FFFFFF")
    total_font = Font(bold=True, size=12)
    title_fill = PatternFill("solid", fgColor="0D6EFD")
    header_fill = PatternFill("solid", fgColor="495057")
    total_fill = PatternFill("solid", fgColor="D1E7DD")

    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # ─── En-tête ──────────────────────────────────────────────────
    ws.merge_cells('A1:F2')
    ws['A1'] = f"FACTURE {invoice.invoice_number}"
    ws['A1'].font = title_font
    ws['A1'].fill = title_fill
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')

    # Info assurance
    ws['A4'] = "Assurance :"
    ws['B4'] = invoice.insurance.name
    ws['A5'] = "Code :"
    ws['B5'] = invoice.insurance.code
    ws['A6'] = "Période :"
    ws['B6'] = f"{invoice.period_start:%d/%m/%Y} → {invoice.period_end:%d/%m/%Y}"

    ws['D4'] = "N° facture :"
    ws['E4'] = invoice.invoice_number
    ws['D5'] = "Date :"
    ws['E5'] = invoice.invoice_date.strftime('%d/%m/%Y')
    ws['D6'] = "Échéance :"
    ws['E6'] = invoice.due_date.strftime('%d/%m/%Y') if invoice.due_date else "—"

    for cell in ['A4', 'A5', 'A6', 'D4', 'D5', 'D6']:
        ws[cell].font = Font(bold=True)

    # ─── Tableau des créances ─────────────────────────────────────
    header_row = 8
    headers = [
        "Date vente", "N° Vente", "Patient",
        "N° assuré", "Montant réclamé (FCFA)", "Statut"
    ]

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border

    # Lignes
    for i, claim in enumerate(claims, start=header_row + 1):
        ws.cell(row=i, column=1, value=claim.sale.created_at.strftime('%d/%m/%Y')).border = border
        ws.cell(row=i, column=2, value=f"#{claim.sale_id}").border = border
        ws.cell(row=i, column=3, value=claim.patient_name).border = border
        ws.cell(row=i, column=4, value=claim.patient_id or "—").border = border

        amount_cell = ws.cell(row=i, column=5, value=float(claim.amount_claimed))
        amount_cell.number_format = '#,##0.00'
        amount_cell.alignment = Alignment(horizontal='right')
        amount_cell.border = border

        ws.cell(row=i, column=6, value=claim.get_status_display()).border = border

    # ─── Total ────────────────────────────────────────────────────
    total_row = header_row + len(claims) + 2
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=4)
    ws.cell(row=total_row, column=1, value="TOTAL À PAYER").font = total_font
    ws.cell(row=total_row, column=1).alignment = Alignment(horizontal='right')
    ws.cell(row=total_row, column=1).fill = total_fill
    ws.cell(row=total_row, column=1).border = border

    total_cell = ws.cell(row=total_row, column=5, value=float(invoice.total_claimed))
    total_cell.font = total_font
    total_cell.number_format = '#,##0.00'
    total_cell.fill = total_fill
    total_cell.border = border

    ws.cell(row=total_row, column=6).fill = total_fill
    ws.cell(row=total_row, column=6).border = border

    # ─── Largeur des colonnes ─────────────────────────────────────
    widths = [12, 10, 30, 15, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ─── Réponse HTTP ─────────────────────────────────────────────
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = (
        f'attachment; filename="{invoice.invoice_number}.xlsx"'
    )
    wb.save(response)
    return response


from decimal import Decimal, InvalidOperation
from django.urls import reverse


@login_required
@can_manage_insurance
def payment_new(request):
    """
    Saisie d'un paiement assurance avec imputation FIFO.
    """
    insurances = Insurance.objects.filter(is_active=True)
    today = timezone.now().date()

    # ─── Cible ────────────────────────────────────────────────────
    target_type = request.GET.get('target_type') or request.POST.get('target_type') or 'all'
    insurance_id = request.GET.get('insurance') or request.POST.get('insurance')
    invoice_id = request.GET.get('invoice_id') or request.POST.get('invoice_id')

    insurance = None
    invoice = None
    eligible_claims = InsuranceClaim.objects.none()

    if insurance_id:
        try:
            insurance = Insurance.objects.get(pk=insurance_id, is_active=True)
        except Insurance.DoesNotExist:
            insurance = None

    if insurance and target_type == 'invoice' and invoice_id:
        try:
            invoice = InsuranceInvoice.objects.get(pk=invoice_id, insurance=insurance)
            eligible_claims = invoice.claims.exclude(
                status__in=['paid', 'rejected', 'written_off']
            ).select_related('sale').order_by('sale__created_at')
        except InsuranceInvoice.DoesNotExist:
            invoice = None

    if insurance and not invoice:
        eligible_claims = insurance.claims.exclude(
            status__in=['paid', 'rejected', 'written_off']
        ).select_related('sale').order_by('sale__created_at')

    total_outstanding = sum((c.balance_due for c in eligible_claims), Decimal('0.00'))

    # Factures ouvertes de cette assurance (pour le sélecteur)
    open_invoices = (
        InsuranceInvoice.objects
        .filter(insurance=insurance)
        .exclude(status__in=['paid', 'cancelled', 'rejected'])
        .order_by('-invoice_date') if insurance else []
    )

    # ─── POST : traitement ────────────────────────────────────────
    if request.method == 'POST' and insurance:
        try:
            amount = Decimal(str(request.POST.get('amount') or '0').replace(',', '.'))
        except (ValueError, InvalidOperation):
            amount = Decimal('0')

        if amount <= 0:
            messages.error(request, "Le montant doit être supérieur à 0.")
            return redirect(
                f"{request.path}?insurance={insurance_id}"
                f"&target_type={target_type}&invoice_id={invoice_id or ''}"
            )

        # Capper au total dû
        if amount > total_outstanding:
            messages.warning(
                request,
                f"Le montant dépasse le total dû ({total_outstanding:.0f} FCFA). "
                f"Seul le total dû sera imputé."
            )
            amount = total_outstanding

        # Champs
        payment_date_str = request.POST.get('payment_date') or today.isoformat()
        try:
            payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d').date()
        except ValueError:
            payment_date = today

        payment_method = request.POST.get('payment_method', 'transfer')
        reference = (request.POST.get('reference') or '').strip()
        if not reference:
            # Auto-génération unique
            count_today = InsurancePayment.objects.filter(
                payment_date=payment_date, insurance=insurance
            ).values('reference').distinct().count()
            reference = f"PMT-{payment_date.strftime('%Y%m%d')}-{count_today + 1:03d}"

        bank_account = (request.POST.get('bank_account') or '').strip()
        notes = (request.POST.get('notes') or '').strip()

        # Allocation FIFO
        remaining = amount
        allocations = []
        for claim in eligible_claims:
            if remaining <= 0:
                break
            balance = claim.balance_due
            if balance <= 0:
                continue
            alloc = min(balance, remaining)
            allocations.append((claim, alloc))
            remaining -= alloc

        if not allocations:
            messages.error(request, "Aucune créance éligible à imputer.")
            return redirect('insurance:payment_new')

        # Création
        with transaction.atomic():
            affected_invoices = set()
            payments_created = []

            for claim, alloc_amount in allocations:
                payment = InsurancePayment.objects.create(
                    insurance=insurance,
                    claim=claim,
                    amount=alloc_amount,
                    payment_method=payment_method,
                    reference=reference,
                    payment_date=payment_date,
                    bank_account=bank_account,
                    notes=notes,
                    recorded_by=request.user,
                )
                payments_created.append(payment)

                # Marquer la créance comme payée (le save() du modèle met déjà à jour amount_paid + status)
                claim.refresh_from_db()

                if claim.invoice_id:
                    affected_invoices.add(claim.invoice_id)

            # Rafraîchir les totaux des factures
            for inv_id in affected_invoices:
                inv = InsuranceInvoice.objects.get(pk=inv_id)
                inv.refresh_totals()
                inv.save()

        messages.success(
            request,
            f"Paiement enregistré : {amount:.0f} FCFA imputés sur "
            f"{len(payments_created)} créance(s). Référence : {reference}"
        )
        return redirect(f"{reverse('insurance:payment_list')}?ref={reference}")

    # ─── GET : affichage ──────────────────────────────────────────
    context = {
        'insurances': insurances,
        'insurance': insurance,
        'invoice': invoice,
        'open_invoices': open_invoices,
        'target_type': target_type,
        'eligible_claims': eligible_claims,
        'total_outstanding': total_outstanding,
        'today': today,
        'payment_methods': InsurancePayment.PAYMENT_METHOD_CHOICES,
    }
    return render(request, 'insurance/payment_form.html', context)


@login_required
@can_manage_insurance
def payment_list(request):
    """Historique des paiements reçus, groupés par référence."""
    from collections import OrderedDict

    qs = InsurancePayment.objects.select_related(
        'insurance', 'claim', 'recorded_by'
    )

    insurance_filter = request.GET.get('insurance', '')
    ref_filter = request.GET.get('ref', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if insurance_filter:
        qs = qs.filter(insurance_id=insurance_filter)
    if ref_filter:
        qs = qs.filter(reference=ref_filter)
    if date_from:
        qs = qs.filter(payment_date__gte=date_from)
    if date_to:
        qs = qs.filter(payment_date__lte=date_to)

    qs = qs.order_by('-payment_date', '-id')[:500]

    # Grouper par (assurance, référence, date)
    groups = OrderedDict()
    for p in qs:
        key = (p.insurance_id, p.reference, p.payment_date)
        if key not in groups:
            groups[key] = {
                'insurance': p.insurance,
                'reference': p.reference,
                'payment_date': p.payment_date,
                'payment_method': p.get_payment_method_display(),
                'bank_account': p.bank_account,
                'notes': p.notes,
                'total': Decimal('0.00'),
                'count': 0,
            }
        groups[key]['total'] += p.amount
        groups[key]['count'] += 1

    payments_grouped = list(groups.values())

    # Total général
    grand_total = sum(g['total'] for g in payments_grouped)

    context = {
        'payments_grouped': payments_grouped,
        'grand_total': grand_total,
        'insurances': Insurance.objects.filter(is_active=True),
        'insurance_filter': insurance_filter,
        'ref_filter': ref_filter,
        'date_from': date_from,
        'date_to': date_to,
    }
    return render(request, 'insurance/payment_list.html', context)


@login_required
@can_manage_insurance
def payment_detail(request, reference):
    """Détail d'un paiement (toutes les lignes avec la même référence)."""
    payments = InsurancePayment.objects.filter(
        reference=reference
    ).select_related('insurance', 'claim', 'claim__sale', 'recorded_by').order_by('id')

    if not payments.exists():
        messages.error(request, "Paiement introuvable.")
        return redirect('insurance:payment_list')

    first = payments.first()
    total = sum(p.amount for p in payments)

    context = {
        'payments': payments,
        'insurance': first.insurance,
        'reference': reference,
        'payment_date': first.payment_date,
        'payment_method': first.get_payment_method_display(),
        'bank_account': first.bank_account,
        'notes': first.notes,
        'total': total,
        'recorded_by': first.recorded_by,
    }
    return render(request, 'insurance/payment_detail.html', context)