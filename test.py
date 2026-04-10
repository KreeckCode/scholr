import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'judeaharvest.settings')
django.setup()

from accounts.models import User
from integration.models import MoodleUserMapping, MoodleInstance
from integration.moodle_client import MoodleClient

# Check mapping
user = User.objects.get(username='judeatest')
print(f"\nDjango User: {user.username} (ID: {user.id})")

try:
    mapping = MoodleUserMapping.objects.get(cas_user=user)
    print(f"✓ Mapping exists: Moodle User ID = {mapping.moodle_user_id}")
    
    # Get Moodle instance
    instance = MoodleInstance.objects.first()
    client = MoodleClient(instance.base_url, instance.ws_token)
    
    # Check enrollments
    courses = client._make_request('core_enrol_get_users_courses', {'userid': mapping.moodle_user_id})
    print(f"\n✓ Enrolled in {len(courses)} courses:")
    for c in courses:
        print(f"   - {c['shortname']}")
    
except MoodleUserMapping.DoesNotExist:
    print("✗ No mapping exists - run the shell commands to create it")