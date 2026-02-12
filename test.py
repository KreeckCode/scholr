from integration.moodle_client import MoodleClient
from integration.models import MoodleInstance
from accounts.models import User

instance = MoodleInstance.objects.get(is_primary=True)
client = MoodleClient(instance.base_url, instance.ws_token)

# Get a synced student
student = User.objects.filter(role='student', is_verified=True).first()
moodle_user_id = student.moodle_mapping.moodle_user_id
user_email = student.email

print(f"Moodle User ID: {moodle_user_id}")
print(f"User Email: {user_email}")

# Since you set mapping to email, try with email
print("\n--- Test 1: Using email (matches your mapping setting) ---")
try:
    response = client._make_request('auth_userkey_request_login_url', {
        'user': {'email': user_email}
    })
    print(f"✓ SUCCESS with email!")
    print(f"Response: {response}")
except Exception as e:
    print(f"✗ Failed: {e}")

# Try with username
print("\n--- Test 2: Using username ---")
try:
    response = client._make_request('auth_userkey_request_login_url', {
        'user': {'username': student.username}
    })
    print(f"✓ SUCCESS with username!")
    print(f"Response: {response}")
except Exception as e:
    print(f"✗ Failed: {e}")

# Try with ID (standard)
print("\n--- Test 3: Using ID ---")
try:
    response = client._make_request('auth_userkey_request_login_url', {
        'user': {'id': moodle_user_id}
    })
    print(f"✓ SUCCESS with ID!")
    print(f"Response: {response}")
except Exception as e:
    print(f"✗ Failed: {e}")