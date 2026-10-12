from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction as db_transaction
from django.db.models import Sum, F, Q, Count, Prefetch
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator

from accounts.permissions import can_manage_inventory
from .models import (
    Medicine, Batch, Supplier, PurchaseOrder, PurchaseOrderItem
)


# ═══════════════════════════════════════════════════════════════════
# DASHBOARD RÉAPPRO
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_manage_inventory
def reorder_dashboard(request):
    """Vue d'ensemble des besoins de réapprovisionnement."""
    # Médicaments à commander (stock <= reorder_level, actifs)
    medicines_to_reorder = []
    for med in Medicine.objects.filter(is_active=True).select_related('supplier', 'category'):
        if med.needs_reorder:
            medicines_to_reorder.append(med)

    # Total valeur estimée
    total_value = sum(
        (m.reorder_quantity * m.purchase_price for m in medicines_to_reorder),
        Decimal('0.00')
    )

    # Regrouper par fournisseur
    per_supplier = {}
    for med in medicines_to_reorder:
        supplier = med.supplier
        key = supplier.id if supplier else 0
        if key not in per_supplier:
            per_supplier[key] = {
                'supplier': supplier,
                'medicines': [],
                'total_value': Decimal('0.00'),
                'count': 0,
            }
        per_supplier[key]['medicines'].append(med)
        per_supplier[key]['total_value'] += med.reorder_quantity * med.purchase_price
        per_supplier[key]['count'] += 1

    suppliers_summary = sorted(
        per_supplier.values(),
        key=lambda x: x['total_value'],
        reverse=True
    )

    # Bons en cours
    open_orders = PurchaseOrder.objects.filter(
        status__in=['draft', 'sent', 'partial']
    ).select_related('supplier').order_by('-order_date')[:10]

    context = {
        'medicines_count': len(medicines_to_reorder),
        'total_value': total_value,
        'suppliers_summary': suppliers_summary,
        'suppliers_count': len([s for s in per_supplier.values() if s['supplier']]),
        'open_orders': open_orders,
    }
    return render(request, 'inventory/reorder_dashboard.html', context)


# ═══════════════════════════════════════════════════════════════════
# SUGGESTIONS DE COMMANDE
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_manage_inventory
def reorder_suggestions(request):
    """
    Affiche les suggestions de commande groupées par fournisseur.
    Le pharmacien peut cocher les produits et générer un bon.
    """
    supplier_id = request.GET.get('supplier', '')

    medicines_to_reorder = [
        m for m in Medicine.objects.filter(is_active=True).select_related('supplier', 'category')
        if m.needs_reorder
    ]

    if supplier_id:
        medicines_to_reorder = [
            m for m in medicines_to_reorder
            if m.supplier and str(m.supplier.id) == supplier_id
        ]

    # Grouper par fournisseur
    groups = {}
    for med in medicines_to_reorder:
        supplier = med.supplier
        key = supplier.id if supplier else 0
        if key not in groups:
            groups[key] = {
                'supplier': supplier,
                'medicines': [],
            }
        groups[key]['medicines'].append({
            'medicine': med,
            'stock': med.total_stock,
            'reorder_level': med.reorder_level,
            'recommended_stock': med.recommended_stock,
            'suggested_qty': med.reorder_quantity,
            'unit_price': med.purchase_price,
        })

    groups_list = list(groups.values())

    context = {
        'groups': groups_list,
        'suppliers': Supplier.objects.order_by('name'),
        'supplier_filter': supplier_id,
    }
    return render(request, 'inventory/reorder_suggestions.html', context)


