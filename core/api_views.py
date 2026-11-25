import json
import datetime
from collections import defaultdict
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Exam, Division, MasterSubject, PortionSubmission, QuestionPatternItem, ExamTimetable, TimetableEntry


def teacher_portal_api(request):
    """
    API endpoint returning data required for the Teacher Portal:
    - Active examinations
    - Divisions
    - Allocated subjects map per (exam_id, division_id)
    - Active allocated requests list
    """
    if request.method != 'GET':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    active_exams = Exam.objects.filter(is_active=True).values('id', 'title', 'academic_year')
    divisions = [
        {
            'id': div.id,
            'standard': div.standard,
            'division_name': div.division_name,
            'display_name': str(div),
        }
        for div in Division.objects.all()
    ]

    active_requests = PortionSubmission.objects.filter(exam__is_active=True).select_related(
        'exam', 'master_subject', 'division_ref'
    )

    alloc_map = defaultdict(list)
    requests_list = []

    for sub in active_requests:
        if sub.master_subject_id and sub.division_ref_id:
            key = f"{sub.exam_id}_{sub.division_ref_id}"
            if not any(item['id'] == sub.master_subject_id for item in alloc_map[key]):
                alloc_map[key].append({
                    'id': sub.master_subject_id,
                    'name': sub.subject_name,
                    'code': (sub.master_subject.code if sub.master_subject else '') or '',
                    'submission_id': str(sub.id),
                    'status': sub.status,
                })

        requests_list.append({
            'id': str(sub.id),
            'exam_id': sub.exam_id,
            'exam_title': sub.exam.title,
            'class_name': sub.class_name,
            'subject_name': sub.subject_name,
            'teacher_name': '' if (sub.status == 'PENDING' and (sub.teacher_name or '').strip().lower() == 'teacher') else (sub.teacher_name or ''),
            'status': sub.status,
            'status_display': sub.get_status_display(),
            'total_marks': sub.total_marks,
            'duration_minutes': sub.duration_minutes,
            'is_readonly': sub.status == 'APPROVED',
        })

    return JsonResponse({
        'status': 'success',
        'active_exams': list(active_exams),
        'divisions': divisions,
        'alloc_map': dict(alloc_map),
        'active_requests': requests_list,
    })


