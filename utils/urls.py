from django.urls import path

from . import views

app_name = "utils"
urlpatterns = [
    path("export_excel", views.export_excel, name="export_excel"),
]
