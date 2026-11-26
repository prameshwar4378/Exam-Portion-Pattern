import json
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .cache_utils import invalidate_on_commit, make_cache_key
from .forms import (
    DivisionForm,
    ExamForm,
    ExamTimetableForm,
    MasterSubjectForm,
    QuestionPatternFormSet,
    TeacherSubmissionForm,
    TimetableEntryFormSet,
)
from .models import (
    Division,
    Exam,
    ExamTimetable,
    MasterSubject,
    PortionSubmission,
    QuestionPatternItem,
    TimetableEntry,
)


def principal_login_view(request):
    if request.user.is_authenticated:
        return redirect('principal_dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, Principal {user.first_name or user.username}!")
                return redirect('principal_dashboard')
            messages.error(request, "Invalid username or password.")
        else:
            messages.error(request, "Invalid login credentials.")
    else:
        form = AuthenticationForm()

    return render(request, 'core/login.html', {'form': form})


@require_POST
def principal_logout_view(request):
    logout(request)
    messages.info(request, "Logged out successfully.")
    return redirect('login')


@login_required
def principal_dashboard(request):
    exams = list(
        Exam.objects.annotate(
            total=Count('submissions'),
            pending=Count('submissions', filter=Q(submissions__status='PENDING')),
            submitted=Count('submissions', filter=Q(submissions__status='SUBMITTED')),
            approved=Count('submissions', filter=Q(submissions__status='APPROVED')),
            revision=Count('submissions', filter=Q(submissions__status='REVISION')),
        )
    )

    exam_list = [
        {
            'exam': exam,
            'total': exam.total,
            'pending': exam.pending,
            'submitted': exam.submitted,
            'approved': exam.approved,
            'revision': exam.revision,
        }
        for exam in exams
    ]

    context = {
        'exam_list': exam_list,
        'total_exams': len(exams),
    }
    return render(request, 'core/principal_dashboard.html', context)


@login_required
def exam_detail_dashboard(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    submissions = list(
        PortionSubmission.objects
        .filter(exam=exam)
        .select_related('division_ref')
        .defer('syllabus_portion', 'instructions_for_students', 'principal_remarks')
        .order_by('order', 'subject_name', 'created_at')
    )

    stats = {
        'total': len(submissions),
        'pending': 0,
        'submitted': 0,
        'approved': 0,
        'revision': 0,
        'pending_hidden_targets': 0,
    }

    grouped_dict = defaultdict(list)
    subj_meta = {}
    for sub in submissions:
        grouped_dict[sub.display_class_name].append(sub)
        if sub.status == 'PENDING' and (sub.total_marks or 0) == 0 and (sub.duration_minutes or 0) == 0:
            stats['pending_hidden_targets'] += 1

        meta = subj_meta.get(sub.subject_name)
        if meta is None:
            meta = {'count': 0, 'marks': set(), 'durations': set()}
            subj_meta[sub.subject_name] = meta
        meta['count'] += 1
        meta['marks'].add(sub.total_marks or 0)
        meta['durations'].add(sub.duration_minutes or 0)

    distinct_subjects = []
    for idx, subj_name in enumerate(sorted(subj_meta.keys(), key=lambda x: x.lower())):
        meta = subj_meta[subj_name]
        marks_set = meta['marks']
        dur_set = meta['durations']

        if len(marks_set) == 1:
            sole_m = next(iter(marks_set))
            marks_val = sole_m if sole_m > 0 else ''
            marks_label = f'{sole_m}M' if sole_m > 0 else 'From Teacher'
        else:
            marks_val = ''
            marks_label = 'Mixed'

        if len(dur_set) == 1:
            sole_d = next(iter(dur_set))
            dur_val = sole_d if sole_d > 0 else ''
            dur_label = f'{sole_d}m' if sole_d > 0 else 'From Teacher'
        else:
            dur_val = ''
            dur_label = 'Mixed'

        distinct_subjects.append({
            'key': idx,
            'subject_name': subj_name,
            'count': meta['count'],
            'marks_val': marks_val,
            'marks_label': marks_label,
            'dur_val': dur_val,
            'dur_label': dur_label,
        })

    submission_groups = []
    for class_name, subs in grouped_dict.items():
        pending_cnt = 0
        submitted_cnt = 0
        approved_cnt = 0
        revision_cnt = 0
        for s in subs:
            if s.status == 'PENDING':
                pending_cnt += 1
            elif s.status == 'SUBMITTED':
                submitted_cnt += 1
            elif s.status == 'APPROVED':
                approved_cnt += 1
            elif s.status == 'REVISION':
                revision_cnt += 1

        stats['pending'] += pending_cnt
        stats['submitted'] += submitted_cnt
        stats['approved'] += approved_cnt
        stats['revision'] += revision_cnt

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
        'distinct_subjects': distinct_subjects,
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


def _parse_optional_non_negative_int(raw_val):
    """Returns (provided, parsed_int_or_None) for optional non-negative form inputs."""
    text = str(raw_val or '').strip()
    if not text:
        return False, None
    try:
        parsed = int(text)
    except (ValueError, TypeError):
        return True, None
    if parsed < 0:
        return True, None
    return True, parsed


def _apply_status_scope(queryset, status_scope):
    if status_scope == 'all':
        return queryset
    if status_scope == 'non_approved':
        return queryset.exclude(status='APPROVED')
    return queryset.filter(status__in=['PENDING', 'REVISION'])


def _safe_redirect_target(request, fallback_url_name, **fallback_kwargs):
    next_url = str(request.POST.get('next') or '').strip()
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(fallback_url_name, **fallback_kwargs)


@login_required
def edit_exam(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    was_hidden = exam.hide_target_before_submission
    if request.method == 'POST':
        form = ExamForm(request.POST, instance=exam)
        if form.is_valid():
            with transaction.atomic():
                exam = form.save()
                if exam.hide_target_before_submission and not was_hidden:
                    PortionSubmission.objects.filter(exam=exam, status='PENDING').update(
                        total_marks=0,
                        duration_minutes=0,
                    )
                    invalidate_on_commit()
            messages.success(request, f"Exam '{exam.title}' updated successfully!")
            return redirect('principal_dashboard')
    else:
        form = ExamForm(instance=exam)
    return render(request, 'core/exam_form.html', {'form': form, 'edit_instance': exam})


@login_required
@require_POST
def delete_exam(request, exam_id):
    """Delete an exam and cascade-delete all related submissions, patterns, and timetables."""
    exam = get_object_or_404(Exam, id=exam_id)
    exam_title = exam.title
    exam.delete()
    messages.success(request, f"Exam '{exam_title}' and all its related records were deleted successfully.")
    return redirect('principal_dashboard')


@login_required
@require_POST
def toggle_exam_active(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    exam.is_active = not exam.is_active
    exam.save(update_fields=['is_active'])
    status_str = "Active" if exam.is_active else "Inactive"
    messages.success(request, f"Exam '{exam.title}' is now {status_str}.")
    return redirect('principal_dashboard')


@login_required
@require_POST
def toggle_exam_target_marks_timing(request, exam_id):
    """Toggles whether pre-set Marks & Timing are removed before teacher submission."""
    exam = get_object_or_404(Exam, id=exam_id)
    hide_targets = not exam.hide_target_before_submission

    with transaction.atomic():
        exam.hide_target_before_submission = hide_targets
        exam.save(update_fields=['hide_target_before_submission'])

        if hide_targets:
            cleared_cnt = PortionSubmission.objects.filter(exam=exam, status='PENDING').update(
                total_marks=0,
                duration_minutes=0,
            )
            msg = (
                f"Removed pre-set Target Marks & Timing on {cleared_cnt} pending subject(s). "
                "Teachers will enter Marks & Exam Duration when submitting."
            )
        else:
            _, restore_m = _parse_optional_non_negative_int(request.POST.get('restore_total_marks'))
            _, restore_d = _parse_optional_non_negative_int(request.POST.get('restore_duration_minutes'))
            restore_m = restore_m if (restore_m and restore_m > 0) else 80
            restore_d = restore_d if (restore_d and restore_d > 0) else 180
            restored_m_cnt = PortionSubmission.objects.filter(
                exam=exam, status='PENDING', total_marks=0
            ).update(total_marks=restore_m)
            restored_d_cnt = PortionSubmission.objects.filter(
                exam=exam, status='PENDING', duration_minutes=0
            ).update(duration_minutes=restore_d)
            restored_cnt = max(restored_m_cnt, restored_d_cnt)
            msg = (
                f"Pre-set Target Marks ({restore_m}M) & Duration ({restore_d} mins) enabled "
                f"(applied to {restored_cnt} pending subject(s))."
            )
        invalidate_on_commit()

    messages.success(request, msg)
    return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=exam.id)


@login_required
@require_POST
def bulk_update_exam_marks_timing(request, exam_id):
    """Bulk updates Marks and/or Duration either for all subjects or per subject across divisions."""
    exam = get_object_or_404(Exam, id=exam_id)
    mode = str(request.POST.get('update_mode') or 'all').strip()
    status_scope = str(request.POST.get('status_scope') or 'pending_only').strip()
    now = timezone.now()

    if mode == 'per_subject':
        row_keys = request.POST.getlist('subject_row_key')
        updates_by_subj = {}
        for key in row_keys:
            subj_name = str(request.POST.get(f'subject_name_{key}') or '').strip()
            if not subj_name:
                continue
            m_set, m_val = _parse_optional_non_negative_int(request.POST.get(f'subj_marks_{key}'))
            d_set, d_val = _parse_optional_non_negative_int(request.POST.get(f'subj_duration_{key}'))
            if (m_set and m_val is None) or (d_set and d_val is None):
                messages.error(request, f"Invalid marks or timing entered for '{subj_name}'. Values must be non-negative whole numbers.")
                return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=exam.id)
            if m_set or d_set:
                updates_by_subj[subj_name] = (m_val, d_val)

        if not updates_by_subj:
            messages.warning(request, "No subject marks or timing values were entered to update.")
            return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=exam.id)

        with transaction.atomic():
            targets = list(
                _apply_status_scope(
                    PortionSubmission.objects.filter(exam=exam, subject_name__in=list(updates_by_subj.keys())),
                    status_scope,
                ).only('id', 'subject_name', 'total_marks', 'duration_minutes', 'updated_at')
            )
            to_update = []
            for sub in targets:
                m_val, d_val = updates_by_subj[sub.subject_name]
                changed = False
                if m_val is not None and sub.total_marks != m_val:
                    sub.total_marks = m_val
                    changed = True
                if d_val is not None and sub.duration_minutes != d_val:
                    sub.duration_minutes = d_val
                    changed = True
                if changed:
                    sub.updated_at = now
                    to_update.append(sub)

            if to_update:
                PortionSubmission.objects.bulk_update(
                    to_update, ['total_marks', 'duration_minutes', 'updated_at']
                )
                invalidate_on_commit()

        messages.success(
            request,
            f"Updated marks/timing across {len(to_update)} allocation record(s) for {len(updates_by_subj)} subject(s)."
        )
        return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=exam.id)

    # Default mode: apply uniform Marks and/or Timing across all subjects in scope
    m_set, m_val = _parse_optional_non_negative_int(request.POST.get('bulk_total_marks'))
    d_set, d_val = _parse_optional_non_negative_int(request.POST.get('bulk_duration_minutes'))
    if (m_set and m_val is None) or (d_set and d_val is None):
        messages.error(request, "Please enter valid non-negative whole numbers for Marks and Duration.")
        return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=exam.id)
    if not m_set and not d_set:
        messages.warning(request, "Enter Total Marks and/or Duration to perform a bulk update.")
        return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=exam.id)

    update_kwargs = {'updated_at': now}
    if m_set:
        update_kwargs['total_marks'] = m_val
    if d_set:
        update_kwargs['duration_minutes'] = d_val

    with transaction.atomic():
        qs = _apply_status_scope(PortionSubmission.objects.filter(exam=exam), status_scope)
        updated_cnt = qs.update(**update_kwargs)
        if m_set and m_val == 0 and d_set and d_val == 0 and not exam.hide_target_before_submission:
            exam.hide_target_before_submission = True
            exam.save(update_fields=['hide_target_before_submission'])
        elif exam.hide_target_before_submission and ((m_val and m_val > 0) or (d_val and d_val > 0)) and request.POST.get('enable_preset_targets') == 'on':
            exam.hide_target_before_submission = False
            exam.save(update_fields=['hide_target_before_submission'])
        invalidate_on_commit()

    messages.success(request, f"Bulk updated marks/timing on {updated_cnt} record(s) in '{exam.title}'.")
    return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=exam.id)


