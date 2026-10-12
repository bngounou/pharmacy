from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Sum

from inventory.models import Medicine, Batch
from sales.models import Sale


@login_required
def dashboard(request):
    today = timezone.now().date()

    # ─── Statistiques existantes ──────────────────────────────────
    total_medicines = Medicine.objects.filter(is_active=True).count()
    low_stock_count = sum(
        1 for m in Medicine.objects.filter(is_active=True) if m.is_low_stock
    )
    expired_batches = Batch.objects.filter(
        expiry_date__lt=today, quantity__gt=0
    ).count()
    expiring_soon = Batch.objects.filter(
        expiry_date__gte=today,
        expiry_date__lte=today + timezone.timedelta(days=30),
        quantity__gt=0,
    ).count()

    today_sales = Sale.objects.filter(created_at__date=today)
    today_revenue = today_sales.aggregate(total=Sum('total_amount'))['total'] or 0
    today_transactions = today_sales.count()

    # ─── NOUVEAU : Alertes péremption ────────────────────────────
    expiring_30 = Batch.objects.filter(
        quantity__gt=0,
        expiry_date__gte=today,
        expiry_date__lte=today + timezone.timedelta(days=30),
    ).count()

    expired_count = Batch.objects.filter(
        quantity__gt=0,
        expiry_date__lt=today,
    ).count()

    # ─── CONTEXTE (tout en dernier) ──────────────────────────────
    context = {
        'total_medicines': total_medicines,
        'low_stock_count': low_stock_count,
        'expired_batches': expired_batches,
        'expiring_soon': expiring_soon,
        'today_revenue': today_revenue,
        'today_transactions': today_transactions,
        'recent_sales': Sale.objects.select_related('cashier')[:5],
        'low_stock_items': [
            m for m in Medicine.objects.filter(is_active=True) if m.is_low_stock
        ][:5],
        # Nouveaux champs
        'expiring_30_count': expiring_30,
        'expired_count': expired_count,

    }
    return render(request, 'dashboard.html', context)