import os
import django
from datetime import date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Exam_Portion_Pattern.settings')
django.setup()

from core.models import Exam, PortionSubmission, QuestionPatternItem, MasterSubject, Division

# 1. Create or Update Exam matching reference image
exam, _ = Exam.objects.get_or_create(
    title="PA-1 Exam 2026-2027",
    academic_year="2026-2027",
    defaults={
        'school_name': "Arohan Academy English School,Vaijapur",
        'header_subtitle': "(Syllabus Paper &Time table)",
        'note_text': "NOTE: Kindly pay your wards fees before exam and collect the exam pass from the office.",
        'is_active': True
    }
)
exam.school_name = "Arohan Academy English School,Vaijapur"
exam.header_subtitle = "(Syllabus Paper &Time table)"
exam.note_text = "NOTE: Kindly pay your wards fees before exam and collect the exam pass from the office."
exam.save()

class_name = "Grade VIII(MM)"

# 2. Subjects data from the image
subjects_data = [
    {
        'subject_name': "English Grammar Writing Skill",
        'syllabus': "Unit 1 and 2.1, 2.2.\nCh-1,2 and 3.\nLetter writing and Paragraph Writing.",
        'total_marks': 40,
        'exam_date': date(2026, 7, 6),
        'pattern': [
            ("Unseen passage", 10),
            ("Grammar", 5),
            ("Writing skill", 5),
            ("Literature", 20),
        ]
    },
    {
        'subject_name': "HINDI",
        'syllabus': "*स्वदेश *दो गौरैया *एक आशीर्वाद\n*व्याकरण(अभ्यास पुस्तिकापाठ क्र.1से5)\n*पत्रलेखन, अनुच्छेद लेखन",
        'total_marks': 40,
        'exam_date': date(2026, 7, 8),
        'pattern': [
            ("खंड 'क' – अपठित गद्यांश –", 7),
            ("खंड 'ख' – व्याकरण –", 8),
            ("खंड 'ग' – पाठ के प्रश्न –", 15),
            ("खंड 'घ' – लेखन विभाग –", 10),
        ]
    },
    {
        'subject_name': "मराठी",
        'syllabus': "घटक क्र १ ते ३\nउतारा वाचन लेखन कौशल्य\nव्याकरण:- काळ, वाक्य प्रकार म्हणी इ.\nलेखन:- निबंधलेखन.",
        'total_marks': 40,
        'exam_date': date(2026, 7, 9),
        'pattern': [
            ("उताऱ्यावरील प्रश्न", 10),
            ("पाठावर आधारित प्रश्नोत्तरे", 15),
            ("व्याकरणावर आधारित प्रश्न", 10),
            ("लेखनावर आधारित प्रश्न", 5),
        ]
    },
    {
        'subject_name': "MATHS",
        'syllabus': "1) A square and a cube\n2) Powerplay.",
        'total_marks': 40,
        'exam_date': date(2026, 7, 11),
        'pattern': [
            ("Section A", 14),
            ("Section B", 26),
        ]
    },
    {
        'subject_name': "SCIENCE",
        'syllabus': "Phy- Electricity: Magnetic & Heating Effects.\nChe- Particulate Nature of Matter.\nBio- The invisible living world: beyond our naked eye.",
        'total_marks': 45,
        'exam_date': date(2026, 7, 7),
        'pattern': [
            ("MCQ.", 18),
            ("V.S.A T.Q", 6),
            ("S.A.T.Q", 9),
            ("L.A.T.Q", 12),
        ]
    },
    {
        'subject_name': "SOCIAL STUDIES",
        'syllabus': "His- Ch-1 Reshaping India's Political Map\nGeo-Ch-1 Natural Resources & their use.\nEco- CH-1 Factors of productio(Half)\nSPL: Universal Franchise And Indias Electoral System.",
        'total_marks': 40,
        'exam_date': date(2026, 7, 10),
        'pattern': [
            ("His Section A", 10),
            ("Geo Section B", 10),
            ("SPL Section C", 10),
            ("Eco Section D", 10),
        ]
    },
]

# Insert or update submissions
for item in subjects_data:
    sub, _ = PortionSubmission.objects.get_or_create(
        exam=exam,
        class_name=class_name,
        subject_name=item['subject_name'],
        defaults={
            'teacher_name': 'Class Teacher',
            'syllabus_portion': item['syllabus'],
            'total_marks': item['total_marks'],
            'exam_date': item['exam_date'],
            'status': 'APPROVED'
        }
    )
    sub.syllabus_portion = item['syllabus']
    sub.total_marks = item['total_marks']
    sub.exam_date = item['exam_date']
    sub.status = 'APPROVED'
    sub.save()

    # Clear existing pattern items & add exact pattern items
    sub.pattern_items.all().delete()
    for sec_name, marks in item['pattern']:
        QuestionPatternItem.objects.create(
            submission=sub,
            section_name=sec_name,
            question_type='',
            number_of_questions=1,
            marks_per_question=marks
        )

print("Exact Arohan Academy demo data populated successfully!")
