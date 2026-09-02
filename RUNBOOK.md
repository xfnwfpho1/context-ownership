# Runbook — operating the deployment

Procedures only; for the machine's anatomy see [ARCHITECTURE.md](ARCHITECTURE.md).
Everything here has been executed for real at least once; where a step exists
because something broke, the incident is named. Conventions: `CTXOWN` =
`/home/z/my-project/context-ownership/ctxown.py`; `HP` =
`/home/z/my-project/adopt-scan/hermes-proj` (flagship fleet); kit =
`/home/z/my-project/opencode-zai-agent-kit`. Long-running steps are always
run as **double-fork daemons** with heartbeat files under `/home/sync/` —
the Bash tool caps at ~590 s and kills whole process trees, so foreground
runs of build/eval/smoke are an error, not a style choice (found live twice:
a smoke review truncated at 590 s left an un-committed corpus).

## 1. Cold-start recovery (after a container recycle)

The container loses local state on recycle; durable state lives in git
remotes and `/home/sync`. Recovery ladder, verified end-to-end on
2026-09-02 when the container restored from an *older* snapshot:

1. **Harness**: `cd kit && bash setup.sh` (re-installs OpenCode + MCP, writes
   config; `.env` with the OpenRouter key is tracked in git on purpose).
   Then `oc-tool doctor` — model ping, MCP, config; fix what it flags before
   anything else.
2. **Repos**: `git pull --ff-only` in context-ownership, opencode-zai-agent-kit,
   opencode-harness. If a repo's local state diverged (container restored to
   an older tar), `git fetch && git reset --hard origin/<branch>` — remote is
   truth.
3. **Fleet corpus**: hermes corpus is its own repo —
   `git -C HP/corpus remote -v` should point at `hermes-ctxown-corpus`; if
   the working tree is a stub, re-clone from the remote.
4. **Bundles**: if `bundles/` was lost, restore from `/home/sync/milestone-*`
   tars (they carry the final LLM bundles), then `python3 $CTXOWN --project
   HP rebuild --no-llm` and verify `status` = 64/64 valid, `fleet_coherent:
   true`. Refresh pins after restore: an empty snapshot commit does not
   invalidate content (stale-but-clean is by design), but clean pins are the
   right hand-off state.
5. **Serving**: `python3 $CTXOWN --project HP serve start` (probe-driven);
   confirm with `serve status` + one single-owner ask.

Known cold-start traps: file-mode drift on kit scripts after restore
(`chmod +x` what setup.sh wrote); `OC_STATE_DIR` defaulting to `/tmp`
(wiped) — set it to a durable path before long detached tasks.

## 2. Server lifecycle

- One OpenCode serve server per project: hermes on 4200, cc-gha on 4400+.
  Project-scoped locks make parallel deployments on disjoint ports safe.
- Start/verify: `serve start` probes the provider chain (OpenRouter paid
  first, keyless zai last) and brings up the server; treat an unverified
  start as a failure.
