from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.conf import settings
from decimal import Decimal

class Category(models.Model):
    name = models.CharField("Nom", max_length=100, unique=True)
    description = models.TextField("Description", blank=True)

    class Meta:
        verbose_name = "Catégorie"
        verbose_name_plural = "Catégories"
        ordering = ['name']

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField("Nom", max_length=200)
    contact_person = models.CharField("Personne de contact", max_length=100, blank=True)
    phone = models.CharField("Téléphone", max_length=20)
    email = models.EmailField("Email", blank=True)
    address = models.TextField("Adresse", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Fournisseur"
        verbose_name_plural = "Fournisseurs"
        ordering = ['name']

    def __str__(self):
        return self.name


class Medicine(models.Model):
    # ─── Informations de base ─────────────────────────────────────
    name = models.CharField("Nom commercial", max_length=200)
    generic_name = models.CharField("Nom générique", max_length=200, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        related_name='medicines', verbose_name="Catégorie"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='medicines', verbose_name="Fournisseur"
    )
    barcode = models.CharField("Code-barres", max_length=50, unique=True, blank=True, null=True)
    description = models.TextField("Description", blank=True)
    image = models.ImageField("Image", upload_to='medicines/', blank=True, null=True)

    # ─── Prix ─────────────────────────────────────────────────────
    purchase_price = models.DecimalField(
        "Prix d'achat", max_digits=10, decimal_places=2, default=0,
        help_text="Prix payé au fournisseur. Sert à calculer la marge."
    )
    cash_price = models.DecimalField(
        "Prix comptant", max_digits=10, decimal_places=2,
        help_text="Prix de vente au comptoir (espèces, carte, mobile)."
    )
    insurance_price = models.DecimalField(
        "Prix assurance", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Laisser vide pour calcul automatique : prix comptant × 1.15."
    )

    # ─── Gestion du stock ─────────────────────────────────────────
    reorder_level = models.PositiveIntegerField(
        "Seuil d'alerte", default=10,
        help_text="En dessous de ce niveau, une alerte est déclenchée."
    )
    recommended_stock = models.PositiveIntegerField(
        "Stock recommandé", default=100,
        help_text="Niveau cible. Quantité à commander = recommandé − stock actuel."
    )

    # ─── Divers ───────────────────────────────────────────────────
    requires_prescription = models.BooleanField("Ordonnance requise", default=False)
    is_active = models.BooleanField("Actif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Promotion
    on_promotion = models.BooleanField("En promotion", default=False)
    promotion_discount_pct = models.DecimalField(
        "Remise promotion (%)", max_digits=5, decimal_places=2, default=0,
        help_text="Pourcentage de remise appliqué au prix comptant (0-100)."
    )

    @property
    def promotion_price(self):
        """Prix comptant avec promotion appliquée."""
        if not self.on_promotion or self.promotion_discount_pct <= 0:
            return self.cash_price
        discount = self.cash_price * (self.promotion_discount_pct / 100)
        return (self.cash_price - discount).quantize(Decimal('0.01'))

    insurance_eligible = models.BooleanField(
        "Éligible assurance", default=True,
        help_text="Décochez pour les produits non remboursables."
    )

    # Taux de marge appliqué au prix assurance (15 %)
    INSURANCE_MARKUP = Decimal('0.15')

    class Meta:
        verbose_name = "Médicament"
        verbose_name_plural = "Médicaments"
        ordering = ['name']

    # ─── Calcul auto du prix assurance ────────────────────────────
    def save(self, *args, **kwargs):
        """Calcule insurance_price si non fourni."""
        if self.insurance_price is None or self.insurance_price == 0:
            if self.cash_price:
                self.insurance_price = (
                    self.cash_price * (1 + self.INSURANCE_MARKUP)
                ).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    # ─── Propriétés métier ────────────────────────────────────────
    @property
    def total_stock(self):
        return sum(b.quantity for b in self.batches.all())

    @property
    def is_low_stock(self):
        """Alerte si on est sous le seuil minimum."""
        return self.total_stock <= self.reorder_level

    @property
    def needs_reorder(self):
        """Vrai s'il faut réapprovisionner (sous le seuil)."""
        return self.total_stock <= self.reorder_level

    @property
    def reorder_quantity(self):
        """Quantité à commander pour revenir au niveau recommandé."""
        if not self.needs_reorder:
            return 0
        return max(0, self.recommended_stock - self.total_stock)

    @property
    def gross_margin(self):
        """Marge brute unitaire (comptant)."""
        return self.cash_price - self.purchase_price

    @property
    def gross_margin_percent(self):
        """Marge brute en % du prix d'achat."""
        if not self.purchase_price:
            return Decimal('0')
        return (
            (self.cash_price - self.purchase_price) / self.purchase_price * 100
        ).quantize(Decimal('0.01'))

    def __str__(self):
        return self.name


class Batch(models.Model):
    """
    Entrée de stock d'un médicament.

    Un « lot » représente une quantité reçue à une date donnée,
    avec sa propre date de péremption. Le numéro de lot est optionnel :
    s'il n'est pas fourni, un identifiant interne est généré
    automatiquement pour traçabilité.
    """
    medicine = models.ForeignKey(
        Medicine, on_delete=models.CASCADE, related_name='batches',
        verbose_name="Médicament"
    )
    batch_number = models.CharField(
        "N° de lot", max_length=50, blank=True,
        help_text="Optionnel. Laisser vide pour génération automatique."
    )
    quantity = models.PositiveIntegerField("Quantité", default=0)
    expiry_date = models.DateField(
        "Date de péremption",
        help_text="Obligatoire — sert au calcul FEFO."
    )
    received_date = models.DateField("Date de réception", default=timezone.now)

    class Meta:
        verbose_name = "Entrée de stock"
        verbose_name_plural = "Entrées de stock"
        ordering = ['expiry_date']   # FEFO par défaut
        indexes = [
            models.Index(fields=['medicine', 'expiry_date']),
        ]

    def save(self, *args, **kwargs):
        # Auto-générer un numéro interne si absent
        if not self.batch_number:
            super().save(*args, **kwargs)   # on sauvegarde d'abord pour avoir un id
            self.batch_number = f"AUTO-{self.pk:06d}"
            return super().save(update_fields=['batch_number'])
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        return self.expiry_date < timezone.now().date()

    @property
    def days_until_expiry(self):
        if not self.expiry_date:
            return 999999  # pas de date → pas d'alerte
        return (self.expiry_date - timezone.now().date()).days

    def __str__(self):
        suffix = "" if self.batch_number.startswith("AUTO-") else f" — Lot {self.batch_number}"
        return f"{self.medicine.name} ({self.quantity} u., exp. {self.expiry_date:%d/%m/%Y}){suffix}"

class StockRemoval(models.Model):
    """
    Retrait de stock — traçabilité des pertes (périmés, endommagés, rappels).
    """
    REASON_CHOICES = [
        ('expired', "Périmé"),
        ('damaged', "Endommagé / cassé"),
        ('recall', "Rappel fournisseur"),
        ('return', "Retour fournisseur"),
        ('loss', "Perte / vol"),
        ('other', "Autre"),
    ]

    batch = models.ForeignKey(
        Batch, on_delete=models.PROTECT, related_name='removals',
        verbose_name="Entrée de stock"
    )
    quantity = models.PositiveIntegerField("Quantité retirée")
    reason = models.CharField(
        "Motif", max_length=20, choices=REASON_CHOICES
    )
    unit_cost = models.DecimalField(
        "Coût unitaire (achat)", max_digits=10, decimal_places=2,
        help_text="Prix d'achat au moment du retrait (figé)."
    )
    total_loss = models.DecimalField(
        "Perte totale", max_digits=12, decimal_places=2, default=0
    )
    notes = models.TextField("Notes", blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="Retiré par"
    )
    removed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Retrait de stock"
        verbose_name_plural = "Retraits de stock"
        ordering = ['-removed_at']

    def save(self, *args, **kwargs):
        self.total_loss = self.quantity * self.unit_cost
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} × {self.batch.medicine.name} — {self.get_reason_display()}"

