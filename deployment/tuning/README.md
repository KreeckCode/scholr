# Server tuning

`tune-server.sh` sets every service on a single Scholr and Moodle server to a fixed share of
memory, sized from the server's own RAM and CPUs, so the server serves more people without
running out of memory. Run it again after resizing the droplet and it re-sizes everything.

| Service | Change | Why |
|---|---|---|
| MySQL | Cache an eighth of RAM, performance monitoring off | Monitoring alone costs about 200 MB |
| PostgreSQL | Fewer connections, SSD-aware planning | Scholr's database is small |
| PHP for Moodle | About one worker per 240 MB, recycled every 500 requests; OPcache covers all of Moodle | These workers are what serve Moodle users |
| Moodle | One cron task at a time; files sent by Nginx; sessions in Redis | Cron stops competing with people; downloads stop holding PHP workers |
| Redis | Capped at an eighth of RAM, never evicting | It also holds Celery's queue, which must never be silently dropped |
| Celery | One worker per CPU, recycled every 100 jobs | Two workers on one CPU only cost memory |
| Gunicorn | Two threaded workers per CPU | One slow request, such as opening the LMS, no longer blocks everyone else |
| Nginx | Compression for text | Faster pages on slow connections |
| Kernel | Swap only when memory is truly short; system log capped | Keeps active services in memory |

Every file is backed up to `/var/backups/scholr-tuning/` before it is changed. If a service will
not start with its new settings, that change is put back automatically and the rest carries on.

## Running it

From your own computer, at a quiet time, measure first:

```bash
bash deployment/tuning/loadtest.sh https://online.metp.ca
```

On the server:

```bash
cd /var/www/scholr
bash deployment/tuning/selftest.sh              # proves the script on copies, touches nothing
sudo bash deployment/tuning/tune-server.sh apply
sudo bash deployment/tuning/tune-server.sh status
free -h
```

Then measure again from your computer and compare.

Each service restarts once, so the site blips for a few seconds. Moodle users are signed out once
when sessions move to Redis.

## Undoing

```bash
sudo bash deployment/tuning/tune-server.sh undo
```

Each `undo` reverses the most recent `apply`, file for file. Run it again to go back further.

## Notes

- The service files changed are the live ones in `/etc/systemd/system`. The copies in
  `deployment/systemd/` are reference copies and are not installed by deploys.
- Deploys reload Gunicorn gracefully, which works the same with threaded workers.
- If `free -h` still shows swap in use under normal load after tuning, the server has outgrown
  2 GB. Resizing the droplet to 2 CPUs and 4 GB, then running `apply` again, roughly doubles
  to triples what it can carry.
