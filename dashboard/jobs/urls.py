from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("add", views.add_job, name="add_job"),
    path("job/<int:pk>/", views.job_detail, name="job_detail"),
    path("job/<int:pk>/delete", views.delete_job, name="delete_job"),
]
