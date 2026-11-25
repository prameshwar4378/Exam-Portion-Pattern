import datetime
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import (
    Division,
    Exam,
    ExamTimetable,
    MasterSubject,
    PortionSubmission,
    QuestionPatternItem,
    TimetableEntry,
)

User = get_user_model()


class PerformanceAndCompatibilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='principal', password='secret_password_123')
        self.exam = Exam.objects.create(title='Mid Term 2026', academic_year='2026-2027', is_active=True)
        self.other_exam = Exam.objects.create(title='Final Term 2026', academic_year='2026-2027', is_active=False)

        self.subjects = [
            MasterSubject.objects.create(name=f'Subject {i}', code=f'SUB{i}')
            for i in range(1, 5)
        ]
        self.divisions = [
            Division.objects.create(standard=f'{i}th', division_name='A')
            for i in range(1, 5)
        ]

        self.submissions = []
        for div in self.divisions:
            for subj in self.subjects:
                sub = PortionSubmission.objects.create(
                    exam=self.exam,
                    master_subject=subj,
                    division_ref=div,
                    subject_name=subj.name,
                    class_name=str(div),
                    teacher_name='Teacher One',
                    status='SUBMITTED',
                    syllabus_portion='Chapters 1 to 3',
                    total_marks=40,
                    duration_minutes=120,
                    exam_date=datetime.date(2026, 12, 1),
                )
                QuestionPatternItem.objects.create(
                    submission=sub,
                    section_name='A',
                    question_type='MCQ',
                    number_of_questions=10,
                    marks_per_question=1.0,
                )
                QuestionPatternItem.objects.create(
                    submission=sub,
                    section_name='B',
                    question_type='Short Answer',
                    number_of_questions=10,
                    marks_per_question=3.0,
                )
                self.submissions.append(sub)

        self.tt = ExamTimetable.objects.create(exam=self.exam, name='Common Timetable')
        TimetableEntry.objects.create(
            timetable=self.tt,
            exam_date=datetime.date(2026, 12, 1),
            subject_name='Subject 1',
            order=1,
        )

    def tearDown(self):
        cache.clear()

    def test_principal_login_and_dashboard_query_budget(self):
        resp = self.client.post(reverse('login'), {
            'username': 'principal',
            'password': 'secret_password_123',
        })
        self.assertRedirects(resp, reverse('principal_dashboard'))

        # Dashboard query budget should stay constant (2 queries: cached session user + 1 annotated Exam query)
        with self.assertNumQueries(2):
            dash = self.client.get(reverse('principal_dashboard'))
        self.assertEqual(dash.status_code, 200)
        self.assertContains(dash, 'Mid Term 2026')

    def test_compiled_report_and_print_submission_render_cleanly(self):
        self.client.force_login(self.user)
        with self.assertNumQueries(7):
            resp = self.client.get(reverse('compiled_exam_report', args=[self.exam.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'img/logo.jpg')
        self.assertContains(resp, 'images/school_logo.png')

        sub = self.submissions[0]
        print_resp = self.client.get(reverse('print_submission', args=[sub.id]))
        self.assertEqual(print_resp.status_code, 200)
        self.assertContains(print_resp, 'MCQ')
        self.assertContains(print_resp, 'Short Answer')

    def test_customize_exam_subjects_bulk_post_and_cache_invalidation(self):
        self.client.force_login(self.user)
        # Prime portal API cache
        r1 = self.client.get(reverse('api_teacher_portal'))
        self.assertEqual(r1.status_code, 200)
        etag1 = r1.get('ETag')

        # Uncheck half of pairs and submit via POST
        post_data = {'total_marks': '50', 'duration_minutes': '90'}
        for div in self.divisions[:2]:
            for subj in self.subjects[:2]:
                post_data[f'alloc_{subj.id}_{div.id}'] = 'on'

        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(reverse('customize_exam_subjects', args=[self.exam.id]), post_data)
        self.assertRedirects(resp, reverse('principal_dashboard'))
        self.assertEqual(PortionSubmission.objects.filter(exam=self.exam).count(), 4)

        # Cache version should have bumped
        r2 = self.client.get(reverse('api_teacher_portal'))
        self.assertNotEqual(r2.get('ETag'), etag1)
        self.assertEqual(len(r2.json()['active_requests']), 4)

    def test_api_timetables_query_budget_and_opt_in_slim_mode(self):
        with self.assertNumQueries(7):
            resp = self.client.get(f"{reverse('api_teacher_timetables')}?exam_id={self.exam.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(len(data['class_wise_timetables']), 4)
        self.assertTrue(len(data['class_wise_timetables'][0]['subjects'][0]['patterns']) > 0)

        # Second hit served from LocMemCache with 0 SQL queries
        with self.assertNumQueries(0):
            resp_cached = self.client.get(f"{reverse('api_teacher_timetables')}?exam_id={self.exam.id}")
        self.assertEqual(resp_cached.status_code, 200)

        # Conditional ETag 304 check
        resp_304 = self.client.get(
            f"{reverse('api_teacher_timetables')}?exam_id={self.exam.id}",
            HTTP_IF_NONE_MATCH=resp_cached.get('ETag'),
        )
        self.assertEqual(resp_304.status_code, 304)

        # Opt-in slim payload omits patterns
        slim_resp = self.client.get(f"{reverse('api_teacher_timetables')}?exam_id={self.exam.id}&include_patterns=0")
        self.assertEqual(slim_resp.status_code, 200)
        self.assertEqual(slim_resp.json()['class_wise_timetables'][0]['subjects'][0]['patterns'], [])

    def test_api_submit_portion_and_copy_to_divisions(self):
        source = self.submissions[0]
        target = [s for s in self.submissions if s.subject_name == source.subject_name and s.id != source.id][0]

        payload = {
            'teacher_name': 'Prof. Sharma',
            'syllabus_portion': 'Updated Units 1-4',
            'instructions_for_students': 'Bring geometry box',
            'duration_minutes': 150,
            'pattern_items': [
                {'section_name': 'Sec A', 'question_type': 'MCQ', 'number_of_questions': 20, 'marks_per_question': 1.0, 'choice_info': ''},
                {'section_name': 'Sec B', 'question_type': 'Long', 'number_of_questions': 5, 'marks_per_question': 6.0, 'choice_info': 'Any 5'},
            ],
            'copy_to_divisions': [str(target.id)],
        }

        resp = self.client.post(
            reverse('api_teacher_submit_portion', args=[source.id]),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        res_data = resp.json()
        self.assertEqual(res_data['total_marks'], 50)
        self.assertEqual(res_data['copied_count'], 1)

        target.refresh_from_db()
        self.assertEqual(target.total_marks, 50)
        self.assertEqual(target.teacher_name, 'Prof. Sharma')
        self.assertEqual(target.pattern_items.count(), 2)

    def test_destructive_routes_reject_get_and_accept_post(self):
        self.client.force_login(self.user)
        sub = self.submissions[0]
        free_subj = MasterSubject.objects.create(name='Unused Subject', code='UN01')
        free_div = Division.objects.create(standard='12th', division_name='Z')
        tt = ExamTimetable.objects.create(exam=self.exam, name='Temp TT')

        get_routes = [
            reverse('toggle_exam_active', args=[self.exam.id]),
            reverse('delete_submission', args=[sub.id]),
            reverse('delete_subject', args=[free_subj.id]),
            reverse('delete_division', args=[free_div.id]),
            reverse('delete_timetable', args=[tt.id]),
            reverse('logout'),
        ]
        for url in get_routes:
            self.assertEqual(self.client.get(url).status_code, 405, f"Expected 405 on GET {url}")

        self.assertEqual(self.client.post(reverse('toggle_exam_active', args=[self.exam.id])).status_code, 302)
        self.assertEqual(self.client.post(reverse('delete_submission', args=[sub.id])).status_code, 302)
        self.assertEqual(self.client.post(reverse('delete_subject', args=[free_subj.id])).status_code, 302)
        self.assertEqual(self.client.post(reverse('delete_division', args=[free_div.id])).status_code, 302)
        self.assertEqual(self.client.post(reverse('delete_timetable', args=[tt.id])).status_code, 302)
        self.assertEqual(self.client.post(reverse('logout')).status_code, 302)