@login_required
@can_manage_inventory
def reorder_create_from_suggestions(request):
    """
    Crée un ou plusieurs bons de commande depuis les suggestions.
    POST : récupère les items cochés et crée un bon par fournisseur.
    """
    if request.method != 'POST':
        return redirect('inventory:reorder_suggestions')

    # Structure attendue : {supplier_id: [medicine_id1, medicine_id2, ...]}
    # Chaque medicine_id est accompagné de sa quantité commandée dans un champ séparé
    # Format : item_<medicine_id>_qty, et on coche via item_<medicine_id>

    selected_medicines = request.POST.getlist('selected_medicine')
    if not selected_medicines:
        messages.warning(request, "Aucun produit sélectionné.")
        return redirect('inventory:reorder_suggestions')

    # Regrouper par fournisseur
    by_supplier = {}
    for med_id in selected_medicines:
        try:
            med = Medicine.objects.select_related('supplier').get(pk=med_id)
        except Medicine.DoesNotExist:
            continue

        qty_field = f'qty_{med_id}'
        try:
            qty = int(request.POST.get(qty_field, 0))
        except (ValueError, TypeError):
            qty = 0

        if qty <= 0:
            continue

        if not med.supplier:
            messages.warning(
                request,
                f"« {med.name} » n'a pas de fournisseur associé et sera ignoré."
            )
            continue

        if med.supplier.id not in by_supplier:
            by_supplier[med.supplier.id] = {'supplier': med.supplier, 'items': []}
        by_supplier[med.supplier.id]['items'].append({'medicine': med, 'qty': qty})

    if not by_supplier:
        messages.warning(request, "Aucune commande à créer.")
        return redirect('inventory:reorder_suggestions')

    created_orders = []
    with db_transaction.atomic():
        for supplier_id, data in by_supplier.items():
            order = PurchaseOrder.objects.create(
                order_number=PurchaseOrder.generate_order_number(),
                supplier=data['supplier'],
                order_date=timezone.now().date(),
                expected_delivery_date=timezone.now().date() + timedelta(days=7),
                status='draft',
                created_by=request.user,
            )
            for item_data in data['items']:
                PurchaseOrderItem.objects.create(
                    order=order,
                    medicine=item_data['medicine'],
                    quantity_ordered=item_data['qty'],
                    unit_price=item_data['medicine'].purchase_price,
                )
            order.refresh_totals()
            order.save(update_fields=['total_amount'])
            created_orders.append(order)

    messages.success(
        request,
        f"{len(created_orders)} bon(s) de commande créé(s)."
    )

    if len(created_orders) == 1:
        return redirect('inventory:order_detail', pk=created_orders[0].pk)
    return redirect('inventory:order_list')


# ═══════════════════════════════════════════════════════════════════
# LISTE DES BONS
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_manage_inventory
def order_list(request):
    """Liste des bons de commande."""
    qs = PurchaseOrder.objects.select_related('supplier', 'created_by')

    status_filter = request.GET.get('status', '')
    supplier_filter = request.GET.get('supplier', '')
    if status_filter:
        qs = qs.filter(status=status_filter)
    if supplier_filter:
        qs = qs.filter(supplier_id=supplier_filter)

    paginator = Paginator(qs.order_by('-order_date', '-id'), 30)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'status_choices': PurchaseOrder.STATUS_CHOICES,
        'suppliers': Supplier.objects.order_by('name'),
        'status_filter': status_filter,
        'supplier_filter': supplier_filter,
    }
    return render(request, 'inventory/order_list.html', context)


# ═══════════════════════════════════════════════════════════════════
# DÉTAIL D'UN BON
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_manage_inventory
def order_detail(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier', 'created_by'),
        pk=pk
    )
    items = order.items.select_related('medicine').order_by('medicine__name')

    context = {
        'order': order,
        'items': items,
    }
    return render(request, 'inventory/order_detail.html', context)


@login_required
@can_manage_inventory
@require_POST
def order_send(request, pk):
    """Marquer un bon comme envoyé au fournisseur."""
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if order.status == 'draft':
        order.status = 'sent'
        order.save(update_fields=['status'])
        messages.success(request, f"Bon {order.order_number} marqué comme envoyé.")
    return redirect('inventory:order_detail', pk=pk)


