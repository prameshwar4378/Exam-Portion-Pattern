from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import Exam, PortionSubmission, QuestionPatternItem
from .forms import ExamForm, TeacherSubmissionForm, QuestionPatternFormSet


def principal_login_view(request):
    if request.user.is_authenticated:
        return redirect('principal_dashboard')
    
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, Principal {user.first_name or user.username}!")
                return redirect('principal_dashboard')
            else:
                messages.error(request, "Invalid username or password.")
        else:
            messages.error(request, "Invalid login credentials.")
    else:
        form = AuthenticationForm()
        
    return render(request, 'core/login.html', {'form': form})

def principal_logout_view(request):
    logout(request)
    messages.info(request, "Logged out successfully.")
    return redirect('login')

@login_required
def principal_dashboard(request):
    exams = Exam.objects.all()
    
    # Calculate summary counts for each exam in the table
    exam_list = []
    for exam in exams:
        subs = exam.submissions.all()
        exam_list.append({
            'exam': exam,
            'total': subs.count(),
            'pending': subs.filter(status='PENDING').count(),
            'submitted': subs.filter(status='SUBMITTED').count(),
            'approved': subs.filter(status='APPROVED').count(),
            'revision': subs.filter(status='REVISION').count(),
        })

    context = {
        'exam_list': exam_list,
        'total_exams': exams.count(),
    }
    return render(request, 'core/principal_dashboard.html', context)