- **Three poison signatures** (all met live; each has a fix):
  1. *Empty-200s after agent hot-reload* — restart (upstream
     anomalyco/opencode#46245).
  2. *Empty-200s keyless* — no live key; rotate keys in `.env`.
  3. *HTTP 500 on every model after a hard-killed review* — the shared
     server is poisoned while the provider is fine. Fix = **verified
     restart**: kill by port, wait for the port to free, fresh start, then a
     single-ask probe before trusting it.
- Never hard-kill a review in flight; if you must, expect signature 3.

## 3. Deploying a corpus (glossary → fleet)

1. Assemble `corpus/docs/` (ownable docs only; append-only logs stay out) +
   author `corpus/glossary.md` (terms, value owners, precedence — mine them
   from the corpus itself; ~1 evening for 50-70 terms).
2. `init` → `check` → fix what `check` flags (oversized → structural split;
   micro → merge; empty docs are flagged but stay owned — they are in the
   tree, cc-gha's `cc-self-review.md` is the live example).
3. `build --workers 2` as a daemon; 64 owners ≈ 2 h. Budget note: at flash
   pricing the whole 64-owner build is ~$0.09 of tokens — the cost is wall
   time, not dollars.
4. `status` must read 64/64 valid before serving.

## 4. Daily operations

- Single-owner ask: `ask --owner <id> "<question>"` (seconds).
- Route a task: `task "<description>"` — resolves owners + LCA manager.
- Review a change: `review --file docs/X.md --base <sha> --concurrency N`.
  12-way is proven clean on OpenRouter; higher concurrency risks the 429
  window. A 63-owner board ≈ 14 min at 12-way on a good night, ~9.5 min at
  6-way on a fast one.
- Telemetry: `telemetry` over oc-tool `events.jsonl` — grep-rate inside vs
  outside owned paths; the rebuild control loop's input.

## 5. Running an eval

1. Author `eval/planted.json` as a **bare JSON list** (a `{"plants": …}`
   wrapper crashes the loader — found live). Every plant: unique `id`,
   `file`, exact `find` (verify it exists verbatim in the corpus), `replace`,
   `category`. For non-bridged plants, run the authoring script's
   uniqueness/echo audits — grep(old-value) must NOT cross to the authority
   document.
2. Launch via the daemon launcher
   (`scripts/launch_nonbridged_eval.py` pattern): double-fork, cost snapshots
   (OpenRouter `/api/v1/key` before/after), heartbeat + `result.json` +
   `stderr.log` + `DONE` under `/home/sync/<run-dir>/`.
3. Monitor: corpus `git log` (plant/restore commits), `eval/plants/*.json`
   (one per completed plant — full per-arm evidence), the run dir's
   `eval.log`.
4. **On rc=5 (honest abort)**: nothing is broken. The corpus self-restored
   (verify `git -C HP/corpus status` clean and HEAD at the run's
   `eval-snapshot` commit); persisted per-plant evidence is already safe.
   Diagnose (usually provider churn — ping the model directly), then resume
   with `--plants <remaining,ids>` relaunching the daemon. Cost of the
   aborted fraction is already measured by the snapshots.
5. After completion: harvest `result.json` (per-arm detections, FP count,
   verdict), write results into ADOPTION-REPORT, commit + push, and drop a
   milestone tar under `/home/sync/`.

## 6. Failure-mode playbook (symptom → first move)

| Symptom | First move |
|---|---|
| `oc-tool doctor` model ping dead | Rotate the key in `.env` (keys expire); then check the chain's free fallbacks |
| Eval aborts rc=5 "transport failure / model not found" | Transient provider churn — ping the model; if alive, resume with `--plants`; if dead, let `serve probe` select the successor and relaunch |
| HTTP 500 on every model | Verified restart (§2.3) |
| Review wall-time ballooning | Check provider latency that night before touching concurrency |
| `build` retry churn on large docs | Timeout is a corpus-scale parameter (420 s since R15); never shrink it back |
| Corpus dirty at eval start | Commit or stash; only `--clean-start` wipes killed-run residue |
| Bash timeout killed a daemon-less run | Re-run as daemon; restore corpus via `git reset --hard <snapshot>` (never HEAD — it may contain plant commits) |

## 7. Persistence & backup discipline

- **Git is disk**: every meaningful artifact lives in a repo and gets pushed
  — the layer, the kit, the spec repo, and the hermes corpus itself
  (`hermes-ctxown-corpus`). `.env` (with keys) is tracked deliberately.
- **Milestones**: `/home/sync/milestone-*` tars at every phase boundary
  (final LLM bundles, eval evidence, pins). `/home/sync` is ossfs and
  survives container recycles; verify a tar after writing it.
- **Run artifacts**: eval runs keep heartbeat/result/evidence under
  `/home/sync/<run-dir>/` precisely because they outlive any Bash call.
- Worklog: append-only, one shared file — the multi-session memory that the
  container cannot provide.

## 8. Cost tracking

- The launcher snapshots OpenRouter `/api/v1/key` usage before/after each
  run; the delta is the run's measured dollar cost. Reference points: full
  64-owner build ≈ $0.09; pilot eval ≈ $1.30; hermes 10-plant eval ≈ $1.50;
  aborted fraction of the non-bridged run (1 full plant + 1 board) = $0.033.
- Free routes (zai proxy, `:free` models) cost dollars but rate-limit hard —
  use for smoke, not for boards.
- Per-request token telemetry is not persisted (container recycles wipe
  session storage); the before/after delta is the accepted accounting
  granularity.
