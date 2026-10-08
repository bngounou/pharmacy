from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal


class InsuranceClaim(models.Model):
    """
    Créance d'une assurance envers la pharmacie.
    Créée automatiquement à chaque vente avec assurance.
    """
    STATUS_CHOICES = [
        ('pending', "En attente d'émission"),
        ('submitted', "Facture émise"),
        ('partially_paid', "Partiellement payée"),
        ('paid', "Payée"),
        ('rejected', "Rejetée"),
        ('written_off', "Passée en perte"),
    ]

    insurance = models.ForeignKey(
        'sales.Insurance', on_delete=models.PROTECT,
        related_name='claims', verbose_name="Assurance"
    )
    sale = models.OneToOneField(
        'sales.Sale', on_delete=models.PROTECT,
        related_name='insurance_claim', verbose_name="Vente"
    )
    invoice = models.ForeignKey(
        'InsuranceInvoice', on_delete=models.SET_NULL,  # ← guillemets
        null=True, blank=True, related_name='claims',
        verbose_name="Facture"
    )
    patient_name = models.CharField("Nom du patient", max_length=200)
    patient_id = models.CharField(
        "N° d'assuré", max_length=100, blank=True,
        help_text="Numéro de matricule de l'assuré."
    )

    amount_claimed = models.DecimalField(
        "Montant réclamé", max_digits=12, decimal_places=2
    )
    amount_paid = models.DecimalField(
        "Montant payé par l'assurance", max_digits=12, decimal_places=2, default=0
    )

    invoice_number = models.CharField(
        "N° de facture", max_length=50, blank=True,
        help_text="N° attribué par la pharmacie lors de l'émission."
    )
    invoice_date = models.DateField("Date d'émission", null=True, blank=True)
    due_date = models.DateField("Date d'échéance", null=True, blank=True)

    status = models.CharField(
        "Statut", max_length=20, choices=STATUS_CHOICES, default='pending'
    )
    notes = models.TextField("Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Créance assurance"
        verbose_name_plural = "Créances assurances"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['insurance', 'status']),
            models.Index(fields=['due_date', 'status']),
        ]

    @property
    def balance_due(self):
        return self.amount_claimed - self.amount_paid

    @property
    def is_fully_paid(self):
        return self.amount_paid >= self.amount_claimed

    @property
    def days_overdue(self):
        if self.due_date and self.balance_due > 0:
            delta = timezone.now().date() - self.due_date
            return max(0, delta.days)
        return 0

    def __str__(self):
        return f"Créance {self.invoice_number or self.pk} — {self.insurance.name} — {self.amount_claimed} FCFA"


