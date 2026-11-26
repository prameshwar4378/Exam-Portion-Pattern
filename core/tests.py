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
            reverse('toggle_exam_target_marks_timing', args=[self.exam.id]),
            reverse('bulk_update_exam_marks_timing', args=[self.exam.id]),
            reverse('delete_exam', args=[self.other_exam.id]),
            reverse('update_submission_marks_timing', args=[sub.id]),
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

    def test_delete_exam_cascades_invalidates_cache_and_renders_modal(self):
        # Anonymous POST must redirect to login without deleting
        anon_resp = self.client.post(reverse('delete_exam', args=[self.exam.id]))
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn(reverse('login'), anon_resp.url)
        self.assertTrue(Exam.objects.filter(id=self.exam.id).exists())

        self.client.force_login(self.user)
        dash_resp = self.client.get(reverse('principal_dashboard'))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, reverse('delete_exam', args=[self.exam.id]))
        self.assertContains(dash_resp, 'id="deleteExamModal"')
        self.assertContains(dash_resp, 'id="deleteExamForm"')

        # Prime portal API cache and capture ETag before deletion
        r1 = self.client.get(reverse('api_teacher_portal'))
        etag1 = r1.get('ETag')

        with self.captureOnCommitCallbacks(execute=True):
            del_resp = self.client.post(reverse('delete_exam', args=[self.exam.id]))
        self.assertRedirects(del_resp, reverse('principal_dashboard'))

        self.assertFalse(Exam.objects.filter(id=self.exam.id).exists())
        self.assertFalse(PortionSubmission.objects.filter(exam=self.exam).exists())
        self.assertFalse(QuestionPatternItem.objects.filter(submission__exam_id=self.exam.id).exists())
        self.assertFalse(ExamTimetable.objects.filter(exam=self.exam).exists())
        self.assertFalse(TimetableEntry.objects.filter(timetable=self.tt).exists())

        # Master data and other exams stay intact
        self.assertTrue(Exam.objects.filter(id=self.other_exam.id).exists())
        self.assertEqual(MasterSubject.objects.count(), 4)
        self.assertEqual(Division.objects.count(), 4)

        # Missing exam returns 404
        self.assertEqual(self.client.post(reverse('delete_exam', args=[99999])).status_code, 404)

        # Cache version should have bumped on commit
        r2 = self.client.get(reverse('api_teacher_portal'))
        self.assertNotEqual(r2.get('ETag'), etag1)
        self.assertEqual(r2.json()['active_exams'], [])

    def test_hide_target_marks_timing_toggle_and_teacher_submission_flow(self):
        self.client.force_login(self.user)

        # Create a mix of PENDING and APPROVED submissions
        pending_subs = self.submissions[:4]
        PortionSubmission.objects.filter(id__in=[s.id for s in pending_subs]).update(
            status='PENDING',
            total_marks=80,
            duration_minutes=180,
        )
        submitted_sub = self.submissions[4]  # remains SUBMITTED (40M / 120m)

        # Toggle ON "Remove Target Marks & Timing Before Submission"
        with self.captureOnCommitCallbacks(execute=True):
            t_resp = self.client.post(reverse('toggle_exam_target_marks_timing', args=[self.exam.id]))
        self.assertRedirects(t_resp, reverse('exam_detail_dashboard', args=[self.exam.id]))

        self.exam.refresh_from_db()
        self.assertTrue(self.exam.hide_target_before_submission)
        for s in pending_subs:
            s.refresh_from_db()
            self.assertEqual(s.total_marks, 0)
            self.assertEqual(s.duration_minutes, 0)
        submitted_sub.refresh_from_db()
        self.assertEqual(submitted_sub.total_marks, 40)
        self.assertEqual(submitted_sub.duration_minutes, 120)

        dash = self.client.get(reverse('exam_detail_dashboard', args=[self.exam.id]))
        self.assertContains(dash, 'From Teacher')

        # API exposes unset targets + hide flag
        detail = self.client.get(reverse('api_teacher_submission_detail', args=[pending_subs[0].id])).json()['submission']
        self.assertTrue(detail['hide_target_before_submission'])
        self.assertEqual(detail['total_marks'], 0)
        self.assertEqual(detail['duration_minutes'], 0)

        # Teacher submit must fail if duration_minutes is omitted/0 when target duration was removed
        bad_resp = self.client.post(
            reverse('api_teacher_submit_portion', args=[pending_subs[0].id]),
            data=json.dumps({
                'teacher_name': 'Prof. Nair',
                'syllabus_portion': 'Units 1 and 2',
                'instructions_for_students': '',
                'duration_minutes': 0,
                'pattern_items': [
                    {'section_name': 'A', 'question_type': 'MCQ', 'number_of_questions': 5, 'marks_per_question': 5.0, 'choice_info': ''},
                ],
            }),
            content_type='application/json',
        )
        self.assertEqual(bad_resp.status_code, 400)

        # Valid submission stores teacher-entered duration & blueprint total marks
        good_resp = self.client.post(
            reverse('api_teacher_submit_portion', args=[pending_subs[0].id]),
            data=json.dumps({
                'teacher_name': 'Prof. Nair',
                'syllabus_portion': 'Units 1 and 2',
                'instructions_for_students': '',
                'duration_minutes': 90,
                'pattern_items': [
                    {'section_name': 'A', 'question_type': 'MCQ', 'number_of_questions': 5, 'marks_per_question': 5.0, 'choice_info': ''},
                ],
            }),
            content_type='application/json',
        )
        self.assertEqual(good_resp.status_code, 200)
        pending_subs[0].refresh_from_db()
        self.assertEqual(pending_subs[0].status, 'SUBMITTED')
        self.assertEqual(pending_subs[0].total_marks, 25)
        self.assertEqual(pending_subs[0].duration_minutes, 90)

        # Toggle back OFF restores defaults on remaining unset PENDING records
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('toggle_exam_target_marks_timing', args=[self.exam.id]))
        pending_subs[1].refresh_from_db()
        self.assertEqual(pending_subs[1].total_marks, 80)
        self.assertEqual(pending_subs[1].duration_minutes, 180)

    def test_bulk_and_individual_marks_timing_updates_and_customize_preservation(self):
        self.client.force_login(self.user)

        # Mark one division as APPROVED and rest PENDING so scope filtering can be tested
        subj1_subs = list(PortionSubmission.objects.filter(exam=self.exam, subject_name='Subject 1'))
        PortionSubmission.objects.filter(exam=self.exam).update(status='PENDING', total_marks=80, duration_minutes=180)
        approved_sub = subj1_subs[0]
        approved_sub.status = 'APPROVED'
        approved_sub.total_marks = 100
        approved_sub.save(update_fields=['status', 'total_marks'])

        # 1. Individual edit with apply_to_subject_all_classes (excluding approved)
        self.client.post(
            reverse('update_submission_marks_timing', args=[subj1_subs[1].id]),
            {
                'total_marks': '50',
                'duration_minutes': '120',
                'apply_to_subject_all_classes': 'on',
            },
        )
        subj1_subs[1].refresh_from_db()
        subj1_subs[2].refresh_from_db()
        approved_sub.refresh_from_db()
        self.assertEqual((subj1_subs[1].total_marks, subj1_subs[1].duration_minutes), (50, 120))
        self.assertEqual((subj1_subs[2].total_marks, subj1_subs[2].duration_minutes), (50, 120))
        self.assertEqual(approved_sub.total_marks, 100)  # Approved stays untouched

        # 2. Customize exam subjects without overwrite preserves per-subject marks
        post_data = {'total_marks': '80', 'duration_minutes': '180'}
        for div in self.divisions:
            for subj in self.subjects:
                post_data[f'alloc_{subj.id}_{div.id}'] = 'on'
        self.client.post(reverse('customize_exam_subjects', args=[self.exam.id]), post_data)
        subj1_subs[1].refresh_from_db()
        self.assertEqual(subj1_subs[1].total_marks, 50)

        # 3. Bulk per-subject update across divisions
        self.client.post(
            reverse('bulk_update_exam_marks_timing', args=[self.exam.id]),
            {
                'update_mode': 'per_subject',
                'status_scope': 'pending_only',
                'subject_row_key': ['0', '1'],
                'subject_name_0': 'Subject 2',
                'subj_marks_0': '35',
                'subj_duration_0': '60',
                'subject_name_1': 'Subject 3',
                'subj_marks_1': '',  # Blank skips Subject 3 marks
                'subj_duration_1': '90',
            },
        )
        s2 = PortionSubmission.objects.filter(exam=self.exam, subject_name='Subject 2').first()
        s3 = PortionSubmission.objects.filter(exam=self.exam, subject_name='Subject 3').first()
        self.assertEqual((s2.total_marks, s2.duration_minutes), (35, 60))
        self.assertEqual((s3.total_marks, s3.duration_minutes), (80, 90))

        # 4. Bulk all subjects together update
        self.client.post(
            reverse('bulk_update_exam_marks_timing', args=[self.exam.id]),
            {
                'update_mode': 'all',
                'status_scope': 'non_approved',
                'bulk_total_marks': '60',
                'bulk_duration_minutes': '150',
            },
        )
        self.assertEqual(
            PortionSubmission.objects.filter(exam=self.exam, total_marks=60, duration_minutes=150).count(),
            len(self.submissions) - 1,
        )
        approved_sub.refresh_from_db()
        self.assertEqual(approved_sub.total_marks, 100)
