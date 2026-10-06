from django.db import models
from django.conf import settings
from inventory.models import Medicine, Batch
from django.utils import timezone
from decimal import Decimal
from django.db.models import Sum, Q

class Insurance(models.Model):
    """Compagnie d'assurance partenaire."""
    name = models.CharField("Nom", max_length=100, unique=True)
    code = models.CharField(
        "Code court", max_length=20, unique=True,
        help_text="Identifiant court, ex: CNPS, MUT-SANTE"
    )
    coverage_rate = models.DecimalField(
        "Taux de couverture", max_digits=5, decimal_places=4,
        default=Decimal('0.8000'),
        help_text="Entre 0 et 1. Ex: 0.8000 = 80% pris en charge par l'assurance."
    )
    payment_delay_days = models.PositiveIntegerField(
        "Délai de paiement (jours)", default=90,
        help_text="Délai moyen avant que l'assurance nous rembourse."
    )
    contact_person = models.CharField("Contact", max_length=100, blank=True)
    phone = models.CharField("Téléphone", max_length=20, blank=True)
    email = models.EmailField("Email", blank=True)
    is_active = models.BooleanField("Active", default=True)
    notes = models.TextField("Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Assurance"
        verbose_name_plural = "Assurances"
        ordering = ['name']

    def __str__(self):
        pct = self.coverage_rate * 100
        return f"{self.name} ({pct:.0f}%)"


class Sale(models.Model):
    PAYMENT_CHOICES = [
        ('cash', 'Espèces'),
        ('card', 'Carte bancaire'),
        ('orange_money', 'Orange Money'),
        ('mtn_momo', 'MTN Mobile Money'),
    ]
    STATUS_CHOICES = [
        ('pending', 'En attente de livraison'),
        ('completed', 'Terminée'),
        ('partial', 'Partiellement livrée'),
        ('cancelled', 'Annulée'),
        ('refunded', 'Remboursée'),
    ]
    TYPE_CHOICES = [
        ('counter', 'Vente comptoir'),
        ('preorder', 'Précommande'),
    ]

    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='sales', verbose_name="Caissier"
    )
    customer_name = models.CharField("Nom du client", max_length=200, blank=True)
    customer_phone = models.CharField("Téléphone du client", max_length=20, blank=True)
    patient_id = models.CharField(
        "N° d'assuré (matricule)", max_length=100, blank=True,
        help_text="Numéro attribué par l'assurance. Obligatoire pour une vente avec assurance."
    )

    sale_type = models.CharField(
        "Type de vente", max_length=20,
        choices=TYPE_CHOICES, default='counter'
    )
    payment_method = models.CharField(
        "Mode de paiement patient", max_length=20,
        choices=PAYMENT_CHOICES, default='cash',
        help_text="Moyen utilisé par le patient pour payer sa part."
    )
    status = models.CharField(
        "Statut", max_length=20, choices=STATUS_CHOICES, default='completed'
    )

    # ─── Assurance (optionnel) ────────────────────────────────────
    insurance = models.ForeignKey(
        Insurance, on_delete=models.PROTECT,
        null=True, blank=True, related_name='sales',
        verbose_name="Assurance"
    )
    insurance_coverage_rate = models.DecimalField(
        "Taux appliqué", max_digits=5, decimal_places=4,
        null=True, blank=True,
        help_text="Copie du taux au moment de la vente (pour historique)."
    )
    patient_share = models.DecimalField(
        "Part patient", max_digits=12, decimal_places=2, default=0
    )
    insurance_share = models.DecimalField(
        "Part assurance", max_digits=12, decimal_places=2, default=0
    )

    # ─── Montants globaux ─────────────────────────────────────────
    total_amount = models.DecimalField(
        "Montant total", max_digits=12, decimal_places=2, default=0
    )
    amount_paid = models.DecimalField(
        "Montant payé par le patient", max_digits=12, decimal_places=2, default=0
    )

    # ─── Livraison (précommande) ──────────────────────────────────
    expected_delivery_date = models.DateField(
        "Date de livraison prévue", null=True, blank=True
    )
    delivered_at = models.DateTimeField("Livrée le", null=True, blank=True)
    notes = models.TextField("Notes", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vente"
        verbose_name_plural = "Ventes"
        ordering = ['-created_at']

    # ─── Propriétés ───────────────────────────────────────────────
    @property
    def has_insurance(self):
        return self.insurance_id is not None

    @property
    def balance_due(self):
        """Reste à payer par le patient (part patient - ce qu'il a payé)."""
        return self.patient_share - self.amount_paid

    @property
    def is_fully_paid(self):
        return self.amount_paid >= self.patient_share

    @property
    def has_backorder(self):
        return self.items.filter(is_backorder=True).exists()

    def __str__(self):
        suffix = f" [Assurance: {self.insurance.name}]" if self.has_insurance else ""
        return f"Vente #{self.pk} — {self.get_sale_type_display()}{suffix} — {self.created_at:%d/%m/%Y}"


class SaleItem(models.Model):
    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name='items',
        verbose_name="Vente"
    )
    medicine = models.ForeignKey(
        Medicine, on_delete=models.PROTECT,
        verbose_name="Médicament"
    )
    batch = models.ForeignKey(
        Batch, on_delete=models.PROTECT, null=True, blank=True,
        verbose_name="Lot"
    )
    quantity = models.PositiveIntegerField("Quantité")

    # ← Nouveaux champs pour la gestion des backorders
    quantity_in_stock = models.PositiveIntegerField(
        "Quantité livrée du stock", default=0,
        help_text="Partie effectivement déduite du stock"
    )
    quantity_backorder = models.PositiveIntegerField(
        "Quantité en précommande", default=0,
        help_text="Partie à livrer plus tard"
    )
    is_backorder = models.BooleanField(
        "En attente de livraison", default=False,
        help_text="Vrai si toute ou partie de la quantité n'était pas en stock"
    )
    delivered = models.BooleanField(
        "Livré", default=True,
        help_text="Passe à True quand la précommande est honorée"
    )

    unit_price = models.DecimalField("Prix unitaire", max_digits=10, decimal_places=2)
    subtotal = models.DecimalField("Sous-total", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "Ligne de vente"
        verbose_name_plural = "Lignes de vente"

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} × {self.medicine.name}"

