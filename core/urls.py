from django.urls import path
from . import views
from . import api_views

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
    path('submission/<uuid:submission_id>/delete/', views.delete_submission, name='delete_submission'),
    path('exam/<int:exam_id>/compiled-report/', views.compiled_exam_report, name='compiled_exam_report'),
    path('exam/<int:exam_id>/customize/', views.customize_exam_subjects, name='customize_exam_subjects'),
    path('exam/<int:exam_id>/copy-allocations/', views.copy_exam_allocations, name='copy_exam_allocations'),
    path('exam/<int:exam_id>/reorder-submissions/', views.reorder_submissions, name='reorder_submissions'),

    # Master Setup (One-time process: Master Subjects & Divisions)
    path('subjects/', views.manage_subjects, name='manage_subjects'),
    path('subjects/<int:subject_id>/delete/', views.delete_subject, name='delete_subject'),
    path('divisions/', views.manage_divisions, name='manage_divisions'),
    path('divisions/<int:division_id>/delete/', views.delete_division, name='delete_division'),

    # Exam Timetable Management
    path('timetables/', views.manage_timetables, name='manage_timetables'),
    path('timetables/create/', views.create_timetable, name='create_timetable'),
    path('timetables/edit/<uuid:timetable_id>/', views.edit_timetable, name='edit_timetable'),
    path('timetables/delete/<uuid:timetable_id>/', views.delete_timetable, name='delete_timetable'),

    # Teacher Public Portal (Web)
    path('teacher/', views.teacher_portal, name='teacher_portal'),
    path('teacher/submit/<uuid:token>/', views.teacher_submit_portion, name='teacher_submit_portion'),
    path('teacher/success/<uuid:token>/', views.teacher_submission_success, name='teacher_submission_success'),

    # Teacher Portal REST API (Mobile App)
    path('api/teacher/portal/', api_views.teacher_portal_api, name='api_teacher_portal'),
    path('api/teacher/find-submission/', api_views.teacher_find_submission_api, name='api_teacher_find_submission'),
    path('api/teacher/submission/<uuid:token>/', api_views.teacher_submission_detail_api, name='api_teacher_submission_detail'),
    path('api/teacher/submission/<uuid:token>/submit/', api_views.teacher_submit_portion_api, name='api_teacher_submit_portion'),
    path('api/teacher/submissions/status/', api_views.teacher_submissions_status_api, name='api_teacher_submissions_status'),
    path('api/teacher/timetables/', api_views.teacher_timetables_api, name='api_teacher_timetables'),
    path('api/teacher/reminder-data/', api_views.teacher_reminder_data_api, name='api_teacher_reminder_data'),
]