@login_required
@require_POST
def update_submission_marks_timing(request, submission_id):
    """Updates Marks and Timing for one submission row and optionally all classes of that subject."""
    submission = get_object_or_404(PortionSubmission, id=submission_id)
    m_set, m_val = _parse_optional_non_negative_int(request.POST.get('total_marks'))
    d_set, d_val = _parse_optional_non_negative_int(request.POST.get('duration_minutes'))

    if (m_set and m_val is None) or (d_set and d_val is None) or (not m_set and not d_set):
        messages.error(request, "Please enter valid non-negative Marks or Duration.")
        return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=submission.exam_id)

    new_marks = m_val if m_set else submission.total_marks
    new_duration = d_val if d_set else submission.duration_minutes
    apply_all_classes = request.POST.get('apply_to_subject_all_classes') == 'on'
    include_approved = request.POST.get('include_approved') == 'on'
    now = timezone.now()

    with transaction.atomic():
        submission.total_marks = new_marks
        submission.duration_minutes = new_duration
        submission.updated_at = now
        submission.save(update_fields=['total_marks', 'duration_minutes', 'updated_at'])

        extra_cnt = 0
        if apply_all_classes:
            peer_qs = PortionSubmission.objects.filter(
                exam_id=submission.exam_id,
                subject_name=submission.subject_name,
            ).exclude(id=submission.id)
            if not include_approved:
                peer_qs = peer_qs.exclude(status='APPROVED')
            extra_cnt = peer_qs.update(
                total_marks=new_marks,
                duration_minutes=new_duration,
                updated_at=now,
            )
        invalidate_on_commit()

    if apply_all_classes:
        messages.success(
            request,
            f"Updated {submission.subject_name} across {1 + extra_cnt} class division(s) to {new_marks} Marks / {new_duration} mins."
        )
    else:
        messages.success(
            request,
            f"Updated {submission.subject_name} ({submission.display_class_name}) to {new_marks} Marks / {new_duration} mins."
        )
    return _safe_redirect_target(request, 'exam_detail_dashboard', exam_id=submission.exam_id)


