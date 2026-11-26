import datetime
import json
import uuid
from collections import defaultdict

from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseNotModified, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .cache_utils import get_cache_version, invalidate_on_commit, make_cache_key
from .models import (
    Division,
    Exam,
    ExamTimetable,
    MasterSubject,
    PortionSubmission,
    QuestionPatternItem,
)

STATUS_DISPLAY_MAP = dict(PortionSubmission.STATUS_CHOICES)


def _clean_teacher_name(status, teacher_name):
    raw = teacher_name or ''
    if status == 'PENDING' and raw.strip().lower() == 'teacher':
        return ''
    return raw


def _json_response_with_etag(request, payload, etag_value):
    quoted_etag = f'"{etag_value}"'
    if request.META.get('HTTP_IF_NONE_MATCH') == quoted_etag:
        resp = HttpResponseNotModified()
        resp['ETag'] = quoted_etag
        resp['Cache-Control'] = 'no-cache'
        return resp
    resp = JsonResponse(payload)
    resp['ETag'] = quoted_etag
    resp['Cache-Control'] = 'no-cache'
    return resp


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

    ver = get_cache_version()
    etag_val = f"portal-{ver}"
    if request.META.get('HTTP_IF_NONE_MATCH') == f'"{etag_val}"':
        resp = HttpResponseNotModified()
        resp['ETag'] = f'"{etag_val}"'
        resp['Cache-Control'] = 'no-cache'
        return resp

    cache_key = make_cache_key('api_portal')
    payload = cache.get(cache_key)

    if payload is None:
        active_exams = list(
            Exam.objects.filter(is_active=True).values(
                'id', 'title', 'academic_year', 'hide_target_before_submission'
            )
        )
        divisions = [
            {
                'id': div['id'],
                'standard': div['standard'],
                'division_name': div['division_name'],
                'display_name': f"{div['standard']} - {div['division_name']}",
            }
            for div in Division.objects.values('id', 'standard', 'division_name')
        ]

        active_requests = PortionSubmission.objects.filter(exam__is_active=True).values(
            'id',
            'exam_id',
            'exam__title',
            'exam__hide_target_before_submission',
            'master_subject_id',
            'master_subject__code',
            'division_ref_id',
            'class_name',
            'subject_name',
            'teacher_name',
            'status',
            'total_marks',
            'duration_minutes',
        )

        alloc_map = defaultdict(list)
        seen_alloc = set()
        requests_list = []

        for sub in active_requests:
            sub_id_str = str(sub['id'])
            subj_id = sub['master_subject_id']
            div_id = sub['division_ref_id']
            status = sub['status']

            if subj_id and div_id:
                key = f"{sub['exam_id']}_{div_id}"
                dedup_key = (key, subj_id)
                if dedup_key not in seen_alloc:
                    seen_alloc.add(dedup_key)
                    alloc_map[key].append({
                        'id': subj_id,
                        'name': sub['subject_name'],
                        'code': sub['master_subject__code'] or '',
                        'submission_id': sub_id_str,
                        'status': status,
                    })

            requests_list.append({
                'id': sub_id_str,
                'exam_id': sub['exam_id'],
                'exam_title': sub['exam__title'],
                'hide_target_before_submission': sub['exam__hide_target_before_submission'],
                'class_name': sub['class_name'],
                'subject_name': sub['subject_name'],
                'teacher_name': _clean_teacher_name(status, sub['teacher_name']),
                'status': status,
                'status_display': STATUS_DISPLAY_MAP.get(status, status),
                'total_marks': sub['total_marks'],
                'duration_minutes': sub['duration_minutes'],
                'is_readonly': status == 'APPROVED',
            })

        payload = {
            'status': 'success',
            'active_exams': active_exams,
            'divisions': divisions,
            'alloc_map': dict(alloc_map),
            'active_requests': requests_list,
        }
        cache.set(cache_key, payload, 300)

    return _json_response_with_etag(request, payload, etag_val)


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

    raw_exam_id = data.get('exam_id')
    raw_division_id = data.get('division_id')
    raw_subject_id = data.get('subject_id')

    if not (raw_exam_id and raw_division_id and raw_subject_id):
        return JsonResponse({
            'status': 'error',
            'message': 'Please provide exam_id, division_id, and subject_id.'
        }, status=400)

    try:
        exam_id = int(raw_exam_id)
        division_id = int(raw_division_id)
        subject_id = int(raw_subject_id)
    except (ValueError, TypeError):
        return JsonResponse({
            'status': 'error',
            'message': 'exam_id, division_id, and subject_id must be valid integers.'
        }, status=400)

    submission_id = PortionSubmission.objects.filter(
        exam_id=exam_id,
        division_ref_id=division_id,
        master_subject_id=subject_id,
    ).values_list('id', flat=True).first()

    if not submission_id:
        return JsonResponse({
            'status': 'error',
            'message': 'The selected subject is not allocated for this examination and division.'
        }, status=404)

    return JsonResponse({
        'status': 'success',
        'submission_id': str(submission_id)
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
        submission = (
            PortionSubmission.objects
            .select_related('exam')
            .prefetch_related('pattern_items')
            .get(id=token)
        )
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

    other_division_subs = (
        PortionSubmission.objects
        .filter(exam_id=submission.exam_id, subject_name=submission.subject_name)
        .exclude(id=submission.id)
        .exclude(status='APPROVED')
        .values('id', 'class_name', 'status')
    )

    other_divisions = [
        {
            'id': str(other['id']),
            'class_name': other['class_name'],
            'status': other['status'],
            'status_display': STATUS_DISPLAY_MAP.get(other['status'], other['status']),
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
            'hide_target_before_submission': submission.exam.hide_target_before_submission,
            'class_name': submission.class_name,
            'subject_name': submission.subject_name,
            'teacher_name': _clean_teacher_name(submission.status, submission.teacher_name),
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
    raw_duration = data.get('duration_minutes')

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

    if raw_duration is None:
        parsed_duration = submission.duration_minutes or 0
    else:
        try:
            parsed_duration = int(raw_duration)
        except (ValueError, TypeError):
            parsed_duration = 0

    if parsed_duration <= 0:
        return JsonResponse({
            'status': 'error',
            'message': 'Please enter Exam Duration (Minutes) greater than 0.'
        }, status=400)

    now = timezone.now()
    copy_target_ids = data.get('copy_to_divisions', [])
    if isinstance(copy_target_ids, str):
        copy_target_ids = [copy_target_ids]
    copy_target_ids = [str(cid).strip() for cid in copy_target_ids if cid and str(cid).strip()][:2]

    with transaction.atomic():
        submission.teacher_name = teacher_name
        submission.syllabus_portion = syllabus_portion
        submission.instructions_for_students = instructions_for_students
        submission.duration_minutes = parsed_duration
        if sum_section_marks > 0:
            submission.total_marks = int(round(sum_section_marks))
        submission.status = 'SUBMITTED'
        submission.submitted_at = now
        submission.save(update_fields=[
            'teacher_name',
            'syllabus_portion',
            'instructions_for_students',
            'duration_minutes',
            'total_marks',
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
                )
                .exclude(status='APPROVED')
                .select_related('division_ref')
            )

        all_target_ids = [submission.id] + [t.id for t in target_subs]
        QuestionPatternItem.objects.filter(submission_id__in=all_target_ids).delete()

        pattern_objs = [
            QuestionPatternItem(
                submission=submission,
                question_type=item['question_type'],
                number_of_questions=item['number_of_questions'],
                marks_per_question=item['marks_per_question'],
                section_name=item['section_name'],
                choice_info=item['choice_info'],
            )
            for item in valid_items
        ]

        copied_submissions = []
        if target_subs:
            for target_sub in target_subs:
                target_sub.teacher_name = submission.teacher_name
                target_sub.total_marks = submission.total_marks
                target_sub.duration_minutes = submission.duration_minutes
                target_sub.syllabus_portion = submission.syllabus_portion
                target_sub.instructions_for_students = submission.instructions_for_students
                target_sub.status = 'SUBMITTED'
                target_sub.submitted_at = now
                target_sub.updated_at = now

                for item in valid_items:
                    pattern_objs.append(
                        QuestionPatternItem(
                            submission=target_sub,
                            question_type=item['question_type'],
                            number_of_questions=item['number_of_questions'],
                            marks_per_question=item['marks_per_question'],
                            section_name=item['section_name'],
                            choice_info=item['choice_info'],
                        )
                    )

                copied_submissions.append({
                    'id': str(target_sub.id),
                    'class_name': target_sub.display_class_name,
                    'subject_name': target_sub.subject_name,
                    'exam_title': submission.exam.title,
                    'total_marks': target_sub.total_marks,
                    'duration_minutes': target_sub.duration_minutes,
                    'teacher_name': target_sub.teacher_name,
                    'status': target_sub.status,
                })

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

    copied_count = len(copied_submissions)
    msg = f"Syllabus Portion & Question Pattern saved & submitted for {submission.class_name}."
    if copied_count > 0:
        msg += f" Also copied to {copied_count} other division(s)."

    return JsonResponse({
        'status': 'success',
        'message': msg,
        'submission_id': str(submission.id),
        'total_marks': submission.total_marks,
        'copied_count': copied_count,
        'copied_submissions': copied_submissions,
    })


def teacher_timetables_api(request):
    """
    Returns exam timetable data matching Django admin logic:
    - Active exams list
    - All-in-one timetables (all ExamTimetable entries for the selected exam)
    - Class-wise timetable mapping (division -> assigned timetable or fallback portion exam dates)
    Supports opt-in ?include_patterns=0 for slim mobile timetable payloads.
    """
    if request.method != 'GET':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    req_exam_id = request.GET.get('exam_id') or ''
    include_patterns = request.GET.get('include_patterns', '1') != '0'

    ver = get_cache_version()
    etag_val = f"tt-{ver}-{req_exam_id or 'def'}-{'1' if include_patterns else '0'}"
    if request.META.get('HTTP_IF_NONE_MATCH') == f'"{etag_val}"':
        resp = HttpResponseNotModified()
        resp['ETag'] = f'"{etag_val}"'
        resp['Cache-Control'] = 'no-cache'
        return resp

    cache_key = make_cache_key('api_timetables', req_exam_id or 'default', 'p1' if include_patterns else 'p0')
    payload = cache.get(cache_key)

    if payload is None:
        active_exam_objs = list(
            Exam.objects.filter(is_active=True).only(
                'id', 'title', 'academic_year', 'school_name', 'header_subtitle', 'note_text'
            )
        )
        active_exams = [
            {
                'id': ex.id,
                'title': ex.title,
                'academic_year': ex.academic_year,
            }
            for ex in active_exam_objs
        ]

        if not active_exams:
            payload = {
                'status': 'success',
                'active_exams': [],
                'all_timetables': [],
                'class_wise_timetables': [],
            }
            cache.set(cache_key, payload, 300)
            return _json_response_with_etag(request, payload, etag_val)

        exam = None
        if req_exam_id:
            try:
                parsed_id = int(req_exam_id)
                for ex in active_exam_objs:
                    if ex.id == parsed_id:
                        exam = ex
                        break
                if exam is None:
                    exam = Exam.objects.filter(id=parsed_id).first()
            except (ValueError, TypeError):
                exam = None
        if exam is None:
            exam = active_exam_objs[0]

        # Fetch timetables for this exam once; TimetableEntry.Meta.ordering handles order
        timetables_qs = list(
            ExamTimetable.objects
            .filter(exam=exam)
            .prefetch_related('divisions', 'entries')
        )

        all_timetables = []
        div_tt_map = {}
        global_tt = None

        for tt in timetables_qs:
            tt_divs = list(tt.divisions.all())
            div_list = [str(d) for d in tt_divs]
            entries = [
                {
                    'id': e.id,
                    'exam_date': e.exam_date.strftime('%Y-%m-%d') if e.exam_date else '',
                    'formatted_date': e.exam_date.strftime('%d/%m/%Y') if e.exam_date else '',
                    'day_name': e.exam_date.strftime('%A') if e.exam_date else '',
                    'subject_name': e.subject_name or '',
                }
                for e in tt.entries.all()
            ]
            all_timetables.append({
                'id': str(tt.id),
                'name': tt.name,
                'is_global': len(div_list) == 0,
                'divisions': div_list,
                'entries': entries,
            })

            tt_bundle = (tt.name, entries)
            if not tt_divs:
                if global_tt is None:
                    global_tt = tt_bundle
            else:
                for div in tt_divs:
                    if div.id not in div_tt_map:
                        div_tt_map[div.id] = tt_bundle

        divisions_all = list(Division.objects.all())

        subs_qs = PortionSubmission.objects.filter(exam=exam).order_by('order', 'subject_name')
        if include_patterns:
            subs_qs = subs_qs.prefetch_related('pattern_items')
        else:
            subs_qs = subs_qs.only(
                'id', 'division_ref_id', 'subject_name', 'status', 'total_marks', 'syllabus_portion', 'exam_date', 'order'
            )

        subs_by_div = defaultdict(list)
        for s in subs_qs:
            if s.division_ref_id:
                subs_by_div[s.division_ref_id].append(s)

        class_wise_timetables = []
        for div_ref in divisions_all:
            class_name = str(div_ref)
            div_subs = subs_by_div.get(div_ref.id, [])

            tt_bundle = div_tt_map.get(div_ref.id) or global_tt
            entries = []
            tt_name = ''

            if tt_bundle is not None:
                tt_name, entries = tt_bundle
            else:
                fallback = [
                    {
                        'id': str(sub.id),
                        'exam_date': sub.exam_date.strftime('%Y-%m-%d'),
                        'formatted_date': sub.exam_date.strftime('%d/%m/%Y'),
                        'day_name': sub.exam_date.strftime('%A'),
                        'subject_name': sub.subject_name,
                    }
                    for sub in div_subs
                    if sub.exam_date
                ]
                fallback.sort(key=lambda x: x['exam_date'])
                if fallback:
                    tt_name = f"{exam.title} Timetable ({class_name})"
                    entries = fallback

            if entries:
                subjects_list = []
                for s in div_subs:
                    patterns = (
                        [
                            {
                                'question_type': p.question_type or 'Question Pattern',
                                'total_marks': p.total_marks,
                            }
                            for p in s.pattern_items.all()
                        ]
                        if include_patterns
                        else []
                    )
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

        payload = {
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
        }
        cache.set(cache_key, payload, 300)

    return _json_response_with_etag(request, payload, etag_val)


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

    has_mcq = False
    has_long = False
    for it in pattern_items:
        qt = (it.get('question_type', '')).lower()
        if not has_mcq and ('mcq' in qt or 'objective' in qt):
            has_mcq = True
        if not has_long and ('long' in qt or 'essay' in qt):
            has_long = True
        if has_mcq and has_long:
            break

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

    exam_id = request.GET.get('exam_id') or ''
    submission_id = request.GET.get('submission_id') or ''
    division_id = request.GET.get('division_id') or ''

    ver = get_cache_version()
    etag_val = f"rem-{ver}-{exam_id or 'def'}-{division_id or 'all'}-{submission_id or 'none'}-{today.isoformat()}"
    if request.META.get('HTTP_IF_NONE_MATCH') == f'"{etag_val}"':
        resp = HttpResponseNotModified()
        resp['ETag'] = f'"{etag_val}"'
        resp['Cache-Control'] = 'no-cache'
        return resp

    # Cache base papers list per (exam_id, division_id, date) so switching submission_id is instant
    base_cache_key = make_cache_key('api_reminder_base', exam_id or 'default', division_id or 'all', today.isoformat())
    cached_base = cache.get(base_cache_key)

    if cached_base is None:
        active_exams = list(
            Exam.objects.filter(is_active=True).values('id', 'title', 'academic_year', 'school_name')
        )
        if not active_exams:
            payload = {
                'status': 'success',
                'tomorrow_date': tomorrow.strftime('%Y-%m-%d'),
                'formatted_tomorrow_date': tomorrow.strftime('%A, %d %b %Y'),
                'active_exams': [],
                'papers': [],
                'selected_paper': None,
            }
            cache.set(base_cache_key, payload, 300)
            return _json_response_with_etag(request, payload, etag_val)

        exam_dict = None
        if exam_id:
            try:
                parsed_exam_id = int(exam_id)
                for ex in active_exams:
                    if ex['id'] == parsed_exam_id:
                        exam_dict = ex
                        break
                if exam_dict is None:
                    exam_dict = Exam.objects.filter(id=parsed_exam_id).values(
                        'id', 'title', 'academic_year', 'school_name'
                    ).first()
            except (ValueError, TypeError):
                exam_dict = None
        if exam_dict is None:
            exam_dict = active_exams[0]

        current_exam_id = exam_dict['id']
        exam_title = exam_dict['title']
        academic_year = exam_dict['academic_year']
        school_name = exam_dict['school_name']

        # Pre-index division timetable entries
        timetable_date_map = {}
        for tt in ExamTimetable.objects.filter(exam_id=current_exam_id).prefetch_related('divisions', 'entries'):
            tt_divs = list(tt.divisions.all())
            for entry in tt.entries.all():
                if entry.exam_date and entry.subject_name:
                    key_global = entry.subject_name.strip().lower()
                    timetable_date_map[key_global] = entry.exam_date
                    for div in tt_divs:
                        timetable_date_map[(div.id, key_global)] = entry.exam_date

        subs_qs = (
            PortionSubmission.objects
            .filter(exam_id=current_exam_id)
            .select_related('division_ref')
            .prefetch_related('pattern_items')
        )
        if division_id:
            try:
                subs_qs = subs_qs.filter(division_ref_id=int(division_id))
            except (ValueError, TypeError):
                pass

        default_notes = (
            "Please report to the examination hall by 8:45 AM. "
            "Carry your Hall Ticket, School ID, and writing stationery. "
            "Electronic devices are strictly forbidden. Best wishes for your exam!"
        )
        tomorrow_str = tomorrow.strftime('%Y-%m-%d')
        tomorrow_fmt = tomorrow.strftime('%A, %d %b %Y')

        papers = []
        for sub in subs_qs:
            assigned_date = sub.exam_date
            subj_key = sub.subject_name.strip().lower()
            if not assigned_date and sub.division_ref_id:
                assigned_date = timetable_date_map.get((sub.division_ref_id, subj_key))
            if not assigned_date:
                assigned_date = timetable_date_map.get(subj_key)

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

            papers.append({
                'submission_id': str(sub.id),
                'exam_id': sub.exam_id,
                'exam_title': exam_title,
                'academic_year': academic_year,
                'school_name': school_name,
                'division_id': sub.division_ref_id,
                'class_name': sub.display_class_name,
                'subject_name': sub.subject_name,
                'teacher_name': _clean_teacher_name(sub.status, sub.teacher_name),
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
                'default_teacher_notes': default_notes,
            })

        default_first_id = papers[0]['submission_id'] if papers else None

        papers.sort(key=lambda p: (
            0 if p['is_tomorrow'] else 1,
            p['exam_date'] or '9999-99-99',
            p['class_name'],
            p['subject_name'],
        ))

        cached_base = {
            'status': 'success',
            'tomorrow_date': tomorrow_str,
            'formatted_tomorrow_date': tomorrow_fmt,
            'active_exams': active_exams,
            'current_exam': exam_dict,
            'papers_count': len(papers),
            'papers': papers,
        }
        cache.set(base_cache_key, (cached_base, default_first_id), 300)
    else:
        cached_base, default_first_id = cached_base

    papers = cached_base.get('papers', [])
    if not cached_base.get('active_exams'):
        return _json_response_with_etag(request, cached_base, etag_val)

    selected_paper = None
    if submission_id:
        for p in papers:
            if p['submission_id'] == submission_id:
                selected_paper = p
                break
    if selected_paper is None:
        for p in papers:
            if p['is_tomorrow']:
                selected_paper = p
                break
    if selected_paper is None and default_first_id:
        for p in papers:
            if p['submission_id'] == default_first_id:
                selected_paper = p
                break
    if selected_paper is None and papers:
        selected_paper = papers[0]

    payload = {
        **cached_base,
        'selected_paper': selected_paper,
    }
    return _json_response_with_etag(request, payload, etag_val)


@csrf_exempt
@require_http_methods(['POST'])
def teacher_submissions_status_api(request):
    """
    Batch status sync endpoint for cached teacher submissions on mobile devices.
    Accepts {"submission_ids": ["uuid1", ...]} and returns live statuses, remarks,
    and metadata for only those specific submissions owned/cached by that device.
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON payload'}, status=400)

    raw_ids = data.get('submission_ids', [])
    if not isinstance(raw_ids, list):
        return JsonResponse({'status': 'error', 'message': 'submission_ids must be a list.'}, status=400)

    valid_uuids = []
    seen = set()
    for raw_id in raw_ids[:50]:
        try:
            uid = uuid.UUID(str(raw_id).strip())
            uid_str = str(uid)
            if uid_str not in seen:
                seen.add(uid_str)
                valid_uuids.append(uid)
        except (ValueError, AttributeError, TypeError):
            continue

    if not valid_uuids:
        return JsonResponse({
            'status': 'success',
            'submissions': {},
            'missing_ids': [],
        })

    qs = (
        PortionSubmission.objects
        .filter(id__in=valid_uuids)
        .select_related('exam', 'division_ref')
        .only(
            'id',
            'exam_id',
            'exam__title',
            'exam__is_active',
            'exam__hide_target_before_submission',
            'division_ref_id',
            'division_ref__standard',
            'division_ref__division_name',
            'class_name',
            'subject_name',
            'teacher_name',
            'status',
            'principal_remarks',
            'total_marks',
            'duration_minutes',
            'updated_at',
            'submitted_at',
        )
    )
    submissions_map = {}
    found_ids = set()

    for sub in qs:
        sub_id = str(sub.id)
        found_ids.add(sub_id)
        submissions_map[sub_id] = {
            'id': sub_id,
            'exam_id': sub.exam_id,
            'exam_title': sub.exam.title,
            'is_exam_active': sub.exam.is_active,
            'hide_target_before_submission': sub.exam.hide_target_before_submission,
            'class_name': sub.display_class_name,
            'subject_name': sub.subject_name,
            'teacher_name': _clean_teacher_name(sub.status, sub.teacher_name),
            'status': sub.status,
            'status_display': sub.get_status_display(),
            'principal_remarks': sub.principal_remarks or '',
            'total_marks': sub.total_marks,
            'duration_minutes': sub.duration_minutes,
            'is_readonly': sub.status == 'APPROVED',
            'updated_at': sub.updated_at.isoformat() if sub.updated_at else '',
            'submitted_at': sub.submitted_at.isoformat() if sub.submitted_at else '',
        }

    missing_ids = [str(uid) for uid in valid_uuids if str(uid) not in found_ids]

    return JsonResponse({
        'status': 'success',
        'submissions': submissions_map,
        'missing_ids': missing_ids,
    })