class InsuranceInvoice(models.Model):
    """
    Facture groupée envoyée à une assurance.
    Regroupe plusieurs InsuranceClaim sur une période.
    """
    STATUS_CHOICES = [
        ('draft', "Brouillon"),
        ('sent', "Envoyée"),
        ('partially_paid', "Partiellement payée"),
        ('paid', "Payée"),
        ('rejected', "Rejetée"),
        ('cancelled', "Annulée"),
    ]

    PERIOD_CHOICES = [
        ('weekly', "Hebdomadaire"),
        ('monthly', "Mensuelle"),
        ('custom', "Personnalisée"),
    ]

    insurance = models.ForeignKey(
        'sales.Insurance', on_delete=models.PROTECT,
        related_name='invoices', verbose_name="Assurance"
    )
    invoice_number = models.CharField(
        "N° de facture", max_length=50, unique=True,
        help_text="Identifiant unique de la facture envoyée à l'assurance."
    )

    # Période couverte par la facture
    period_type = models.CharField(
        "Type de période", max_length=20,
        choices=PERIOD_CHOICES, default='monthly'
    )
    period_start = models.DateField("Début de période")
    period_end = models.DateField("Fin de période")

    # Montants
    total_claimed = models.DecimalField(
        "Montant réclamé", max_digits=14, decimal_places=2, default=0
    )
    total_paid = models.DecimalField(
        "Montant payé", max_digits=14, decimal_places=2, default=0
    )

    # Dates clés
    invoice_date = models.DateField(
        "Date d'émission", default=timezone.now
    )
    sent_date = models.DateField(
        "Date d'envoi", null=True, blank=True
    )
    due_date = models.DateField(
        "Date d'échéance", null=True, blank=True
    )

    status = models.CharField(
        "Statut", max_length=20, choices=STATUS_CHOICES, default='draft'
    )

    # Référence externe (n° attribué par l'assurance)
    external_reference = models.CharField(
        "Référence externe", max_length=100, blank=True,
        help_text="N° attribué par l'assurance lors de la réception."
    )

    notes = models.TextField("Notes", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name='created_invoices',
        verbose_name="Créée par"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facture assurance"
        verbose_name_plural = "Factures assurances"
        ordering = ['-invoice_date', '-id']
        indexes = [
            models.Index(fields=['insurance', 'status']),
            models.Index(fields=['due_date', 'status']),
        ]

    @property
    def balance_due(self):
        return self.total_claimed - self.total_paid

    @property
    def is_fully_paid(self):
        return self.total_paid >= self.total_claimed

    @property
    def days_overdue(self):
        if self.due_date and self.balance_due > 0:
            delta = timezone.now().date() - self.due_date
            return max(0, delta.days)
        return 0

    @property
    def claims_count(self):
        return self.claims.count()

    def refresh_totals(self):
        """Recalcule total_claimed et total_paid depuis les créances liées."""
        agg = self.claims.aggregate(
            claimed=models.Sum('amount_claimed'),
            paid=models.Sum('amount_paid'),
        )
        self.total_claimed = agg['claimed'] or Decimal('0.00')
        self.total_paid = agg['paid'] or Decimal('0.00')

        # Mise à jour du statut
        if self.status not in ('cancelled', 'rejected'):
            if self.total_paid >= self.total_claimed and self.total_claimed > 0:
                self.status = 'paid'
            elif self.total_paid > 0:
                self.status = 'partially_paid'
            # sinon on laisse en draft/sent

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} — {self.insurance.name} — {self.total_claimed:.0f} FCFA"

    @classmethod
    def generate_invoice_number(cls, insurance):
        """
        Génère un numéro de facture séquentiel.
        Format : <CODE>-<ANNEE>-<NNNN>
        Exemple : CNPS-2026-0042
        """
        year = timezone.now().year
        prefix = f"{insurance.code}-{year}-"

        last = cls.objects.filter(
            invoice_number__startswith=prefix
        ).order_by('-invoice_number').first()

        if last:
            try:
                last_num = int(last.invoice_number.split('-')[-1])
                next_num = last_num + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1

        return f"{prefix}{next_num:04d}"

class InsurancePayment(models.Model):
    """
    Paiement reçu d'une assurance (virement, chèque...).
    N'entre PAS dans la caisse quotidienne : c'est un flux bancaire.
    """
    PAYMENT_METHOD_CHOICES = [
        ('transfer', "Virement bancaire"),
        ('check', "Chèque"),
        ('cash_deposit', "Dépôt espèces"),
        ('other', "Autre"),
    ]

    insurance = models.ForeignKey(
        'sales.Insurance', on_delete=models.PROTECT,
        related_name='payments', verbose_name="Assurance"
    )
    claim = models.ForeignKey(
        InsuranceClaim, on_delete=models.PROTECT,
        related_name='payments', verbose_name="Créance",
        null=True, blank=True,
        help_text="Laisser vide pour un paiement global à imputer plus tard."
    )
    amount = models.DecimalField("Montant", max_digits=12, decimal_places=2)
    payment_method = models.CharField(
        "Mode", max_length=20, choices=PAYMENT_METHOD_CHOICES, default='transfer'
    )
    reference = models.CharField(
        "Référence", max_length=100, blank=True,
        help_text="N° de virement, chèque, etc."
    )
    payment_date = models.DateField("Date de paiement", default=timezone.now)
    bank_account = models.CharField(
        "Compte crédité", max_length=100, blank=True
    )
    notes = models.TextField("Notes", blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="Enregistré par"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Paiement assurance"
        verbose_name_plural = "Paiements assurances"
        ordering = ['-payment_date']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Mettre à jour la créance liée
        if self.claim:
            total_paid = self.claim.payments.aggregate(
                total=models.Sum('amount')
            )['total'] or Decimal('0')
            self.claim.amount_paid = total_paid
            if self.claim.amount_paid >= self.claim.amount_claimed:
                self.claim.status = 'paid'
            elif self.claim.amount_paid > 0:
                self.claim.status = 'partially_paid'
            self.claim.save(update_fields=['amount_paid', 'status', 'updated_at'])

    def __str__(self):
        return f"Paiement {self.insurance.name} — {self.amount} FCFA ({self.payment_date})"

    # =======================
