#!/usr/bin/env bash
# Tunes a single server running Scholr and Moodle together so it serves as many people as
# possible without running out of memory. Every value is worked out from this server's RAM and
# CPUs, so after resizing the droplet, run it again.
#
#   sudo bash deployment/tuning/tune-server.sh apply    make the changes
#   sudo bash deployment/tuning/tune-server.sh status   show what is set now
#   sudo bash deployment/tuning/tune-server.sh undo     put every file back as it was
#
# Every file is copied to /var/backups/scholr-tuning/<time>/ before it is changed. If a service
# will not start after a change, that change is put back on its own and the rest carries on.

set -Eeuo pipefail

ROOT="${TUNING_ROOT:-}"                     # a scratch folder in tests, empty on a real server
NO_RESTART="${TUNING_NO_RESTART:-}"         # tests set this so no service is touched
BACKUP_BASE="$ROOT/var/backups/scholr-tuning"
# Nanoseconds keep runs apart and in order, even two in the same second
STAMP="$(date +%Y%m%d-%H%M%S-%N)"
BACKUP_DIR="$BACKUP_BASE/$STAMP"
PHP_VERSION="${PHP_VERSION:-8.3}"
MOODLE_CONFIG="$ROOT${MOODLE_CONFIG:-/var/www/moodle/config.php}"
NGINX_SNIPPET="$ROOT/etc/nginx/snippets/scholr-locations.conf"
PHP_POOL="$ROOT/etc/php/$PHP_VERSION/fpm/pool.d/www.conf"
CELERY_UNIT="$ROOT/etc/systemd/system/celery.service"
GUNICORN_UNIT="$ROOT/etc/systemd/system/gunicorn.service"
REDIS_CONF="$ROOT/etc/redis/redis.conf"
MARK_START="// Scholr tuning start"
MARK_END="// Scholr tuning end"

log() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*"; }

# ---------- sizing ----------

ram_mb() {
    if [[ -n "${TUNING_RAM_MB:-}" ]]; then echo "$TUNING_RAM_MB"; return; fi
    awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo
}
cpus() { echo "${TUNING_CPUS:-$(nproc)}"; }

size_everything() {
    RAM="$(ram_mb)"
    CPU="$(cpus)"
    # MySQL's cache: an eighth of memory, between 128 MB and 2 GB
    MYSQL_POOL=$(( RAM / 8 )); (( MYSQL_POOL < 128 )) && MYSQL_POOL=128; (( MYSQL_POOL > 2048 )) && MYSQL_POOL=2048
    # Moodle's PHP workers: about one per 240 MB of memory, at least 4
    PHP_CHILDREN=$(( RAM / 240 )); (( PHP_CHILDREN < 4 )) && PHP_CHILDREN=4
    # Background jobs: one worker per CPU
    CELERY_CONCURRENCY="$CPU"
    # Scholr: two threaded workers per CPU, at least two
    GUNICORN_WORKERS=$(( CPU * 2 )); (( GUNICORN_WORKERS < 2 )) && GUNICORN_WORKERS=2
    GUNICORN_THREADS=3
    REDIS_MAX_MB=$(( RAM / 8 )); (( REDIS_MAX_MB < 128 )) && REDIS_MAX_MB=128
    POSTGRES_CACHE_MB=$(( RAM / 4 ))
}

# ---------- safe editing ----------

