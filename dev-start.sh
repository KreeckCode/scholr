#!/bin/bash

# Development startup script for macOS

echo "Starting Judea Harvest Development Environment..."

# Check Redis is running
if ! redis-cli ping > /dev/null 2>&1; then
    echo "Starting Redis..."
    brew services start redis
    sleep 2
fi

echo "✓ Redis is running"

# Start Celery worker in background
echo "Starting Celery worker..."
celery -A scholr worker -l info --detach

# Start Celery beat in background
echo "Starting Celery beat..."
celery -A scholr beat -l info --detach

echo ""
echo "✓✓✓ All services started! ✓✓✓"
echo ""
echo "Running services:"
echo "  - Redis: localhost:6379"
echo "  - Celery Worker: Running in background"
echo "  - Celery Beat: Running in background"
echo ""
echo "To start Django:"
echo "  python manage.py runserver"
echo ""
echo "To monitor Celery tasks:"
echo "  celery -A scholr flower"
echo "  Then visit: http://localhost:5555"
echo ""
echo "To stop Celery:"
echo "  pkill -f 'celery worker'"
echo ""