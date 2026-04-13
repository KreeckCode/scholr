import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'judeaharvest.settings')

app = Celery('judeaharvest')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()

# Celery Beat Schedule for periodic tasks
app.conf.beat_schedule = {
    'cleanup-expired-enrollments': {
        'task': 'shop.tasks.cleanup_expired_enrollments',
        'schedule': crontab(hour=2, minute=0),  # Run daily at 2 AM
    },
    'sync-all-moodle-progress': {
        'task': 'shop.tasks.sync_all_user_progress',
        'schedule': crontab(hour=3, minute=0),  # Run daily at 3 AM
    },
}

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')