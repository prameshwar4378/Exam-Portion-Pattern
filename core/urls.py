from django.urls import path
from . import views

urlpatterns = [
    # Principal Auth & Admin Views
    path('principal/login/', views.principal_login_view, name='login'),
    path('principal/logout/', views.principal_logout_view, name='logout'),
    path('', views.principal_dashboard, name='principal_dashboard'),
    path('exam/create/', views.create_exam, name='create_exam'),
    path('exam/<int:exam_id>/edit/', views.edit_exam, name='edit_exam'),
    path('exam/<int:exam_id>/toggle-active/', views.toggle_exam_active, name='toggle_exam_active'),
    path('exam/<int:exam_id>/dashboard/', views.exam_detail_dashboard, name='exam_detail_dashboard'),

    path('submission/<uuid:submission_id>/review/', views.review_submission, name='review_submission'),
    path('submission/<uuid:submission_id>/print/', views.print_submission_view, name='print_submission'),
    path('exam/<int:exam_id>/compiled-report/', views.compiled_exam_report, name='compiled_exam_report'),
    path('exam/<int:exam_id>/customize/', views.customize_exam_subjects, name='customize_exam_subjects'),

    # Master Setup (One-time process: Master Subjects & Divisions)
    path('subjects/', views.manage_subjects, name='manage_subjects'),
    path('subjects/<int:subject_id>/delete/', views.delete_subject, name='delete_subject'),
    path('divisions/', views.manage_divisions, name='manage_divisions'),
    path('divisions/<int:division_id>/delete/', views.delete_division, name='delete_division'),

    # Teacher Public Portal (No login needed)

    path('teacher/', views.teacher_portal, name='teacher_portal'),
    path('teacher/submit/<uuid:token>/', views.teacher_submit_portion, name='teacher_submit_portion'),
    path('teacher/success/<uuid:token>/', views.teacher_submission_success, name='teacher_submission_success'),
]
