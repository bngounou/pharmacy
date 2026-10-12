from django import forms
from .models import Medicine, Category, Supplier, Batch


class MedicineForm(forms.ModelForm):
    class Meta:
        model = Medicine
        fields = (
            'name', 'generic_name', 'category', 'supplier',
            'barcode', 'description', 'image',
            'purchase_price', 'cash_price', 'insurance_price',
            'reorder_level', 'recommended_stock',
            'requires_prescription', 'insurance_eligible', 'is_active',
        )
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "Nom commercial"}),
            'generic_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "DCI / nom générique"}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'supplier': forms.Select(attrs={'class': 'form-select'}),
            'barcode': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "Code-barres (optionnel)"}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'purchase_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'cash_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'insurance_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'reorder_level': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'recommended_stock': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'requires_prescription': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'insurance_eligible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'insurance_price': "Laisser vide pour calcul automatique (prix comptant × 1.15).",
            'reorder_level': "Seuil minimum déclenchant une alerte de réapprovisionnement.",
            'recommended_stock': "Niveau cible après réapprovisionnement.",
        }


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ('name', 'description')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ('name', 'contact_person', 'phone', 'email', 'address')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_person': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class BatchForm(forms.ModelForm):
    """Formulaire pour ajouter une entrée de stock à un médicament existant."""
    class Meta:
        model = Batch
        fields = ('quantity', 'expiry_date', 'batch_number', 'received_date')
        widgets = {
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'expiry_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "Optionnel"}),
            'received_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def clean_quantity(self):
        qty = self.cleaned_data['quantity']
        if qty <= 0:
            raise forms.ValidationError("La quantité doit être supérieure à 0.")
        return qty