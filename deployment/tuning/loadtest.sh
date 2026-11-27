#!/usr/bin/env bash
# Measures how much the server can take, from your own computer (not the server: load created on
# the server would compete with what it is measuring). Uses ab, which macOS already includes.
# Run it once before tuning and once after, at a quiet time: for a minute or two the site is busy.
#
#   bash deployment/tuning/loadtest.sh https://online.metp.ca
#
# It steps up the number of simultaneous visitors on Scholr's and Moodle's sign-in pages and
# stops when responses get slow or start failing.

set -euo pipefail
SITE="${1:?Usage: bash loadtest.sh https://online.metp.ca}"
SITE="${SITE%/}"
LEVELS="${LEVELS:-2 5 10 20 30 40}"
SLOW_MS="${SLOW_MS:-2000}"
# A person reading and clicking asks the server for a new page about every 20 seconds
THINK_SECONDS="${THINK_SECONDS:-20}"
command -v ab >/dev/null || { echo "Needs ab (ApacheBench). On Ubuntu: sudo apt install apache2-utils" >&2; exit 1; }

measure() {
    # measure <label> <url>: prints one row per level, returns the best healthy requests/second
    local label="$1" url="$2" best=0 level output rate p95 failed
    printf '\n%s  (%s)\n' "$label" "$url"
    printf '  %-12s %-14s %-14s %s\n' 'at once' 'pages/second' '95% within' 'failed'
    for level in $LEVELS; do
        output="$(ab -q -k -n $(( level * 15 )) -c "$level" -s 30 "$url" 2>&1 || true)"
        rate="$(awk '/Requests per second/ {print $4}' <<<"$output")"
        p95="$(awk '$1 == "95%" {print $2}' <<<"$output")"
        failed="$(awk '/Failed requests/ {print $3}' <<<"$output")"
        failed=$(( ${failed:-0} + $(awk '/Non-2xx responses/ {print $3}' <<<"$output" | head -1 | grep -E '^[0-9]+$' || echo 0) ))
        printf '  %-12s %-14s %-14s %s\n' "$level" "${rate:-?}" "${p95:-?} ms" "$failed"
        if [[ -z "$rate" || -z "$p95" ]] || (( p95 > SLOW_MS || failed > level )); then
            printf '  stopped: slower than %s ms or failing\n' "$SLOW_MS"
            break
        fi
        best="$(awk -v a="$best" -v b="$rate" 'BEGIN {print (b > a) ? b : a}')"
        sleep 3
    done
    BEST="$best"
}

measure "Scholr" "$SITE/dashboard/login/"
SCHOLR_BEST="$BEST"
measure "Moodle" "$SITE/mylearn/login/index.php"
MOODLE_BEST="$BEST"

# Moodle pages are what learners load most, and the heavier of the two, so they set the limit
awk -v s="$SCHOLR_BEST" -v m="$MOODLE_BEST" -v think="$THINK_SECONDS" 'BEGIN {
    printf "\nBest healthy rate: Scholr %.1f, Moodle %.1f pages/second\n", s, m
    users = m * think
    printf "Roughly %d people actively using Moodle at once (one page every %ds each).\n", users, think
    printf "Logged-in course pages and quizzes are heavier than sign-in pages, so plan for about half: %d.\n", users / 2
}'
