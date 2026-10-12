from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib import messages


def require_permission(field_name, error_message=None):
    """
    Décorateur générique : exige qu'un champ booléen du user soit True.

    Usage :
        @require_permission('can_access_cash_closure')
        def my_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')

            # Superuser a toujours accès
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            if not getattr(request.user, field_name, False):
                msg = error_message or "Vous n'avez pas la permission d'accéder à cette page."
                messages.error(request, msg)
                raise PermissionDenied(msg)

            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


# Raccourcis nommés pour lisibilité
can_access_cash_closure = require_permission(
    'can_access_cash_closure',
    "Accès refusé : vous n'êtes pas autorisé à clôturer la caisse."
)

can_manage_insurance = require_permission(
    'can_manage_insurance',
    "Accès refusé : vous n'êtes pas autorisé à gérer les assurances."
)

can_view_reports = require_permission(
    'can_view_reports',
    "Accès refusé : vous n'êtes pas autorisé à voir les rapports."
)

can_manage_inventory = require_permission(
    'can_manage_inventory',
    "Accès refusé : vous n'êtes pas autorisé à gérer l'inventaire."
)