from django.urls import path
from . import views

app_name = 'insurance'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('claims/', views.claim_list, name='claim_list'),
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/new/', views.invoice_new, name='invoice_new'),
    path('invoices/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    path('invoices/<int:pk>/excel/', views.invoice_excel, name='invoice_excel'),    # ← MANQUAIT
    path('invoices/<int:pk>/send/', views.invoice_send, name='invoice_send'),       # ← VÉRIFIEZ
    path('payments/', views.payment_list, name='payment_list'),
    path('payments/new/', views.payment_new, name='payment_new'),
    path('payments/<str:reference>/', views.payment_detail, name='payment_detail'),
]