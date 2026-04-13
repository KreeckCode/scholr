#!/bin/bash

# Stop development services

echo "Stopping Celery workers..."
pkill -f 'celery worker'

echo "Stopping Celery beat..."
pkill -f 'celery beat'

echo "✓ All background services stopped"