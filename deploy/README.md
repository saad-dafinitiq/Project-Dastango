# Deploying to a server

The whole app (web interface, API, workers, Postgres, HTTPS) runs on one
small Linux machine with Docker Compose. The target is Oracle Cloud's Always
Free ARM VM: **2 OCPUs and 12 GB of RAM** (cut from 4/24 in June 2026), free
for good, with no card charge as long as you stay on Always Free resources.

How fast it is, measured on that VM: a storyboard takes about 20 s (it is
mostly waiting on the model and image APIs). A 43 s, 5-scene film with Kokoro
voices and live images renders in **4 min 52 s**. Rendering is CPU work and
an A1 core is ~2.8× slower than a laptop's at it, so it draws two shots at
once to use both cores (one at a time, a film like that took 6 min 38 s).

## 1. The VM (Oracle console)

1. Create the account at <https://www.oracle.com/cloud/free/>. It asks for a
   card to verify you; Always Free resources are never charged. **Choose the
   home region carefully; it can't be changed**, and ARM capacity varies by
   region.
2. **The network first**: Networking → Virtual cloud networks → **Start VCN
   Wizard → Create VCN with Internet Connectivity**. It makes the public
   subnet *and* the internet gateway and route a public address needs. (A VCN
   made any other way has neither: the instance form's "assign a public IPv4
   address" switch then won't turn on, and even with an address nothing could
   reach the VM. If yours lacks them: Gateways → Create Internet Gateway, then
   the subnet's route table → rule `0.0.0.0/0` → that gateway.)
3. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04** (the aarch64 build is picked for you
     once the shape is ARM).
   - Shape: **Ampere → VM.Standard.A1.Flex**, 2 OCPUs, 12 GB memory.
   - Networking: **select the existing VCN** and its public subnet, and turn
     on the public IPv4 address.
   - SSH key: paste the public key you made for this (see below).
   - Boot volume: 100 GB is plenty (Always Free covers 200 GB in total).

   "Out of host capacity" is common for A1 shapes: try another availability
   domain, or again later. The cost estimate on the review page ignores the
   free tier (it says so); 100 GB of the 200 GB free storage is covered.
4. **Open the web ports**: Networking → Virtual cloud networks → your VCN →
   the subnet's security list → *Add ingress rules*, source `0.0.0.0/0`:
   TCP 80, TCP 443, and UDP 443 (HTTP/3). The VM's own firewall is opened by
   the setup script; both have to allow a port.

An SSH key for the VM, made on your own machine (the private half never
leaves it):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/storygen_vm -C storygen-vm
```

Paste `~/.ssh/storygen_vm.pub` into the console, then connect with
`ssh -i ~/.ssh/storygen_vm ubuntu@<public-ip>`.

## 2. On the VM

The repository is private, so the VM needs a way to read it. Give it a
**read-only deploy key**, not your own login: if the VM is ever compromised, that
key can read the code and nothing else.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/storygen_deploy -N "" -C storygen-vm-deploy
cat ~/.ssh/storygen_deploy.pub
```

Paste that public key into GitHub: the repository, Settings, Deploy keys, Add
deploy key, and leave "Allow write access" unticked. Then tell the VM to use it:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/storygen_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
git clone git@github.com:saad-dafinitiq/Project-Dastango.git storygen
cd storygen && bash deploy/setup-vm.sh
```

The first connection asks whether to trust github.com's host key: say yes.

**A VM cloned before the repository moved** (from `SaadAbdullaH3/Automated-Story-Generation`)
still points at the old repository, which gets no new commits. Check with
`git remote -v`, and if it names the old one, repoint it after adding the deploy
key above: `git remote set-url origin git@github.com:saad-dafinitiq/Project-Dastango.git`
and then `git fetch`.

The script (safe to run again) installs Docker, opens ports 80/443 in the
VM's iptables (Oracle's Ubuntu image rejects everything but SSH), gives the
`data/` folder to the containers' user (uid 10001; without it the first film
fails with "permission denied"), and writes a `.env` with a generated
database password and `DOMAIN`.

**The address.** Without a domain of your own, `DOMAIN` is set to
`<your-ip-with-dashes>.sslip.io`, a free name that resolves to your IP, no
account needed, and Caddy gets a real certificate for it. To use your own name
instead, point an A record at the VM and set `DOMAIN` in `.env`.

Then add your API keys to `.env` (the same ones as on your laptop) and start:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

The first start builds the image on the VM (it is ARM, so the laptop's image
won't run there) and downloads the Kokoro voice model into `data/` once,
checking its SHA-256. Then open `https://<DOMAIN>`: the first account made is
the administrator.

**GitHub sign-in:** in the OAuth app's settings, add a redirect URI
`https://<DOMAIN>/api/auth/github/callback` (an app takes up to ten, so the
laptop's `http://localhost:8000/...` one can stay; the match is exact: a
stray character and GitHub refuses with "redirect_uri is not associated with
this application"). Put the app's id and secret in the server's `.env` and
restart the API. Then sign in with your password and use **Connect GitHub** in
the top bar once: a GitHub sign-in never joins an existing account by email,
and with sign-ups closed it won't make a new one.

## What runs

| Service | |
|---|---|
| `caddy` | HTTPS on 80/443, certificate obtained and renewed automatically |
| `api` | the web interface and API, reachable only through Caddy |
| `worker` | renders and edits; scale with `--scale worker=2` (on 2 cores, one is right) |
| `db` | Postgres 16, its data in a Docker volume |
| `models` | runs once per start: fetches/verifies the voice model |
| `monitor` | watches the queue, the API's error rate and the disk, and emails the owner (set up once: [docs/MONITORING.md](../docs/MONITORING.md)) |

## Day to day

```bash
P="-f docker-compose.yml -f deploy/docker-compose.prod.yml"
docker compose $P ps                     # health of each service
docker compose $P logs -f worker         # what a render is doing
```

To update, follow "Updating" below: it backs up first and records what was running.

`api`, `worker` and `monitor` all report health: the API through `/health`, the
worker by having reached the job queue in the last two minutes, the monitor by
having finished a pass in the last three. A worker that can't reach the
database turns unhealthy rather than looking fine.

**Who tells you when it breaks:** UptimeRobot (the site stopped answering),
and the `monitor` through Healthchecks.io (a stuck or slow job, a dead worker,
errors piling up, the disk filling, the monitor itself stopping). Each email
names what broke; [docs/MONITORING.md](../docs/MONITORING.md) says what to do
first for each. Traces and API errors are in Langfuse, tagged `production`.

An update that changes `deploy/Caddyfile` needs one more step:
`docker compose $P up -d --no-deps --force-recreate caddy`. `git pull`
replaces the file instead of editing it, and the running container keeps
reading the old one through its bind mount, so `caddy reload` on its own
serves the old configuration.

## Updating

In order; each step says what to check before the next. `P` is the same
shorthand as above.

```bash
cd ~/storygen
P="-f docker-compose.yml -f deploy/docker-compose.prod.yml"

# 1. Nothing should be rendering: an update restarts the worker.
docker compose $P exec -T api python main.py jobs --status running
docker compose $P exec -T api python main.py jobs --status queued

# 2. Back up first (the database and the films, on the VM and in the bucket).
bash deploy/backup.sh

# 3. Write down what is running now. This is the rollback target.
PREVIOUS=$(git rev-parse --short HEAD)
echo "$(date -u +%FT%TZ) before update: $PREVIOUS" | tee -a ~/deploy-history.txt
#    Keep the running image under its own name too, so going back is instant
#    and needs no rebuild.
docker tag storygen:local "storygen:before-$PREVIOUS"

# 4. See what is coming, then take it. --ff-only refuses anything but a plain
#    fast-forward, so the VM never ends up with a merge nobody reviewed.
git fetch
git log --oneline HEAD..origin/main
git pull --ff-only

# 5. New settings? The names added to .env.example since the running version.
#    Most are optional with defaults; add a value to .env only on purpose.
git diff "$PREVIOUS" HEAD -- .env.example | grep -oE '^\+#? ?[A-Z_][A-Z0-9_]*' | tr -d '+# '

# 6. Rebuild and restart. The build runs first, so if it fails the old version
#    keeps running. Dependencies install from uv.lock, so rebuild; do not only restart.
docker compose $P up -d --build

# 7. Check, and write down the version that is now running.
docker compose $P ps
curl -fsS "https://$(grep '^DOMAIN=' .env | cut -d= -f2)/health"
echo "$(date -u +%FT%TZ) now running: $(git rev-parse --short HEAD)" | tee -a ~/deploy-history.txt
```

Values for new settings go in the VM's `.env` only: never copy them from a
laptop's `.env`, and never commit one.

## Rolling back

Go back to the commit from step 3 of "Updating". The quickest way uses the image
tagged there, so nothing is rebuilt:

```bash
git switch --detach "$PREVIOUS"          # or the hash from ~/deploy-history.txt
docker tag "storygen:before-$PREVIOUS" storygen:local
docker compose $P up -d --no-build
```

If that image is gone, rebuild from the old commit instead: the same
`git switch --detach` and then `docker compose $P up -d --build`.

That leaves the VM on a detached commit. When the problem is fixed on `main`,
return with `git switch main && git pull --ff-only` and update as usual.

The database: a release that only adds a table (the sign-in limit's
`auth_attempts` table, in November 2026) needs no database rollback, because the
older code ignores a table it does not know. A release that changed existing
tables needs the backup from step 2 put back after the code is rolled back:
`bash deploy/restore.sh latest`. That replaces the database and the films with
that backup, so anything made since is lost.

## Giving someone an account

Sign-ups are closed. Create the account from the VM. The command asks for the
password, so it never ends up in the shell history:

```bash
docker compose $P exec api python main.py users create someone@example.com
```

The password must be 12 to 128 characters and not on the bundled list of
breached passwords. Hand it over in person or on a call, never by chat or email.
There is no change-password screen yet, so an admin changes one with
`docker compose $P exec api python main.py users passwd someone@example.com`.

## Backups

The database (accounts, every version, the queue) and `data/` (the films and
their versions) are the only things that can't be rebuilt. Nightly:

```bash
mkdir -p ~/backups
( crontab -l 2>/dev/null; echo "15 3 * * * cd ~/storygen && bash deploy/backup.sh >> ~/backups/backup.log 2>&1" ) | crontab -
```

Each night is a `pg_dump` plus an rsync snapshot of `data/` hard-linked to the
night before, so a night costs only what changed; the newest seven of each
are kept (`BACKUP_KEEP`). To put one back (it replaces the current database
and films):

```bash
bash deploy/restore.sh latest          # or a name from ~/backups/db
```

This was tested as a round trip: back up twice, empty the database and delete
the films, restore: the account, its version and every file came back,
owned by the containers' user, and the account could sign in and see its
film.

### Off the machine

Backups on the VM's own disk cover mistakes, not losing the VM. Name a bucket
in `.env` and every nightly run also sends them to it (any S3-compatible one);
Cloudflare R2 is free to 10 GB, with the same keys the app would use:

```bash
BACKUP_BUCKET=multi-agent-storygen
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

The bucket keeps every database dump (the newest 14, `BACKUP_KEEP_REMOTE`)
and a mirror of the newest films; only files that changed are uploaded. On a
new machine, after `setup-vm.sh` and `up -d`:

```bash
bash deploy/restore.sh bucket
```

Tested as the disaster it is for: a stack with an account and a film backed
up, then its database volume, its films *and its local backups* deleted; a
fresh empty stack, `restore.sh bucket`, and the account, its version and all
eight files came back, and the account signed in and saw its film.
