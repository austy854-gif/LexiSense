# LexiSense MongoDB backup and restore runbook

Covers the daily encrypted `mongodump` of the Atlas cluster **LexiSense**
(M0 free tier, AWS `us-west-2`, project `6aa82c447747faec03281fd6`) to
Cloudflare R2, how to restore from it, the monthly test restore, and the
one-time setup.

- Workflow: [`.github/workflows/daily-db-backup.yml`](../../.github/workflows/daily-db-backup.yml)
- Scripts: [`ops/backup/`](../../ops/backup/) (`atlas-access-list.sh`, `prune-keys.sh`, `prune-keys.test.sh`)
- Encryption recipients: [`ops/backup/age-recipients.txt`](../../ops/backup/age-recipients.txt)

> The repository is **public**. Workflow logs are public, so the job never
> prints secrets, masks the runner IP, keeps mongodump's collection/count log
> out of the job log (it goes into an encrypted manifest), and never uses
> `actions/upload-artifact`. Only age-encrypted ciphertext leaves the runner.

## How it works

| Item | Value |
|---|---|
| Schedule | `0 9 * * *` UTC = **03:00 MDT** in summer, **02:00 MST** in winter (GitHub cron is UTC only). Also `workflow_dispatch`. Scheduled runs can start late when GitHub is busy. |
| Database | `MONGODB_DB_NAME` repo variable, default `lexisense`. Mirrors the backend: `DB_NAME`, else the URI path database, else `lexisense` (`backend/server.py`, `_resolve_db_name`). |
| Network | No `0.0.0.0/0`. The job finds the runner's IPv4 (`api.ipify.org`), adds `<ip>/32` to the Atlas project access list via Admin API v2 (`deleteAfterDate` = now + 60 min as a backstop, comment `gha-backup-<run_id>`), waits for `ACTIVE`, dumps, then deletes the entry in an `if: always()` step (404 counts as success). If that exact IP is already on the list, it is used and left untouched. |
| Dump | MongoDB Database Tools **100.19.1** (Ubuntu 24.04 x86_64 `.tgz` from `fastdl.mongodb.org`, SHA-256 pinned from MongoDB's `release.json`). `mongodump --uri … --db … --archive --gzip` piped straight into `age -R ops/backup/age-recipients.txt`. Plaintext exists only inside the pipe. |
| Encryption | age **v1.3.2** (GitHub release tarball, SHA-256 pinned; computed by us on 2026-10-19, since the release ships a `.proof` rather than a checksum file). |
| Upload | R2 via the S3 API: `aws s3 cp --endpoint-url https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`, region `auto`. |
| Layout | `daily/YYYY-MM-DD.archive.gz.age` plus `daily/YYYY-MM-DD.manifest.txt.age` (encrypted mongodump log with per-collection document counts). On Sundays (UTC) the same two objects also go to `weekly/`. |
| Verify | `head-object` on the uploaded archive: its size must equal the local ciphertext size and be ≥ `BACKUP_MIN_BYTES` (repo variable, default 2048). The manifest must exist. |
| Retention | Keep the newest **7 daily** and **4 weekly** dates. `ops/backup/prune-keys.sh` decides which keys to delete from a `list-objects-v2` listing. It only ever deletes keys that exactly match the layout above. |
| Alerting | GitHub emails on failed runs. Optional `HEALTHCHECKS_URL` (Healthchecks.io): `/start` at the beginning, the base URL on success, `/fail` on failure. Skipped when unset. |
| Hardening | `permissions: contents: read`; `concurrency: daily-db-backup` (queued, never cancelled); actions pinned to full commit SHAs; `persist-credentials: false`; sparse checkout of `ops/backup` only; `set -euo pipefail` everywhere; job skipped on forks. |

Note: on M0, `mongodump --oplog` isn't supported, so the dump isn't a
point-in-time snapshot across collections. Writes during the dump (a few
seconds at 03:00) can leave cross-collection references slightly inconsistent.

## GitHub configuration (names only)

Repository **secrets** (Settings → Secrets and variables → Actions → Secrets):

| Secret | What it is |
|---|---|
| `MONGODB_BACKUP_URI` | SRV URI for the read-only backup DB user (`mongodb+srv://<user>:<password>@<host>/?retryWrites=true&w=majority&appName=lexisense-backup`). Leave the database path empty, or make it equal `MONGODB_DB_NAME`. |
| `ATLAS_PUBLIC_KEY` | Atlas project API key, public part. |
| `ATLAS_PRIVATE_KEY` | Atlas project API key, private part. |
| `ATLAS_PROJECT_ID` | Atlas project (group) ID. Not very sensitive, but kept as a secret to keep logs clean. |
| `R2_ACCOUNT_ID` | Cloudflare account ID (used in the R2 endpoint host). |
| `R2_ACCESS_KEY_ID` | R2 API token, S3 access key ID. |
| `R2_SECRET_ACCESS_KEY` | R2 API token, S3 secret access key. |
| `R2_BUCKET` | R2 bucket name. |
| `HEALTHCHECKS_URL` | *Optional.* Healthchecks.io ping URL. |

Repository **variables** (optional, not secret):

| Variable | Default | Purpose |
|---|---|---|
| `MONGODB_DB_NAME` | `lexisense` | Must match the production `DB_NAME` on Railway. |
| `BACKUP_MIN_BYTES` | `2048` | Minimum encrypted archive size. Raise it once you know the typical size, e.g. to about 50% of a normal backup. |

The age **public** key isn't secret and lives in
`ops/backup/age-recipients.txt`. The workflow refuses to run while that file
still contains only the placeholder.

## One-time setup (Austin)

1. **Cloudflare R2.** Create a Cloudflare account and enable R2 (it may ask for
   a payment method; the free tier covers 10 GB-month). Create a **private**
   bucket (no public access, no r2.dev URL, no custom domain). Create an R2 API
   token with **Object Read & Write** scoped to **that bucket only**. Note the
   account ID, the access key ID and the secret access key. Optional backstop: add
   bucket lifecycle rules to delete objects under `daily/` after 10 days and
   under `weekly/` after 35 days, so retention still holds if the prune step
   ever stops running.
2. **age keypair, on your own machine (never in CI).** Install age
   (`brew install age`, `apt install age`, or a release binary) and run
   `age-keygen -o lexisense-backup.key`. It prints the public key (`age1…`).
   Keep the private key file **offline**: store it in your password manager
   **and** keep an offline copy (a printed sheet or an encrypted USB stick in a
   safe place). Then delete stray copies. Put the **public** key into
   `ops/backup/age-recipients.txt` through this PR, replacing the placeholder
   line.
3. **Atlas backup DB user.** Atlas → Database Access → Add New Database User:
   password auth, a long random password, and the built-in role **`backup`**
   (under *Specific Privileges*) or **`readAnyDatabase`**. Optionally restrict it
   to the `LexiSense` cluster. Build the SRV URI from Connect → Drivers with this
   user's credentials. Don't reuse the app user.
4. **Atlas API key for the access list.** Atlas → Project `6aa82c447747faec03281fd6`
   → Access Manager → Applications → API Keys → Create API Key, with the single
   project role **Project Network Access Manager** (`GROUP_NETWORK_ACCESS_MANAGER`).
   That is the minimum role for managing the IP access list. *Project Owner*
   would also work but grants far more (database users, clusters, backups).
   Sources: the Atlas Admin API v2 "Add Entries to Project IP Access List"
   operation lists *Project Network Access Manager* as its required role
   (https://www.mongodb.com/docs/api/doc/atlas-admin-api-v2/operation/operation-creategroupaccesslistentry),
   and Atlas User Roles describes that role as "update project network
   settings for: Access lists; VPC peering; Private Link"
   (https://www.mongodb.com/docs/atlas/reference/user-roles/). The status
   check needs only *Project Read Only*, which every project role includes.
   **API access list for the key:** don't add one. GitHub-hosted runner IPs
   change on every run, so any list would break the job. The tradeoff is that
   a leaked key works from anywhere. With this role, someone holding the key
   could edit the network access list, for example open `0.0.0.0/0`, but
   couldn't read data or manage DB users. Data access still needs a DB
   password. If your Atlas organization enables *Require IP Access List for
   the Atlas Administration API*, this design stops working. Then use a
   self-hosted runner with a static IP instead. Rotate the key yearly and on
   any suspicion of exposure.
5. **GitHub Actions secrets.** Add each one by name under Settings → Secrets
   and variables → Actions: `MONGODB_BACKUP_URI`, `ATLAS_PUBLIC_KEY`,
   `ATLAS_PRIVATE_KEY`, `ATLAS_PROJECT_ID`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
   `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, and optionally `HEALTHCHECKS_URL`.
   Optionally add the variables `MONGODB_DB_NAME` (must equal production
   `DB_NAME`) and `BACKUP_MIN_BYTES`.
6. **First run and test restore.** After the PR merges, go to Actions →
   daily-db-backup → Run workflow. Confirm that the run is green, that
   `daily/<today>.archive.gz.age` and its manifest are in the bucket, and that
   the Atlas access list has no leftover `gha-backup-*` entry. Then do the
   [monthly test restore](#monthly-test-restore) once, right away.

After step 6 succeeds, remove `0.0.0.0/0` from the Atlas access list (a
separate change). Make sure Railway's egress is allowed some other way first,
or the app loses its database.

## Restore runbook

Prerequisites on the restoring machine: `age`, MongoDB Database Tools
(`mongorestore`), `mongosh`, and `aws` CLI. You also need the age private key,
R2 credentials, and a database user with write rights on the **target**. The
backup user is read-only and can't restore. Create a temporary user with
`readWrite` on the target database (or the `restore` role) and delete it
afterwards. Add your own IP to the Atlas access list temporarily.

Prefer a separate **read-only** R2 token (Object Read, same bucket) for restores
and drills, so the CI token never needs to leave GitHub.

```bash
export AWS_ACCESS_KEY_ID=…  AWS_SECRET_ACCESS_KEY=…  AWS_DEFAULT_REGION=auto
export R2_ENDPOINT="https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com"
export R2_BUCKET=…            # bucket name
export AGE_KEY=~/secure/lexisense-backup.key
export SRC_DB=lexisense       # MONGODB_DB_NAME used by the backup
export STAGE_DB=lexisense_restore_$(date +%Y%m%d)
```

1. **Pick and download a backup.**
   ```bash
   aws s3 ls --endpoint-url "$R2_ENDPOINT" "s3://$R2_BUCKET/daily/"
   aws s3 ls --endpoint-url "$R2_ENDPOINT" "s3://$R2_BUCKET/weekly/"
   D=2026-10-19
   aws s3 cp --endpoint-url "$R2_ENDPOINT" "s3://$R2_BUCKET/daily/$D.archive.gz.age" .
   aws s3 cp --endpoint-url "$R2_ENDPOINT" "s3://$R2_BUCKET/daily/$D.manifest.txt.age" .
   ```
2. **Decrypt the manifest** and note the expected document counts:
   ```bash
   age -d -i "$AGE_KEY" "$D.manifest.txt.age" | grep -E 'done dumping'
   ```
3. **Restore into a staging database first, never straight into prod.**
   Stream the decrypted archive so the plaintext never lands on disk:
   ```bash
   age -d -i "$AGE_KEY" "$D.archive.gz.age" |
     mongorestore --uri "$TARGET_URI" --archive --gzip \
       --nsInclude="${SRC_DB}.*" \
       --nsFrom="${SRC_DB}.*" --nsTo="${STAGE_DB}.*"
   ```
   (Alternatively: `age -d -i "$AGE_KEY" -o "$D.archive.gz" "$D.archive.gz.age"`
   and then `mongorestore … --archive="$D.archive.gz" --gzip …`. Shred the
   plaintext file afterwards.)
   On the M0 cluster a staging copy counts against the 512 MB cap. If space is
   tight, restore to a local `mongo:8.0` container instead (see the monthly
   test).
4. **Verify the counts** against the manifest and spot-check key records:
   ```bash
   mongosh "$TARGET_URI" --quiet --eval "
     const d = db.getSiblingDB('$STAGE_DB');
     d.getCollectionNames().sort().forEach(c => print(c, d[c].estimatedDocumentCount()));"
   ```
   Also check that the indexes exist (`d.users.getIndexes()`) and log in to a
   staging app pointed at `$STAGE_DB` if one is available.
5. **Go live. Requires Austin's explicit approval.** Choose one:
   - **Swap (preferred, reversible).** Set Railway `DB_NAME=$STAGE_DB` on the
     backend (and on the worker/Celery services, if any), redeploy, and check
     `/api/v1/ready` reports the database OK. Keep the old database untouched
     for 7 days, then drop it.
   - **In-place restore.** Only in a maintenance window. Take a fresh dump of
     the current prod database first, then:
     ```bash
     age -d -i "$AGE_KEY" "$D.archive.gz.age" |
       mongorestore --uri "$TARGET_URI" --archive --gzip \
         --nsInclude="${SRC_DB}.*" --drop
     ```
     `--drop` deletes each collection before restoring it. Never run it
     without approval and a fresh pre-restore dump.
6. **Clean up.** Delete the temporary restore DB user and your temporary
   access-list entry. Shred any plaintext archive
   (`shred -u "$D.archive.gz"`). Record the outcome.

## Monthly test restore

On the first Monday of each month (calendar reminder), about 20 minutes:

1. Download the newest `daily/` archive and manifest (runbook steps 1–2).
2. Restore it into a throwaway local MongoDB, which doesn't touch Atlas:
   ```bash
   docker run -d --rm --name lexi-restore -p 127.0.0.1:27018:27017 mongo:8.0
   age -d -i "$AGE_KEY" "$D.archive.gz.age" |
     mongorestore --uri mongodb://127.0.0.1:27018 --archive --gzip \
       --nsInclude="${SRC_DB}.*" --nsFrom="${SRC_DB}.*" --nsTo="restore_test.*"
   ```
3. Compare the per-collection counts with the manifest (runbook step 4, with
   `--eval` against `mongodb://127.0.0.1:27018` and db `restore_test`).
4. Run `docker stop lexi-restore`.
5. Record the date, the backup date, the archive size, and pass/fail in the ops
   log (`/workspace/devops` or wherever ops notes live). If it fails, treat it
   as P1: backups that don't restore aren't backups.
6. Once a quarter, also restore a **weekly** copy and decrypt with the
   **offline copy** of the key (not the password-manager copy). That proves
   both copies work.

## Key-loss and key-safety warnings

- **If the age private key is lost, every backup is permanently unreadable.**
  Nobody can recover it: not Cloudflare, not MongoDB, not GitHub. Keep at least
  two independent copies (password manager plus offline) and test both (see
  above).
- **If the private key leaks**, anyone holding the R2 objects can read the full
  database. Generate a new keypair, replace the recipient in
  `age-recipients.txt` (PR), and consider deleting old objects encrypted to the
  leaked key once new backups exist.
- Rotating the key **doesn't** re-encrypt old backups. Keep the old private
  key until every object encrypted to it has aged out (7 days for daily, 4
  weeks for weekly).
- You can add a second recipient line (for example a sealed "break-glass" key
  held by a trusted person). Any one key decrypts.
- Never commit the private key (`AGE-SECRET-KEY-1…`), never paste it into
  GitHub secrets, CI, chat or tickets, and never decrypt on shared machines.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Preflight: "no age1... public key yet" | `age-recipients.txt` still has the placeholder. |
| Preflight: "Missing secret X" | Secret not created or misspelled. Names are case-sensitive. |
| `atlas exists/add: HTTP 401` | Wrong API key, or the org requires an API access list for the key. |
| `atlas add: HTTP 403` | The key lacks *Project Network Access Manager* on this project. |
| `atlas wait: timed out` | Atlas is slow to apply the entry. Re-run. A leftover entry expires via `deleteAfterDate` within the hour. |
| `mongodump` auth failed | Wrong backup user password, or the URI database path differs from `MONGODB_DB_NAME`. |
| Archive below `BACKUP_MIN_BYTES` | Wrong `MONGODB_DB_NAME` (an empty database), or real data loss. Investigate before re-running. |
| R2 `SignatureDoesNotMatch` or `AccessDenied` | Token not scoped to this bucket, or `R2_ACCOUNT_ID` wrong. |
| Leftover `gha-backup-*` access-list entry | The cleanup step failed. It expires on its own within 60 minutes. Delete it in the Atlas UI if you want it gone sooner. |

## Local checks (no network)

```bash
ops/backup/prune-keys.test.sh                 # retention logic against fake key lists
ATLAS_DRY_RUN=1 ATLAS_PROJECT_ID=6aa82c447747faec03281fd6 \
  ops/backup/atlas-access-list.sh add 203.0.113.7   # prints the request, sends nothing
actionlint .github/workflows/daily-db-backup.yml
```