@login_required
def review_submission(request, submission_id):
    submission = get_object_or_404(
        PortionSubmission.objects
        .select_related('exam', 'division_ref')
        .prefetch_related('pattern_items'),
        id=submission_id,
    )

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
        submission.save(update_fields=['status', 'principal_remarks', 'updated_at'])
        return redirect('exam_detail_dashboard', exam_id=submission.exam_id)

    return render(request, 'core/review_submission.html', {'submission': submission})


@login_required
def print_submission_view(request, submission_id):
    submission = get_object_or_404(
        PortionSubmission.objects
        .select_related('exam')
        .prefetch_related('pattern_items'),
        id=submission_id,
    )
    return render(request, 'core/print_submission.html', {'submission': submission})


@login_required
@require_POST
def delete_submission(request, submission_id):
    submission = get_object_or_404(PortionSubmission, id=submission_id)
    exam_id = submission.exam_id
    subject_name = submission.subject_name
    class_name = submission.class_name

    with transaction.atomic():
        submission.syllabus_portion = ''
        submission.instructions_for_students = ''
        submission.status = 'PENDING'
        submission.submitted_at = None
        submission.teacher_name = ''
        submission.principal_remarks = ''
        submission.save(update_fields=[
            'syllabus_portion',
            'instructions_for_students',
            'status',
            'submitted_at',
            'teacher_name',
            'principal_remarks',
            'updated_at',
        ])
        submission.pattern_items.all().delete()
        invalidate_on_commit()

    messages.success(request, f"Submission content for {subject_name} ({class_name}) has been cleared and reset to Pending!")
    return redirect('exam_detail_dashboard', exam_id=exam_id)


