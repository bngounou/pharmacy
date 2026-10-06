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