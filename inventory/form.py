from django import forms
from .models import Batch, Medicine


class QuickStockEntryForm(forms.ModelForm):
    """Formulaire simplifié pour ajouter du stock en 3 champs."""
    class Meta:
        model = Batch
        fields = ('medicine', 'quantity', 'expiry_date')
        widgets = {
            'medicine': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'expiry_date': forms.DateInput(
                attrs={'class': 'form-control', 'type': 'date'}
            ),
        }
        labels = {
            'medicine': "Médicament",
            'quantity': "Quantité reçue",
            'expiry_date': "Date de péremption",
        }

    def clean_quantity(self):
        qty = self.cleaned_data['quantity']
        if qty <= 0:
            raise forms.ValidationError("La quantité doit être supérieure à 0.")
        return qty