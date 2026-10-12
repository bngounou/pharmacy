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
        """Somme du stock sur tous les lots. Force en int."""
        try:
            return sum(int(b.quantity) for b in self.batches.all())
        except (TypeError, ValueError):
            return 0

    @property
    def is_low_stock(self):
        return self.total_stock <= int(self.reorder_level or 0)

    @property
    def needs_reorder(self):
        return self.total_stock <= int(self.reorder_level or 0)

    @property
    def reorder_quantity(self):
        """Quantité à commander pour revenir au niveau recommandé."""
        if not self.needs_reorder:
            return 0
        try:
            target = int(self.recommended_stock or 0)
            current = self.total_stock
            return max(0, target - current)
        except (TypeError, ValueError):
            return 0

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

class PurchaseOrder(models.Model):
    """
    Bon de commande fournisseur.
    Généré automatiquement depuis les suggestions de réapprovisionnement
    ou créé manuellement.
    """
    STATUS_CHOICES = [
        ('draft', "Brouillon"),
        ('sent', "Envoyée au fournisseur"),
        ('partial', "Partiellement reçue"),
        ('received', "Reçue intégralement"),
        ('cancelled', "Annulée"),
    ]

    order_number = models.CharField(
        "N° de commande", max_length=50, unique=True,
        help_text="Format : CMD-AAAAMM-NNNN"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name='purchase_orders',
        verbose_name="Fournisseur"
    )
    status = models.CharField(
        "Statut", max_length=20, choices=STATUS_CHOICES, default='draft'
    )

    order_date = models.DateField("Date de commande", default=timezone.now)
    expected_delivery_date = models.DateField(
        "Livraison prévue", null=True, blank=True
    )
    received_date = models.DateField("Reçue le", null=True, blank=True)

    total_amount = models.DecimalField(
        "Montant total", max_digits=12, decimal_places=2, default=0
    )

    notes = models.TextField("Notes", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="Créée par"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bon de commande"
        verbose_name_plural = "Bons de commande"
        ordering = ['-order_date', '-id']
        indexes = [
            models.Index(fields=['status', 'supplier']),
        ]

    @property
    def items_count(self):
        return self.items.count()

    @property
    def total_ordered(self):
        return sum(item.quantity_ordered for item in self.items.all())

    @property
    def total_received(self):
        return sum(item.quantity_received for item in self.items.all())

    @property
    def is_fully_received(self):
        return all(item.is_fully_received for item in self.items.all())

    @property
    def progress_pct(self):
        if not self.total_ordered:
            return 0
        return int(self.total_received / self.total_ordered * 100)

    def refresh_totals(self):
        """Recalcule le montant total depuis les lignes."""
        total = sum(
            (item.subtotal for item in self.items.all()),
            Decimal('0.00')
        )
        self.total_amount = total

    def refresh_status(self):
        """Met à jour le statut selon les quantités reçues."""
        if self.status in ('cancelled',):
            return
        if self.items.filter(quantity_received__gt=0).exists():
            if self.is_fully_received:
                self.status = 'received'
                if not self.received_date:
                    self.received_date = timezone.now().date()
            else:
                self.status = 'partial'

    @classmethod
    def generate_order_number(cls):
        """Génère un numéro séquentiel CMD-AAAAMM-NNNN."""
        now = timezone.now()
        prefix = f"CMD-{now.year}{now.month:02d}-"

        last = cls.objects.filter(
            order_number__startswith=prefix
        ).order_by('-order_number').first()

        if last:
            try:
                last_num = int(last.order_number.split('-')[-1])
                next_num = last_num + 1
            except (ValueError, IndexError):
                next_num = 1
        else:
            next_num = 1

        return f"{prefix}{next_num:04d}"

    def __str__(self):
        return f"{self.order_number} — {self.supplier.name}"


class PurchaseOrderItem(models.Model):
    """Ligne d'un bon de commande."""
    order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='items',
        verbose_name="Bon de commande"
    )
    medicine = models.ForeignKey(
        Medicine, on_delete=models.PROTECT,
        verbose_name="Médicament"
    )
    quantity_ordered = models.PositiveIntegerField("Quantité commandée")
    quantity_received = models.PositiveIntegerField(
        "Quantité reçue", default=0
    )
    unit_price = models.DecimalField(
        "Prix unitaire d'achat", max_digits=10, decimal_places=2,
        help_text="Prix d'achat convenu avec le fournisseur."
    )
    subtotal = models.DecimalField(
        "Sous-total", max_digits=12, decimal_places=2, default=0
    )

    class Meta:
        verbose_name = "Ligne de commande"
        verbose_name_plural = "Lignes de commande"
        ordering = ['medicine__name']

    @property
    def is_fully_received(self):
        return self.quantity_received >= self.quantity_ordered

    @property
    def balance_to_receive(self):
        return max(0, self.quantity_ordered - self.quantity_received)

    def save(self, *args, **kwargs):
        self.subtotal = self.quantity_ordered * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity_ordered} × {self.medicine.name}"