@login_required
@can_manage_inventory
@require_POST
def order_cancel(request, pk):
    """Annuler un bon de commande."""
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if order.status in ('received', 'cancelled'):
        messages.error(request, "Impossible d'annuler ce bon.")
    else:
        order.status = 'cancelled'
        order.save(update_fields=['status'])
        messages.warning(request, f"Bon {order.order_number} annulé.")
    return redirect('inventory:order_detail', pk=pk)


# ═══════════════════════════════════════════════════════════════════
# RÉCEPTION D'UN BON
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_manage_inventory
def order_receive(request, pk):
    """
    Formulaire de réception : saisir les quantités reçues + dates de péremption.
    Crée automatiquement les Batch correspondants.
    """
    order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier'),
        pk=pk
    )
    items = order.items.select_related('medicine').order_by('medicine__name')

    if order.status == 'cancelled':
        messages.error(request, "Ce bon a été annulé.")
        return redirect('inventory:order_detail', pk=pk)

    if order.is_fully_received:
        messages.info(request, "Ce bon est déjà entièrement reçu.")
        return redirect('inventory:order_detail', pk=pk)

    if request.method == 'POST':
        # Récupérer les données du formulaire
        received_items = []
        errors = []

        for item in items:
            if item.is_fully_received:
                continue

            qty_field = f'qty_{item.id}'
            expiry_field = f'expiry_{item.id}'
            batch_field = f'batch_{item.id}'

            try:
                qty = int(request.POST.get(qty_field, 0))
            except (ValueError, TypeError):
                qty = 0

            if qty <= 0:
                continue

            if qty > item.balance_to_receive:
                errors.append(
                    f"{item.medicine.name} : quantité reçue ({qty}) "
                    f"supérieure au solde à recevoir ({item.balance_to_receive})."
                )
                continue

            # Date de péremption obligatoire
            expiry_str = request.POST.get(expiry_field, '').strip()
            if not expiry_str:
                errors.append(f"{item.medicine.name} : date de péremption manquante.")
                continue

            try:
                from datetime import datetime as dt
                expiry_date = dt.strptime(expiry_str, '%Y-%m-%d').date()
            except ValueError:
                errors.append(f"{item.medicine.name} : date de péremption invalide.")
                continue

            batch_number = request.POST.get(batch_field, '').strip()

            received_items.append({
                'item': item,
                'qty': qty,
                'expiry_date': expiry_date,
                'batch_number': batch_number,
            })

        if errors:
            for err in errors:
                messages.error(request, err)
            return redirect('inventory:order_receive', pk=pk)

        if not received_items:
            messages.warning(request, "Aucune quantité saisie.")
            return redirect('inventory:order_receive', pk=pk)

        # Traitement atomique
        with db_transaction.atomic():
            for data in received_items:
                item = data['item']

                # Créer le Batch (entrée de stock)
                Batch.objects.create(
                    medicine=item.medicine,
                    batch_number=data['batch_number'],  # auto si vide
                    quantity=data['qty'],
                    expiry_date=data['expiry_date'],
                    received_date=timezone.now().date(),
                )

                # Mettre à jour la ligne
                item.quantity_received += data['qty']
                item.save(update_fields=['quantity_received'])

            # Mettre à jour le statut du bon
            order.refresh_status()
            order.save(update_fields=['status', 'received_date'])

        messages.success(
            request,
            f"Réception enregistrée : {sum(d['qty'] for d in received_items)} unités. "
            f"Statut du bon : {order.get_status_display()}."
        )
        return redirect('inventory:order_detail', pk=pk)

    context = {
        'order': order,
        'items': items,
        'today': timezone.now().date(),
    }
    return render(request, 'inventory/order_receive.html', context)


