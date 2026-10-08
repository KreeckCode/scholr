import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'scholr.settings')

app = Celery('scholr')

# CELERY_BEAT_SCHEDULE in settings.py is the single source of the periodic schedule.
# Assigning app.conf.beat_schedule here would silently replace it.
app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