backup() {
    local file="$1"
    mkdir -p "$BACKUP_DIR"
    touch "$BACKUP_DIR/manifest"
    # Only the first copy in a run is the original; never overwrite it with a changed version
    grep -q " ${file#"$ROOT"}\$" "$BACKUP_DIR/manifest" && return 0
    if [[ -e "$file" ]]; then
        mkdir -p "$BACKUP_DIR$(dirname "${file#"$ROOT"}")"
        cp -a "$file" "$BACKUP_DIR${file#"$ROOT"}"
        printf 'changed %s\n' "${file#"$ROOT"}" >> "$BACKUP_DIR/manifest"
    else
        printf 'created %s\n' "${file#"$ROOT"}" >> "$BACKUP_DIR/manifest"
    fi
}

write_file() {
    # write_file <path> : contents from stdin, backed up first
    local file="$1" folder
    folder="$(dirname "$file")"
    backup "$file"
    if [[ ! -d "$folder" ]]; then
        printf 'folder %s\n' "${folder#"$ROOT"}" >> "$BACKUP_DIR/manifest"
        mkdir -p "$folder"
    fi
    cat > "$file"
}

restore_one() {
    # Put a single file back from this run's backup, or remove it if this run created it
    local file="$1" relative="${1#"$ROOT"}"
    if [[ -e "$BACKUP_DIR$relative" ]]; then
        cp -a "$BACKUP_DIR$relative" "$file"
    else
        rm -f "$file"
    fi
}

service() {
    # service <action> <unit>: does nothing in tests
    [[ -n "$NO_RESTART" ]] && return 0
    systemctl "$1" "$2"
}

restart_or_undo() {
    # restart_or_undo <unit> <file>...: restart, and if it will not come back, undo these files
    local unit="$1"; shift
    if service restart "$unit"; then
        log "  $unit restarted"
        return 0
    fi
    warn "$unit would not start with the new settings, so they were put back"
    local file
    for file in "$@"; do restore_one "$file"; done
    [[ -z "$NO_RESTART" ]] && systemctl daemon-reload
    service restart "$unit" || warn "$unit is still not running: check journalctl -u $unit"
    return 0
}

# ---------- the changes ----------

tune_kernel() {
    log "Memory behaviour"
    write_file "$ROOT/etc/sysctl.d/99-scholr-tuning.conf" <<EOF
# Keep the services that are in use in memory, and swap only when memory is truly short
vm.swappiness = 10
vm.vfs_cache_pressure = 50
EOF
    [[ -n "$NO_RESTART" ]] || sysctl -q -p /etc/sysctl.d/99-scholr-tuning.conf
    write_file "$ROOT/etc/systemd/journald.conf.d/99-scholr-tuning.conf" <<EOF
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=40M
EOF
    service restart systemd-journald
}

tune_mysql() {
    log "MySQL: ${MYSQL_POOL} MB cache, performance monitoring off"
    local file="$ROOT/etc/mysql/mysql.conf.d/zz-scholr-tuning.cnf"
    write_file "$file" <<EOF
[mysqld]
# Performance monitoring costs around 200 MB and Moodle does not need it
performance_schema = OFF
innodb_buffer_pool_size = ${MYSQL_POOL}M
innodb_log_buffer_size = 16M
max_connections = 60
thread_cache_size = 16
table_open_cache = 1000
tmp_table_size = 32M
max_heap_table_size = 32M
EOF
    restart_or_undo mysql "$file"
}

tune_postgres() {
    local folder
    folder="$(find "$ROOT/etc/postgresql" -maxdepth 3 -type d -name conf.d 2>/dev/null | head -1)"
    [[ -n "$folder" ]] || { warn "No PostgreSQL conf.d folder found; skipped"; return 0; }
    log "PostgreSQL: sized for a small database on an SSD"
    local file="$folder/zz-scholr-tuning.conf"
    write_file "$file" <<EOF
max_connections = 40
effective_cache_size = ${POSTGRES_CACHE_MB}MB
work_mem = 4MB
maintenance_work_mem = 64MB
random_page_cost = 1.1
EOF
    restart_or_undo postgresql "$file"
}

tune_php() {
    log "PHP for Moodle: up to ${PHP_CHILDREN} workers, recycled regularly; OPcache sized for Moodle"
    local opcache="$ROOT/etc/php/$PHP_VERSION/fpm/conf.d/99-scholr-tuning.ini"
    write_file "$opcache" <<EOF
; Moodle has more files than OPcache tracks by default, which forces recompiling
opcache.max_accelerated_files = 32531
opcache.memory_consumption = 160
opcache.interned_strings_buffer = 16
opcache.revalidate_freq = 60
realpath_cache_size = 4096K
realpath_cache_ttl = 600
EOF
    if [[ -f "$PHP_POOL" ]]; then
        backup "$PHP_POOL"
        local start=2 min=2 max=$(( PHP_CHILDREN / 2 ))
        (( max < 3 )) && max=3
        sed -i -E \
            -e "s/^pm\.max_children\s*=.*/pm.max_children = $PHP_CHILDREN/" \
            -e "s/^pm\.start_servers\s*=.*/pm.start_servers = $start/" \
            -e "s/^pm\.min_spare_servers\s*=.*/pm.min_spare_servers = $min/" \
            -e "s/^pm\.max_spare_servers\s*=.*/pm.max_spare_servers = $max/" \
            "$PHP_POOL"
        grep -q '^pm.max_requests' "$PHP_POOL" \
            && sed -i -E 's/^pm\.max_requests\s*=.*/pm.max_requests = 500/' "$PHP_POOL" \
            || printf '\n; Recycle workers so slow memory leaks cannot build up\npm.max_requests = 500\n' >> "$PHP_POOL"
    else
        warn "No PHP pool file at $PHP_POOL; workers left as they were"
    fi
    restart_or_undo "php$PHP_VERSION-fpm" "$opcache" "$PHP_POOL"
}

tune_redis() {
    [[ -f "$REDIS_CONF" ]] || { warn "No Redis config found; skipped"; return 0; }
    log "Redis: capped at ${REDIS_MAX_MB} MB, never evicting (it also holds queued background jobs)"
    backup "$REDIS_CONF"
    sed -i -E '/^# Scholr tuning start$/,/^# Scholr tuning end$/d; /^maxmemory /d; /^maxmemory-policy /d' "$REDIS_CONF"
    printf '# Scholr tuning start\nmaxmemory %smb\nmaxmemory-policy noeviction\n# Scholr tuning end\n' \
        "$REDIS_MAX_MB" >> "$REDIS_CONF"
    restart_or_undo redis-server "$REDIS_CONF"
}

tune_moodle() {
    [[ -f "$MOODLE_CONFIG" ]] || { warn "No Moodle config at $MOODLE_CONFIG; skipped"; return 0; }
    local dataroot
    dataroot="$(sed -n "s/^[[:space:]]*\$CFG->dataroot[[:space:]]*=[[:space:]]*'\([^']*\)'.*/\1/p" "$MOODLE_CONFIG" | head -1)"
    [[ -n "$dataroot" ]] || { warn "Could not read Moodle's dataroot; skipped"; return 0; }

    local sessions_in_redis="yes"
    if [[ -z "$NO_RESTART" ]] && ! php -m 2>/dev/null | grep -qi '^redis$'; then
        log "Installing PHP's Redis support for Moodle sessions"
        apt-get install -y -q "php$PHP_VERSION-redis" >/dev/null || sessions_in_redis=""
    fi

    log "Moodle: one cron task at a time, files sent by Nginx${sessions_in_redis:+, sessions in Redis}"
    backup "$MOODLE_CONFIG"
    # Remove an earlier run's block, so running again replaces it rather than doubling it
    sed -i "\#$MARK_START#,\#$MARK_END#d" "$MOODLE_CONFIG"
    local block
    block="$(cat <<PHP