# ═══════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════
@login_required
@can_manage_inventory
def order_pdf(request, pk):
    """Génère le PDF du bon de commande."""
    from io import BytesIO
    from django.template.loader import render_to_string
    from django.http import HttpResponse
    from xhtml2pdf import pisa

    order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier', 'created_by'),
        pk=pk
    )
    items = order.items.select_related('medicine').order_by('medicine__name')

    html = render_to_string('inventory/order_pdf.html', {
        'order': order,
        'items': items,
        'today': timezone.now(),
    })

    result = BytesIO()
    pdf_status = pisa.CreatePDF(src=html, dest=result, encoding='utf-8')

    if pdf_status.err:
        messages.error(request, "Erreur lors de la génération du PDF.")
        return redirect('inventory:order_detail', pk=pk)

    pdf_bytes = result.getvalue()
    result.close()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'inline; filename="{order.order_number}.pdf"'
    )
    return response


@login_required
@can_manage_inventory
def order_excel(request, pk):
    """Génère l'Excel du bon de commande."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter
    from django.http import HttpResponse

    order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier'),
        pk=pk
    )
    items = order.items.select_related('medicine').order_by('medicine__name')

    wb = Workbook()
    ws = wb.active
    ws.title = "Commande"

    title_font = Font(size=16, bold=True, color="FFFFFF")
    header_font = Font(bold=True, color="FFFFFF")
    total_font = Font(bold=True, size=12)
    title_fill = PatternFill("solid", fgColor="0D6EFD")
    header_fill = PatternFill("solid", fgColor="495057")
    total_fill = PatternFill("solid", fgColor="D1E7DD")

    border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    ws.merge_cells('A1:F2')
    ws['A1'] = f"BON DE COMMANDE {order.order_number}"
    ws['A1'].font = title_font
    ws['A1'].fill = title_fill
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')

    ws['A4'] = "Fournisseur :"; ws['B4'] = order.supplier.name
    ws['A5'] = "Contact :";     ws['B5'] = order.supplier.contact_person or "—"
    ws['A6'] = "Téléphone :";   ws['B6'] = order.supplier.phone or "—"

    ws['D4'] = "N° commande :"; ws['E4'] = order.order_number
    ws['D5'] = "Date :";        ws['E5'] = order.order_date.strftime('%d/%m/%Y')
    ws['D6'] = "Livraison prévue :"
    ws['E6'] = order.expected_delivery_date.strftime('%d/%m/%Y') if order.expected_delivery_date else "—"

    for cell in ['A4', 'A5', 'A6', 'D4', 'D5', 'D6']:
        ws[cell].font = Font(bold=True)

    header_row = 8
    headers = ["Médicament", "Code-barres", "Qté commandée", "Prix unitaire", "Sous-total"]

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border

    for i, item in enumerate(items, start=header_row + 1):
        ws.cell(row=i, column=1, value=item.medicine.name).border = border
        ws.cell(row=i, column=2, value=item.medicine.barcode or "—").border = border

        qty_cell = ws.cell(row=i, column=3, value=item.quantity_ordered)
        qty_cell.alignment = Alignment(horizontal='center')
        qty_cell.border = border

        price_cell = ws.cell(row=i, column=4, value=float(item.unit_price))
        price_cell.number_format = '#,##0.00'
        price_cell.border = border

        sub_cell = ws.cell(row=i, column=5, value=float(item.subtotal))
        sub_cell.number_format = '#,##0.00'
        sub_cell.border = border

    total_row = header_row + len(items) + 2
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
    ws.cell(row=total_row, column=1, value="TOTAL").font = total_font
    ws.cell(row=total_row, column=1).alignment = Alignment(horizontal='right')
    ws.cell(row=total_row, column=1).fill = total_fill
    ws.cell(row=total_row, column=1).border = border

    total_cell = ws.cell(row=total_row, column=4, value=float(order.total_amount))
    total_cell.font = total_font
    total_cell.number_format = '#,##0.00'
    total_cell.fill = total_fill
    total_cell.border = border

    ws.cell(row=total_row, column=5).fill = total_fill
    ws.cell(row=total_row, column=5).border = border

    widths = [40, 15, 15, 15, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = (
        f'attachment; filename="{order.order_number}.xlsx"'
    )
    wb.save(response)
    return response