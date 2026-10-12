from django.urls import path
from . import views
from . import order_views

app_name = 'inventory'

urlpatterns = [
    # Médicaments
    path('medicines/', views.MedicineListView.as_view(), name='medicine_list'),
    path('medicines/new/', views.MedicineCreateView.as_view(), name='medicine_create'),
    path('medicines/<int:pk>/', views.MedicineDetailView.as_view(), name='medicine_detail'),
    path('medicines/<int:pk>/edit/', views.MedicineUpdateView.as_view(), name='medicine_update'),
    path('medicines/<int:pk>/delete/', views.MedicineDeleteView.as_view(), name='medicine_delete'),
    path('medicines/<int:pk>/reactivate/', views.MedicineReactivateView.as_view(), name='medicine_reactivate'),

    # Batches
    path('medicines/<int:medicine_pk>/batches/new/', views.BatchCreateView.as_view(), name='batch_create'),
    path('batches/<int:pk>/edit/', views.BatchUpdateView.as_view(), name='batch_update'),
    path('batches/<int:pk>/delete/', views.BatchDeleteView.as_view(), name='batch_delete'),

    # Catégories
    path('categories/', views.CategoryListView.as_view(), name='category_list'),
    path('categories/new/', views.CategoryCreateView.as_view(), name='category_create'),
    path('categories/<int:pk>/edit/', views.CategoryUpdateView.as_view(), name='category_update'),
    path('categories/<int:pk>/delete/', views.CategoryDeleteView.as_view(), name='category_delete'),

    # Fournisseurs
    path('suppliers/', views.SupplierListView.as_view(), name='supplier_list'),
    path('suppliers/new/', views.SupplierCreateView.as_view(), name='supplier_create'),
    path('suppliers/<int:pk>/edit/', views.SupplierUpdateView.as_view(), name='supplier_update'),
    path('suppliers/<int:pk>/delete/', views.SupplierDeleteView.as_view(), name='supplier_delete'),

    # Péremption
    path('expiring/', views.expiring_dashboard, name='expiring_dashboard'),
    path('expiring/list/', views.expiring_list, name='expiring_list'),
    path('expiring/export/', views.expiring_export_csv, name='expiring_export'),
    path('batches/<int:batch_pk>/remove/', views.remove_from_stock, name='remove_from_stock'),
    path('medicines/<int:medicine_pk>/toggle-promotion/', views.toggle_promotion, name='toggle_promotion'),


    # Réapprovisionnement
    path('reorder/', order_views.reorder_dashboard, name='reorder_dashboard'),
    path('reorder/suggestions/', order_views.reorder_suggestions, name='reorder_suggestions'),
    path('reorder/create/', order_views.reorder_create_from_suggestions, name='reorder_create'),

    # Bons de commande
    path('orders/', order_views.order_list, name='order_list'),
    path('orders/<int:pk>/', order_views.order_detail, name='order_detail'),
    path('orders/<int:pk>/send/', order_views.order_send, name='order_send'),
    path('orders/<int:pk>/cancel/', order_views.order_cancel, name='order_cancel'),
    path('orders/<int:pk>/receive/', order_views.order_receive, name='order_receive'),
    path('orders/<int:pk>/pdf/', order_views.order_pdf, name='order_pdf'),
    path('orders/<int:pk>/excel/', order_views.order_excel, name='order_excel'),


]