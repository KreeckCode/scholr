"""
Monitor Redis queues and cache
"""

import redis
import time
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'judeaharvest.settings')
django.setup()

from django.core.cache import cache


def monitor_redis():
    """Monitor Redis stats"""
    r = redis.Redis(host='127.0.0.1', port=6379, db=0)
    
    print("\n" + "="*60)
    print("REDIS MONITORING")
    print("="*60 + "\n")
    
    try:
        while True:
            # Queue lengths
            celery_queue = r.llen('celery')
            sync_queue = r.llen('sync') 
            priority_queue = r.llen('priority')
            email_queue = r.llen('email')
            
            # Memory usage
            info = r.info('memory')
            used_memory = info['used_memory_human']
            
            # Cache stats
            cache_keys = r.keys('judeaharvest:*')
            moodle_keys = r.keys('moodle:*')
            session_keys = r.keys('session:*')
            
            # Print stats
            print(f"\r📊 Queues: celery={celery_queue} sync={sync_queue} "
                  f"priority={priority_queue} email={email_queue} | "
                  f"Memory: {used_memory} | "
                  f"Cache: {len(cache_keys)} Moodle: {len(moodle_keys)} "
                  f"Sessions: {len(session_keys)}", end='')
            
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n\n✅ Monitoring stopped")


if __name__ == '__main__':
    monitor_redis()