from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("add", views.add_job, name="add_job"),
    path("job/<int:pk>/", views.job_detail, name="job_detail"),
    path("job/<int:pk>/delete", views.delete_job, name="delete_job"),
    path("job/<int:pk>/push", views.push_job, name="push_job"),
    path("job/<int:pk>/send", views.send_job, name="send_job"),
    path("job/<int:pk>/reset", views.reset_job, name="reset_job"),
    path("refresh", views.refresh_states_view, name="refresh_states"),
]
