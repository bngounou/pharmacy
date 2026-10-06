from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_CHOICES = [
        ('admin', 'Administrateur'),
        ('pharmacist', 'Pharmacien'),
        ('cashier', 'Caissier'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    phone = models.CharField("Téléphone", max_length=20, blank=True)

    # ─── Permissions granulaires ──────────────────────────────────
    can_access_cash_closure = models.BooleanField(
        "Peut clôturer la caisse", default=False,
        help_text="Autorise l'accès à l'écran de clôture de caisse. "
                  "À réserver aux responsables."
    )
    can_manage_insurance = models.BooleanField(
        "Peut gérer les assurances", default=False,
        help_text="Autorise la gestion des créances et paiements d'assurance."
    )
    can_view_reports = models.BooleanField(
        "Peut voir les rapports", default=False,
        help_text="Autorise l'accès aux rapports de vente et de caisse."
    )

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == 'admin' or self.is_superuser

    @property
    def is_pharmacist(self):
        return self.role == 'pharmacist'

    @property
    def is_cashier(self):
        return self.role == 'cashier'