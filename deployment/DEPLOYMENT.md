# Deployment Guide

## Automated Deployment

Every push to `main` branch triggers:

1. **Testing** - Unit tests, system checks
2. **Security** - Vulnerability scanning, code analysis
3. **Quality** - Linting, code standards
4. **Build** - Static file collection
5. **Deploy** - Database backup, code deployment, migrations
6. **Health Check** - Endpoint verification
7. **Post-Deploy** - Cache clearing, warmup

## Manual Deployment

Trigger manually from GitHub Actions:
1. Go to **Actions** tab
2. Select **Deploy to Production**
3. Click **Run workflow**
4. Select `main` branch
5. Click **Run workflow**

## Rollback

On deployment failure, automatic rollback:
- Reverts to previous Git commit
- Restores previous dependencies
- Re-runs migrations
- Restarts all services

### Manual Rollback

```bash
ssh root@165.232.37.122

cd /var/www/scholr
git log --oneline -n 10  # Find commit to rollback to

# Rollback to specific commit
git reset --hard COMMIT_HASH

source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --no-input

systemctl restart gunicorn celery celery-beat
systemctl reload nginx
```

## Database Backups

Automatic backups before each deployment:
- Location: `/var/backups/scholr/`
- Format: PostgreSQL custom format (`.sql`)
- Retention: Last 7 backups

### Manual Backup

```bash
ssh root@165.232.37.122

PGPASSWORD='YOUR_DB_PASSWORD' pg_dump \
  -U admin \
  -h localhost \
  -d scholr \
  -F c \
  -f /var/backups/scholr/manual_$(date +%Y%m%d_%H%M%S).sql
```

### Restore from Backup

```bash
# Stop services
systemctl stop gunicorn celery celery-beat

# Drop and recreate database
sudo -u postgres psql << EOF
DROP DATABASE scholr;
CREATE DATABASE scholr OWNER admin;
EOF

# Restore backup
PGPASSWORD='YOUR_DB_PASSWORD' pg_restore \
  -U admin \
  -h localhost \
  -d scholr \
  -v \
  /var/backups/scholr/BACKUP_FILE.sql

# Restart services
systemctl start gunicorn celery celery-beat
```

## Monitoring Deployments

### View Deployment Logs

```bash
ssh root@165.232.37.122
tail -f /var/www/scholr/logs/deployments.log
```

### Check Service Status

```bash
systemctl status gunicorn celery celery-beat nginx
```

### View Application Logs

```bash
journalctl -u gunicorn -f
journalctl -u celery -f
tail -f /var/log/nginx/error.log
```

## Troubleshooting

### Deployment Failed

1. Check GitHub Actions logs
2. Check server logs: `journalctl -u gunicorn -n 50`
3. Verify services: `systemctl status gunicorn celery`
4. Test manually: `cd /var/www/scholr && source venv/bin/activate && python manage.py check`

### Health Check Failed

1. Check if services are running
2. Check Nginx configuration: `nginx -t`
3. Check application logs
4. Verify database connection

### Migration Issues

```bash
# Show migration status
python manage.py showmigrations

# Fake a migration if needed (DANGEROUS)
python manage.py migrate --fake app_name migration_name

# Rollback migration
python manage.py migrate app_name previous_migration_name
```

## Emergency Contacts

- **System Admin**: [Your Email]
- **Database Issues**: Check `/var/backups/scholr/`
- **Moodle Integration**: Check Moodle web services status