@login_required
def compiled_exam_report(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    submissions = list(
        PortionSubmission.objects
        .filter(exam=exam)
        .select_related('division_ref')
        .prefetch_related('pattern_items')
        .order_by('order', 'subject_name', 'created_at')
    )

    # Pre-load all timetables & entries for this exam in 3 total queries (no loop queries)
    timetables = list(
        ExamTimetable.objects
        .filter(exam=exam)
        .prefetch_related('divisions', 'entries')
    )
    div_tt_map = {}
    global_tt = None
    for tt in timetables:
        tt_divs = list(tt.divisions.all())
        # TimetableEntry.Meta.ordering already orders by ['exam_date', 'order']; do not call .order_by() here
        tt_obj = {
            'name': tt.name,
            'entries': list(tt.entries.all()),
        }
        if not tt_divs:
            if global_tt is None:
                global_tt = tt_obj
        else:
            for div in tt_divs:
                if div.id not in div_tt_map:
                    div_tt_map[div.id] = tt_obj

    grouped_classes = defaultdict(list)
    for sub in submissions:
        grouped_classes[sub.display_class_name].append(sub)

    class_reports = []
    for class_name, subs in grouped_classes.items():
        first_sub = subs[0] if subs else None
        div_ref_id = first_sub.division_ref_id if first_sub else None

        timetable_obj = div_tt_map.get(div_ref_id) if div_ref_id else None
        if timetable_obj is None:
            timetable_obj = global_tt

        if timetable_obj is None:
            fallback_items = [
                {
                    'exam_date': sub.exam_date,
                    'subject_name': sub.subject_name,
                }
                for sub in subs
                if sub.exam_date
            ]
            fallback_items.sort(key=lambda x: x['exam_date'])
            if fallback_items:
                timetable_obj = {
                    'name': f"{exam.title} TIMETABLE-{exam.academic_year}",
                    'entries': fallback_items,
                }

        class_reports.append({
            'class_name': class_name,
            'submissions': subs,
            'timetable': timetable_obj,
        })

    return render(request, 'core/compiled_report.html', {
        'exam': exam,
        'class_reports': class_reports,
    })


@login_required
def reorder_submissions(request, exam_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

    exam = get_object_or_404(Exam, id=exam_id)
    try:
        data = json.loads(request.body)
        ordered_ids = data.get('ordered_ids', [])
        if not isinstance(ordered_ids, list):
            return JsonResponse({'status': 'error', 'message': 'ordered_ids must be a list.'}, status=400)

        order_map = {str(sub_id): index for index, sub_id in enumerate(ordered_ids)}
        if order_map:
            with transaction.atomic():
                subs = list(
                    PortionSubmission.objects
                    .filter(exam=exam, id__in=list(order_map.keys()))
                    .only('id', 'order')
                )
                to_update = []
                for sub in subs:
                    new_order = order_map.get(str(sub.id))
                    if new_order is not None and sub.order != new_order:
                        sub.order = new_order
                        to_update.append(sub)
                if to_update:
                    PortionSubmission.objects.bulk_update(to_update, ['order'])
                    invalidate_on_commit()

        return JsonResponse({'status': 'success', 'message': 'Subject sequence updated successfully!'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# --- MASTER SUBJECT & DIVISION MANAGEMENT VIEWS (ONE-TIME SETUP) ---

@login_required
def manage_subjects(request):
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

    subjects = list(MasterSubject.objects.annotate(direct_cnt=Count('submissions')))
    unlinked_counts = {
        row['lname']: row['cnt']
        for row in PortionSubmission.objects
        .filter(master_subject__isnull=True)
        .annotate(lname=Lower('subject_name'))
        .values('lname')
        .annotate(cnt=Count('id'))
    }
    for subj in subjects:
        subj.linked_count = subj.direct_cnt + unlinked_counts.get(subj.name.lower(), 0)

    return render(request, 'core/manage_subjects.html', {
        'subjects': subjects,
        'form': form,
        'edit_instance': edit_instance,
    })


@login_required
@require_POST
def delete_subject(request, subject_id):
    subject = get_object_or_404(MasterSubject, id=subject_id)

    linked_count = PortionSubmission.objects.filter(
        Q(master_subject=subject) | Q(subject_name__iexact=subject.name)
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

    divisions = list(Division.objects.annotate(direct_cnt=Count('submissions')))
    unlinked_counts = {
        row['lname']: row['cnt']
        for row in PortionSubmission.objects
        .filter(division_ref__isnull=True)
        .annotate(lname=Lower('class_name'))
        .values('lname')
        .annotate(cnt=Count('id'))
    }
    for div in divisions:
        div.linked_count = div.direct_cnt + unlinked_counts.get(str(div).lower(), 0)

    return render(request, 'core/manage_divisions.html', {
        'divisions': divisions,
        'form': form,
        'edit_instance': edit_instance,
    })


@login_required
@require_POST
def delete_division(request, division_id):
    div = get_object_or_404(Division, id=division_id)

    linked_count = PortionSubmission.objects.filter(
        Q(division_ref=div) | Q(class_name__iexact=str(div))
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
    exam = get_object_or_404(Exam, id=exam_id)
    subjects = list(MasterSubject.objects.all())
    divisions = list(Division.objects.all())

    existing_submissions = list(
        PortionSubmission.objects
        .filter(
            exam=exam,
            master_subject__isnull=False,
            division_ref__isnull=False,
        )
        .only('id', 'master_subject_id', 'division_ref_id', 'status', 'total_marks', 'duration_minutes')
    )

    allocated_pairs = {}
    allocated_by_subj = defaultdict(set)
    for sub in existing_submissions:
        allocated_pairs[(sub.master_subject_id, sub.division_ref_id)] = sub
        allocated_by_subj[sub.master_subject_id].add(sub.division_ref_id)

    has_existing_allocations = len(existing_submissions) > 0

    for subj in subjects:
        subj.allocated_div_ids = allocated_by_subj.get(subj.id, set())

    other_exams = list(
        Exam.objects
        .exclude(id=exam.id)
        .annotate(
            alloc_count=Count(
                'submissions',
                filter=Q(
                    submissions__master_subject__isnull=False,
                    submissions__division_ref__isnull=False,
                ),
            )
        )
        .order_by('-created_at')
    )

    if request.method == 'POST':
        hide_target = request.POST.get('hide_target_before_submission') == 'on'
        apply_defaults_to_existing = request.POST.get('apply_defaults_to_existing') == 'on'
        try:
            total_marks = int(request.POST.get('total_marks', 80))
        except (ValueError, TypeError):
            total_marks = 80
        try:
            duration_minutes = int(request.POST.get('duration_minutes', 180))
        except (ValueError, TypeError):
            duration_minutes = 180

        if hide_target:
            alloc_marks = 0
            alloc_duration = 0
        else:
            alloc_marks = max(total_marks, 0)
            alloc_duration = max(duration_minutes, 0)

        selected_keys = set()
        for key in request.POST:
            if key.startswith('alloc_'):
                parts = key.split('_')
                if len(parts) == 3:
                    try:
                        subj_id, div_id = int(parts[1]), int(parts[2])
                        selected_keys.add((subj_id, div_id))
                    except ValueError:
                        continue

        subjects_by_id = {s.id: s for s in subjects}
        divisions_by_id = {d.id: d for d in divisions}

        to_create = []
        to_update = []
        updated_count = 0

        for subj_id, div_id in selected_keys:
            subj = subjects_by_id.get(subj_id)
            div = divisions_by_id.get(div_id)
            if subj is None:
                subj = get_object_or_404(MasterSubject, id=subj_id)
            if div is None:
                div = get_object_or_404(Division, id=div_id)

            pair_key = (subj_id, div_id)
            if pair_key in allocated_pairs:
                sub = allocated_pairs[pair_key]
                updated_count += 1
                if hide_target:
                    # Only clear PENDING targets when removing pre-set Marks & Timing
                    if sub.status == 'PENDING' and (sub.total_marks != 0 or sub.duration_minutes != 0):
                        sub.total_marks = 0
                        sub.duration_minutes = 0
                        to_update.append(sub)
                elif apply_defaults_to_existing or (sub.status == 'PENDING' and sub.total_marks == 0 and sub.duration_minutes == 0):
                    if sub.total_marks != alloc_marks or sub.duration_minutes != alloc_duration:
                        sub.total_marks = alloc_marks
                        sub.duration_minutes = alloc_duration
                        to_update.append(sub)
            else:
                to_create.append(
                    PortionSubmission(
                        exam=exam,
                        master_subject=subj,
                        division_ref=div,
                        subject_name=subj.name,
                        class_name=str(div),
                        teacher_name="",
                        total_marks=alloc_marks,
                        duration_minutes=alloc_duration,
                        status='PENDING',
                    )
                )

        to_delete_ids = [
            sub.id
            for pair_key, sub in allocated_pairs.items()
            if pair_key not in selected_keys
        ]

        with transaction.atomic():
            if exam.hide_target_before_submission != hide_target:
                exam.hide_target_before_submission = hide_target
                exam.save(update_fields=['hide_target_before_submission'])
            if to_create:
                PortionSubmission.objects.bulk_create(to_create)
            if to_update:
                PortionSubmission.objects.bulk_update(to_update, ['total_marks', 'duration_minutes'])
            if to_delete_ids:
                PortionSubmission.objects.filter(id__in=to_delete_ids).delete()
            invalidate_on_commit()

        created_count = len(to_create)
        deleted_count = len(to_delete_ids)

        messages.success(
            request,
            f"Customization saved for '{exam.title}'! Created {created_count} allocations, updated {updated_count}, removed {deleted_count} disabled allocations."
        )
        return redirect('principal_dashboard')

    context = {
        'exam': exam,
        'subjects': subjects,
        'divisions': divisions,
        'has_existing_allocations': has_existing_allocations,
        'other_exams': other_exams,
    }
    return render(request, 'core/customize_exam_subjects.html', context)


@login_required
def copy_exam_allocations(request, exam_id):
    """
    Copies subject-division allocations from another selected exam into this exam.
    Supports 'replace' (mirror source exam) or 'merge' (add new allocations without deleting existing).
    """
    if request.method != 'POST':
        return redirect('customize_exam_subjects', exam_id=exam_id)

    target_exam = get_object_or_404(Exam, id=exam_id)
    source_exam_id = request.POST.get('source_exam_id')
    copy_mode = request.POST.get('copy_mode', 'replace')

    if not source_exam_id:
        messages.error(request, "Please select an exam to copy allocations from.")
        return redirect('customize_exam_subjects', exam_id=exam_id)

    source_exam = get_object_or_404(Exam, id=source_exam_id)
    source_allocs = list(
        PortionSubmission.objects
        .filter(
            exam=source_exam,
            master_subject__isnull=False,
            division_ref__isnull=False,
        )
        .select_related('master_subject', 'division_ref')
    )

    if not source_allocs:
        messages.warning(request, f"Source exam '{source_exam.title}' has no allocated subjects to copy.")
        return redirect('customize_exam_subjects', exam_id=exam_id)

    with transaction.atomic():
        if copy_mode == 'replace':
            PortionSubmission.objects.filter(exam=target_exam).delete()
            existing_pairs = set()
        else:
            existing_pairs = set(
                PortionSubmission.objects
                .filter(
                    exam=target_exam,
                    master_subject__isnull=False,
                    division_ref__isnull=False,
                )
                .values_list('master_subject_id', 'division_ref_id')
            )

        to_create = []
        for s in source_allocs:
            pair = (s.master_subject_id, s.division_ref_id)
            if pair not in existing_pairs:
                to_create.append(
                    PortionSubmission(
                        exam=target_exam,
                        master_subject=s.master_subject,
                        division_ref=s.division_ref,
                        subject_name=s.subject_name or (s.master_subject.name if s.master_subject else ""),
                        class_name=s.class_name or (str(s.division_ref) if s.division_ref else ""),
                        teacher_name="",
                        total_marks=0 if target_exam.hide_target_before_submission else (s.total_marks or 80),
                        duration_minutes=0 if target_exam.hide_target_before_submission else (s.duration_minutes or 180),
                        status='PENDING',
                    )
                )
                existing_pairs.add(pair)

        if to_create:
            PortionSubmission.objects.bulk_create(to_create)
        invalidate_on_commit()

    created_count = len(to_create)
    messages.success(
        request,
        f"Successfully copied {created_count} subject allocation(s) from '{source_exam.title}' to '{target_exam.title}'!"
    )
    return redirect('customize_exam_subjects', exam_id=exam_id)


# --- TEACHER PUBLIC VIEWS (NO LOGIN REQUIRED) ---

def teacher_portal(request):
    """
    Public portal home where teachers select Exam -> Division -> Subject to add or edit portion details.
    No login or individual links required!
    """
    if request.method == 'POST':
        exam_id = request.POST.get('exam_id')
        division_id = request.POST.get('division_id')
        subject_id = request.POST.get('subject_id')

        if exam_id and division_id and subject_id:
            submission_id = (
                PortionSubmission.objects
                .filter(
                    exam_id=exam_id,
                    division_ref_id=division_id,
                    master_subject_id=subject_id,
                )
                .values_list('id', flat=True)
                .first()
            )
            if submission_id:
                return redirect('teacher_submit_portion', token=submission_id)
            messages.error(request, "The selected subject is not allocated for this examination and division.")
        else:
            messages.error(request, "Please select an Active Examination, Division, and Subject to continue.")

    active_exams = list(Exam.objects.filter(is_active=True))
    divisions = list(Division.objects.all())

    active_requests = list(
        PortionSubmission.objects
        .filter(exam__is_active=True)
        .select_related('exam', 'master_subject')
        .only(
            'id',
            'exam_id',
            'exam__title',
            'master_subject_id',
            'master_subject__code',
            'division_ref_id',
            'class_name',
            'subject_name',
            'status',
            'total_marks',
            'order',
        )
    )

    alloc_cache_key = make_cache_key('web_teacher_portal_alloc_json')
    alloc_map_json = cache.get(alloc_cache_key)
    if alloc_map_json is None:
        alloc_map = defaultdict(list)
        seen_alloc = set()
        for sub in active_requests:
            if sub.master_subject_id and sub.division_ref_id:
                key = f"{sub.exam_id}_{sub.division_ref_id}"
                dedup_key = (key, sub.master_subject_id)
                if dedup_key not in seen_alloc:
                    seen_alloc.add(dedup_key)
                    alloc_map[key].append({
                        'id': sub.master_subject_id,
                        'name': sub.subject_name,
                        'code': (sub.master_subject.code if sub.master_subject else '') or '',
                    })
        alloc_map_json = json.dumps(alloc_map)
        cache.set(alloc_cache_key, alloc_map_json, 300)

    context = {
        'active_exams': active_exams,
        'divisions': divisions,
        'active_requests': active_requests,
        'alloc_map_json': alloc_map_json,
    }
    return render(request, 'core/teacher_portal.html', context)


def teacher_submit_portion(request, token):
    """
    Public access route for teachers to submit or edit their syllabus portion and question pattern.
    No account creation or password required!
    """
    submission = get_object_or_404(PortionSubmission.objects.select_related('exam'), id=token)

    if not submission.exam.is_active:
        messages.error(request, f"Examination '{submission.exam.title}' is currently inactive. Submissions for this exam are closed.")
        return redirect('teacher_portal')

    is_readonly = (submission.status == 'APPROVED')

    if request.method == 'POST':
        if is_readonly:
            messages.warning(request, f"Submission for {submission.subject_name} ({submission.class_name}) has already been Approved by the Principal and is locked.")
            return redirect('teacher_submit_portion', token=submission.id)

        form = TeacherSubmissionForm(request.POST, instance=submission)
        formset = QuestionPatternFormSet(request.POST, instance=submission)

        form_valid = form.is_valid()
        formset_valid = formset.is_valid()

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

            sum_section_marks = 0.0
            valid_pattern_rows = []
            for pattern_form in formset.forms:
                if pattern_form.cleaned_data and not pattern_form.cleaned_data.get('DELETE', False):
                    q_type = str(pattern_form.cleaned_data.get('question_type') or '').strip()
                    num_q = pattern_form.cleaned_data.get('number_of_questions', 0) or 0
                    marks_p_q = pattern_form.cleaned_data.get('marks_per_question', 0.0) or 0.0
                    sec_name = str(pattern_form.cleaned_data.get('section_name') or '').strip()
                    c_info = str(pattern_form.cleaned_data.get('choice_info') or '').strip()

                    if q_type:
                        sum_section_marks += (num_q * marks_p_q)
                    if q_type and num_q > 0 and marks_p_q > 0:
                        valid_pattern_rows.append({
                            'question_type': q_type,
                            'number_of_questions': num_q,
                            'marks_per_question': marks_p_q,
                            'section_name': sec_name,
                            'choice_info': c_info,
                        })

            if valid_row_count > 0 and sum_section_marks > 0:
                sub.total_marks = int(round(sum_section_marks))

            now = timezone.now()
            sub.status = 'SUBMITTED'
            sub.submitted_at = now

            raw_copy_target_ids = request.POST.getlist('copy_to_divisions')
            copy_target_ids = [cid.strip() for cid in raw_copy_target_ids if cid and cid.strip()][:2]

            with transaction.atomic():
                sub.save(update_fields=[
                    'teacher_name',
                    'syllabus_portion',
                    'instructions_for_students',
                    'total_marks',
                    'duration_minutes',
                    'status',
                    'submitted_at',
                    'updated_at',
                ])

                target_subs = []
                if copy_target_ids:
                    target_subs = list(
                        PortionSubmission.objects.filter(
                            id__in=copy_target_ids,
                            exam_id=submission.exam_id,
                            subject_name=submission.subject_name,
                        ).exclude(status='APPROVED')
                    )

                all_target_ids = [sub.id] + [t.id for t in target_subs]
                QuestionPatternItem.objects.filter(submission_id__in=all_target_ids).delete()

                pattern_objs = [
                    QuestionPatternItem(
                        submission=sub,
                        question_type=row['question_type'],
                        number_of_questions=row['number_of_questions'],
                        marks_per_question=row['marks_per_question'],
                        section_name=row['section_name'],
                        choice_info=row['choice_info'],
                    )
                    for row in valid_pattern_rows
                ]

                if target_subs:
                    for target_sub in target_subs:
                        target_sub.teacher_name = sub.teacher_name
                        target_sub.total_marks = sub.total_marks
                        target_sub.duration_minutes = sub.duration_minutes
                        target_sub.syllabus_portion = sub.syllabus_portion
                        target_sub.instructions_for_students = sub.instructions_for_students
                        target_sub.status = 'SUBMITTED'
                        target_sub.submitted_at = now
                        target_sub.updated_at = now

                        for row in valid_pattern_rows:
                            pattern_objs.append(
                                QuestionPatternItem(
                                    submission=target_sub,
                                    question_type=row['question_type'],
                                    number_of_questions=row['number_of_questions'],
                                    marks_per_question=row['marks_per_question'],
                                    section_name=row['section_name'],
                                    choice_info=row['choice_info'],
                                )
                            )

                    PortionSubmission.objects.bulk_update(
                        target_subs,
                        [
                            'teacher_name',
                            'total_marks',
                            'duration_minutes',
                            'syllabus_portion',
                            'instructions_for_students',
                            'status',
                            'submitted_at',
                            'updated_at',
                        ],
                    )

                QuestionPatternItem.objects.bulk_create(pattern_objs)
                invalidate_on_commit()

            copied_count = len(target_subs)
            if copied_count > 0:
                messages.success(request, f"Thank you! Syllabus Portion & Question Pattern saved & submitted for {sub.class_name} AND copied to {copied_count} other division(s) successfully!")
            else:
                messages.success(request, f"Thank you! Syllabus Portion & Question Pattern for {sub.subject_name} ({sub.class_name}) saved & submitted successfully.")

            return redirect('teacher_submission_success', token=submission.id)
        else:
            messages.error(request, "Please fix the validation errors in the form below.")
    else:
        form = TeacherSubmissionForm(instance=submission)
        formset = QuestionPatternFormSet(instance=submission)

    other_division_submissions = (
        PortionSubmission.objects
        .filter(exam_id=submission.exam_id, subject_name=submission.subject_name)
        .exclude(id=submission.id)
        .exclude(status='APPROVED')
        .only('id', 'class_name', 'status')
    )

    context = {
        'submission': submission,
        'form': form,
        'formset': formset,
        'is_readonly': is_readonly,
        'other_division_submissions': other_division_submissions,
    }
    return render(request, 'core/teacher_submit_form.html', context)


def teacher_submission_success(request, token):
    submission = get_object_or_404(PortionSubmission.objects.select_related('exam'), id=token)
    return render(request, 'core/teacher_success.html', {'submission': submission})


# --- EXAM TIMETABLE MANAGEMENT VIEWS ---

@login_required
def manage_timetables(request):
    active_exams = (
        Exam.objects
        .filter(is_active=True)
        .prefetch_related('timetables__divisions', 'timetables__entries')
    )

    return render(request, 'core/manage_timetables.html', {
        'active_exams': active_exams,
    })


@login_required
def create_timetable(request):
    if request.method == 'POST':
        form = ExamTimetableForm(request.POST)
        formset = TimetableEntryFormSet(request.POST)

        form_valid = form.is_valid()
        formset_valid = formset.is_valid()

        if form_valid and formset_valid:
            with transaction.atomic():
                timetable = form.save()
                entries_to_create = []
                for entry_form in formset.forms:
                    if entry_form.cleaned_data and not entry_form.cleaned_data.get('DELETE', False):
                        e_date = entry_form.cleaned_data.get('exam_date')
                        s_name = str(entry_form.cleaned_data.get('subject_name') or '').strip()
                        if e_date and s_name:
                            entry = entry_form.save(commit=False)
                            entry.timetable = timetable
                            entry.subject_name = s_name
                            entries_to_create.append(entry)
                if entries_to_create:
                    TimetableEntry.objects.bulk_create(entries_to_create)
                invalidate_on_commit()

            messages.success(request, f"Exam Timetable '{timetable.name}' created successfully!")
            return redirect('manage_timetables')
        else:
            if not form_valid:
                messages.error(request, f"Validation Error in Config: {form.errors.as_text()}")
            elif not formset_valid:
                messages.error(request, f"Validation Error in Schedule Entries: {formset.errors}")
    else:
        initial_exam_id = request.GET.get('exam_id')
        initial_dict = {'exam': initial_exam_id} if initial_exam_id else {}
        form = ExamTimetableForm(initial=initial_dict)
        formset = TimetableEntryFormSet()

    return render(request, 'core/timetable_form.html', {
        'form': form,
        'formset': formset,
        'is_edit': False,
    })


@login_required
def edit_timetable(request, timetable_id):
    timetable = get_object_or_404(ExamTimetable, id=timetable_id)

    if request.method == 'POST':
        form = ExamTimetableForm(request.POST, instance=timetable)
        formset = TimetableEntryFormSet(request.POST, instance=timetable)

        form_valid = form.is_valid()
        formset_valid = formset.is_valid()

        if form_valid and formset_valid:
            with transaction.atomic():
                tt = form.save()
                tt.entries.all().delete()
                entries_to_create = []
                for entry_form in formset.forms:
                    if entry_form.cleaned_data and not entry_form.cleaned_data.get('DELETE', False):
                        e_date = entry_form.cleaned_data.get('exam_date')
                        s_name = str(entry_form.cleaned_data.get('subject_name') or '').strip()
                        if e_date and s_name:
                            entry = entry_form.save(commit=False)
                            entry.timetable = tt
                            entry.subject_name = s_name
                            entries_to_create.append(entry)
                if entries_to_create:
                    TimetableEntry.objects.bulk_create(entries_to_create)
                invalidate_on_commit()

            messages.success(request, f"Exam Timetable '{tt.name}' updated successfully!")
            return redirect('manage_timetables')
        else:
            if not form_valid:
                messages.error(request, f"Validation Error in Config: {form.errors.as_text()}")
            elif not formset_valid:
                messages.error(request, f"Validation Error in Schedule Entries: {formset.errors}")
    else:
        form = ExamTimetableForm(instance=timetable)
        formset = TimetableEntryFormSet(instance=timetable)

    return render(request, 'core/timetable_form.html', {
        'form': form,
        'formset': formset,
        'timetable': timetable,
        'is_edit': True,
    })


@login_required
@require_POST
def delete_timetable(request, timetable_id):
    timetable = get_object_or_404(ExamTimetable, id=timetable_id)
    name = timetable.name
    timetable.delete()
    messages.success(request, f"Timetable '{name}' deleted successfully!")
    return redirect('manage_timetables')