@csrf_exempt
@require_http_methods(['POST'])
def teacher_find_submission_api(request):
    """
    Finds a portion submission UUID given exam_id, division_id, and subject_id.
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST

    exam_id = data.get('exam_id')
    division_id = data.get('division_id')
    subject_id = data.get('subject_id')

    if not (exam_id and division_id and subject_id):
        return JsonResponse({
            'status': 'error',
            'message': 'Please provide exam_id, division_id, and subject_id.'
        }, status=400)

    submission = PortionSubmission.objects.filter(
        exam_id=exam_id,
        division_ref_id=division_id,
        master_subject_id=subject_id
    ).first()

    if not submission:
        return JsonResponse({
            'status': 'error',
            'message': 'The selected subject is not allocated for this examination and division.'
        }, status=404)

    return JsonResponse({
        'status': 'success',
        'submission_id': str(submission.id)
    })


def teacher_submission_detail_api(request, token):
    """
    Returns full details for a teacher portion submission:
    - Submission metadata (marks, teacher name, syllabus, status, principal remarks)
    - Question pattern blueprint items
    - Other divisions for copying
    """
    if request.method != 'GET':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    try:
        submission = PortionSubmission.objects.select_related('exam', 'division_ref').get(id=token)
    except PortionSubmission.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Submission not found'}, status=404)

    is_readonly = (submission.status == 'APPROVED')

    pattern_items = [
        {
            'id': item.id,
            'section_name': item.section_name or '',
            'question_type': item.question_type or '',
            'number_of_questions': item.number_of_questions,
            'marks_per_question': item.marks_per_question,
            'choice_info': item.choice_info or '',
            'total_marks': item.total_marks,
        }
        for item in submission.pattern_items.all()
    ]

    other_division_subs = PortionSubmission.objects.filter(
        exam=submission.exam,
        subject_name=submission.subject_name
    ).exclude(id=submission.id).exclude(status='APPROVED').select_related('division_ref')

    other_divisions = [
        {
            'id': str(other.id),
            'class_name': other.class_name,
            'status': other.status,
            'status_display': other.get_status_display(),
        }
        for other in other_division_subs
    ]

    return JsonResponse({
        'status': 'success',
        'submission': {
            'id': str(submission.id),
            'exam_id': submission.exam.id,
            'exam_title': submission.exam.title,
            'academic_year': submission.exam.academic_year,
            'is_exam_active': submission.exam.is_active,
            'class_name': submission.class_name,
            'subject_name': submission.subject_name,
            'teacher_name': '' if (submission.status == 'PENDING' and (submission.teacher_name or '').strip().lower() == 'teacher') else (submission.teacher_name or ''),
            'status': submission.status,
            'status_display': submission.get_status_display(),
            'is_readonly': is_readonly,
            'principal_remarks': submission.principal_remarks or '',
            'total_marks': submission.total_marks,
            'duration_minutes': submission.duration_minutes,
            'syllabus_portion': submission.syllabus_portion or '',
            'instructions_for_students': submission.instructions_for_students or '',
            'pattern_items': pattern_items,
            'other_divisions': other_divisions,
        }
    })


@csrf_exempt
@require_http_methods(['POST'])
def teacher_submit_portion_api(request, token):
    """
    API endpoint for teachers to submit or edit syllabus portion and question pattern.
    """
    try:
        submission = PortionSubmission.objects.select_related('exam').get(id=token)
    except PortionSubmission.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Submission record not found'}, status=404)

    if not submission.exam.is_active:
        return JsonResponse({
            'status': 'error',
            'message': f"Examination '{submission.exam.title}' is currently inactive. Submissions are closed."
        }, status=403)

    if submission.status == 'APPROVED':
        return JsonResponse({
            'status': 'error',
            'message': f"Submission for {submission.subject_name} ({submission.class_name}) is approved and locked."
        }, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON payload'}, status=400)

    teacher_name = str(data.get('teacher_name') or '').strip()
    syllabus_portion = str(data.get('syllabus_portion') or '').strip()
    instructions_for_students = str(data.get('instructions_for_students') or '').strip()
    duration_minutes = data.get('duration_minutes', submission.duration_minutes) or 180

    if not teacher_name:
        return JsonResponse({'status': 'error', 'message': 'Teacher Full Name is required.'}, status=400)

    if not syllabus_portion:
        return JsonResponse({'status': 'error', 'message': 'Syllabus Portion (Chapters / Units / Topics) is required.'}, status=400)

    raw_items = data.get('pattern_items', [])
    valid_items = []
    has_incomplete_row = False
    sum_section_marks = 0.0

    for item in raw_items:
        q_type = str(item.get('question_type') or '').strip()
        num_q = item.get('number_of_questions')
        marks_p_q = item.get('marks_per_question')
        sec_name = str(item.get('section_name') or '').strip()
        c_info = str(item.get('choice_info') or '').strip()

        try:
            num_q = int(num_q) if num_q is not None else 0
            marks_p_q = float(marks_p_q) if marks_p_q is not None else 0.0
        except (ValueError, TypeError):
            num_q = 0
            marks_p_q = 0.0

        if q_type and num_q > 0 and marks_p_q > 0:
            valid_items.append({
                'section_name': sec_name,
                'question_type': q_type,
                'number_of_questions': num_q,
                'marks_per_question': marks_p_q,
                'choice_info': c_info,
            })
            sum_section_marks += (num_q * marks_p_q)
        elif q_type or num_q > 0 or marks_p_q > 0:
            has_incomplete_row = True

    if has_incomplete_row:
        return JsonResponse({
            'status': 'error',
            'message': 'Every Question Paper Blueprint row must be fully filled out (Question Type, No. of Qs, Marks / Q).'
        }, status=400)

    if len(valid_items) == 0:
        return JsonResponse({
            'status': 'error',
            'message': 'At least one complete Question Paper Blueprint row is required.'
        }, status=400)

    # Save submission
    submission.teacher_name = teacher_name
    submission.syllabus_portion = syllabus_portion
    submission.instructions_for_students = instructions_for_students
    try:
        submission.duration_minutes = int(duration_minutes)
    except (ValueError, TypeError):
        submission.duration_minutes = 180

    if sum_section_marks > 0:
        submission.total_marks = int(round(sum_section_marks))

    submission.status = 'SUBMITTED'
    submission.submitted_at = timezone.now()
    submission.save()

    # Recreate QuestionPatternItem records
    submission.pattern_items.all().delete()
    saved_items = []
    for item in valid_items:
        created_item = QuestionPatternItem.objects.create(
            submission=submission,
            question_type=item['question_type'],
            number_of_questions=item['number_of_questions'],
            marks_per_question=item['marks_per_question'],
            section_name=item['section_name'],
            choice_info=item['choice_info']
        )
        saved_items.append(created_item)

    # Process copying to other selected divisions if provided
    copy_target_ids = data.get('copy_to_divisions', [])
    if isinstance(copy_target_ids, str):
        copy_target_ids = [copy_target_ids]
    copy_target_ids = [cid.strip() for cid in copy_target_ids if cid and str(cid).strip()][:2]
    copied_count = 0

    if copy_target_ids:
        target_subs = PortionSubmission.objects.filter(
            id__in=copy_target_ids,
            exam=submission.exam,
            subject_name=submission.subject_name
        ).exclude(status='APPROVED')

        for target_sub in target_subs:
            target_sub.teacher_name = submission.teacher_name
            target_sub.total_marks = submission.total_marks
            target_sub.duration_minutes = submission.duration_minutes
            target_sub.syllabus_portion = submission.syllabus_portion
            target_sub.instructions_for_students = submission.instructions_for_students
            target_sub.status = 'SUBMITTED'
            target_sub.submitted_at = timezone.now()
            target_sub.save()

            target_sub.pattern_items.all().delete()
            for item in saved_items:
                QuestionPatternItem.objects.create(
                    submission=target_sub,
                    question_type=item.question_type,
                    number_of_questions=item.number_of_questions,
                    marks_per_question=item.marks_per_question,
                    section_name=item.section_name,
                    choice_info=item.choice_info
                )
            copied_count += 1

    msg = f"Syllabus Portion & Question Pattern saved & submitted for {submission.class_name}."
    if copied_count > 0:
        msg += f" Also copied to {copied_count} other division(s)."

    return JsonResponse({
        'status': 'success',
        'message': msg,
        'submission_id': str(submission.id),
        'total_marks': submission.total_marks,
        'copied_count': copied_count,
    })


def teacher_timetables_api(request):
    """
    Returns exam timetable data matching Django admin logic:
    - Active exams list
    - All-in-one timetables (all ExamTimetable entries for the selected exam)
    - Class-wise timetable mapping (division -> assigned timetable or fallback portion exam dates)
    """
    if request.method != 'GET':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    active_exams = list(Exam.objects.filter(is_active=True).values('id', 'title', 'academic_year'))
    if not active_exams:
        return JsonResponse({
            'status': 'success',
            'active_exams': [],
            'all_timetables': [],
            'class_wise_timetables': [],
        })

    exam_id = request.GET.get('exam_id')
    if exam_id:
        try:
            exam = Exam.objects.get(id=exam_id)
        except (Exam.DoesNotExist, ValueError):
            exam = Exam.objects.filter(is_active=True).first()
    else:
        exam = Exam.objects.filter(is_active=True).first()

    # 1. All-in-One: All timetables configured for this exam
    timetables_qs = ExamTimetable.objects.filter(exam=exam).prefetch_related('divisions', 'entries')
    all_timetables = []
    for tt in timetables_qs:
        div_list = [str(d) for d in tt.divisions.all()]
        entries = [
            {
                'id': e.id,
                'exam_date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
                'formatted_date': e.exam_date.strftime('%d/%m/%Y') if e.exam_date else '',
                'day_name': e.exam_date.strftime('%A') if e.exam_date else '',
                'subject_name': e.subject_name or '',
            }
            for e in tt.entries.all().order_by('exam_date', 'order')
        ]
        all_timetables.append({
            'id': str(tt.id),
            'name': tt.name,
            'is_global': len(div_list) == 0,
            'divisions': div_list,
            'entries': entries,
        })

    # 2. Class-wise timetable mapping (following exact Django admin compiled report logic)
    divisions_all = Division.objects.all()
    class_wise_timetables = []

    for div_ref in divisions_all:
        class_name = str(div_ref)

        # Exact Django logic:
        # Step A: Check if a timetable is explicitly assigned to this division
        assigned_tt = ExamTimetable.objects.filter(exam=exam, divisions=div_ref).prefetch_related('entries').first()

        # Step B: Check if there is a global timetable (with no specific divisions assigned)
        if not assigned_tt:
            assigned_tt = ExamTimetable.objects.filter(exam=exam, divisions__isnull=True).prefetch_related('entries').first()

        entries = []
        tt_name = ''
        if assigned_tt:
            tt_name = assigned_tt.name
            for e in assigned_tt.entries.all().order_by('exam_date', 'order'):
                entries.append({
                    'id': e.id,
                    'exam_date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
                    'formatted_date': e.exam_date.strftime('%d/%m/%Y') if e.exam_date else '',
                    'day_name': e.exam_date.strftime('%A') if e.exam_date else '',
                    'subject_name': e.subject_name or '',
                })
        else:
            # Step C: Fallback to PortionSubmission exam_date for this division & exam
            subs = PortionSubmission.objects.filter(exam=exam, division_ref=div_ref).order_by('order', 'subject_name')
            fallback = []
            for sub in subs:
                if sub.exam_date:
                    fallback.append({
                        'id': str(sub.id),
                        'exam_date': sub.exam_date.strftime('%Y-%m-%d'),
                        'formatted_date': sub.exam_date.strftime('%d/%m/%Y'),
                        'day_name': sub.exam_date.strftime('%A'),
                        'subject_name': sub.subject_name,
                    })
            fallback.sort(key=lambda x: x['exam_date'])
            if fallback:
                tt_name = f"{exam.title} Timetable ({class_name})"
                entries = fallback

        if entries:
            # Query portion submissions for this division and exam to match Django compiled report
            subs_qs = PortionSubmission.objects.filter(exam=exam, division_ref=div_ref).prefetch_related('pattern_items').order_by('order', 'subject_name')
            subjects_list = []
            for s in subs_qs:
                patterns = [
                    {
                        'question_type': p.question_type or 'Question Pattern',
                        'total_marks': p.total_marks,
                    }
                    for p in s.pattern_items.all()
                ]
                subjects_list.append({
                    'id': str(s.id),
                    'subject_name': s.subject_name,
                    'status': s.status,
                    'total_marks': s.total_marks,
                    'syllabus_portion': s.syllabus_portion or '',
                    'patterns': patterns,
                })

            class_wise_timetables.append({
                'division_id': div_ref.id,
                'class_name': class_name,
                'timetable_name': tt_name,
                'entries': entries,
                'subjects': subjects_list,
            })

    return JsonResponse({
        'status': 'success',
        'active_exams': active_exams,
        'current_exam': {
            'id': exam.id,
            'title': exam.title,
            'academic_year': exam.academic_year,
            'school_name': exam.school_name,
            'header_subtitle': exam.header_subtitle,
            'note_text': exam.note_text,
        } if exam else None,
        'all_timetables': all_timetables,
        'class_wise_timetables': class_wise_timetables,
    })


def generate_ai_learning_strategies(subject_name, pattern_items, duration_minutes=180):
    """
    Generates intelligent pedagogical learning strategies, time distribution,
    and revision tips based on subject and question pattern breakdown.
    """
    strategies = []
    sub_lower = (subject_name or '').lower()

    if any(k in sub_lower for k in ['english', 'grammar', 'literature', 'language', 'marathi', 'hindi']):
        strategies.append({
            'category': 'Reading & Comprehension',
            'tip': 'Read the unseen passage twice before answering. Underline key keywords and dates in the passage.'
        })
        strategies.append({
            'category': 'Grammar & Vocabulary',
            'tip': 'Double check verb tenses, subject-verb agreement, and prepositions. Avoid overwriting or scribbling.'
        })
        strategies.append({
            'category': 'Creative Writing & Literature',
            'tip': 'Follow the standard layout for letters/notices. Use neat paragraphs with intro, body, and conclusion.'
        })
    elif any(k in sub_lower for k in ['math', 'arithmetic', 'algebra', 'geometry']):
        strategies.append({
            'category': 'Formula & Concept Review',
            'tip': 'Revise all standard identities, formulas, and geometrical theorems before the exam.'
        })
        strategies.append({
            'category': 'Step-by-Step Working',
            'tip': 'Write given data, formula, and step-by-step substitution clearly. Marks are awarded for every step!'
        })
        strategies.append({
            'category': 'Rough Margin & Calculations',
            'tip': 'Draw a neat rough work column on the right side of every page. Highlight your final answer in a box.'
        })
    elif any(k in sub_lower for k in ['science', 'physics', 'chemistry', 'biology', 'evs']):
        strategies.append({
            'category': 'Scientific Terminology & Definitions',
            'tip': 'Memorize exact scientific definitions and laws. Include standard SI units for all quantities.'
        })
        strategies.append({
            'category': 'Diagrams & Labelling',
            'tip': 'Draw neat diagrams with sharp pencil only. Label all parts horizontally on one side with a ruler.'
        })
        strategies.append({
            'category': 'Scientific Reasoning',
            'tip': 'For "Give Reasons" questions, state the core principle and cause clearly in 2-3 numbered points.'
        })
    elif any(k in sub_lower for k in ['history', 'geography', 'social', 'civics', 'sst']):
        strategies.append({
            'category': 'Chronology & Key Dates',
            'tip': 'Review timelines, constitutional terms, and causes of major events. State answers in bullet points.'
        })
        strategies.append({
            'category': 'Map Work & Geography',
            'tip': 'Ensure accurate symbol markings and clean labelling on maps. Use index/legends properly.'
        })
    else:
        strategies.append({
            'category': 'Core Concepts & Fundamentals',
            'tip': 'Review summary sheets and highlighted textbook terms. Practice writing key definitions.'
        })

    has_mcq = any('mcq' in (it.get('question_type', '')).lower() or 'objective' in (it.get('question_type', '')).lower() for it in pattern_items)
    has_long = any('long' in (it.get('question_type', '')).lower() or 'essay' in (it.get('question_type', '')).lower() for it in pattern_items)

    if has_mcq:
        strategies.append({
            'category': 'Objective & MCQs Strategy',
            'tip': 'Eliminate obviously incorrect choices first. Be cautious of tricky words like "NOT", "ALL", or "EXCEPT".'
        })
    if has_long:
        strategies.append({
            'category': 'Long Answer Structure',
            'tip': 'Break long answers into subheadings or bullet points. Include diagrams or examples where applicable.'
        })

    reading_time = 15
    review_time = 15
    writing_time = max(duration_minutes - (reading_time + review_time), 30)
    strategies.append({
        'category': 'Time Management Strategy',
        'tip': f'First {reading_time} mins: Read paper & plan order. Next {writing_time} mins: Write answers steadily. Final {review_time} mins: Verify question numbers & recheck.'
    })

    return strategies


def teacher_reminder_data_api(request):
    """
    Returns tomorrow's and upcoming examination reminder packages with
    portions, question patterns, and AI-generated learning strategies.
    """
    if request.method != 'GET':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)

    exam_id = request.GET.get('exam_id')
    submission_id = request.GET.get('submission_id')
    division_id = request.GET.get('division_id')

    active_exams = list(Exam.objects.filter(is_active=True).values('id', 'title', 'academic_year', 'school_name'))
    if not active_exams:
        return JsonResponse({
            'status': 'success',
            'tomorrow_date': tomorrow.strftime('%Y-%m-%d'),
            'formatted_tomorrow_date': tomorrow.strftime('%A, %d %b %Y'),
            'active_exams': [],
            'papers': [],
            'selected_paper': None,
        })

    if exam_id:
        try:
            exam = Exam.objects.get(id=exam_id)
        except (Exam.DoesNotExist, ValueError):
            exam = Exam.objects.filter(is_active=True).first()
    else:
        exam = Exam.objects.filter(is_active=True).first()

    # Pre-index division timetable entries
    timetable_date_map = {}
    for tt in ExamTimetable.objects.filter(exam=exam).prefetch_related('divisions', 'entries'):
        for entry in tt.entries.all():
            if entry.exam_date and entry.subject_name:
                key_global = entry.subject_name.strip().lower()
                timetable_date_map[key_global] = entry.exam_date
                for div in tt.divisions.all():
                    timetable_date_map[(div.id, key_global)] = entry.exam_date

    # Query all portion submissions for this exam
    subs_qs = PortionSubmission.objects.filter(exam=exam).select_related(
        'exam', 'division_ref', 'master_subject'
    ).prefetch_related('pattern_items')

    if division_id:
        subs_qs = subs_qs.filter(division_ref_id=division_id)

    papers = []
    selected_paper = None

    for sub in subs_qs:
        assigned_date = sub.exam_date
        if not assigned_date and sub.division_ref_id:
            assigned_date = timetable_date_map.get((sub.division_ref_id, sub.subject_name.strip().lower()))
        if not assigned_date:
            assigned_date = timetable_date_map.get(sub.subject_name.strip().lower())

        is_tomorrow = (assigned_date == tomorrow) if assigned_date else False

        pattern_items = [
            {
                'id': item.id,
                'section_name': item.section_name or '',
                'question_type': item.question_type,
                'number_of_questions': item.number_of_questions,
                'marks_per_question': item.marks_per_question,
                'total_marks': item.total_marks,
                'choice_info': item.choice_info or '',
            }
            for item in sub.pattern_items.all()
        ]

        ai_strategies = generate_ai_learning_strategies(
            sub.subject_name, pattern_items, sub.duration_minutes
        )

        paper_data = {
            'submission_id': str(sub.id),
            'exam_id': sub.exam_id,
            'exam_title': sub.exam.title,
            'academic_year': sub.exam.academic_year,
            'school_name': sub.exam.school_name,
            'division_id': sub.division_ref_id,
            'class_name': sub.display_class_name,
            'subject_name': sub.subject_name,
            'teacher_name': '' if (sub.status == 'PENDING' and (sub.teacher_name or '').strip().lower() == 'teacher') else (sub.teacher_name or ''),
            'status': sub.status,
            'status_display': sub.get_status_display(),
            'total_marks': sub.total_marks,
            'duration_minutes': sub.duration_minutes,
            'exam_date': assigned_date.strftime('%Y-%m-%d') if assigned_date else '',
            'formatted_exam_date': assigned_date.strftime('%A, %d %b %Y') if assigned_date else 'Scheduled Date TBA',
            'is_tomorrow': is_tomorrow,
            'syllabus_portion': sub.syllabus_portion or '',
            'instructions_for_students': sub.instructions_for_students or '',
            'pattern_items': pattern_items,
            'ai_strategies': ai_strategies,
            'default_teacher_notes': (
                f"Please report to the examination hall by 8:45 AM. "
                f"Carry your Hall Ticket, School ID, and writing stationery. "
                f"Electronic devices are strictly forbidden. Best wishes for your exam!"
            ),
        }

        papers.append(paper_data)

        if submission_id and str(sub.id) == submission_id:
            selected_paper = paper_data
        elif not selected_paper and is_tomorrow:
            selected_paper = paper_data

    if not selected_paper and papers:
        selected_paper = papers[0]

    papers.sort(key=lambda p: (
        0 if p['is_tomorrow'] else 1,
        p['exam_date'] or '9999-99-99',
        p['class_name'],
        p['subject_name']
    ))

    return JsonResponse({
        'status': 'success',
        'tomorrow_date': tomorrow.strftime('%Y-%m-%d'),
        'formatted_tomorrow_date': tomorrow.strftime('%A, %d %b %Y'),
        'active_exams': active_exams,
        'current_exam': {
            'id': exam.id,
            'title': exam.title,
            'academic_year': exam.academic_year,
            'school_name': exam.school_name,
        } if exam else None,
        'papers_count': len(papers),
        'papers': papers,
        'selected_paper': selected_paper,
    })