$MARK_START
// One cron task of each kind at a time, so cron never competes with people for the CPU
\$CFG->task_scheduled_concurrency_limit = 1;
\$CFG->task_adhoc_concurrency_limit = 1;
// Nginx delivers files directly, so downloads do not hold a PHP worker for their whole length
\$CFG->xsendfile = 'X-Accel-Redirect';
\$CFG->xsendfilealiases = array('/dataroot/' => \$CFG->dataroot);
PHP
)"
    if [[ -n "$sessions_in_redis" ]]; then
        block+="$(cat <<'PHP'

// Sessions in Redis instead of files: faster, and no file locking between requests
$CFG->session_handler_class = '\core\session\redis';
$CFG->session_redis_host = '127.0.0.1';
$CFG->session_redis_port = 6379;
$CFG->session_redis_database = 5;
$CFG->session_redis_prefix = 'moodle_sess_';
$CFG->session_redis_acquire_lock_timeout = 120;
$CFG->session_redis_lock_expire = 7200;
PHP
)"
    fi
    block+=$'\n'"$MARK_END"
    # Insert just before Moodle loads itself
    python3 - "$MOODLE_CONFIG" "$block" <<'PYTHON'
import sys
path, block = sys.argv[1], sys.argv[2]
text = open(path).read()
anchor = "require_once(__DIR__ . '/lib/setup.php');"
if anchor not in text:
    sys.exit("Moodle's config.php has no setup.php line to insert before")
