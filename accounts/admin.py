from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        'username', 'email', 'role', 'phone',
        'can_access_cash_closure', 'can_manage_insurance',
        'can_view_reports', 'is_staff', 'is_active'
    )
    list_filter = (
        'role', 'is_staff', 'is_active',
        'can_access_cash_closure', 'can_manage_insurance', 'can_view_reports'
    )
    search_fields = ('username', 'email', 'phone', 'first_name', 'last_name')

    fieldsets = UserAdmin.fieldsets + (
        ("Informations pharmacie", {
            'fields': ('role', 'phone'),
        }),
        ("Permissions granulaires", {
            'fields': ('can_access_cash_closure', 'can_manage_insurance', 'can_view_reports', 'can_manage_inventory'),
            'description': "Ces permissions sont indépendantes du rôle. "
                           "Un caissier peut par exemple avoir accès à la clôture de caisse.",
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Rôle et permissions", {
            'fields': ('role', 'phone',
                       'can_access_cash_closure', 'can_manage_insurance', 'can_view_reports'),
        }),
    )