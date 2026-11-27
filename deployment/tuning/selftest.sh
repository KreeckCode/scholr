#!/usr/bin/env bash
# Runs tune-server.sh against a copy of this server's kind of config files in a scratch folder,
# touching no real file or service: checks the changes, that running twice changes nothing more,
# and that undo puts every file back exactly.
#
#   bash deployment/tuning/selftest.sh

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAILURES=0
pass() { printf '  pass  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
expect() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

build_server() {
    local root="$1"
    mkdir -p "$root"/etc/{mysql/mysql.conf.d,postgresql/16/main/conf.d,php/8.3/fpm/pool.d,php/8.3/fpm/conf.d,redis,systemd/system,nginx/snippets,nginx/conf.d,sysctl.d} \
             "$root/var/www/moodle"
    cat > "$root/etc/php/8.3/fpm/pool.d/www.conf" <<'EOF'
[www]
user = www-data
pm = dynamic
pm.max_children = 5
pm.start_servers = 2
pm.min_spare_servers = 1
pm.max_spare_servers = 3
;pm.max_requests = 500
EOF
    printf '# maxmemory <bytes>\n# maxmemory-policy noeviction\nport 6379\n' > "$root/etc/redis/redis.conf"
    cat > "$root/etc/systemd/system/celery.service" <<'EOF'
[Service]
ExecStart=/var/www/scholr/venv/bin/celery -A scholr multi start worker \
    --pidfile=/run/celery/%n.pid \
    --loglevel=INFO \
    --concurrency=2
EOF
    cat > "$root/etc/systemd/system/gunicorn.service" <<'EOF'
[Service]
ExecStart=/var/www/scholr/venv/bin/gunicorn \
    --workers 2 \
    --worker-class sync \
    --bind unix:/run/gunicorn/scholr.sock \
    --timeout 120 \
    scholr.wsgi:application
EOF
    cat > "$root/var/www/moodle/config.php" <<'EOF'
<?php
unset($CFG);
global $CFG;
$CFG = new stdClass();
$CFG->dbtype    = 'mysqli';
$CFG->wwwroot   = 'https://online.metp.ca/mylearn';
$CFG->dataroot  = '/var/moodledata';
$CFG->sslproxy = false;
require_once(__DIR__ . '/lib/setup.php');
EOF
    printf 'location /static/ {\n    alias /var/www/scholr/staticfiles/;\n}\n' > "$root/etc/nginx/snippets/scholr-locations.conf"
}

tune() { TUNING_ROOT="$1" TUNING_NO_RESTART=1 TUNING_RAM_MB="$2" TUNING_CPUS="$3" bash "$HERE/tune-server.sh" "$4" > "$WORK/output" 2>&1; }

ROOT="$WORK/server"
build_server "$ROOT"
cp -a "$ROOT" "$WORK/original"

echo "This server: 1967 MB, 1 CPU"
expect "apply succeeds" 'tune "$ROOT" 1967 1 apply'
expect "MySQL cache an eighth of memory" 'grep -q "innodb_buffer_pool_size = 245M" "$ROOT/etc/mysql/mysql.conf.d/zz-scholr-tuning.cnf"'
expect "MySQL monitoring off" 'grep -q "performance_schema = OFF" "$ROOT/etc/mysql/mysql.conf.d/zz-scholr-tuning.cnf"'
expect "8 PHP workers, recycled" 'grep -q "^pm.max_children = 8" "$ROOT/etc/php/8.3/fpm/pool.d/www.conf" && grep -q "^pm.max_requests = 500" "$ROOT/etc/php/8.3/fpm/pool.d/www.conf"'
expect "OPcache tracks all of Moodle" 'grep -q "max_accelerated_files = 32531" "$ROOT/etc/php/8.3/fpm/conf.d/99-scholr-tuning.ini"'
expect "one Celery worker, recycled" 'grep -q -- "--concurrency=1 --max-tasks-per-child=100" "$ROOT/etc/systemd/system/celery.service"'
expect "Gunicorn threaded" 'grep -q -- "--worker-class gthread --threads 3 --max-requests 1000" "$ROOT/etc/systemd/system/gunicorn.service"'
expect "Gunicorn keeps 2 workers" 'grep -q -- "--workers 2" "$ROOT/etc/systemd/system/gunicorn.service"'
expect "Redis capped without eviction" 'grep -q "^maxmemory 245mb" "$ROOT/etc/redis/redis.conf" && grep -q "^maxmemory-policy noeviction" "$ROOT/etc/redis/redis.conf"'
expect "Moodle block before setup.php" 'python3 -c "t=open(\"$ROOT/var/www/moodle/config.php\").read(); assert t.index(\"Scholr tuning start\") < t.index(\"lib/setup.php\")"'
expect "Moodle cron one at a time" 'grep -q "task_scheduled_concurrency_limit = 1" "$ROOT/var/www/moodle/config.php"'
expect "Moodle sessions in Redis database 5" 'grep -q "session_redis_database = 5" "$ROOT/var/www/moodle/config.php"'
expect "Moodle files via Nginx" 'grep -q "X-Accel-Redirect" "$ROOT/var/www/moodle/config.php"'
expect "Nginx internal location for Moodle files" 'grep -A3 "location /dataroot/" "$ROOT/etc/nginx/snippets/scholr-locations.conf" | grep -q "alias /var/moodledata/;"'
expect "Nginx location is internal only" 'grep -A2 "location /dataroot/" "$ROOT/etc/nginx/snippets/scholr-locations.conf" | grep -q "internal;"'
if command -v php >/dev/null; then
    expect "Moodle config is valid PHP" 'php -l "$ROOT/var/www/moodle/config.php" >/dev/null'
fi

echo "Running it again"
cp -a "$ROOT" "$WORK/after-first"
expect "second apply succeeds" 'tune "$ROOT" 1967 1 apply'
expect "nothing doubled" 'diff -r --exclude=scholr-tuning "$WORK/after-first" "$ROOT" >/dev/null'
expect "one Moodle block" '[[ $(grep -c "Scholr tuning start" "$ROOT/var/www/moodle/config.php") -eq 1 ]]'

echo "Undo"
expect "undo the second run" 'tune "$ROOT" 1967 1 undo && diff -r --exclude=scholr-tuning "$WORK/after-first" "$ROOT" >/dev/null'
expect "undo the first run: every file exactly as it was" 'tune "$ROOT" 1967 1 undo && diff -r --exclude=var "$WORK/original" "$ROOT" >/dev/null && diff -r "$WORK/original/var/www" "$ROOT/var/www" >/dev/null'
expect "files the script created are gone" '[[ ! -e "$ROOT/etc/mysql/mysql.conf.d/zz-scholr-tuning.cnf" && ! -e "$ROOT/etc/nginx/conf.d/scholr-compression.conf" ]]'
expect "nothing left to undo" 'tune "$ROOT" 1967 1 undo && grep -q "Nothing to undo" "$WORK/output"'

echo "After upgrading to 4 GB and 2 CPUs"
BIG="$WORK/bigger"
build_server "$BIG"
expect "apply succeeds" 'tune "$BIG" 4096 2 apply'
expect "MySQL cache grows" 'grep -q "innodb_buffer_pool_size = 512M" "$BIG/etc/mysql/mysql.conf.d/zz-scholr-tuning.cnf"'
expect "PHP workers grow" 'grep -q "^pm.max_children = 17" "$BIG/etc/php/8.3/fpm/pool.d/www.conf"'
expect "two Celery workers" 'grep -q -- "--concurrency=2" "$BIG/etc/systemd/system/celery.service"'
expect "four Gunicorn workers" 'grep -q -- "--workers 4" "$BIG/etc/systemd/system/gunicorn.service"'

echo
if (( FAILURES )); then echo "$FAILURES check(s) failed"; cat "$WORK/output"; exit 1; fi
echo "All checks passed"