open(path, 'w').write(text.replace(anchor, block + "\n" + anchor, 1))
PYTHON

    if [[ -f "$NGINX_SNIPPET" ]] && ! grep -q 'location /dataroot/' "$NGINX_SNIPPET"; then
        backup "$NGINX_SNIPPET"
        cat >> "$NGINX_SNIPPET" <<EOF

# Moodle files, handed over by PHP with X-Accel-Redirect. Never reachable directly.
location /dataroot/ {
    internal;
    alias ${dataroot%/}/;
}
EOF
    fi
    if [[ -z "$NO_RESTART" ]]; then
        if ! php -l "$MOODLE_CONFIG" >/dev/null; then
            warn "The Moodle config change was not valid PHP, so it was put back"
            restore_one "$MOODLE_CONFIG"; restore_one "$NGINX_SNIPPET"
            return 0
        fi
        if ! nginx -t 2>/dev/null; then
            warn "Nginx rejected the file-delivery change, so the Moodle changes were put back"
            restore_one "$NGINX_SNIPPET"; restore_one "$MOODLE_CONFIG"
            return 0
        fi
        systemctl reload nginx
        restart_or_undo "php$PHP_VERSION-fpm" "$MOODLE_CONFIG"
        sudo -u www-data php "$(dirname "${MOODLE_CONFIG#"$ROOT"}")/admin/cli/purge_caches.php" || true
    fi
}

tune_celery() {
    [[ -f "$CELERY_UNIT" ]] || { warn "No Celery service at $CELERY_UNIT; skipped"; return 0; }
    if ! grep -qE -- '--concurrency[= ][0-9]+' "$CELERY_UNIT"; then
        warn "No --concurrency in the Celery service; left as it was"
        return 0
    fi
    log "Background jobs: ${CELERY_CONCURRENCY} worker(s), recycled after 100 jobs"
    backup "$CELERY_UNIT"
    sed -i -E "s/--concurrency[= ][0-9]+/--concurrency=$CELERY_CONCURRENCY/" "$CELERY_UNIT"
    grep -q -- '--max-tasks-per-child' "$CELERY_UNIT" \
        || sed -i -E "s/--concurrency=$CELERY_CONCURRENCY/--concurrency=$CELERY_CONCURRENCY --max-tasks-per-child=100/" "$CELERY_UNIT"
    [[ -n "$NO_RESTART" ]] || systemctl daemon-reload
    restart_or_undo celery "$CELERY_UNIT"
}

tune_gunicorn() {
    [[ -f "$GUNICORN_UNIT" ]] || { warn "No Gunicorn service at $GUNICORN_UNIT; skipped"; return 0; }
    log "Scholr: ${GUNICORN_WORKERS} workers x ${GUNICORN_THREADS} threads, so one slow request no longer blocks others"
    backup "$GUNICORN_UNIT"
    sed -i -E \
        -e "s/--workers[= ][0-9]+/--workers $GUNICORN_WORKERS/" \
        -e "s/--worker-class[= ][a-z]+/--worker-class gthread --threads $GUNICORN_THREADS/" \
        "$GUNICORN_UNIT"
    # A second run must not add the threads option twice
    sed -i -E "s/(--threads [0-9]+)( --threads [0-9]+)+/\1/" "$GUNICORN_UNIT"
    grep -q -- '--max-requests' "$GUNICORN_UNIT" \
        || sed -i -E "s/(--threads [0-9]+)/\1 --max-requests 1000 --max-requests-jitter 100/" "$GUNICORN_UNIT"
    [[ -n "$NO_RESTART" ]] || systemctl daemon-reload
    restart_or_undo gunicorn "$GUNICORN_UNIT"
}

