# Storage migration — typed operation / plan contract (v3.4, 2026-11-09)

*v3: passive schema work admitted by Codex 17:40Z; executable paths closed by E1–E6 (traceability: §10, rows Codex v2-1 … v2-6).*

*v3.2: Codex's targeted review of v3 (17:49Z) closed by F1–F6: link only what is immutable, retention by live references, recover with ownership checks, cutover pin order, steady-state address drift, one committed pointer (traceability: §10, rows Codex v3-1 … v3-6).*

*v3.3 pins the epoch arithmetic (econ GO for the executable design, 2026-11-09) and fixes consistency nits.*

*v3.4 aligns the text with the built code after the consistency review (§10 row "consistency review").*

Owner: pol-core (storage/publication side).  Consumers: econ (Isle App planned-action executor, independent
negative tests, build/review), Isle Codex (admission review).  Status: **contract, source only**.  Nothing here is packaged, installed or live.  Built
so far: stm-1 (`storage_contract.py`, afa8e00) and stm-3 (`storage_placement.py` plus the reserved `snapshot/` name, e95927a), then aligned to the
v2 decisions D1/D5/D6 in 97cc8a8 (request shapes with `--dataset`, full-tuple digest, three actions with `dependsOn`, five placement states with CAS,
generation and history); §9 lists what still differs.  Everything else is design.

**Phase A (this contract)** = a serving replica on the target plus a verified cutover of the served name.  It frees **nothing** on the core: the core
keeps the full repository and keeps publishing into it, so every receipt says `sourceRemovable: false`.  Phase A does not complete the requested
storage saving.  **Phase B** (REQUIRED follow-on, next contract; it is what saves core storage) = publishers write small signed indices on the core and commit the bulk onto
the target; core staging then becomes bounded and reclaimable.

v2 replaces v1.1.  Where a v1 statement and a v2 decision disagree, v2 holds.  v3 amends v2 (E1–E6); v3.2 amends v3 (F1–F6); v3.3 pins the epoch arithmetic (§5) and fixes nits; where they disagree, the later one holds.  Requirements text: Codex's STORAGE-PLACEMENT-CONTRACT (2026-11-08).
Facts about today's DNS/CA/registry behaviour are cited as `(audit Fn)` from the DNS ownership audit of 2026-11-09.

The first dataset is the public APT / catalog / runtime repository on the core (`/etc/isle-mesh/apt/repo`, 16 GB, served as `https://apt.isle/`), to
be hosted on pol-core's USB SSD.  Every shape is generic: a *dataset* is a directory of immutable public content served by one app; nothing names apt
in code.

## 0. Principles (rules the code enforces)
R1  **The placement record is the one ownership authority.**  `/etc/isle-mesh/storage/placement.json` on the core says who owns a dataset's domain.
    The CA, `dns-reconcile`, `hosts-reconcile` and every storage operation read it; nothing else decides (audit: no owner record exists today).
R2  **Every ownership change is a compare-and-set** on `(state, generation)` under one flock, with a history entry. A retry after a lost response
    reads the state and continues; it never repeats a committed step.
R3  **Identity is the filesystem UUID, checked locally at execution time.**  Writes go through directory descriptors anchored at the verified mount
    root; UUID and `st_dev` are re-verified against that descriptor before every generation step.  Device names and central reports are observations.
R4  **Fail closed before writes**: stale or inactive plan, wrong device, absent or mismatched mount, symlink in the path, read-only, root-fs fallback,
    insufficient capacity, unreadable trust, unauthorised placement.
R5  **The source is never changed by a migration.**  Its removal is a separate, dependent, separately approved action.
R6  **Content moves as a snapshot into generations**, fetched over the isle's trusted HTTPS from a source origin distinct from the cut-over name
    (`curl --resolve`, never `-k`, never a bare IP), verified per file, promoted atomically.  Serving never sees a half-updated tree.
R7  **Staged deploy.**  The hosting app is deployed without publishing its domain; the domain moves only in the core-side cutover operation.
R8  **Private material stays on the core.**  Only signed public content moves.  Docker root, live databases and generated compose files are never
    edited by hand.
R9  **One parameterised request shape per verb**, shared by terminal and App: fixed arguments, no shell, no path, no command text from backend
    metadata.  The dataset is a request argument, so locks and journal names derive from the tokens before any child starts.
R10 **Durable journal and receipts**, resumable; every stage is idempotent and re-checks evidence; a retry keeps the normalized request and digest; an
    interrupted copy can never report success.
R11 **Honest phases and words.**  `succeeded` for a migrate means "replica ready, awaiting cutover", not "moved". Report source-tested / packaged /
    installed / live-verified separately; `health` is not data preservation.
R12 **Rollback is an emergency verb**: it undoes only this migration's transition, only after verifying the old source still serves.  If that cannot
    be verified the state is `recovery-needed`, never `rolledBack: true`.

## 1. Plan item: the typed `storage` block (backend, PlanStore)
`validate()` (islemesh_planning.py) accepts `kind: "storage"` **additively**: existing kinds, checks and the old-plan test vector are unchanged byte
for byte; the `storage` block is kept (unknown keys inside it are dropped).

```json
{"id": "apt-repo-to-pol-core", "kind": "storage", "state": "planned",   // planned | existing | deferred (unchanged set)
 "device": "pol-core", "originDevice": "isle-core",                   // canonical TARGET; today's serving device
                                                                      // (at authorize it is copied into the placement entry as sourceDevice)
 "app": "isle-content-host",
 "hostingRelease": {"name": "isle-content-host", "version": "2026.11.1", "manifestSha256": "<64 hex: catalog release manifest>"},
 "title": "…", "notes": "…", "protectedRoles": ["apt"],               // display only; never trusted for safety (§6)
 "storage": {"schemaVersion": 1, "dataset": "apt-public-repo",         // [a-z0-9][a-z0-9-]{0,63}
   "domain": "apt.isle",                                              // the name that cuts over
   "source": {"device": "isle-core", "path": "/etc/isle-mesh/apt/repo", "apps": ["apt"], "snapshotSha256": "<64 hex manifestSha>|null", "contentSha256": "<64 hex>|null",
              "snapshotTakenAt": "2026-11-09T12:00:00+00:00", "totalBytes": 17179869184, "entries": 1234},
   "target": {"device": "pol-core", "filesystemUuid": "3bd28d7c-ba1e-4e6e-9a9f-23f684f4adf8",
              "relativePath": "isle/datasets/apt-public-repo", "capacityBytes": 21474836480, "mountpoint": "/mnt/pol-ssd"},
   "sourceRemoval": "dependent"}}                                     // the only allowed value
```
Constraints: `domain` is a DNS hostname ending `.isle` (labels `[a-z0-9-]`, 1–63 chars); `relativePath` relative, no `..`, ≤ 200; `snapshotSha256` 64
lowercase hex or `null`, and `contentSha256` likewise; both are `null` or both set, `snapshotSha256` is the manifestSha and `contentSha256` the
inventory-only hash (§1.3) (a plan with `null` is saved but **not executable**); `capacityBytes` ≥ `totalBytes` × 1.05; `originDevice == source.device`;
`device == target.device`; `hostingRelease.name == app`; `sourceRemoval == "dependent"`.  `source.apps` is optional: at most 8 distinct registry app names `[a-z0-9][a-z0-9-]{0,63}`, the source device's app(s) that serve the dataset today (apt: `["apt"]`), emitted in the whitelisted block only when non-empty.  `source.contentSha256` is likewise emitted only when non-null (the backend and CLI whitelists agree).

**Execution authority** (Codex 3).  A storage item is executable only when its plan is the **active** plan (`active: true`), the item `state` is
`planned`, and the item is not `deferred`.  Stage 1, stage 7 and the cutover operation each re-fetch the plan by id and revision and refuse if the
plan is no longer active, the revision is no longer retained, or the digest differs.  A digest proves bytes match approved intent; it does not by
itself grant the right to claim a domain — that is the placement record (§5).

**Auto-removals.**  `PlanStore.save` adds no removal for the source app of a storage item.  The source stays an `existing` item on `originDevice`.  `PlanStore.save` also suppresses the auto-removal of a dropped `existing` item when its device is the storage item's `originDevice` and its app equals the storage item's `app` or is listed in `source.apps`.  Until stm-2b's receipt-gated `removal-check` exists, the passive code prevents only AUTO-removal (what Codex admitted); a hand-written removal is not yet gated.
Dropping it, or saving any plan that omits it, is checked against receipts (`removal-check` runs on every save, not only on explicit removal requests;
§6).

### 1.1 Execution digest
```
storageDigest = sha256( json.dumps(
    {"planId": id, "revision": revision, "itemId": item.id, "device": item.device,
     "originDevice": item.originDevice, "app": item.app, "state": item.state,
     "storage": <whitelisted storage block>,
     "hostingRelease": {"name": …, "version": …, "manifestSha256": …}},
    sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() ).hexdigest()
```
Any change to plan, revision, device pair, app, item state, storage block or the hosting release identity changes the digest (the whitelisted storage block now includes `source.contentSha256` automatically; the digest form is unchanged).  Resource numbers
(`ramMiB`, …) and `protectedRoles` are not in the tuple: the pinned release manifest fixes what is deployed, and safety never reads `protectedRoles`.
The executor recomputes the digest from the plan it fetched and records `digestRecomputed: true` in the receipt.  Stage 7 resolves the catalog release
manifest for `app`, requires its sha256 to equal `hostingRelease.manifestSha256`, and pins the resolved image/config/archive identities in the journal
before deploying.  One function in the backend (`islemesh_planning.storage_digest`) and the CLI (`storage_contract.storage_digest`), checked against
one shared vector (`fixtures/storage-digest-vector.json`; the v1 vector is regenerated).

