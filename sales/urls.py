from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    path('pos/', views.pos, name='pos'),
    path('pos/create/', views.create_sale, name='create_sale'),  # ← AJOUT
    path('<int:pk>/', views.sale_detail, name='detail'),
    path('cash-report/', views.cash_report, name='cash_report'),
    path('cash-report/', views.cash_report, name='cash_report'),
    path('cash-report/export/', views.cash_report_export, name='cash_report_export'),
    path('closures/', views.cash_closure_list, name='cash_closure_list'),
    path('closures/new/', views.cash_closure_new, name='cash_closure_new'),
    path('closures/<int:pk>/', views.cash_closure_detail, name='cash_closure_detail'),
    path('closures/<int:pk>/validate/', views.cash_closure_validate, name='cash_closure_validate'),
    path('preorders/', views.preorder_list, name='preorder_list'),
    path('preorders/<int:pk>/', views.preorder_detail, name='preorder_detail'),
    path('preorders/<int:pk>/deliver/', views.preorder_deliver, name='preorder_deliver'),
]