class CashMovement(models.Model):
    """
    Journal de caisse — UNIQUEMENT les flux physiques d'argent.
    Un paiement d'assurance par virement ne passe PAS ici : voir InsurancePayment.
    """
    TYPE_CHOICES = [
        ('sale_patient', "Encaissement patient"),
        ('refund', "Remboursement"),
        ('purchase', "Achat fournisseur"),
        ('expense', "Dépense"),
        ('cash_in', "Entrée de caisse manuelle"),
        ('cash_out', "Sortie de caisse manuelle"),
    ]

    movement_type = models.CharField(
        "Type", max_length=30, choices=TYPE_CHOICES, db_index=True
    )
    amount = models.DecimalField(
        "Montant", max_digits=12, decimal_places=2,
        help_text="Toujours positif."
    )
    payment_method = models.CharField(
        "Mode de paiement", max_length=20,
        choices=Sale.PAYMENT_CHOICES,
        help_text="Espèces, OM, MoMo, carte — le canal physique."
    )

    # Lien vers la vente source
    sale = models.ForeignKey(
        Sale, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cash_movements',
        verbose_name="Vente liée"
    )

    description = models.CharField("Description", max_length=255, blank=True)
    reference = models.CharField(
        "Référence", max_length=50, blank=True, db_index=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="Créé par"
    )
    date = models.DateTimeField("Date", default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mouvement de caisse"
        verbose_name_plural = "Mouvements de caisse"
        ordering = ['-date']
        indexes = [
            models.Index(fields=['date', 'movement_type']),
            models.Index(fields=['date', 'payment_method']),
        ]

    @property
    def is_inflow(self):
        return self.movement_type in ('sale_patient', 'cash_in')

    @property
    def is_outflow(self):
        return self.movement_type in ('refund', 'purchase', 'expense', 'cash_out')

    @property
    def signed_amount(self):
        return self.amount if self.is_inflow else -self.amount

    def __str__(self):
        return f"{self.get_movement_type_display()} — {self.amount} FCFA ({self.date:%d/%m/%Y})"

class CashClosure(models.Model):
    """
    Clôture de caisse d'un caissier pour une période donnée.
    Peut être faite plusieurs fois par jour (shifts).
    """
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='cash_closures', verbose_name="Caissier"
    )
    closure_date = models.DateField("Date", db_index=True)
    period_start = models.DateTimeField("Début de période")
    period_end = models.DateTimeField("Fin de période")

    # Théorique
    theoretical_cash = models.DecimalField(
        "Espèces (théorique)", max_digits=12, decimal_places=2, default=0
    )
    theoretical_om = models.DecimalField(
        "Orange Money (théorique)", max_digits=12, decimal_places=2, default=0
    )
    theoretical_momo = models.DecimalField(
        "MTN MoMo (théorique)", max_digits=12, decimal_places=2, default=0
    )
    theoretical_card = models.DecimalField(
        "Carte (théorique)", max_digits=12, decimal_places=2, default=0
    )
    theoretical_total = models.DecimalField(
        "Total théorique", max_digits=12, decimal_places=2, default=0
    )

    # Compté
    counted_cash = models.DecimalField(
        "Espèces (compté)", max_digits=12, decimal_places=2, default=0
    )
    counted_om = models.DecimalField(
        "Orange Money (compté)", max_digits=12, decimal_places=2, default=0
    )
    counted_momo = models.DecimalField(
        "MTN MoMo (compté)", max_digits=12, decimal_places=2, default=0
    )
    counted_card = models.DecimalField(
        "Carte (compté)", max_digits=12, decimal_places=2, default=0
    )
    counted_total = models.DecimalField(
        "Total compté", max_digits=12, decimal_places=2, default=0
    )

    # Écart
    discrepancy = models.DecimalField(
        "Écart", max_digits=12, decimal_places=2, default=0,
        help_text="Total compté − total théorique. Positif = excédent, négatif = manquant."
    )

    # Validation par un responsable (facultatif)
    is_validated = models.BooleanField("Validée", default=False)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='validated_closures',
        verbose_name="Validée par"
    )
    validated_at = models.DateTimeField("Validée le", null=True, blank=True)

    notes = models.TextField("Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Clôture de caisse"
        verbose_name_plural = "Clôtures de caisse"
        ordering = ['-period_end']
        indexes = [
            models.Index(fields=['closure_date', 'cashier']),
        ]

    @property
    def has_discrepancy(self):
        return self.discrepancy != 0

    @property
    def is_balanced(self):
        return self.discrepancy == 0

    def compute_totals(self):
        """Calcule et met à jour les totaux théoriques depuis les CashMovement."""
        movements = CashMovement.objects.filter(
            created_by=self.cashier,
            date__gte=self.period_start,
            date__lte=self.period_end,
        )

        def total_for(method, types):
            result = movements.filter(
                payment_method=method,
                movement_type__in=types,
            ).aggregate(
                inflow=Sum('amount', filter=Q(movement_type__in=['sale_patient', 'cash_in'])),
                outflow=Sum('amount', filter=Q(movement_type__in=['refund', 'purchase', 'expense', 'cash_out'])),
            )
            inflow = result['inflow'] or Decimal('0.00')
            outflow = result['outflow'] or Decimal('0.00')
            return inflow - outflow

        self.theoretical_cash = total_for('cash', list(dict(CashMovement.TYPE_CHOICES).keys()))
        self.theoretical_om = total_for('orange_money', list(dict(CashMovement.TYPE_CHOICES).keys()))
        self.theoretical_momo = total_for('mtn_momo', list(dict(CashMovement.TYPE_CHOICES).keys()))
        self.theoretical_card = total_for('card', list(dict(CashMovement.TYPE_CHOICES).keys()))
        self.theoretical_total = (
            self.theoretical_cash + self.theoretical_om
            + self.theoretical_momo + self.theoretical_card
        )

    def compute_counted_total(self):
        """Met à jour le total compté à partir des 4 modes."""
        self.counted_total = (
            self.counted_cash + self.counted_om
            + self.counted_momo + self.counted_card
        )

    def compute_discrepancy(self):
        """Calcule l'écart = compté − théorique."""
        self.discrepancy = self.counted_total - self.theoretical_total

    def save(self, *args, **kwargs):
        """Recalcule les totaux dérivés avant sauvegarde."""
        self.compute_counted_total()
        self.compute_discrepancy()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"Clôture {self.cashier.username} — "
            f"{self.period_end:%d/%m/%Y %H:%M} — "
            f"{self.counted_total:.0f} FCFA"
        )