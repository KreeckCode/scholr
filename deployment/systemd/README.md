# Production Deployment - Systemd Services

These systemd service files are for production deployment on Ubuntu/Linux servers.

## Installation on Ubuntu Server

1. Copy service files to systemd directory:
```bash
sudo cp deployment/systemd/celery.service /etc/systemd/system/
sudo cp deployment/systemd/celerybeat.service /etc/systemd/system/
```

2. Create required directories:
```bash
sudo mkdir -p /var/run/celery
sudo mkdir -p /var/log/celery
sudo chown www-data:www-data /var/run/celery
sudo chown www-data:www-data /var/log/celery
```

3. Update paths in service files if needed (default: /var/www/judeaharvest)

4. Reload systemd and start services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable celery celerybeat
sudo systemctl start celery celerybeat
```

5. Check status:
```bash
sudo systemctl status celery
sudo systemctl status celerybeat
```

## For macOS Development

Don't use these files - see main README for development commands.