tune_nginx() {
    log "Nginx: compression for text, so pages download faster on slow connections"
    local file="$ROOT/etc/nginx/conf.d/scholr-compression.conf"
    write_file "$file" <<EOF
gzip_vary on;
gzip_proxied any;
gzip_comp_level 5;
gzip_min_length 1024;
gzip_types text/plain text/css text/xml application/json application/javascript application/xml image/svg+xml;
EOF
    if [[ -z "$NO_RESTART" ]]; then
        if nginx -t 2>/dev/null; then
            systemctl reload nginx
        else
            # Usually means nginx.conf already sets these
            warn "Nginx already had compression settings; this file was removed again"
            restore_one "$file"
        fi
    fi
}

# ---------- commands ----------

command_apply() {
    [[ -n "$ROOT" || $EUID -eq 0 ]] || { echo "Run as root: sudo bash $0 apply" >&2; exit 1; }
    size_everything
    log "Tuning for ${RAM} MB of memory and ${CPU} CPU(s). Backups: ${BACKUP_DIR#"$ROOT"}"
    tune_kernel
    tune_mysql
    tune_postgres
    tune_php
    tune_redis
    tune_moodle
    tune_celery
    tune_gunicorn
    tune_nginx
    log ""
    log "Done. Moodle users were signed out once if sessions moved to Redis."
    log "To put everything back: sudo bash $0 undo"
}

command_undo() {
    local latest
    latest="$(find "$BACKUP_BASE" -mindepth 1 -maxdepth 1 -type d ! -name '*.undone' 2>/dev/null | sort | tail -1)"
    [[ -n "$latest" && -f "$latest/manifest" ]] || { echo "Nothing to undo."; exit 0; }
    log "Putting back the changes from ${latest#"$ROOT"}"
    local action path
    # Newest change first, so a file changed twice ends up as it originally was
    while read -r action path; do
        case "$action" in
            changed) cp -a "$latest$path" "$ROOT$path"; log "  $path" ;;
            created) rm -f "$ROOT$path"; log "  $path" ;;
        esac
    done < "$latest/manifest"
    # Folders this run created go last, once the files in them are gone
    while read -r action path; do
        [[ "$action" == folder ]] && rmdir "$ROOT$path" 2>/dev/null || true
    done < "$latest/manifest"
    mv "$latest" "$latest.undone"
    if [[ -z "$NO_RESTART" ]]; then
        systemctl daemon-reload
        sysctl -q --system || true
        local unit
        for unit in systemd-journald mysql postgresql "php$PHP_VERSION-fpm" redis-server celery gunicorn; do
            systemctl restart "$unit" || warn "$unit did not restart"
        done
        nginx -t && systemctl reload nginx
    fi
    log "Undone."
}

command_status() {
    size_everything
    log "Server: ${RAM} MB memory, ${CPU} CPU(s)"
    free -h 2>/dev/null || true
    log ""
    grep -h -E 'innodb_buffer_pool_size|performance_schema' "$ROOT"/etc/mysql/mysql.conf.d/zz-scholr-tuning.cnf 2>/dev/null || log "MySQL: not tuned"
    grep -h -E '^pm\.max_children|^pm\.max_requests' "$PHP_POOL" 2>/dev/null || true
    grep -h -o -E -- '--concurrency=[0-9]+' "$CELERY_UNIT" 2>/dev/null || true
    grep -h -o -E -- '--workers [0-9]+|--threads [0-9]+' "$GUNICORN_UNIT" 2>/dev/null || true
    grep -q "$MARK_START" "$MOODLE_CONFIG" 2>/dev/null && log "Moodle: tuned" || log "Moodle: not tuned"
}

case "${1:-}" in
    apply) command_apply ;;
    undo) command_undo ;;
    status) command_status ;;
    *) echo "Usage: sudo bash $0 apply|status|undo" >&2; exit 64 ;;
esac
