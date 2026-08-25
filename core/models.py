import uuid
from django.db import models

class MasterSubject(models.Model):
    name = models.CharField(max_length=150, unique=True, help_text="e.g. Mathematics, Science, English")
    code = models.CharField(max_length=50, blank=True, help_text="e.g. MATH101")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name


class Division(models.Model):
    standard = models.CharField(max_length=50, help_text="e.g. 10th, Grade 9, Class 12")
    division_name = models.CharField(max_length=50, help_text="e.g. Div A, Section B, A, B")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['standard', 'division_name']
        unique_together = ('standard', 'division_name')

    def __str__(self):
        return f"{self.standard} - {self.division_name}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_name = None
        if not is_new:
            old_obj = Division.objects.filter(pk=self.pk).first()
            if old_obj:
                old_name = str(old_obj)

        super().save(*args, **kwargs)

        new_name = str(self)
        # Instantly update all linked PortionSubmissions when a Division is created or edited
        from core.models import PortionSubmission
        PortionSubmission.objects.filter(division_ref=self).update(class_name=new_name)
        if old_name and old_name != new_name:
            PortionSubmission.objects.filter(class_name=old_name).update(class_name=new_name, division_ref=self)


class Exam(models.Model):
    title = models.CharField(max_length=200, help_text="e.g. PA-1 Exam 2026-2027")
    academic_year = models.CharField(max_length=50, help_text="e.g. 2026-2027")
    school_name = models.CharField(max_length=250, default="Arohan Academy School", help_text="School Name Header")
    header_subtitle = models.CharField(max_length=200, default="(Syllabus Paper & Time table)", help_text="Subtitle e.g. (Syllabus Paper & Time table)")

    note_text = models.CharField(max_length=300, default="NOTE: Kindly pay your wards fees before exam and collect the exam pass from the office.", help_text="Bottom note")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.academic_year})"


class PortionSubmission(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Submission'),
        ('SUBMITTED', 'Submitted by Teacher'),
        ('APPROVED', 'Approved by Principal'),
        ('REVISION', 'Revision Requested'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='submissions')
    master_subject = models.ForeignKey(MasterSubject, on_delete=models.SET_NULL, null=True, blank=True, related_name='submissions')
    division_ref = models.ForeignKey(Division, on_delete=models.SET_NULL, null=True, blank=True, related_name='submissions')
    
    class_name = models.CharField(max_length=100, help_text="e.g. Grade VIII(MM) or Class 10-A")
    subject_name = models.CharField(max_length=100, help_text="e.g. Mathematics, Physics")
    teacher_name = models.CharField(max_length=150, help_text="Teacher's full name")
    teacher_email = models.EmailField(blank=True, null=True, help_text="Optional teacher contact email")
    exam_date = models.DateField(blank=True, null=True, help_text="Date of exam e.g. 2026-07-06")

    
    # Syllabus Portion details
    syllabus_portion = models.TextField(blank=True, help_text="List chapters, topics, and units covered")
    instructions_for_students = models.TextField(blank=True, help_text="Special instructions for question paper e.g. Use of calculator allowed")
    total_marks = models.PositiveIntegerField(default=80)
    duration_minutes = models.PositiveIntegerField(default=180, help_text="Exam duration in minutes")
    
    order = models.PositiveIntegerField(default=0, help_text="Sequence order of subject for display and reports")

    # Submission Metadata
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    principal_remarks = models.TextField(blank=True, help_text="Remarks from Principal/Admin")
    submitted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'class_name', 'subject_name']

    @property
    def display_class_name(self):
        if self.division_ref:
            return str(self.division_ref)
        return self.class_name

    def save(self, *args, **kwargs):
        if self.division_ref:
            self.class_name = str(self.division_ref)
        super().save(*args, **kwargs)

    @property
    def total_calculated_marks(self):
        return sum(item.total_marks for item in self.pattern_items.all())


class QuestionPatternItem(models.Model):
    submission = models.ForeignKey(PortionSubmission, on_delete=models.CASCADE, related_name='pattern_items')
    section_name = models.CharField(max_length=100, blank=True, default='', help_text="e.g. Section A, Part 1")
    question_type = models.CharField(max_length=100, help_text="e.g. MCQ, Short Answer, Essay")
    number_of_questions = models.PositiveIntegerField(default=1)
    marks_per_question = models.FloatField(default=1.0)
    choice_info = models.CharField(max_length=150, blank=True, help_text="e.g. Answer any 5 out of 7, compulsory")

    def __str__(self):
        return f"{self.question_type} ({self.number_of_questions} x {self.marks_per_question}m)"


    @property
    def total_marks(self):
        return self.number_of_questions * self.marks_per_question


class ExamTimetable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='timetables')
    name = models.CharField(max_length=200, help_text="e.g. PA-1 EXAM TIMETABLE-2026-26 or Primary Section Timetable")
    divisions = models.ManyToManyField(Division, related_name='timetables', blank=True, help_text="Divisions configured for this timetable")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.exam.title})"


class TimetableEntry(models.Model):
    timetable = models.ForeignKey(ExamTimetable, on_delete=models.CASCADE, related_name='entries')
    exam_date = models.DateField(blank=True, null=True, help_text="Exam Date e.g. 2026-07-06")
    subject_name = models.CharField(max_length=150, blank=True, help_text="Subject Name e.g. English, Science, Hindi")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['exam_date', 'order']

    def __str__(self):
        return f"{self.exam_date}: {self.subject_name}"