@login_required
def exam_detail_dashboard(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    submissions = PortionSubmission.objects.filter(exam=exam).select_related('master_subject', 'division_ref')
    
    stats = {
        'total': submissions.count(),
        'pending': submissions.filter(status='PENDING').count(),
        'submitted': submissions.filter(status='SUBMITTED').count(),
        'approved': submissions.filter(status='APPROVED').count(),
        'revision': submissions.filter(status='REVISION').count(),
    }

    # Group submissions by class/grade
    from collections import defaultdict
    grouped_dict = defaultdict(list)
    for sub in submissions:
        grouped_dict[sub.class_name].append(sub)

    submission_groups = []
    for class_name, subs in grouped_dict.items():
        pending_cnt = sum(1 for s in subs if s.status == 'PENDING')
        submitted_cnt = sum(1 for s in subs if s.status == 'SUBMITTED')
        approved_cnt = sum(1 for s in subs if s.status == 'APPROVED')
        revision_cnt = sum(1 for s in subs if s.status == 'REVISION')
        
        submission_groups.append({
            'class_name': class_name,
            'submissions': subs,
            'total_count': len(subs),
            'pending_cnt': pending_cnt,
            'submitted_cnt': submitted_cnt,
            'approved_cnt': approved_cnt,
            'revision_cnt': revision_cnt,
        })

    context = {
        'active_exam': exam,
        'submissions': submissions,
        'submission_groups': submission_groups,
        'stats': stats,
    }
    return render(request, 'core/exam_detail_dashboard.html', context)



@login_required
def create_exam(request):
    if request.method == 'POST':
        form = ExamForm(request.POST)
        if form.is_valid():
            exam = form.save()
            messages.success(request, f"Exam '{exam.title}' created successfully!")
            return redirect('principal_dashboard')
    else:
        form = ExamForm()
    return render(request, 'core/exam_form.html', {'form': form})

@login_required
def edit_exam(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if request.method == 'POST':
        form = ExamForm(request.POST, instance=exam)
        if form.is_valid():
            form.save()
            messages.success(request, f"Exam '{exam.title}' updated successfully!")
            return redirect('principal_dashboard')
    else:
        form = ExamForm(instance=exam)
    return render(request, 'core/exam_form.html', {'form': form, 'edit_instance': exam})

@login_required
def toggle_exam_active(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    exam.is_active = not exam.is_active
    exam.save()
    status_str = "Active" if exam.is_active else "Inactive"
    messages.success(request, f"Exam '{exam.title}' is now {status_str}.")
    return redirect('principal_dashboard')

@login_required
def review_submission(request, submission_id):
    submission = get_object_or_404(PortionSubmission, id=submission_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        remarks = request.POST.get('principal_remarks', '')
        submission.principal_remarks = remarks
        
        if action == 'approve':
            submission.status = 'APPROVED'
            messages.success(request, f"Submission for {submission.subject_name} ({submission.class_name}) has been Approved!")
        elif action == 'request_revision':
            submission.status = 'REVISION'
            messages.warning(request, f"Revision requested for {submission.subject_name} ({submission.class_name}).")
        submission.save()
        return redirect('exam_detail_dashboard', exam_id=submission.exam.id)

    return render(request, 'core/review_submission.html', {'submission': submission})


@login_required
def print_submission_view(request, submission_id):
    submission = get_object_or_404(PortionSubmission, id=submission_id)
    return render(request, 'core/print_submission.html', {'submission': submission})

@login_required
def compiled_exam_report(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    submissions = PortionSubmission.objects.filter(exam=exam).prefetch_related('pattern_items')
    
    # Group submissions by class/grade
    from collections import defaultdict
    grouped_classes = defaultdict(list)
    for sub in submissions:
        grouped_classes[sub.class_name].append(sub)
        
    class_reports = []
    for class_name, subs in grouped_classes.items():
        timetable_items = []
        for sub in subs:
            if sub.exam_date:
                timetable_items.append({
                    'date': sub.exam_date,
                    'subject': sub.subject_name
                })
        timetable_items.sort(key=lambda x: x['date'])
        
        class_reports.append({
            'class_name': class_name,
            'submissions': subs,
            'timetable': timetable_items
        })

    return render(request, 'core/compiled_report.html', {
        'exam': exam,
        'class_reports': class_reports
    })



# --- MASTER SUBJECT & DIVISION MANAGEMENT VIEWS (ONE-TIME SETUP) ---

@login_required
def manage_subjects(request):
    from .models import MasterSubject, PortionSubmission
    from .forms import MasterSubjectForm
    from django.db import models

    edit_subject_id = request.GET.get('edit')
    edit_instance = get_object_or_404(MasterSubject, id=edit_subject_id) if edit_subject_id else None

    if request.method == 'POST':
        form = MasterSubjectForm(request.POST, instance=edit_instance)
        if form.is_valid():
            subject = form.save()
            messages.success(request, f"Subject '{subject.name}' saved successfully!")
            return redirect('manage_subjects')
    else:
        form = MasterSubjectForm(instance=edit_instance)

    subjects = list(MasterSubject.objects.all())
    for subj in subjects:
        subj.linked_count = PortionSubmission.objects.filter(
            models.Q(master_subject=subj) | models.Q(subject_name__iexact=subj.name)
        ).count()

    return render(request, 'core/manage_subjects.html', {
        'subjects': subjects,
        'form': form,
        'edit_instance': edit_instance
    })

@login_required
def delete_subject(request, subject_id):
    from .models import MasterSubject, PortionSubmission
    from django.db import models
    subject = get_object_or_404(MasterSubject, id=subject_id)
    
    # Check if any PortionSubmission records exist for this subject
    linked_count = PortionSubmission.objects.filter(
        models.Q(master_subject=subject) | models.Q(subject_name__iexact=subject.name)
    ).count()

    if linked_count > 0:
        messages.error(
            request,
            f"Cannot delete subject '{subject.name}': It is currently used in {linked_count} examination portion record(s). Deletion is blocked to preserve portion data."
        )
        return redirect('manage_subjects')

    name = subject.name
    subject.delete()
    messages.success(request, f"Subject '{name}' deleted successfully.")
    return redirect('manage_subjects')

@login_required
def manage_divisions(request):
    from .models import Division, PortionSubmission
    from .forms import DivisionForm
    from django.db import models

    edit_div_id = request.GET.get('edit')
    edit_instance = get_object_or_404(Division, id=edit_div_id) if edit_div_id else None

    if request.method == 'POST':
        form = DivisionForm(request.POST, instance=edit_instance)
        if form.is_valid():
            div = form.save()
            messages.success(request, f"Division '{div}' saved successfully!")
            return redirect('manage_divisions')
    else:
        form = DivisionForm(instance=edit_instance)

    divisions = list(Division.objects.all())
    for div in divisions:
        div.linked_count = PortionSubmission.objects.filter(
            models.Q(division_ref=div) | models.Q(class_name__iexact=str(div))
        ).count()

    return render(request, 'core/manage_divisions.html', {
        'divisions': divisions,
        'form': form,
        'edit_instance': edit_instance
    })

@login_required
def delete_division(request, division_id):
    from .models import Division, PortionSubmission
    from django.db import models
    div = get_object_or_404(Division, id=division_id)
    
    # Check if any PortionSubmission records exist for this division
    linked_count = PortionSubmission.objects.filter(
        models.Q(division_ref=div) | models.Q(class_name__iexact=str(div))
    ).count()

    if linked_count > 0:
        messages.error(
            request,
            f"Cannot delete division '{div}': It is currently used in {linked_count} examination portion record(s). Deletion is blocked to preserve portion data."
        )
        return redirect('manage_divisions')

    name = str(div)
    div.delete()
    messages.success(request, f"Division '{name}' deleted successfully.")
    return redirect('manage_divisions')



# --- EXAM SUBJECT & DIVISION CUSTOMIZATION (BULK ALLOCATION) ---

@login_required
def customize_exam_subjects(request, exam_id):
    from .models import MasterSubject, Division
    exam = get_object_or_404(Exam, id=exam_id)
    subjects = MasterSubject.objects.all()
    divisions = Division.objects.all()

    # Pre-fetch existing allocations for this exam where foreign keys are present
    existing_submissions = PortionSubmission.objects.filter(
        exam=exam,
        master_subject__isnull=False,
        division_ref__isnull=False
    )
    
    allocated_pairs = {
        f"{sub.master_subject_id}_{sub.division_ref_id}": sub
        for sub in existing_submissions
    }

    has_existing_allocations = (existing_submissions.count() > 0)

    # Attach allocated_div_ids set to each subject object for fast native template checking
    for subj in subjects:
        subj.allocated_div_ids = set(
            sub.division_ref_id
            for sub in existing_submissions
            if sub.master_subject_id == subj.id
        )


    if request.method == 'POST':
        total_marks = int(request.POST.get('total_marks', 80))
        duration_minutes = int(request.POST.get('duration_minutes', 180))
        
        # Selected check boxes in format: "alloc_<subject_id>_<division_id>"
        selected_keys = set()
        for key in request.POST:
            if key.startswith('alloc_'):
                parts = key.split('_')
                if len(parts) == 3:
                    subj_id, div_id = int(parts[1]), int(parts[2])
                    selected_keys.add((subj_id, div_id))

        created_count = 0
        updated_count = 0
        deleted_count = 0

        # Create or Update selected allocations
        for subj_id, div_id in selected_keys:
            subj = get_object_or_404(MasterSubject, id=subj_id)
            div = get_object_or_404(Division, id=div_id)
            
            pair_key = f"{subj_id}_{div_id}"

            if pair_key in allocated_pairs:
                sub = allocated_pairs[pair_key]
                sub.total_marks = total_marks
                sub.duration_minutes = duration_minutes
                sub.save()
                updated_count += 1
            else:
                PortionSubmission.objects.create(
                    exam=exam,
                    master_subject=subj,
                    division_ref=div,
                    subject_name=subj.name,
                    class_name=str(div),
                    teacher_name="Teacher",
                    total_marks=total_marks,
                    duration_minutes=duration_minutes,
                    status='PENDING'
                )
                created_count += 1

        # Remove allocations that were unchecked/disabled by the Principal
        for pair_key, sub in list(allocated_pairs.items()):
            subj_id, div_id = sub.master_subject_id, sub.division_ref_id
            if (subj_id, div_id) not in selected_keys:
                sub.delete()
                deleted_count += 1

        messages.success(request, f"Customization saved for '{exam.title}'! Created {created_count} allocations, updated {updated_count}, removed {deleted_count} disabled allocations.")
        return redirect('principal_dashboard')

    context = {
        'exam': exam,
        'subjects': subjects,
        'divisions': divisions,
        'has_existing_allocations': has_existing_allocations,
    }
    return render(request, 'core/customize_exam_subjects.html', context)




# --- TEACHER PUBLIC VIEWS (NO LOGIN REQUIRED) ---

def teacher_portal(request):
    """
    Public portal home where teachers select Exam -> Division -> Subject to add or edit portion details.
    No login or individual links required!
    """
    from .models import Division, MasterSubject
    import json
    from collections import defaultdict
    
    active_exams = Exam.objects.filter(is_active=True)
    divisions = Division.objects.all()
    subjects = MasterSubject.objects.all()

    # Active allocated portion records
    active_requests = PortionSubmission.objects.filter(exam__is_active=True).select_related('exam', 'master_subject', 'division_ref')

    # Build JSON map of allocated subjects per exam & division: "examId_divId": [ {id: ..., name: ...}, ... ]
    alloc_map = defaultdict(list)
    for sub in active_requests:
        if sub.master_subject_id and sub.division_ref_id:
            key = f"{sub.exam_id}_{sub.division_ref_id}"
            if not any(item['id'] == sub.master_subject_id for item in alloc_map[key]):
                alloc_map[key].append({
                    'id': sub.master_subject_id,
                    'name': sub.subject_name,
                    'code': (sub.master_subject.code if sub.master_subject else '') or ''
                })

    if request.method == 'POST':
        exam_id = request.POST.get('exam_id')
        division_id = request.POST.get('division_id')
        subject_id = request.POST.get('subject_id')

        if exam_id and division_id and subject_id:
            submission = PortionSubmission.objects.filter(
                exam_id=exam_id,
                division_ref_id=division_id,
                master_subject_id=subject_id
            ).first()

            if submission:
                return redirect('teacher_submit_portion', token=submission.id)
            else:
                messages.error(request, "The selected subject is not allocated for this examination and division.")
        else:
            messages.error(request, "Please select an Active Examination, Division, and Subject to continue.")

    context = {
        'active_exams': active_exams,
        'divisions': divisions,
        'subjects': subjects,
        'active_requests': active_requests,
        'alloc_map_json': json.dumps(alloc_map),
    }
    return render(request, 'core/teacher_portal.html', context)


def teacher_submit_portion(request, token):
    """
    Public access route for teachers to submit or edit their syllabus portion and question pattern.
    No account creation or password required!
    """
    submission = get_object_or_404(PortionSubmission, id=token)

    if not submission.exam.is_active:
        messages.error(request, f"Examination '{submission.exam.title}' is currently inactive. Submissions for this exam are closed.")
        return redirect('teacher_portal')

    # Read-only check: Once APPROVED, submission cannot be edited!
    is_readonly = (submission.status == 'APPROVED')

    if request.method == 'POST':
        if is_readonly:
            messages.warning(request, f"Submission for {submission.subject_name} ({submission.class_name}) has already been Approved by the Principal and is locked.")
            return redirect('teacher_submit_portion', token=submission.id)

        form = TeacherSubmissionForm(request.POST, instance=submission)
        formset = QuestionPatternFormSet(request.POST, instance=submission)
        
        form_valid = form.is_valid()
        formset_valid = formset.is_valid()

        # Count fully filled rows and detect incomplete rows
        valid_row_count = 0
        has_incomplete_row = False

        if formset_valid:
            for pattern_form in formset.forms:
                if pattern_form.cleaned_data and not pattern_form.cleaned_data.get('DELETE', False):
                    q_type = str(pattern_form.cleaned_data.get('question_type') or '').strip()
                    num_q = pattern_form.cleaned_data.get('number_of_questions')
                    marks_p_q = pattern_form.cleaned_data.get('marks_per_question')
                    
                    if q_type and num_q and marks_p_q:
                        valid_row_count += 1
                    elif q_type or num_q or marks_p_q:
                        has_incomplete_row = True

        if not form_valid:
            messages.error(request, "Validation Error: Please fill in all required fields (Teacher Name & Chapters / Units / Topics Included).")
        elif has_incomplete_row:
            messages.error(request, "Validation Error: Every Question Paper Blueprint row must be fully filled out (Question Type, No. of Qs, Marks / Q). Incomplete rows are not allowed.")
        elif valid_row_count == 0:
            messages.error(request, "Validation Error: At least one complete Question Paper Blueprint row is required.")
        elif formset_valid:
            sub = form.save(commit=False)
            
            # Calculate sum of section marks for non-deleted rows
            sum_section_marks = 0.0
            for pattern_form in formset.forms:
                if pattern_form.cleaned_data and not pattern_form.cleaned_data.get('DELETE', False):
                    q_type = str(pattern_form.cleaned_data.get('question_type') or '').strip()
                    if q_type:
                        num_q = pattern_form.cleaned_data.get('number_of_questions', 0) or 0
                        marks_p_q = pattern_form.cleaned_data.get('marks_per_question', 0.0) or 0.0
                        sum_section_marks += (num_q * marks_p_q)

            # Auto-update total_marks to match exact section breakdown sum
            if valid_row_count > 0 and sum_section_marks > 0:
                sub.total_marks = int(round(sum_section_marks))

            sub.status = 'SUBMITTED'
            sub.submitted_at = timezone.now()
            sub.save()
            formset.save()
            
            messages.success(request, f"Thank you! Syllabus Portion & Question Pattern for {sub.subject_name} ({sub.class_name}) saved & submitted successfully.")
            return redirect('teacher_submission_success', token=submission.id)
        else:
            messages.error(request, "Please fix the validation errors in the form below.")

    else:
        form = TeacherSubmissionForm(instance=submission)
        formset = QuestionPatternFormSet(instance=submission)

    context = {
        'submission': submission,
        'form': form,
        'formset': formset,
        'is_readonly': is_readonly,
    }
    return render(request, 'core/teacher_submit_form.html', context)




def teacher_submission_success(request, token):
    submission = get_object_or_404(PortionSubmission, id=token)
    return render(request, 'core/teacher_success.html', {'submission': submission})

