# Monitoring the live site

What watches https://139-185-59-132.sslip.io, how you find out when something
is wrong, and what to do first. Written for whoever is on call, which today is
one person: you get an email, you read the row for it below.

Three things work together. Each does one job:

| Piece | Watches | Tells you by |
|---|---|---|
| **UptimeRobot** (outside the VM) | the public address answers: `GET /health` every 5 minutes | email |
| **`monitor` service** (a container on the VM) | stuck or slow jobs, a worker that is down, job and API error rates, disk | email, through Healthchecks.io |
| **Langfuse** | every job and model call, and every API server error, by class and route | you look; it does not email |

A site that is down cannot report that it is down, which is why the first one
lives outside the VM. A monitor that dies cannot report that either, which is
why Healthchecks.io also expects a ping from it every 10 minutes and emails when
they stop.

## The alerts

One Healthchecks.io check per row. Its name is the email's subject, so the
subject alone says what broke. All thresholds are in
[config/monitoring.yaml](../config/monitoring.yaml), each with its reason.
Below, `P` is the shorthand from [deploy/README.md](../deploy/README.md):
`P="-f docker-compose.yml -f deploy/docker-compose.prod.yml"`.

| Check | Fires when | First steps |
|---|---|---|
| **site down** (UptimeRobot) | `/health` fails to answer | `docker compose $P ps` to see which service is not healthy, then `docker compose $P logs --tail 100 caddy api`. If nothing is running, `docker compose $P up -d`. If the VM itself does not answer, check it in the Oracle console. |
| `dastango-monitor` | no ping from the monitor for 10 minutes | The monitor is down, or the VM is. `docker compose $P ps monitor`, then `docker compose $P logs --tail 50 monitor`. While it is down nothing else here can alert you. |
| `dastango-database` | the monitor cannot reach Postgres | `docker compose $P ps db` and `logs --tail 50 db`. The disk being full is a common cause: check `df -h`. The job checks are paused while this fires (they cannot be read). |
| `dastango-job-silent` | a running job has not checked in for 5 minutes | The worker died mid-render. `docker compose $P ps worker`, `logs --tail 100 worker`. `docker compose $P restart worker` gives the job back to the queue (it gets `max_attempts`, 2 tries). Check `docker compose $P exec api python main.py jobs --status running`. |
| `dastango-job-slow` | a job is still checking in but has run over 30 minutes | A model call or ffmpeg is hung. The slowest real film took 398 s. Cancel it from the interface, or `docker compose $P restart worker`, and look at the worker's log for what it was doing. |
| `dastango-queue-waiting` | a job has waited over 10 minutes and nothing is running | No worker is taking jobs. `docker compose $P ps worker`; the worker's health check reads "reached the queue in the last 2 minutes". `restart worker`. |
| `dastango-job-errors` | at least 3 jobs failed in the last hour and over half of those finished failed | Read why: `docker compose $P exec api python main.py jobs --status failed`. Usual causes are a model provider's daily quota (Langfuse shows `daily_quota: true` on the failed call) or a provider down. `python main.py providers` shows what each role will use. |
| `dastango-api-errors` | at least 5 server errors (500+) in 10 minutes and over 5% of requests | Often a bad deploy: `docker compose $P logs --tail 200 api`. In Langfuse, filter observations named `api-error` and group by `route` and `error`. If it started right after an update, roll back as in [deploy/README.md](../deploy/README.md) "Rolling back". |
| `dastango-disk` | the data disk is 80% full | `df -h`, `du -sh data/outputs/* \| sort -h \| tail`, `docker system df`. Old images: `docker image prune`. Films belong to people: do not delete one without asking. |
| `dastango-disk-critical` | 90% full | Same, now: a render or a backup can fail halfway. Clear space before anything else, and do not deploy. |

The monitor reports a check when its state changes and then every 5 minutes.
Healthchecks.io alerts on a change of state, so a problem that goes on should
email when it starts and when it ends, not every minute. The alert tests below
are where to confirm that on the real account.

**Limits worth knowing.** UptimeRobot's free plan checks every 5 minutes, so a
short outage can pass between two checks. The monitor measures the disk its
`data/` folder sits on; on the VM that is the same disk as everything else.
The monitor sees only what the database, the request log and the disk show: a
site that is slow but answering raises none of these.

## Errors, in Langfuse

Langfuse is a tracing tool, not a crash reporter, so be clear about what it
covers (all of it tagged `production`, service `dastango`):

- **A job that failed**: the job's root observation (`render-film`, `plan-storyboard`,
  `edit-film`, ...) at level ERROR, with the exception's class name as its status.
- **A model call that failed**: a generation at level ERROR, with the provider,
  the HTTP status and whether a daily quota was used up.