### 1.2 Planned actions, dependsOn and status
Emitted by the backend in `plannedActions[]` of `GET /api/islemesh/placement-plan?id=…` for every storage item with `state: planned` and a non-null
`snapshotSha256` and `contentSha256`.  `execute` is always `null`; `operatorCommand` and `undo` are display text.  The client maps `kind` to its own fixed native door.

**actionId format** (stable, the same for the life of one digest): `isle.storage.<verb>:<dataset>@<device>:<storageDigest>` with `<device>` = the
device that runs the action.

| kind | device | dependsOn | runs |
|---|---|---|---|
| `isle.storage.authorize` | core | `[]` | `sudo isle storage authorize --dataset D --plan P --revision N --item I --digest H` (§2.1, §5); its own approval on the core; the by-hand verb is the same journaled operation |
| `isle.storage.migrate` | target | `[authorize id]` | stages 1–8, ends with receipt and a request that the core mark `migrated` |
| `isle.storage.cutover` | core | `[migrate id]` | `sudo isle storage cutover --dataset D --generation G` |

`isle.storage.retire-source` is **not emitted in Phase A**: the source host is the origin that sync and rollback need.  It returns in Phase B, after a
replacement origin exists.  Every core verb (authorize, mark-migrated, cutover, rollback, recover, accept) is a journaled operation (§2.1).

```json
{"kind": "isle.storage.cutover", "id": "isle.storage.cutover:apt-public-repo@isle-core:<storageDigest>", "device": "isle-core",
 "dependsOn": ["isle.storage.migrate:apt-public-repo@pol-core:<storageDigest>"], "status": "blocked",
 "placementState": "migrated", "execute": null, "operatorCommand": null,
 "undo": "sudo isle storage rollback --dataset apt-public-repo --generation <G>   (EMERGENCY ONLY)"}   // plus the §1.2 common fields
```
**Common fields** (every action, listed once): `kind`, `id`, `device`, `dataset`, `planId`, `revision`, `itemId`, `digest`, `dependsOn`, `status`, `statusWords`, `placementState`, `epoch`, `execute` (always null), `operatorCommand`, `undo`, `chain`.  Kind strings are identical everywhere: `isle.storage.{authorize,migrate,cutover,witness,accept,reconcile-address}` (witness and accept are chain steps, reconcile-address is system-initiated and never planned).  The GET door is one thin lock-free call into `islemesh_storage_plan.planned_actions`.
`operatorCommand` of cutover is null; the App fills `--generation` at click time from the live placement view (below).  The migrate action's `operatorCommand` is the §2.1 argv.
Each action also carries `chain[]`, display-only steps with status and no approval: `isle.storage.witness` (third device) and
`isle.storage.accept` (core).

**Status derivation** (from journal when known, else from receipts and placement; never from the plan revision): `planned` (dependencies satisfied,
nothing recorded) · `blocked` (a `dependsOn` is not `succeeded`) · `running` · `succeeded` · `failed` · `interrupted`.  `succeeded` for migrate needs
a stored receipt with `ok`, `contentVerified`, `stagedDeploy`, `readinessVerified` and a digest equal to the stored plan's.  `placementState` is shown
beside it, so "succeeded, awaiting cutover" and "accepted, hosting" are different on screen.  **The App never matches on epoch** (§5, Epoch arithmetic): it matches receipts to actions by `actionId` + `digest`, displays `placement.generation` live from the placement view, fills cutover's `--generation` at click time from that view, and treats "generation moved, re-read" as a normal refusal.

### 1.3 Manifest binding and immutable source objects (`repo/snapshot/`)
**Two hashes.**  The published manifest is the canonical content inventory **plus** `binding`: `{dataset, sourceDevice, takenAt, lockPath, planId|null, itemId|null}`; `lockPath` + `takenAt` identify the publication lock under which it was taken.  `manifestSha` = sha256 of the published document bytes (inventory + binding); `contentSha` = sha256 of the encoded inventory alone.  Generations, `current`, `binding.currentGeneration` and the witness are keyed by `contentSha`; the plan carries both (`source.snapshotSha256` = manifestSha, `source.contentSha256` = contentSha).  The target checks (1) the fetched document's sha256 == `snapshotSha256` (bound to the reviewed plan by the digest), (2) `binding.dataset`, and when present `planId`/`itemId`, equal the plan item's, and later (3) that the re-inventoried generation hashes to `contentSha256` (§2.2 stage 5).  The two hashes are never compared with each other.  The manifest cannot carry the revision or digest (circular).

**Publisher words (v3.4).**  `apt-repo.sh publish`, when the placement state is `cutover|hosting`, skips its private compose redeploy and prints "published on the core; hosting replica lags until sync" (§4).

**Namespace.**  `snapshot/` is a narrowly reserved top-level repository-control namespace, excluded from the inventory and nothing else (a nested `x/snapshot/` or a file named `snapshot` is content).  It holds `objects/<sha256>`, `manifests/<manifestSha>.json` and `latest.json`.  Under the publication lock `publish-manifest` also places every inventoried file in `snapshot/objects/<sha256>` (same filesystem), so an approved snapshot never depends on mutable source paths and a retry after a source publish still succeeds.  **Link versus copy (F1, Codex v3-1).**  A hardlink does not make a mutable file immutable: an in-place writer (`gpg -o`, `cp`, `tee`) truncates the shared inode and would corrupt `objects/<old-sha>`.  A file is therefore **hardlinked only when its repo path is immutable by construction** (`by-hash/**`, `catalog/<name>/<version>/**`, `runtime/<sha>…`, `rollouts/**`, `*.deb`, `*.tar`, `*.tar.*`); every other file (keyring, CA, bootstrap scripts, `index.html`, `InRelease`/`Release`/`Packages*`, descriptors; all small) is **copied** into `objects/` on a new inode.  The pol-owned publishers (`apt-repo.sh`, `catalog_publish.py`, `runtime_publish.py`) additionally write every file by stage + fsync + rename (a new inode each time), belt and braces.  A link is never chmod'ed.  **An object is created once**: an existing `objects/<sha>` is left alone (size verified equal) and never overwritten.  The source-side inventory keeps rejecting hardlinks for public content (`st_nlink != 1`) with one precise exception: a file passes when `st_nlink == 1`, or `st_nlink == 2` and `snapshot/objects/<its sha256>` has the same `(st_dev, st_ino)`; any other extra link is refused.  Equal-content files with distinct inodes keep `st_nlink == 1` and need no exception.  (Copying everything would double the 16 GB on the core; inventorying before linking would break every later inventory.)  **Retention is by live references, not a count (F2, Codex v3-2).**  `isle storage gc --repo` (root CLI, run under the publication lock) derives the pin set and removes only what is unpinned.  Pinned manifests: (a) every storage item of every retained plan revision that is active or has a placement entry; (b) each placement entry's plan, accepted and rollback checkpoints (`acceptedManifest`, `rollbackManifest`, §5); (c) every operation journal in state running, interrupted or failed-retryable that names a `manifestSha`; (d) `latest.json`; (e) the last 3 manifests as a floor.  Objects referenced by a pinned manifest are kept until released, so four publications during a slow copy cannot destroy its source.  APT by-hash artifacts referenced by the previous `InRelease` are immutable in `apt-repo.sh` and so stay in the next snapshot too.  **Bootstrap** (Codex 1): `publish-manifest` runs with no placement record, so the source snapshot exists before any plan is executable; `latest.json` `{manifestSha, contentSha, at, dataset, totalBytes, entries}` is the descriptor.  Accept is not a prerequisite of the first manifest.

## 2. Operations (isle-cli)
### 2.1 Request shapes
All values are regex-validated tokens; `storage_contract.parse_request` is the single parser the allowlist branch calls; the existing identity branch
of `operations.py` stays intact.

| verb | runs on | argv (every verb is a journaled operation, root) |
|---|---|---|
| authorize | core | `['storage','authorize','--dataset',D,'--plan',P,'--revision',N,'--item',I,'--digest',H]` |
| migrate | target | `['storage','migrate','--dataset',D,'--plan',P,'--revision',N,'--item',I,'--digest',H]` |
| mark-migrated | core | `['storage','mark-migrated','--dataset',D,'--digest',H]` |
| cutover | core | `['storage','cutover','--dataset',D,'--generation',G]` |
| rollback | core | `['storage','rollback','--dataset',D,'--generation',G]` |
| recover | core | `['storage','recover','--dataset',D,'--generation',G,'--to','migrated'\|'cutover'\|'hosting']` |
| accept | core | `['storage','accept','--dataset',D,'--generation',G]` |
| reconcile-address | core (run by `dns-reconcile`, §2.5a) | `['storage','reconcile-address','--dataset',D,'--generation',G]` |
| sync | target | `['storage','sync','--dataset',D]` |
| witness | third device | `['storage','witness','--dataset',D,'--generation',G]` |
| units-apply | hosting device (typed as `isle storage units apply --dataset D [--overrides keep\|remove]`) | `['storage','units-apply','--dataset',D]` or with `'--overrides','keep'\|'remove'` |

(`authorize-placement` / `accept-placement` in older text mean `authorize` / `accept`.)  `gc` is not a dataset verb: it is a root CLI (`isle storage gc --repo`) that runs under the publication lock (§1.3).  `publish-manifest` and `gc` are publisher-side root CLIs on the core, not journaled operations: they take the publication lock, not the dataset lock.

