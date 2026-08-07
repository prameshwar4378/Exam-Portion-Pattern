import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Exam_Portion_Pattern.settings')
django.setup()

from django.contrib.auth.models import User
from core.models import Exam, PortionSubmission, QuestionPatternItem, MasterSubject, Division

# 1. Create Principal Account
if not User.objects.filter(username='principal').exists():
    user = User.objects.create_superuser('principal', 'principal@school.edu', 'admin123')
    user.first_name = "Principal"
    user.last_name = "Office"
    user.save()
    print("Principal superuser created: username='principal', password='admin123'")

# 2. Master Subjects (One-time setup)
subjects_data = [
    ("Mathematics", "MATH10"),
    ("Physics & Chemistry", "SCI10"),
    ("English Literature", "ENG10"),
    ("Social Science", "SOC10"),
    ("Computer Science", "CS10"),
]

for s_name, s_code in subjects_data:
    MasterSubject.objects.get_or_create(name=s_name, defaults={'code': s_code})
print("Master subjects seeded.")

# 3. Master Divisions (One-time setup)
divisions_data = [
    ("10th Grade", "Div A"),
    ("10th Grade", "Div B"),
    ("9th Grade", "Div A"),
    ("9th Grade", "Div B"),
]

for std, div_n in divisions_data:
    Division.objects.get_or_create(standard=std, division_name=div_n)
print("Master divisions seeded.")

print("Seed data completed successfully!")
