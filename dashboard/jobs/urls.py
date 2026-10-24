from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("add", views.add_job, name="add_job"),
    path("job/<int:pk>/", views.job_detail, name="job_detail"),
    path("job/<int:pk>/interview", views.interview_prep, name="interview_prep"),
    path("job/<int:pk>/ats", views.ats_scan, name="ats_scan"),
    path("job/<int:pk>/delete", views.delete_job, name="delete_job"),
    path("job/<int:pk>/push", views.push_job, name="push_job"),
    path("job/<int:pk>/execute", views.execute_job, name="execute_job"),
    path("job/<int:pk>/recover", views.recover_job, name="recover_job"),
    path("job/<int:pk>/preview", views.preview_job, name="preview_job"),
    path("job/<int:pk>/transmit", views.transmit_job, name="transmit_job"),
    path("job/<int:pk>/doc/<str:kind>", views.serve_doc, name="serve_doc"),
    path("job/<int:pk>/att/<int:idx>", views.serve_attachment, name="serve_attachment"),
    path("cauta/", views.search_index, name="search_index"),
    path("cauta/<int:pk>/", views.search_run, name="search_run"),
    path("cauta/<int:pk>/import", views.import_search_results,
         name="import_search_results"),
    path("trimise/", views.sent_list, name="sent_list"),
]
