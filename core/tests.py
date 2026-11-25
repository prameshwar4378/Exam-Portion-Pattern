import datetime
from django.contrib.auth.models import User
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


class DeleteExamTests(TestCase):
    """Tests for deleting an exam and cascading related records."""

    def setUp(self):
        self.user = User.objects.create_user(username='principal', password='secret')
        self.subject = MasterSubject.objects.create(name='Mathematics', code='MATH101')
        self.division = Division.objects.create(standard='10th', division_name='A')

        self.exam = Exam.objects.create(title='Term 1 Exam', academic_year='2026-2027')
        self.other_exam = Exam.objects.create(title='Term 2 Exam', academic_year='2026-2027')

        self.submission = PortionSubmission.objects.create(
            exam=self.exam,
            master_subject=self.subject,
            division_ref=self.division,
            subject_name='Mathematics',
            class_name=str(self.division),
            teacher_name='Alice',
        )
        QuestionPatternItem.objects.create(
            submission=self.submission,
            question_type='MCQ',
            number_of_questions=10,
            marks_per_question=1.0,
        )
        self.timetable = ExamTimetable.objects.create(exam=self.exam, name='Term 1 TT')
        self.timetable.divisions.add(self.division)
        TimetableEntry.objects.create(
            timetable=self.timetable,
            exam_date=datetime.date(2026, 12, 1),
            subject_name='Mathematics',
        )

        self.other_submission = PortionSubmission.objects.create(
            exam=self.other_exam,
            master_subject=self.subject,
            division_ref=self.division,
            subject_name='Mathematics',
            class_name=str(self.division),
            teacher_name='Bob',
        )

    def test_requires_login(self):
        response = self.client.post(reverse('delete_exam', args=[self.exam.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)
        self.assertTrue(Exam.objects.filter(id=self.exam.id).exists())

    def test_get_does_not_delete_exam(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('delete_exam', args=[self.exam.id]))
        self.assertRedirects(response, reverse('principal_dashboard'))
        self.assertTrue(Exam.objects.filter(id=self.exam.id).exists())

    def test_post_deletes_exam_and_cascades_without_touching_master_or_other_exams(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('delete_exam', args=[self.exam.id]))
        self.assertRedirects(response, reverse('principal_dashboard'))

        self.assertFalse(Exam.objects.filter(id=self.exam.id).exists())
        self.assertFalse(PortionSubmission.objects.filter(exam_id=self.exam.id).exists())
        self.assertFalse(QuestionPatternItem.objects.filter(submission_id=self.submission.id).exists())
        self.assertFalse(ExamTimetable.objects.filter(exam_id=self.exam.id).exists())
        self.assertFalse(TimetableEntry.objects.filter(timetable_id=self.timetable.id).exists())

        self.assertTrue(Exam.objects.filter(id=self.other_exam.id).exists())
        self.assertTrue(PortionSubmission.objects.filter(id=self.other_submission.id).exists())
        self.assertTrue(MasterSubject.objects.filter(id=self.subject.id).exists())
        self.assertTrue(Division.objects.filter(id=self.division.id).exists())

    def test_post_nonexistent_exam_returns_404(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('delete_exam', args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_principal_dashboard_renders_delete_button_and_modal(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('principal_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('delete_exam', args=[self.exam.id]))
        self.assertContains(response, 'id="deleteExamModal"')
        self.assertContains(response, 'id="deleteExamForm"')