- **An API request that ended in a server error**: an `api-error` event at level
  ERROR with `error` (the exception class, or `HTTP503` for an error response) and
  `route` (the pattern such as `/api/pipeline/film/{pid}`). Group by those two to
  see which failure is which. At most 30 a minute are sent.

Not covered: a process that crashes before it can send anything (a container
that will not start). That shows up in `docker compose $P ps`, in the
container's log and, if it is the monitor, in the `dastango-monitor` alert.

**Never in an error report**: the request's URL, query, headers or body, the
exception's message, a prompt, a script or a key. The event is built from a
fixed list of fields (`API_ERROR_FIELDS` in `shared/tracing.py`) and
credential values from the environment are blanked from whatever strings
remain. The same holds for job and model-call traces ([TRACING.md](TRACING.md)).
The monitor's request log holds a time and a status per request, nothing else.
The emails and pings hold job ids, kinds and counts, never a prompt.

## Setting it up once

You do this by hand, in these services' own sites; none of it is in the repository.

**1. Healthchecks.io** (free: 20 checks)

1. Sign up, make a project, and add yourself as the email recipient.
2. Create nine checks with exactly these names (the monitor reaches each by
   name): `dastango-monitor`, `dastango-database`, `dastango-job-silent`,
   `dastango-job-slow`, `dastango-queue-waiting`, `dastango-job-errors`,
   `dastango-api-errors`, `dastango-disk`, `dastango-disk-critical`.
3. `dastango-monitor`: **Period 10 minutes, Grace 5 minutes**. The other eight:
   **Period 1 day, Grace 1 hour**. (The monitor pings each every 5 minutes and
   sends the failure the moment it sees one, so the long period is only a
   backstop. Giving all nine a short period would send nine emails if the
   monitor stopped.)
4. In the project's settings find the **ping key**. The address to use is
   `https://hc-ping.com/<ping-key>`.

**2. The VM's `.env`** (nano, on the VM only; never commit it):

```
MONITOR_PING_BASE=https://hc-ping.com/<ping-key>
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com   # the region your project is in
```

`LANGFUSE_TRACING_ENVIRONMENT=production` and `OTEL_SERVICE_NAME=dastango` are
set by `deploy/docker-compose.prod.yml` for the API and worker, so a `.env`
copied from a laptop cannot tag the live site as development.

**3. UptimeRobot**: add an HTTP(s) monitor on `https://<DOMAIN>/health`,
interval 5 minutes, with your email as the alert contact.

**4. Redeploy** following "Updating" in [deploy/README.md](../deploy/README.md).
The image now includes the tracing package, and a new `monitor` container starts.
`docker compose $P ps` should list it as healthy, and `docker compose $P logs monitor`
should say `monitor up (every 60 s; reporting to Healthchecks.io)`.

## Testing each alert once, on purpose

Do this after the first deploy and note when each email arrived.

1. **Delivery for every Healthchecks.io check**, without breaking anything:
   `docker compose $P exec monitor python main.py monitor --send-test job-silent`
   sends that check's alert, waits a minute, then sends its all-clear. Run it
   once for each of the nine names. Two emails each: the alert, then the
   recovery. (`--hold 30` shortens the wait.)
2. **The logic on the real site**: `docker compose $P exec monitor python main.py monitor --once`
   prints every check's state and sends nothing. To see a rule fire, copy
   `config/monitoring.yaml` somewhere, lower `disk.warn_percent` below the
   disk's real use, and run it with `MONITORING_FILE=/path/to/that.yaml`.
3. **A real stopped worker** (the one most likely to matter): queue a film, then
   `docker compose $P kill worker`. After 5 minutes `dastango-job-silent` fires.
   `docker compose $P up -d worker` and the job is picked up again and the
   alert clears.
4. **Site down**: `docker compose $P stop caddy`; UptimeRobot emails within
   about 5 to 10 minutes; `docker compose $P up -d caddy` and it emails again
   when the site is back.
5. **A server error in Langfuse**: any forced 500 will do on a throwaway
   instance; on the live site, look under Observations for `api-error` after a
   real one rather than causing it.

## Where each piece lives in the code

| What | Where |
|---|---|
| The checks, thresholds loader, request log, ping client | `shared/monitoring.py` |
| Thresholds, with reasons | `config/monitoring.yaml` |
| The command | `python main.py monitor` (`--once`, `--send-test CHECK`) |
| The container | `monitor` in `deploy/docker-compose.prod.yml` |
| Request log + API error event | `backend/request_log.py`, `record_api_error` in `shared/tracing.py` |
| Tests | `tests/unit/test_dst32_monitoring.py`, `tests/unit/test_dst32_api_errors.py` |