`operations.execute` takes `app-storage-<dataset>.lock` **before the child starts**, from the tokens, and names the journal `storage-<dataset>` at
creation, so `latest(name)` works per dataset.  The lock is dedicated: a long copy does not block `update apply`/`reconcile`.  Every storage verb of one dataset (authorize, migrate, mark-migrated, cutover, rollback, recover, accept, sync, witness, reconcile-address) takes it; rollback never runs beside a copy, and a hung migrate must be marked `interrupted` first. **Device-update lease (E4a).**  No acquire-and-release probe (it is a TOCTOU).  The storage **critical stages** hold `app-device-update.lock` SHARED (`LOCK_SH`): on the target stages 6–8 (binding write, staged deploy, readiness); on the core every step of mark-migrated, cutover, rollback, recover, accept and reconcile-address.  `authorize` and `witness` hold no lease.  The shared hold never covers the multi-hour copy (stages 3–5) or the sync fetch, which hold nothing, so a device update is not refused for the whole transfer.  `update apply` and `reconcile` take the lock EXCLUSIVE non-blocking and refuse while it is held; `isle update apply`, `isle reconcile` and the identity-reconcile, when refused for that reason, say "storage operation in progress for dataset D" instead of the generic busy text (`isle update plan` is read-only and takes no lock).  Conversely a storage stage 6 or core verb refused because the exclusive update lock is held says "device update in progress on <device>; retry after it completes".  The identity-reconcile operation, which takes the same exclusive lock on purpose, is likewise refused during a storage critical stage.  **Cross-device fencing** is the ownership epoch (E4b): every core commit takes `--generation` and the CAS checks it; the target's receipt and the witness carry the epoch they observed. Stage 1 refuses if the plan item's `storage.dataset` differs from `--dataset`.  A retry reuses the journal's normalized request.  Root only, through the existing dispatcher and PolicyKit action; there is no unjournaled lane for any core mutator.  The dataset lock is per device.  Not operations (direct CLI): `check`, `status`, and root `gc` (publication lock, §1.3).

