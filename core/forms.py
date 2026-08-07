from django import forms
from django.forms import inlineformset_factory
from .models import Exam, PortionSubmission, QuestionPatternItem, MasterSubject, Division

class MasterSubjectForm(forms.ModelForm):
    class Meta:
        model = MasterSubject
        fields = ['name', 'code']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Mathematics'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. MATH101'}),
        }

class DivisionForm(forms.ModelForm):
    class Meta:
        model = Division
        fields = ['standard', 'division_name']
        widgets = {
            'standard': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 10th or Grade 9'}),
            'division_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Div A or Section B'}),
        }

class ExamForm(forms.ModelForm):
    school_name = forms.CharField(
        initial="Arohan Academy School",
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Arohan Academy School'})
    )
    header_subtitle = forms.CharField(
        initial="(Syllabus Paper & Time table)",
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. (Syllabus Paper & Time table)'})
    )
    note_text = forms.CharField(
        initial="NOTE: Kindly pay your wards fees before exam and collect the exam pass from the office.",
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. NOTE: Kindly pay your wards fees...'})
    )

    class Meta:
        model = Exam
        fields = ['school_name', 'title', 'academic_year', 'header_subtitle', 'note_text', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Annual Examination 2026, Mid-Term Test'}),
            'academic_year': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 2026-2027'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_school_name(self):
        val = self.cleaned_data.get('school_name')
        return val if val else "Arohan Academy School"

    def clean_header_subtitle(self):
        val = self.cleaned_data.get('header_subtitle')
        return val if val else "(Syllabus Paper & Time table)"

    def clean_note_text(self):
        val = self.cleaned_data.get('note_text')
        return val if val else "NOTE: Kindly pay your wards fees before exam and collect the exam pass from the office."


class TeacherSubmissionForm(forms.ModelForm):
    syllabus_portion = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 6, 'placeholder': 'List Unit / Chapters / Topics covered in detail...', 'required': 'required'}),
        required=True,
        error_messages={'required': 'Syllabus Portion (Chapters / Units / Topics Included) is required.'}
    )

    class Meta:
        model = PortionSubmission
        fields = ['teacher_name', 'syllabus_portion', 'instructions_for_students', 'total_marks', 'duration_minutes']
        widgets = {
            'teacher_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter your full name', 'required': 'required'}),
            'instructions_for_students': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'e.g. Scientific calculators allowed'}),
            'total_marks': forms.NumberInput(attrs={'class': 'form-control'}),
            'duration_minutes': forms.NumberInput(attrs={'class': 'form-control'}),
        }

class QuestionPatternItemForm(forms.ModelForm):
    question_type = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. MCQs, Short Answer, Essay'}),
        required=False
    )
    number_of_questions = forms.IntegerField(
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        min_value=1,
        required=False
    )
    marks_per_question = forms.FloatField(
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'min': '0.5'}),
        min_value=0.5,
        required=False
    )

    class Meta:
        model = QuestionPatternItem
        fields = ['question_type', 'number_of_questions', 'marks_per_question']





QuestionPatternFormSet = inlineformset_factory(
    PortionSubmission,
    QuestionPatternItem,
    form=QuestionPatternItemForm,
    extra=0,
    can_delete=True
)


