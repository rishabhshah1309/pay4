from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path("admin/", admin.site.urls),

    path("accounts/login/",  auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("", views.dashboard, name="dashboard"),
    path("new/", views.new_receipt, name="new_receipt"),
    path("<int:receipt_id>/upload/", views.upload_receipt, name="upload_receipt"),
    path("<int:receipt_id>/presign/", views.presign_endpoint, name="presign"),
    path("<int:receipt_id>/process/", views.process_receipt, name="process_receipt"),
    path("<int:receipt_id>/select/", views.select_items, name="select_items"),
    path("<int:receipt_id>/split/", views.split_view, name="split"),
]