### 2.2 `migrate` stages (1–8; each idempotent; evidence re-checked on retry)
| # | label | does | refuses (nothing written) when |
|---|---|---|---|
| 1 | `fetching-plan` | GET plan by id+revision over trusted HTTPS; recompute digest; require active plan, item `planned`; `dataset` matches; read the placement view (`GET …/storage/placement?dataset=`, §5) | plan missing/not active/revision not retained/digest mismatch; item not storage or deferred; `device` ≠ own identity; placement view absent, or not `authorized` (or `migrated` with an entry digest equal to the request digest: a legitimate re-run, e.g. host redeploy after a disk swap) for this device and domain |
| 2 | `preflight-storage` | `storage_preflight.inspect`; mount must be a systemd mount unit (an fstab entry qualifies through `systemd-fstab-generator`, which is pol's `/mnt/pol-ssd`); capacity = `totalBytes` + 5 % headroom; write probe; open the mount root fd and capture `st_dev` | any inspect refusal; mountpoint resolves to `/`; `<mount>/<relativePath>` exists on the root fs; existing `binding.json` names another uuid/dataset |
| 3 | `fetching-manifest` | GET `https://<domain>/snapshot/manifests/<manifestSha>.json` from the **source origin** (D11 below); document sha256 == `snapshotSha256`; validate `binding` (dataset, planId/itemId); `totalBytes` equals plan; validate every path (below); capacity re-check = `totalBytes` + largest file + 5 % + store delta | sha mismatch; binding mismatch; > 64 MiB; schema ≠ `isle-content-snapshot` v1; any unsafe path |
| 4 | `receiving-content` | sequential; deduped by sha (one fetch per distinct sha256): skip if in `store/` with equal sha+size; else fetch `https://<domain>/snapshot/objects/<sha256>` (never a path) into `staging/<opid>/`, verify sha+size, fsync, link into `store/<sha256>`, hard-link into `generations/<contentSha>/<path>`; progress every 64 MiB | UUID/`st_dev` re-check fails before a file; capacity below remaining; sha/size mismatch (file deleted, stage fails, retryable) |
| 5 | `verifying-content` | re-inventory `generations/<contentSha>/` with the generation verifier (§3) and **require `contentSha256`**; verify only, nothing is promoted in stage 5 (promote is stage 6, v3.4) | mismatch: generation left for inspection, `contentVerified:false` |
| 6 | `writing-binding` | (shared device-update lease, stages 6–8, §2.1) **promote**: write `control/manifest-<sha>.json`, atomically replace `current`, then `latest.json` and the `current` one-liner from `readlink(current)` (§3, F6); atomic `binding.json` (§3); install the mount-gate unit (§4) and the sync timer (§2.5) and **enable** the hosting unit and the timer (`systemctl`); the receipt records `syncTimerInstalled: true` | promote or `systemctl enable` fails (receipt `syncTimerInstalled: false`) |
| 7 | `deploying-host` | (shared lease) `isle app deploy --staged --bind ISLE_DATASET_PATH=<verified path>` of the pinned release (D3 below) | hosting preflight fails; release identity differs from the pin; `storage check` fails; placement no longer authorized |
| 8 | `verifying-readiness` | (shared lease) health plus `curl --resolve <domain>:443:<this agent ip>` of 3 sampled entries and the InRelease-equivalent (SNI = domain): sha256 equals manifest | not ready in 60 s; sample mismatch |

After stage 8 the operation writes the receipt (§2.6), POSTs it to `POST /api/islemesh/storage/receipt` and exits `succeeded`.  The backend only
stores it (after verifying `plan.digest` against the stored plan and revision); the core-side `isle storage mark-migrated` decides (§5).  **No DNS
write happens in migrate.**  The operation never removes anything on the source (it has no credentials there).

**Source origin (D11, Codex 2).**  The source address comes from the authenticated core devices door (`api.polari.isle`) for `originDevice`.  The
target fetches `https://<domain>/…` with `--resolve <domain>:443:<source agent address>`, verified against the isle root CA, `--max-redirs 0`, no
`-k`.  The same origin serves rollback checks and post-cutover sync, so the source origin stays distinct from the cut-over name.  The domain must be a
DNS hostname; manifest paths are canonical relative data paths, built into URLs by one percent-encoding function.

**Manifest path validation** (receiver, stage 3 and 4): relative; no absolute, `..`, backslash, empty segment, `?`, `#`, `%`, control character; every
parent directory on the receiver is a real directory (no symlink, no special file) checked with `O_NOFOLLOW` descriptors; fetched objects are regular
files only.

**Staged deploy (D3).**  `isle app deploy --staged` (app-deploy.sh): hosting preflight → `certs ensure` → compose up → `agent-manager register` →
`hosts-reconcile` → **no `dns_publication`** (audit F13: no such mode exists today) → health via `curl --resolve <domain>:443:<agent ip>` →
self-report with `staged: true`.  `certs ensure` succeeds on the target only because the placement entry is `authorized` (§5; audit F11).  `--bind
PARAM=<path>` is consumed by the overlay generator only after `storage check` proves the path.

### 2.3 `cutover` (core; `sudo isle storage cutover --dataset D --generation G`; journaled, shared device-update lease for every step)
1. Take the dataset lock and the shared lease; re-fetch the plan: active, digest equals the placement entry's `plan.digest`.
2. **Precondition** (epoch arithmetic: §5): require placement `state == migrated`, `generation == G`, where `G` is the value the caller observed (a rerun whose stored `pending` matches skips to step 4, see step 3); the target's `isle-storage-sync@<dataset>.timer` installed and enabled, read from the migrate receipt's claim (`syncTimer: "receipt-claimed"`, stage 6 `syncTimerInstalled`; else refuse, "sync
   trigger not installed"); and the target's `last-committed` contentSha equal to the source `latest.json` contentSha (else "run sync first").
   The router's own managed-names list (`isle dns managed-names list`) holds the domain: cutover reads it and does not trust that authorize
   pushed it; a missing name is pushed once and read again, and cutover refuses when the router does not answer or still does not hold it.
   Re-verify the target serves, using `curl --resolve` to its lease IP.
3. **Durable intent.**  CAS-write `pending: {action:"cutover", digest, from:"migrated", to:"cutover", epoch:G+1, address:<target lease IP>, previousPin, newPin, at, operation}`
   under the placement flock: the CAS expects `generation == G` and leaves `generation = G+1`, with `pending.epoch = G+1` (the post-write value, §5).  No DNS has been touched.  Pins are part of the intent (`previousPin` = the core's current hosts pin for the domain, `newPin` = none) so recovery restores both the router record and the pin.  From here `dns-reconcile`/`hosts-reconcile` write nothing
   for the domain.  A rerun with the same `G` resumes at step 4 only when the stored `pending` has this exact action+digest+epoch **and operation id** (`G` no longer equals `generation`, so resume is decided by the `pending` match, not by `G`); any other `G` is refused with the current value in the words; any other stored `pending` is refused (a stale one of another operation goes through `recover`, §2.4a).
4. **Managed-pin transition (F4, Codex v3-4).**  Under the intent, on the core, the domain's `/etc/hosts` pin leaves the managed block (`apt-repo.sh`'s line is already managed by then, §5).  Without this the by-name check would still reach the source.
5. `isle dns register domain <target ip>`: `dns-register.sh` accepts only the intent's address, only from the intent's operation, and holds `placement.lock` across the router mutation (§5).
6. Verify with `curl --resolve <domain>:443:<router-derived address>` (the address from `dig @router`, which must equal the target's), SNI = domain: the InRelease-equivalent, 3 sampled entries and the control `latest.json` carry the manifest's sha256.
7. Final ordinary-resolution check from the core: `getent hosts <domain>` and https by name return the target's address and the same samples.
8. **Commit: CAS `migrated → cutover`** (after verification): expects `generation == pending.epoch` (G+1), leaves **E = G+2**, clears `pending`; the witness binds E; then the receipt (kind `isle.storage.cutover`, `actionId`, epoch E,
   previous and new address and pin).  A rerun after a lost response sees `cutover` and continues with the post-commit rules below.

**After the commit.**  From now `dns-reconcile`, `hosts-reconcile` and the registry push consult the entry (§5): the authoritative address is the target's lease IP found
   by device hostname; the core's `127.0.0.1` pin for the domain stays gone (moved at step 4); the domain is omitted from the registry copy the core pushes to the
   backend.  The core's local `registry.json` entry and app stay as they are, so the source keeps serving on its own address (source origin and
   rollback).
**Auto-revert on failure of steps 4–7**: CAS-replace the intent with `{action:"cutover-revert", to:"migrated", address:<core address>, …}`,
   restore the managed pin (`previousPin`) alongside `isle dns register domain <core address>` (both from the intent), verify `dig @router` and that the old source serves (https sample), CAS back to `migrated` clearing
   `pending`; receipt `rolledBack: true`.  If **that** verification fails: CAS `→ recovery-needed` (pending cleared), record both addresses, exit 1,
   `rolledBack` stays false.  `recovery-needed` freezes all automated DNS writes for the domain (§5).  A crash at any point leaves state plus `pending`;
   the matching retry resumes, otherwise `recover` (§2.4a).

### 2.4 `rollback` (EMERGENCY ONLY)
The auto-revert paragraph of §2.3 run on demand: allowed only from `cutover` with the matching `G`; it first writes the intent `{action:"rollback", from:"cutover", to:"migrated", epoch:G+1, address:<core address>, previousPin, newPin}`, then registers, verifies and CASes, clearing `pending`; same verification and the same `recovery-needed` outcome; never in a sequential command block; user-facing text says EMERGENCY ONLY.  It deletes nothing and never touches data.  From `hosting`, rollback is refused: reversing an accepted placement needs a new plan.

### 2.4a `recover` (core; `sudo isle storage recover --dataset D --generation G --to migrated|cutover|hosting`)
`--to hosting` exists only for a pending reconcile-address that must land back in `hosting`.  Entering `recovery-needed` clears `pending`, so `recovery-needed` never has a `pending`: recover from it is authorised by `--generation` alone.  Allowed from `recovery-needed` with the matching `G`, and from **any** state while a `pending` exists (F3, Codex v3-3); a crash between a DNS write and its commit leaves `migrated` or `cutover` plus `pending`.  Only the exact matching retry (action, digest, epoch **and operation id**) or `recover` may proceed.  **Ownership and liveness check first**: before superseding a `pending` of another operation, recover proves the original operation is not live (its per-operation journal lock is free, i.e. `operations.read()` says interrupted or failed, never running) and records `{supersededOperation, at}` in `history`; it never steals from a live operation (it refuses and names it).  Intent next (`action:"recover"`, `to` = the chosen state, `address` and pins = that state's owner), then `dig @router` and https checks of both addresses must agree with the chosen state; register only if needed, CAS to the chosen state, clear `pending`.  **Two crash windows** are handled separately: (i) *after the DNS write, before the CAS*: the retry or recover re-reads the router (`dig @router`) and either completes the CAS when the router shows the intent's address, or reverts router and pin; (ii) *after the CAS, before the receipt*: the retry writes the receipt from the stored state and never repeats the transition.  `dns-register.sh` holds `placement.lock` for the whole router mutation (uci set/commit/restart) and re-reads the intent under that lock immediately before writing (§5).  Journaled, shared lease, like every core verb.

### 2.5 `sync` (target; `sudo isle storage sync --dataset D`)
Allowed in `migrated`, `cutover`, `hosting`.  Fetches `snapshot/latest.json` from the source origin (by address, SNI = domain; before and after cutover alike), then stages 3–5 into a **new generation** keyed by contentSha: unchanged files link from `store/`; `current` moves only after the new generation re-inventories to its `contentSha`; the previous generation stays and keeps serving on interruption.  Old generations are not reclaimed here (a later verb).  Shares the dataset lock.  **The trigger is mandatory before cutover is admitted** (§2.3): root timer `isle-storage-sync@<dataset>.timer` on the target, every 10 min, compares the source `latest.json` contentSha with `current` and runs the sync operation on change (plus the App action). `syncStatus` `{lastPublished, lastReceived, lastCommitted (contentShas + times), lagSeconds, error}` is reported by `status` (§2.7); a later contentSha has its own status and is never implied by an old witness.  Publisher words: when the entry is `cutover|hosting`, a publisher's success line says "published on the core; hosting replica lags until sync" and never announces success for artifacts absent from the serving target.

### 2.5a Steady-state address drift: `reconcile-address` (core; `sudo isle storage reconcile-address --dataset D --generation G`; F5, Codex v3-5)
Without this, `dns-register.sh` (which allows only a pending cutover, rollback or recover) could not follow a changed target lease.  For a managed domain in `cutover|hosting` with **no** `pending`, `dns-reconcile` (root timer on the core) detects router address ≠ the owner device's current lease and runs this journaled operation under the same authority as every core verb: dataset lock and shared lease, then an intent `{action:"reconcile-address", epoch:G+1, from-address, to-address}`, registration through `dns-register.sh` under it, verification (`dig @router`, https by `--resolve` to the new address), clear.  Ownership and state do not change (one `history` row appended); the epoch advances by the same two writes as every transition (intent G→G+1, clear G+1→G+2), so a witness bound to the pre-drift epoch is stale by construction and a dataset in `cutover` must be witnessed again at the new address before `accept`.  A concurrent cutover, rollback or recover refuses because a `pending` exists; a crash leaves `pending`, resumed by the exact retry (operation id included) or `recover`.

### 2.6 Receipt (`<ISLE_OPERATION_DIR>/storage-receipt.json`, appended to `/var/lib/isle-mesh/storage/<dataset>/receipts.jsonl`)
```json
{
  "kind": "isle.storage.migrate", "schemaVersion": 2, "phase": "A",
  "actionId": "isle.storage.migrate:apt-public-repo@pol-core:<storageDigest>",
  "operation": "<op id>", "attempt": 2, "at": "<iso>", "ok": true,
  "plan": {"id": "…", "revision": 2, "itemId": "…", "digest": "<storageDigest>"}, "digestRecomputed": true,
  "hostingRelease": {"name": "isle-content-host", "version": "2026.11.1", "manifestSha256": "…"},
  "dataset": "apt-public-repo", "domain": "apt.isle", "placement": {"state": "authorized", "generation": 1},
  "target": {"device": "pol-core", "filesystemUuid": "…", "filesystemUuidObserved": "…",
             "mountSourceObserved": "/dev/sda1", "mount": "/mnt/pol-ssd", "stDev": "8:49",
             "path": "/mnt/pol-ssd/isle/datasets/apt-public-repo", "availableBytesBefore": 0, "availableBytesAfter": 0},
  "source": {"device": "isle-core", "snapshotSha256": "<manifestSha>", "contentSha256": "<contentSha>", "totalBytes": 0, "entries": 0, "origin": "<address used>"},
  "stages": {"preflight-storage": {"ok": true, "at": "…"}, "receiving-content": {"ok": true, "bytes": 0, "files": 0, "skipped": 0}},
  "generation": "<contentSha>", "contentSha": "<contentSha>", "manifestSha": "<manifestSha>", "epoch": 1, "contentVerified": true, "stagedDeploy": true, "readinessVerified": true,
  "sourceRemovable": false,
  "mountsBefore": ["<findmnt rows>"], "mountsAfter": ["…"], "words": "…one sentence…"
}
```
Cutover and rollback receipts add `cutover: {done, at, previousAddress, newAddress, rolledBack, recoveryNeeded}` and
`placement.generationBefore/After`.  A failed attempt writes the same file with `ok:false`, the failing stage and `retryable`.  Receipts are the
evidence the core CLI checks; the backend stores them and never decides.  The receipt carries **both** `manifestSha` and `contentSha`; the `actionId`
stays keyed by the storage digest.  **Binding (E5)**: every stored receipt is bound to `{device (authenticated), actionId, digest, epoch, contentSha}`;
a door rejects a receipt whose plan/revision is not the stored one, stores the presented Bearer as `posterPresented` with `verified: false` (the
backend calls no core verifier), and treats re-posting an identical receipt as idempotent (same row, `seen` count).  Core verbs verify the poster
against `members.json` locally and never log tokens (fingerprint only).

### 2.7 `isle storage status --dataset D` (read-only, no root)
```json
{"dataset": "apt-public-repo", "device": "pol-core", "placement": {"state": "migrated", "generation": 3},
 "operation": {"id": "…", "state": "succeeded", "stage": "verifying-readiness"}, "receipt": {"ok": true, "digest": "…", "epoch": 3, "at": "…"},
 "mount": {"bound": true, "mounted": true, "uuidMatches": true, "currentGeneration": "<contentSha>", "hostRunning": true, "pointerAhead": false},
 "syncStatus": {"lastPublished": "<contentSha>", "lastReceived": "<contentSha>", "lastCommitted": "<contentSha>", "lagSeconds": 0, "error": null}, "timer": "enabled"}
```
`check` and `status` read the root-filesystem copy `/var/lib/isle-mesh/storage/<dataset>/binding.json` for `mountpoint`, uuid and `relativePath`, then verify the disk against it. When the disk is absent they report from the root-filesystem copies (§3) and say so.  `pointerAhead: true` means `control/latest.json` disagrees with `readlink(current)` (§3); `current` is the truth.

## 3. Target layout, binding record, write discipline
```
<mount>/<relativePath>/
  binding.json                  marker (evidence, not authority)
  store/<sha256>                one file per distinct content hash
  generations/<contentSha>/<path>    hard links into store/
  current -> generations/<contentSha>    relative symlink, replaced atomically (rename)
  control/                      manifest-<manifestSha>.json (before the swap); latest.json + current one-liner (after it)
  staging/<opid>/               partial fetches
```
```json
{"schemaVersion": 2, "dataset": "apt-public-repo", "filesystemUuid": "…", "mountpoint": "/mnt/pol-ssd", "relativePath": "…",
 "currentGeneration": "<contentSha>", "boundAt": "…",
 "plan": {"id": "…", "revision": 2, "digest": "…"}, "hostApp": "isle-content-host"}
```
The hosting app bind-mounts `<mount>/<relativePath>` read-only at `/data` and nginx's root is `/data/current`: a bind of the `current` symlink itself
would pin one generation's inode and would not see a promotion.  Only `current/` (at `/`) and `control/` (at `/.well-known/isle-storage/`, §7) are
served.  **Generation verifier** (stage 5, sync): a narrowly scoped re-inventory that tolerates only the generation's own store links (same inode as
`store/<sha>`) and refuses any other hardlink, symlink or special file; the source-side inventory is a different code path and keeps rejecting
hardlinks (§1.3), so public source rejection is not weakened.  `binding.json` proves "this is the dataset's filesystem"
but is not the authority: the authority is the placement record plus the receipt.  `currentGeneration` in the marker is updated after the symlink;
`check` treats the symlink as the truth.  Copies live at `/var/lib/isle-mesh/storage/<dataset>/{binding.json,receipts.jsonl}`; stage 6 writes the root-filesystem `binding.json` copy together with the on-disk marker.

**Promote order and control endpoint (F6, Codex v3-6).**  `current` is the only committed generation pointer.  `control/manifest-<manifestSha>.json` is written first (immutable, harmless if the swap never happens); `current` is swapped atomically; then `control/latest.json` `{manifestSha, contentSha, promotedAt, epoch, dataset}` (`epoch` = the placement generation at promote time, **informational**: sync does not rewrite it, so after cutover it lags the entry's generation; no check depends on it, v3.4) and the one-line `control/current` (the `contentSha`) are written, both derived from `readlink(current)`, never from the writer's own variable.  `check`, `status` and the witness treat `current` as truth and `latest.json` as advertised.  A reader finding `latest.json` ≠ `readlink(current)` (a crash between swap and write, or a promotion in progress) trusts `current` and reports `pointerAhead: true`; the next promote or sync repairs it by rewriting `latest.json` from `current` first.

**Anchored-fd discipline.**  Open the mount root once with `O_DIRECTORY|O_NOFOLLOW`; verify UUID via `findmnt --json` for the descriptor's path and
`st_dev` equality; do every create/link/rename with `openat`/`linkat`/`renameat` relative to descriptors derived from it (`O_NOFOLLOW` on each
component).  Before each generation step reopen the root and re-verify; any mismatch aborts retryably.  No path string is re-resolved between check
and use.

## 4. Mount gate for the hosting app
- The release's compose uses a long-syntax bind with `create_host_path: false` and the parameter `ISLE_DATASET_PATH`, set by the overlay generator via
  `--bind` only after `storage check`.  The relative dataset directory exists only on the disk, so an unmounted disk makes the bind fail instead of
  creating a root-fs directory.  `restart: "no"` on the hosting service.
- `isle-storage-<dataset>.service` (generated, root): `RequiresMountsFor=<mount>`, `BindsTo=<mount unit>` and `After=<mount unit>` (the unit stops
  when the disk goes), `AssertPathExists=<mount>/<rel>/binding.json` (a visible failure, not `ConditionPathExists`'s silent skip), `ExecStartPre=isle
  storage check --dataset D`, `ExecStart=docker compose -p isle-<app> -f /etc/isle-mesh/apps/<app>/docker-compose.yml -f isle-overlay.yml up -d`, `ExecStop=… stop`.  Stage 2 refuses a mount that is not a systemd
  mount unit.
- `isle storage check --dataset D` (read-only) reads the root-filesystem copy `/var/lib/isle-mesh/storage/<dataset>/binding.json` (written by stage 6 with the on-disk marker) for `mountpoint`, uuid and `relativePath`, then verifies the disk against it: `{bound, mounted, uuidMatches, markerMatches, currentGeneration, placementAgrees, hostRunning}`.  UUID
  and current generation sha are re-read; the marker JSON is not proof.
- **Every launch path** goes through `storage check` before compose up when the app has a storage binding: `isle app deploy`, `isle app reconcile`,
  `isle update apply`, and the systemd unit.  A hand-run `docker compose up` fails on the missing bind path.
- Storage critical stages hold the shared device-update lease (§2.1); `isle update apply`/`reconcile` take it exclusive and refuse while it is held.

## 5. Placement record: the ownership authority
`/etc/isle-mesh/storage/placement.json` (core, root-written, 0644, atomic, flock `placement.lock`):
```json
{"schemaVersion": 2, "datasets": {"apt-public-repo": {
  "dataset": "apt-public-repo", "domain": "apt.isle", "device": "pol-core", "sourcePath": "/etc/isle-mesh/apt/repo",
  "state": "migrated", "generation": 3,
  "pending": null,   // durable transition intent: {action, digest, from, to, epoch, address, previousPin, newPin, at, operation}
  "plan": {"id": "ce23b61c…", "revision": 2, "digest": "<storageDigest>"},
  "acceptedManifest": null, "rollbackManifest": "<manifestSha>|null",   // checkpoints pinned by gc (§1.3)
  "history": [{"from": null, "to": "authorized", "at": "…", "by": "isle-core:root", "operation": "authorize"},
              {"from": "authorized", "to": "migrated", "at": "…", "by": "isle-core:root", "operation": "<migrate op id>"}]}}}
```
`generation` is an integer ownership epoch, +1 on every transition; it is not a content generation (that is the manifest sha).  **CAS rule**: under
the flock, read the entry, require `(state, generation)` equal the caller's expectation, write the new state with `generation+1` and a history entry,
fsync file and directory, rename.  A mismatch fails with the current values.  **Every write to an entry, including writing or clearing `pending`, bumps `generation`**, so a stale holder of an older epoch always fails its CAS; `pending.epoch` is the generation at which the intent was written (the post-write value) and the operation's later CAS expects it (Epoch arithmetic, below).  `acceptedManifest` is set at accept, `rollbackManifest` at cutover; both are manifestShas that `gc` pins until released.  `recovery-needed` additionally holds `recovery: {sourceAddress, targetAddress, at}`.
**Intent and fencing.**  Every core commit takes `--generation` and the CAS checks it (cross-device fencing).  Before any DNS write the entry gets `pending` (CAS-written; state unchanged, generation bumped); while it exists dns-reconcile/hosts-reconcile write nothing for the domain and `dns-register.sh` accepts only its address from its operation.  The writer completes (CAS to `to`, clears `pending`) or fails (`pending` stays, state unchanged); a retry resumes only when the stored `pending` has its exact action+digest+epoch **and operation id**; `recover` (§2.4a) is the only verb that supersedes another, after a liveness check.  Pins are part of the intent (`previousPin`, `newPin`), so recovery restores router record and managed pin together.  Exactly one hosting instance per dataset in v1.

| transition | command | where / who | precondition |
|---|---|---|---|
| ∅ → authorized | `authorize` | core, root | plan active, digest matches, source snapshot published, no entry; the domain is not the core itself; `source.device` owns the domain now |
| authorized → authorized | `authorize` (new revision) | core, root | same dataset/device/domain, new plan revision |
| authorized → migrated | `mark-migrated` | core, root (invoked by the receipt relay or by hand) | stored receipt `ok`, `stagedDeploy`, `readinessVerified`, `contentVerified`; receipt digest == stored plan digest and == entry digest; receipt device == entry device; poster identity verified |
| migrated + pending(cutover) → cutover | `cutover` | core, root | after verification, §2.3 steps 1–7 |
| migrated + pending(cutover\|cutover-revert) → migrated | auto-revert | core, root | old source verified serving; clears `pending` |
| migrated + pending(cutover\|cutover-revert) → recovery-needed | auto | core, root | revert verification failed; clears `pending` |
| cutover + pending(rollback) → migrated | `rollback` | core, root | old source verified serving; clears `pending` |
| cutover + pending(rollback) → recovery-needed | `rollback` | core, root | verification failed; clears `pending` |
| recovery-needed → migrated\|cutover | `recover --to` (by `--generation`; no `pending`) | core, root | `dig` and https checks of both addresses agree with the chosen state |
| cutover → hosting | `accept` | core, root | a stored, fresh witness whose epoch equals the entry's `generation`; records `acceptedWitness` (§6) |
| any state + pending → chosen state | `recover` | core, root | original operation proven not live; `{supersededOperation, at}` row in `history` (§2.4a) |
| cutover\|hosting + pending(reconcile-address) → same state | `reconcile-address` | core, root | no other `pending`; verified new address (§2.5a); one `history` row |
| hosting → (none) | — | — | Phase A has no transition out of `hosting` (rollback refused) |

`pending` is only ever written in `migrated`, `cutover` and `hosting`; `authorized` and `recovery-needed` never carry one.

**Epoch arithmetic.**  Worked for cutover, with `G` = the `--generation` the caller observed:
1. Precondition: `generation == G`.
2. Intent write: CAS expecting `G`; leaves `generation = G+1` and writes `pending.epoch = G+1` (the post-write value).
3. Commit: CAS expecting `generation == pending.epoch` (G+1); leaves **E = G+2**; clears `pending`.
4. The witness binds E; `accept` requires `witness.epoch == E` and leaves E+1.

A rerun with the same `G` and a stored `pending` whose action, digest and operation id match resumes at the step after the intent: `G` no longer equals `generation`, so resume is decided by the `pending` match, not by `G`.  Any other `G` is refused with the current value in the words.  A failed cutover is the same pattern again: the intent takes G→G+1, the revert intent takes G+1→G+2, and clearing back to `migrated` takes G+2→G+3 (every write bumps; the App never matches on epoch).  Rollback, recover and reconcile-address follow the same two-write pattern (intent at +1, commit at +2) from their own observed `G`.

**Who consults it.**
- **CA** (`cert-authority.issue`; audit F10–F12).  Today `CORE_NAMES` (including `apt.isle`), any domain in the core's registry and any existing core
  cert are refused for members.  v2: such a domain is issued to **exactly the device named by an entry** in state
  `authorized|migrated|cutover|hosting`; `claims[domain]` in `claims.json` stays a STRING (device) so every reader keeps working; placement-granted
  claims live in a separate `placement-claims.json` consulted by `cert-authority.issue` only.  The core's own cert for the domain stays valid and
  `certs.sh` renewal of it keeps working (its cert file is never removed in Phase A).  **Policy** (the user, 2026-11-14: "everything may move with the exception of the core
  itself"): `authorize` refuses one name, the core itself (`storage_core.core_itself()`: the address members reach the core at, `api.polari.isle`);
  no list of movable or protected names exists, and the earlier `hostable-names` file is no longer read.  At authorize the
  entry's source device must be the domain's current owner (core registry or claims).  `recovery-needed` issues nothing new.  Without an entry the refusal is
  unchanged.
- **dns-reconcile** (audit F3–F5).  A domain with an entry is excluded from the "first sorted catalog instance" rule that otherwise flaps by name
  order.  Address: `authorized|migrated` → the core's agent address; `cutover|hosting` → the target's lease IP by device hostname; `recovery-needed` →
  no writes.  Reconciliation queries the router (`dig @router`), never `getent`; a mismatch with that address is the only trigger to register.  It writes nothing
  while `pending` exists.  **Role (F5):** it never calls `dns-register.sh` for a managed domain on its own authority: drift (router address ≠ the owner's current lease) in `cutover|hosting` with no `pending` is repaired only by running the journaled `reconcile-address` operation (§2.5a).
- **Every DNS writer is fenced.**  `dns-register.sh` (the only router writer for `.isle` names) consults the entry: for a managed domain it refuses
  unless called by the storage cutover/rollback/recover/reconcile-address operation (`ISLE_OPERATION_ID` plus a matching `pending`) and the address equals the intent's.  It holds `placement.lock` for the whole router mutation (uci set/commit/restart) and re-reads the intent under that lock immediately before writing; the lock is not released between check and mutation (§2.4a).
  The router's mDNS join scan and agent-manager mDNS are non-authoritative (audit §1) and skip managed names via a managed-names list the core pushes
  to the router (`/etc/isle-managed-names`, read by `configure-join-protocol.sh`).  Core app redeploy / update of the source app does not call
  `dns_publication` for a managed domain in `cutover|hosting` (`app-deploy.sh` checks the record).
- **hosts-reconcile** (audit F6, F7).  For `cutover|hosting` the core keeps **no** pin for the domain ("none"), so the core resolves it like every member and the ordinary-resolution step of cutover is meaningful; `apt-repo.sh`'s
  unmanaged `127.0.0.1 apt.isle` line moves into the managed block in Phase A, because it would make `getent` mismatch forever and re-register every
  two minutes.  The pin leaves the managed block at cutover step 4 under the intent (§2.3); `previousPin` lets rollback and recover restore it; `hosts-reconcile` writes nothing for the domain while `pending` exists.
- **Registry push** (`push-to-polari.sh`; audit F6): omits domains whose entry is `cutover|hosting`.
- **Backend** (audit F8, F9): keeps no arbitration; two `IsleApp` rows for one domain may coexist.  The hosting service is named `content` to avoid
  the isle-wide `app/svc` dedupe collision with the core's `apt/apt`.
- **Access model.**  The placement file has no network write door; only root on the core writes it.  **Read path for members (econ PIN 1, v3.4):** the
  placement view reaches the backend as a **file the core writes into the backend's data directory** (`docker exec`, root on the core, the same mechanism as the catalog release index `isle-catalog-releases.json`), exported after every transition and every 120 s by the push timer.  The view carries `sourceDevice` and `addresses{source,target}` besides device, state, generation, plan and pending.  `POST /api/islemesh/storage/placement` (core identity only) stays as the future authenticated path; anyone reads `GET /api/islemesh/storage/placement?dataset=`.  **Conditions on the file (v3.4):** (1) it is written atomically inside the container (temp file plus rename, via `docker exec`) and only **after** the placement lock commit, never from uncommitted state; (2) shape `{schemaVersion: 1, writtenAt: <ISO UTC>, writtenBy: <core device>, datasets: {dataset: entry}}`; (3) the GET answers `503 {"ok": false, "error": "placement view unavailable"}` on any parse or schema error or when `writtenAt` is older than 600 s (the core rewrites every 120 s), so a torn or stale file fails closed at stage 1 exactly like an absent one; (4) every GET answer carries `"source": "file" | "door"` and the two are **never merged**: valid file, file only; absent file, the stored rows (`door`); present but invalid or stale, 503, not the rows.  The
  view is a hint: stage 1 refuses unless it says `authorized` (or `migrated` with the same digest, a re-run) for this device and domain, and a stale view therefore fails closed; the authoritative
  checks stay where root on the core enforces them (CA issue in stage 7, the cutover CAS).  No member ever reads the core's filesystem.
  **Poster identity (econ PIN 2):** the receipt and witness doors verify shape and `plan.digest`/revision against the stored plan, record the
  presented Bearer's fingerprint and subject as `posterPresented` with `verified: false`, and store; the backend never calls a core-local tool.
  `mark-migrated` and `accept` (root on the core) verify the poster against the CA members list and the receipt's `target.device` before
  they transition.  A forged receipt therefore changes nothing anywhere; a JSON field naming a device proves nothing.
  **The relay (v3.4).**  `isle storage relay` (core, root; run by the 120 s push path) pulls `GET …/storage/receipts` and `GET …/storage/witnesses`, appends the new rows (with `posterPresented`) to the core's local evidence files (`<state>/<dataset>/inbound-receipts.jsonl`, `witnesses.jsonl`), then runs the journaled `mark-migrated` when a stored receipt qualifies.  `accept` reads the local witness store only.

## 6. Witness, acceptance, removal and retirement
`isle storage witness --dataset D --generation G` is a **root journaled operation** on a **third device** (it needs the root-only enrollment Bearer, which is never exposed to the frontend); it refuses when its own device name is the source or the target (both taken from the placement view: `device` and `sourceDevice`).  It binds the generation G from the placement view (state `cutover`, generation G) and does **not** require `latest.json` `epoch == G` (it records the advertised epoch as `promotedAtEpoch`, §3).  It refuses when the connected address differs from the view's `addresses.target`.  Through normal DNS it fetches `https://<domain>/.well-known/isle-storage/latest.json`, `/.well-known/isle-storage/current` (committed vs advertised, §3) and the manifest (§7), samples 3 small entries plus **one** bounded entry (≤ 64 MiB) chosen deterministically from `(G, device)`, and records `resolvedAddress`, the `dig` route and the time.  It POSTs to `/api/islemesh/storage/witness`.  The witness is bound to `{dataset, epoch, contentSha, cutover actionId}` and must show the target's address.  Sampling proves routing and content, nothing more.

Freshness and revocation: `witness.at` must lie within [`cutover.at` − 120 s, `cutover.at` + 24 h] (120 s clock-skew allowance); every placement transition raises `generation`, so a witness for an older epoch is stale and ignored; an IDENTICAL re-post (same bytes) is idempotent (same row, `seen`+1), while a DIFFERENT witness for the same `(dataset, G, device)` is rejected as replay/conflict (a receipt is evidence of a state, so repeating it is harmless; a witness is evidence of a moment, so a second one would rewrite what was observed). **Epoch rule**: the cutover CAS makes epoch E and the witness binds E; `accept` requires `witness.epoch == entry.generation` (E) and records `acceptedWitness: {id, epoch: E}` before its own bump to E+1, so the proof is retained as "witness of epoch E".  A later contentSha gets its own `syncStatus` (§2.5) and is never implied by an old witness.  Rollback or a failed target revokes by construction.  The core relay (§5) verifies identity against `members.json` locally and writes the verified witness to the core's local store; `accept` reads that store and **rejects a witness whose `device` is the target or the source**.

`accept` (cutover → hosting; earlier text: `accept-placement`) is the only point at which a dataset is "fully accepted"; until then status shows "succeeded, awaiting witness".  **Phase A never sets `sourceRemovable: true`.**

`removal-check` (backend, run on every plan save): in Phase A **every source removal is refused** (`PHASE_A_SOURCE_RETAINED`), stricter than the Phase B rule it keeps ready (a stored migrate receipt for that item and digest, placement `hosting`, and the source's own requirement, the dataset still served for publishers, covered).  It derives protection from the presence of the storage block, never from `protectedRoles`; it denies removing the last provider of a domain; a plan edit that merely omits the source item is checked the same way.  The decision is enforceable from journal and receipts alone.

**No `retire-source` in Phase A**: it is not in the actions or the chain, because the source host is the origin that sync and rollback need.  It returns in Phase B after a replacement origin exists (it would stop the source's *hosting*, not the publisher, and record the rollback identity first).  Standard `uninstall`/`disable` of the source's private compose must not be undone by the next publish: `apt-repo.sh publish` does not redeploy its private compose while an entry is `cutover|hosting`; it prints "published on the core; hosting replica lags until sync" (§1.3).

## 7. Hosting app: `isle-content-host`
- Catalog entry kind **`mesh-app`** with `category: "infrastructure"` (a string on `IsleCatalogEntry`, a purpose category, not a deployment kind).
  Installs through the existing release/manifest/verified-archive path.
- Release pins: `nginx:alpine` by image digest, the compose file, the archive sha, and the manifest sha that the plan's `hostingRelease` carries.
  Declares the bind parameter `ISLE_DATASET_PATH` (long-syntax bind, `create_host_path: false`, read-only at `/data`), nginx root `/data/current`,
  `autoindex on`, `restart: "no"`, health `GET /` through the existing resolver.
- nginx serves `/data/current` at `/` and `/data/control` at `/.well-known/isle-storage/` (`latest.json`, `manifest-<manifestSha>.json`, and `current`, a one-line file holding the contentSha, i.e. `/.well-known/isle-storage/current`): manifests are written before `current` moves, `latest.json` and `current` after it (§3, F6); nothing else of the dataset root (no `store/`, `staging/`, `binding.json`) is reachable.
- Service name `content`, domain from the plan.  Role `apt` for plan safety comes from the storage block (§6), not from the app name.
- On the core, `apt-repo.sh` keeps serving and publishing; Phase A has no `retire-source` and does not change that.

## 8. Negative controls (each must refuse with the source retained; econ tests independently)
**Disk and mount.** wrong disk (UUID mismatch) · unplugged mid-copy (`st_dev`/UUID changes: stop, retryable, no success) · unplugged while serving
(BindsTo stops the host) · insufficient capacity, including staging and store delta · root-filesystem fallback · relative dataset dir present on the
root fs · mount not a systemd unit · marker present but placement disagrees · hand-run `compose up` with the disk absent · each launch path (deploy,
reconcile, update, unit) runs `storage check`. **Content and paths.** tampered manifest · tampered file · manifest path escape (absolute, `..`,
backslash, empty segment, `?#%`) · symlink or special file at the receiver / symlink parent · interrupted sync keeps the previous generation serving ·
promotion only after generation re-inventory · missing trust (CA unreadable) · fetch with redirect refused · fetch by bare IP or with `-k` impossible.
**Plan and request.** stale plan (revision bumped) · inactive, superseded or deferred plan · changes to `app`, `originDevice`, `state`,
`hostingRelease` each change the digest · release manifest sha differs from the pin · `--dataset` differs from the item's dataset · wrong device (plan
names econ-core, runs on pol) · old-plan vector still validates byte for byte · retry reuses the normalized request · dataset lock taken before side
effects · migrate stage 6 refused while a device update runs (the copy, stages 3–5, is not) · rollback refused beside a copy · nested publication lock (publisher → `after-publication`) does
not deadlock. **Ownership and DNS.** migrate with no `authorized` entry (cert and every DNS path refused) · cert issued for the wrong device ·
`recovery-needed` issues no cert · member-to-member claim still 403 without an entry · host not ready in 60 s · stage 7 never publishes DNS · lost
response after the cutover CAS, rerun continues · DNS does not move: auto-revert, state back to `migrated` · old source not serving on revert:
`recovery-needed`, `rolledBack` false · `dns-reconcile` does not re-claim the domain for the core after cutover · unmanaged `127.0.0.1 apt.isle` line
is gone · core registry push omits the domain · cutover with a stale replica refused · rollback from `hosting` refused · generation mismatch
(`--generation` stale) refused. **Receipts and witness.** forged receipt (digest, device, poster) · receipt digest ≠ stored plan · witness from the
source · witness posted by the target · witness JSON naming a device other than the authenticated one · replayed or stale witness (older generation or
sha) · oversize witness download refused · accept with no witness · removal of the source before acceptance · plan edit that omits the
source item · removal of the last infrastructure provider · client-supplied `protectedRoles` ignored · uninstall/disable of the core's private compose
not undone by the next publish.
**v3 additions.** *Content:* real manifest → receive → verify (positive) · old `InRelease` → promote → old by-hash fetch still served · manifest → source publish → retry of the approved snapshot succeeds from `snapshot/objects/` · foreign hardlink in a generation refused. *Locks:* update refused during critical stages, allowed during the copy (message names the dataset). *DNS:* `dns-register.sh` refuses a managed name outside an intent · dns-reconcile does not write while `pending` · retry with a different action/digest does not resume a pending transition · router mDNS scan skips managed names · core app redeploy does not publish a managed name · cutover refused without the sync timer. *CA:* cert renewal of the core's own cert still works · `CORE_NAME` not on the allowlist refused · source not the current owner refused at authorize. *Receipts and witness:* identical receipt re-post idempotent · receipt for another plan/revision refused · witness outside the freshness window · witness epoch mismatch · no `retire-source` emitted.
**v3.2 additions.** *Objects (F1):* snapshot → fixture publisher rewrites bootstrap/CA/index in place → every prior object still hashes to its filename and the old approved snapshot still verifies and receives · equal-content files with distinct inodes accepted (nlink 1, no exception) · an existing `objects/<sha>` is never overwritten · a foreign extra link still refused. *Retention (F2):* four publications during a slow copy keep its source objects · `gc` holds the publication lock, pins live references and removes only the unpinned. *Recover (F3):* recover from a pending in `migrated`, `cutover` and `hosting` (reconcile-address); recover from `recovery-needed` by `--generation` (it never has a pending) · lost response right after the pending write · crash after DNS write before CAS (router re-read, CAS completed or reverted) · crash after CAS before receipt (receipt from stored state) · recover refuses a live operation · concurrent recover vs writer: one wins, no steal · resume with right action+digest+epoch but wrong operation id refused · `cutover-revert` and `rollback` intents resume and recover · a stale holder of an older epoch fails its CAS after any `pending` write · `dns-register.sh` re-reads the intent under `placement.lock` through the router mutation. *Stage 1:* placement view `migrated` with the same digest accepted (re-run), any other state or digest refused. *Cutover (F4):* pin transition under the intent before verification · verification by `--resolve` to the router-derived address, then a final ordinary-resolution check · auto-revert restores pin and router together · cutover refused while a reconcile-address `pending` exists. *Drift (F5):* target DHCP address changes, domain follows, owner and epoch unchanged · concurrent cutover/rollback refuses. *Pointer (F6):* `pointerAhead` reported when `latest.json` and `current` disagree, repaired by the next promote/sync · `/.well-known/isle-storage/current` matches `readlink(current)` · identical witness re-post idempotent vs a different witness for the same (dataset, G, device) rejected. *Locks:* authorize and witness hold no lease; `isle update plan` takes no lock.

## 9. Slices, ownership, code changes required by v2
| slice | what | files | owner | base |
|---|---|---|---|---|
| stm-1 | contract module + vector (BUILT) | `storage_contract.py`, `test-storage-contract.py`, vector | pol | afa8e00 |
| stm-2 | backend: kind `storage`, digest, `plannedActions[]` with dependsOn/status, passive `removal-check`, `category` string | `islemesh_planning.py`, `islemesh_api.py`, `islemesh_catalog.py` | pol; econ reviews | polari-framework a49d8537 |
| stm-2b | mutating doors after Codex's passive ruling: `POST /api/islemesh/storage/placement` (view, core identity only), `POST …/storage/receipt`, `POST …/storage/witness`, each `@_serialized` + `_persistence()`, digest verified against stored plan+revision; receipt-gated `removal-check`; `agent_address` on the devices door | `islemesh_api.py`, `islemesh_planning.py` | pol; econ reviews | after stm-2 |
| stm-3 | placement record + manifests + reserved `snapshot/` (BUILT) | `storage_placement.py`, `storage_snapshot.py`, `apt-repo.sh`, tests | pol | e95927a |
| stm-3b | objects: copy-vs-link by path class, never overwrite, `isle storage gc --repo` with the pin set, writer stage + fsync + rename in the publishers (F1, F2) | `storage_snapshot.py`, `storage_cli.py`, `apt-repo.sh`, `catalog_publish.py`, `runtime_publish.py` | pol | after stm-3 |
| stm-4 | **BUILT** (`storage_receive.py`, `storage_mount.py`, `check`/`status`; 165 checks); executor stage driver in stm-4b. Receive engine: store/generations/current, anchored fds, mount gate, `check`, `status`, `sync` + timer, migrate stages 1–8; generations by contentSha, `control/` dir (manifests before the swap, `latest.json` and `control/current` after), fetch by `snapshot/objects/<sha256>`, generation verifier; `storage_snapshot.py` manifests/latest (objects and retention: stm-3b) | new `storage_receive.py`, `storage_mount.py`, `storage_cli.py` | pol | after stm-3 |
| stm-4b | executor stage driver: migrate stages 1–8 wiring, fetch over HTTPS with `--resolve`, receipt writing (TO DO) | `storage_cli.py` | pol | after stm-4 |
| stm-5 | ownership: journaled `authorize`/`mark-migrated`/`cutover`/`rollback`/`recover`/`accept`/`reconcile-address` with `pending` intent; CA (`placement-claims.json`, allowlist), dns-reconcile (router query), hosts-reconcile, registry push; `dns-register.sh` fence (holds `placement.lock` across the router mutation), router managed-names list (`/etc/isle-managed-names`), app-deploy guard | `storage_placement.py`, `cert-authority.py`, `dns-reconcile.sh`, `hosts-reconcile.sh`, `push-to-polari.sh`, `dns-register.sh`, `configure-join-protocol.sh`, `app-deploy.sh` | pol (CA/dns files: tell the isle-core Claude first) | after stm-4 |
| stm-6 | `app-deploy.sh --staged`, `--bind`; `operations.py` allowlist, early dataset lock, journal name, busy probe | `app-deploy.sh`, `operations.py` (shared: econ ACK) | pol | after stm-5 |
| stm-7 | `isle-content-host` release (compose, manifest, publish) | polari-isle / catalog | pol | — |
| stm-8 | witness as a root journaled operation, `accept`, the relay (`isle storage relay`, §5) and the placement view export (file into the backend data dir); no `retire-source` in Phase A. Seams closed 2026-11-09: B1 view export, B2 relay, B3 timer enable + `syncTimerInstalled`, B4 `plan.digest` read, B5 witness binds G from the view | `storage_witness.py`, `storage_core.py` | pol | after stm-5 |
| stm-9 | App mapping of the three actions + chain; negative suite (§8) in disposable containers and a loop device with a real UUID | Isle App; isle-cli tests | econ | after stm-6 |

Order: stm-1,3 (built, amended) → stm-2 ∥ stm-4 → stm-5 → stm-6 → stm-7 → stm-8 → stm-9 → candidate cut (econ) → install on pol (member window) →
source snapshot published → authorize → migrate → cutover → witness from econ → accept → **Phase B (REQUIRED follow-on, not optional: it is what saves core storage; includes `retire-source` and the replacement origin)**.  No deployment claim until a
real release and three-device tests; the core stays live unchanged.

**Code changes required by v2 to what is built** (after 97cc8a8)
- Done in 97cc8a8: `--dataset` in the migrate form; `cutover`/`rollback` (`--dataset`, `--generation`) and `sync` forms; `lock_name`/`journal_name`
  from the tokens; full-tuple digest with the vector regenerated (318003eb…); three actions with `dependsOn` and the
  `isle.storage.<verb>:<dataset>@<device>:<digest>` ids; eight migrate stage labels + `CUTOVER_STAGES`; receipt fields `actionId`,
  `filesystemUuidObserved`, `mountSourceObserved`, `digestRecomputed`, `generation`; placement entry `device`, five states, `generation`, `history[]`,
  flock + `(state, generation)` CAS in `transition()`, `mark-migrated`, `accept --generation` (formerly `accept-placement`).
- Still open in `storage_contract.py`: `DOMAIN` as the strict DNS-hostname-under-`.isle` rule; `capacityBytes ≥ totalBytes × 1.05`; `STATUSES` gains
  `blocked`; `RECEIPT_KINDS` gains witness, accept, reconcile-address; `receipt_skeleton` gains `hostingRelease`, `stagedDeploy`, `phase` and drops
  `cutover`/`neighbourCheck` from the migrate receipt; `planned_action` gains `placementState` and `chain[]`; `binding_record` gains
  `currentGeneration`, schema 2.
- Still open in `storage_placement.py`: the `cutover`, `rollback` and `recover` verbs (they call `transition()`; stm-5), `accept` requiring
  a stored witness and recording `acceptedWitness` (stm-8), `authorize` re-run in `authorized` for a new revision, the `pending` field and
  `placement-claims.json`, all core verbs as journaled operations.
- IN stm-4 (v3): generations keyed by contentSha, `control/` manifests written before the swap and `latest.json` + `control/current` after it, the generation verifier and the object fetch.
- `apt-repo.sh`: unmanaged hosts line moves into hosts-reconcile's block; `publish` does not resurrect the private compose in `cutover|hosting`.
- `storage_snapshot.py`: reserved top-level `snapshot/` stays narrow; gains contentSha, `manifests/`, `latest.json` (stm-4), and `objects/` copy-vs-link, never-overwrite and pin-set `gc` instead of a count (stm-3b).

## 10. Review traceability (review point → section)
| point | summary | where |
|---|---|---|
| econ a1–a3 | fixed token shape with regex values; dedicated lock + device-update lease matrix (no probe); journal `storage-<dataset>`, identity branch intact | §2.1 |
| econ b1 | additive `kind storage`, old vector unchanged | §1, §8 |
| econ b2 | source stays until the item's receipt; removal separate, from journal alone | §1, §1.2, §6 |
| econ pull 1–4 | manifest bound to plan and sha-checked first; per-object sha, partial, fsync, delete on mismatch; reserved `snapshot/` out of inventory and lock view; resumable partials, no claim until verified, pull never retires source | §1.3, §2.2, §6 |
| econ F1 | authorize before migrate; cert and DNS check it | §5, §2.2 stage 1/7 |
| econ F2 | `--dataset` in request; lock before child; shared lock; interrupted first | §2.1 |
| econ base/serialize | stm-2 on a49d8537; `@_serialized`, `_persistence()`; digest verified before storing | §9 stm-2, §2.2 |
| econ §1 note | publication lock id and time in manifest | §1.3 |
| econ §1.2 note | dependsOn; migrate/cutover split; `execute: null` | §1.2 |
| econ §2.2 notes | sequential stage 4, `st_dev` per file, unsafe paths; SNI via `--resolve` | §2.2, §3 |
| econ §2.3 note | actionId, uuid observed, mount source, digestRecomputed | §2.6 |
| econ §4 note | AssertPathExists / ExecStartPre | §4 |
| econ §6 note | sync from source by address with SNI | §2.5, §2.2 |
| econ §8 note | path escape, receiver symlink, forged receipt, no authorize, target witness | §8 |
| econ C needs | stable actionId; chain; `status` verb | §1.2, §2.7 |
| Codex 1 | bootstrap, narrow reserved namespace | §1.3 |
| Codex 2 | trusted origin, validation, authenticated writes | §2.2, §5 |
| Codex 3 | full digest, active-plan check, recheck | §1, §1.1, §2.3 |
| Codex 4 | staged deploy, CAS ownership, rollback/recovery-needed | §2.2, §2.3, §5, R7, R12 |
| Codex 5 | anchored fds, BindsTo, all launch paths | §3, §4 |
| Codex 6 | generations, locking, capacity, nested lock | §2.5, §3, §8 |
| Codex 7 | honest phases, Phase B named, no resurrection | header, §6, §11 |
| Codex 8 | witness binding, freshness, removal safety | §6 |
| Codex 9 | category not kind, pinned identities, real verbs, operations scope | §7, §2.1, §9 |
| Codex v2-1 | two hashes; generation verifier; real manifest → receive → verify tests | §1.3, §2.2 (3–5), §3, §8 |
| Codex v2-2 | immutable source objects, retention, control endpoint, by-hash across promotion | §1.3, §3, §7, §8 |
| Codex v2-3 | every DNS writer fenced, router query, claims compat, allowlist, one instance | §5, §2.3, §8 |
| Codex v2-4 | shared lease, epoch fencing, durable intent, journaled mutators | §2.1, §2.3, §2.4a, §5 |
| Codex v2-5 | root witness, receipt binding, freshness, epoch rule, no backend verifier | §2.6, §6 |
| Codex v2-6 | no retire-source in Phase A, sync timer before cutover, publisher words, Phase B required | §1.2, §2.5, §6, §9, §11 |
| econ PIN 1 / PIN 2 | read path by placement view door; poster identity recorded unverified, verified by core verbs | §5, §2.6 |
| econ v3 1–6 | lock matrix and refusal wording; stage-1 re-run; kind strings and common fields; control endpoint; stm-2b; (state, pending) table, every write bumps generation | §2.1, §2.2, §1.2, §3, §7, §9, §5 |
| Codex v3-1 | hardlinks do not make mutable files immutable: link immutable paths, copy the rest, stage+rename writers, precise exception | §1.3, §8, §9 |
| Codex v3-2 | retention by live references: pin set, `gc` under the publication lock | §1.3, §2.1, §5, §8, §9 |
| Codex v3-3 | recover from any pending with liveness/ownership, two crash windows, operation id, router mutation fenced | §2.4a, §5, §8 |
| Codex v3-4 | cutover pin ordering: pin transition under the intent before verification, `--resolve` then ordinary check | §2.3, §8 |
| Codex v3-5 | steady-state address drift under the same authority | §2.1, §2.5a, §5, §8, §9 |
| Codex v3-6 | one committed pointer; witness idempotent re-post vs conflicting witness | §2.7, §3, §6, §7, §8 |

| consistency review B1–B5, M1–M10 | B1 view export §5; B2 relay §5/§6/§9; B3 timer §2.2/§2.3; B4 `plan.digest` §5; B5 `epoch` informational §3/§6; M2/M3 `sourceDevice`, `addresses` §1/§5/§6; M4 accept rejects target/source witness §6; M5 stage 5 verify, stage 6 promote §2.2; M6 publisher words §1.3/§4; M7 revert epoch G+3 (3-write revert, §5 arithmetic unchanged otherwise); M10 removal refused in Phase A §6 | §1, §2.2, §2.3, §3, §4, §5, §6, §9 |

## 11. Open questions (1–4 settled with econ 2026-11-09; 5 open)
1. Settled: `authorize` is a planned action on the core (`isle.storage.authorize`, first in the chain, §1.2), with its own approval there,
   per the user's bulk-planned-actions ruling; the by-hand verb is the same operation.
2. Settled: `sync` runs from a root timer on the target that compares the source origin's `latest.json` sha with `current` and runs the sync
   operation only on change, plus the App action; no relay-triggered cross-device push.
3. Checked 2026-11-09: the devices door (`GET /api/islemesh/devices`) returns `name, agent_mode, hosts_router, last_seen, …` and **no address**.
   Decision: stm-2b adds `agent_address` to the device row from the device self-report (the agent's own interface address, already in its facts),
   exposed on the devices door; until then the core CLI relay supplies the source address from the router leases by device hostname (audit §1).
   Either way the address is only a connection hint: the TLS name verified is the domain, so a wrong address fails closed.
4. Settled (v3.4): the 120 s push path on the core runs the relay (`isle storage relay`, §5), which stores the pulled receipts and witnesses locally and then runs the journaled `mark-migrated` (with the CA members check of §5 PIN 2); the by-hand verb is the fallback.
5. Phase B design (required): which files remain on the core (signed indices only?) and how the hosting device commits bulk publications with a verified receipt.

