"""
Load testing script for Judea Harvest
Simulates concurrent users hitting the system
"""

import time
import threading
import statistics
from queue import Queue
import django
import os

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'judeaharvest.settings')
django.setup()

from accounts.models import User
from shop.tasks import sync_user_progress_task


class LoadTester:
    """Load testing framework"""
    
    def __init__(self, num_users=100, num_requests_per_user=5):
        self.num_users = num_users
        self.num_requests_per_user = num_requests_per_user
        self.results = Queue()
        self.errors = []
        
    def simulate_user(self, user_id, user_number):
        """Simulate one user making multiple requests"""
        times = []
        
        for i in range(self.num_requests_per_user):
            try:
                start = time.time()
                
                # Queue a background task (like progress sync)
                result = sync_user_progress_task.delay(user_id)
                
                elapsed = time.time() - start
                times.append(elapsed)
                
                # Small delay between requests
                time.sleep(0.1)
                
            except Exception as e:
                self.errors.append(f"User {user_number}: {str(e)}")
        
        # Store results
        self.results.put({
            'user_number': user_number,
            'user_id': user_id,
            'times': times,
            'avg_time': statistics.mean(times) if times else 0
        })
    
    def run_test(self):
        """Run the load test"""
        print(f"\n{'='*60}")
        print(f"LOAD TEST: {self.num_users} concurrent users")
        print(f"Each user makes {self.num_requests_per_user} requests")
        print(f"Total requests: {self.num_users * self.num_requests_per_user}")
        print(f"{'='*60}\n")
        
        # Get test users
        users = User.objects.filter(
            role=User.STUDENT,
            is_active=True
        ).values_list('id', flat=True)[:self.num_users]
        
        if len(users) < self.num_users:
            print(f"⚠️  Warning: Only {len(users)} users found, using those")
            self.num_users = len(users)
        
        # Start timing
        test_start = time.time()
        
        # Create threads (one per user)
        threads = []
        for i, user_id in enumerate(users, 1):
            thread = threading.Thread(
                target=self.simulate_user,
                args=(user_id, i)
            )
            threads.append(thread)
        
        # Start all threads at once (concurrent users)
        print("🚀 Starting load test...")
        for thread in threads:
            thread.start()
        
        # Wait for all to complete
        for thread in threads:
            thread.join()
        
        test_duration = time.time() - test_start
        
        # Analyze results
        self.print_results(test_duration)
    
    def print_results(self, test_duration):
        """Print test results"""
        all_times = []
        
        while not self.results.empty():
            result = self.results.get()
            all_times.extend(result['times'])
        
        print(f"\n{'='*60}")
        print("RESULTS")
        print(f"{'='*60}\n")
        
        print(f"✅ Test Duration: {test_duration:.2f}s")
        print(f"✅ Total Requests: {len(all_times)}")
        print(f"✅ Requests/Second: {len(all_times)/test_duration:.2f}")
        
        if all_times:
            print(f"\n📊 Response Times:")
            print(f"   Average: {statistics.mean(all_times)*1000:.2f}ms")
            print(f"   Median:  {statistics.median(all_times)*1000:.2f}ms")
            print(f"   Min:     {min(all_times)*1000:.2f}ms")
            print(f"   Max:     {max(all_times)*1000:.2f}ms")
            
            if len(all_times) > 1:
                print(f"   StdDev:  {statistics.stdev(all_times)*1000:.2f}ms")
        
        if self.errors:
            print(f"\n❌ Errors: {len(self.errors)}")
            for error in self.errors[:10]:  # Show first 10
                print(f"   - {error}")
        
        print(f"\n{'='*60}\n")


class SimpleLoadTest:
    """Simpler load test - just queue tasks"""
    
    @staticmethod
    def queue_tasks(num_tasks=200):
        """Queue many tasks at once"""
        print(f"\n🚀 Queuing {num_tasks} tasks...")
        
        users = User.objects.filter(
            role=User.STUDENT,
            is_active=True
        ).values_list('id', flat=True)[:num_tasks]
        
        start = time.time()
        
        task_ids = []
        for user_id in users:
            result = sync_user_progress_task.delay(user_id)
            task_ids.append(result.id)
        
        elapsed = time.time() - start
        
        print(f"✅ Queued {len(task_ids)} tasks in {elapsed:.2f}s")
        print(f"✅ Queue rate: {len(task_ids)/elapsed:.2f} tasks/second")
        print(f"\n📊 Monitor progress at: http://localhost:5555")
        print(f"\nTask IDs (first 5):")
        for task_id in task_ids[:5]:
            print(f"   {task_id}")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        
        if mode == 'simple':
            # Simple: Just queue tasks
            num = int(sys.argv[2]) if len(sys.argv) > 2 else 200
            SimpleLoadTest.queue_tasks(num)
        
        elif mode == 'concurrent':
            # Full load test with concurrent users
            num_users = int(sys.argv[2]) if len(sys.argv) > 2 else 100
            requests_per = int(sys.argv[3]) if len(sys.argv) > 3 else 5
            
            tester = LoadTester(num_users, requests_per)
            tester.run_test()
    else:
        print("\nUsage:")
        print("  python load_test.py simple [num_tasks]")
        print("  python load_test.py concurrent [num_users] [requests_per_user]")
        print("\nExamples:")
        print("  python load_test.py simple 200")
        print("  python load_test.py concurrent 100 5")