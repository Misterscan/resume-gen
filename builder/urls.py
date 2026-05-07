from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('generate/', views.generate, name='generate'),
    path('verify-ats/', views.verify_ats, name='verify_ats'),
    path('job-status/<str:job_id>/', views.check_job_status, name='check_job_status'),
    path('google-login/', views.google_login, name='google_login'),
    path('google-callback/', views.google_callback, name='google_callback'),
]