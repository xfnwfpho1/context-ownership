---
name: oc-agent-fleet
description: Deploy and operate an OC (OpenCode) sub-agent serving layer with a context-ownership memory fleet — one agent-owner per document, compiled durable bundles, standing review boards, and planted-contradiction evals. Use this skill whenever the user mentions OpenCode or OC agents, the opencode-zai-agent-kit, context-ownership or ctxown, owner fleets / bundles / registry, serving one agent per document, reviewing document changes with an agent board, running planted-contradiction evals, recovering the OC harness after a container recycle, bringing the fleet back up, or deploying the Aurora demo corpus — even when they only say "the fleet is down" or "set up agents on this corpus".
license: MIT
---

# OC Agent Fleet — deployment & operations skill

Two repos make one system. The **kit** (`/home/z/my-project/opencode-zai-agent-kit`,
github.com/xfnwfpho1/opencode-zai-agent-kit) is the serving substrate:
OpenCode wrapped as a CLI-invocable sub-agent tool (`oc-tool`) with
retries, a model-fallback chain, MCP web/browser tools, and a keyless
GLM proxy. The **layer** (`/home/z/my-project/context-ownership`,
github.com/xfnwfpho1/context-ownership) is the controller (`ctxown.py`,
stdlib-only single file): it partitions a doc corpus into one owner per
document, compiles each a durable **bundle** (verbatim doc + periphery +
a derived layer), and serves owners from those bundles. Comprehension
cost moves from *per query* to *per change*.

Why OC (OpenCode) at all: the main agent's own sub-agents are the same
model lineage. OpenCode gives a clean external sub-agent — different
model, vision, web/browser tools — callable as a terminal tool with
clean JSON out. The controller (not OpenCode) owns identity,
invalidation, and routing; OpenCode is just the serving substrate.

The whole stack runs **keyless at $0** in this sandbox if no OpenRouter
key is available. A paid key only buys speed.

## The 60-second orientation

```bash
oc-tool doctor    # health: model pings (affordability-mirroring), MCP, keyless proxy
CTXOWN=/home/z/my-project/context-ownership/ctxown.py
python3 $CTXOWN --project <proj> status      # fleet coherence
python3 $CTXOWN --project <proj> serve status
```

If `doctor` and `status` are green, everything below is optional.

**Reading doctor honestly:** top-level `ok: true` means "a usable
serving route exists" — NOT "the default paid model is alive". On a
de-keyed box the critical `model ping` line reads
"OpenRouter DEAD (...) — keyless zai/glm-4-plus proxy healthy" and
that is the system working as designed: the route exists, the money
doesn't.

**Ports (a recorded project property):** `COV_BASE_PORT` assigns a
project's port range at **init** time (default 4200; the range is
base..base+owner_count, recorded in the registry — only the base
listens in shared mode). Every LATER command (build/serve/ask/eval/
status/stop) resolves the project's server from the RECORDED range —
no env exports needed after init, and a project deployed on 4600
comes back on 4600 from any shell. Pick a range disjoint from what's
already used on the box (check listeners AND this doc's ranges — the
flagship fleet RESERVES 4200–4263 even when not listening):

```bash
ss -ltn | awk '{print $4}' | grep -oE '[0-9]+$' | sort -n | uniq   # ports in use
export COV_BASE_PORT=4700   # then run `init` — the range is recorded
```

## Provider routes (read this before touching keys)

`serve start` PROBES a preference-ordered chain and pins the selection
for the run — probe affordability mirrors the real ask's max_tokens
(32000), so a pair that probes OK cannot 402 mid-job:

1. **Paid OpenRouter** — `OPENROUTER_API_KEY` (.env in the kit),
   model `openrouter/z-ai/glm-5.3-flash` ($0.075/M in, $0.25/M out).
   Optional. Keys die without notice (401/402) — the chain notices.
2. **Free OpenRouter models** — `minimax-m3:free`, `dots-3-note-preview:free`.
   Work with a valid key even at $0 balance, but have their own **daily
   request caps (RPD)**: when a cap exhausts, the model looks dead and the
   chain demotes to the next member — an eval may honest-abort (rc=5) on
   the way; resume per the runbook.
3. **Keyless zai proxy** — the sandbox's native GLM access, no key,
   `glm-4-plus` on `127.0.0.1:4570`. Starts with
   `node <kit>/scripts/zai_proxy.mjs` (self-daemonizing, idempotent).
   Rate-limited (trickle ~0.05 req/s sustained) — fine for builds and
   small fleets; slow for big boards.

Never screen or wire third-party API keys of unknown provenance — that
is someone else's credential pool. A key is only ever a user-supplied,
user-owned value in `.env`.

## Cold start (after a container recycle)

The sandbox recycles and loses locally-installed state (opencode binary,
MCP deps, servers). Git remotes and `/home/z/my-project` survive.
Recovery is minutes, verified end-to-end many times (measured live by a
blind test: 3.5 minutes total). Four numbered steps, in order —
doctor's exit-0 criterion is only reachable AFTER the proxy step:

```bash
# 1. Restore the kit (idempotent installer, ~1 min — foreground is fine)
cd /home/z/my-project/opencode-zai-agent-kit && bash setup.sh

# 2. Start the keyless proxy EXPLICITLY (doctor only CHECKS the proxy —
#    it never starts anything; serve/chain paths auto-start it, doctor
#    does not. One JSON line, self-daemonizing, idempotent):
node /home/z/my-project/opencode-zai-agent-kit/scripts/zai_proxy.mjs

# 3. Health check — must exit 0. On a de-keyed box expect the honest
#    green state: "OpenRouter DEAD (...) — keyless zai/glm-4-plus proxy
#    healthy" (top-level ok:true = a usable route exists).
oc-tool doctor

# 4. Repos: remote = truth
git -C /home/z/my-project/context-ownership pull --ff-only
git -C /home/z/my-project/opencode-zai-agent-kit pull --ff-only
```

If a repo restored from an older snapshot: `git fetch && git reset
--hard origin/main`. Known restore artifact: file-mode drift
(0644→0755, zero content changes) — silence it once per repo with
`git config core.fileMode false`.

Bringing an EXISTING project back up afterwards: its registry records
the port range (a project deployed on 4600 comes back on 4600 — no env
exports needed, any shell); stale `.ctxown-state.json` surviving the
recycle is harmless — `serve start` re-probes the provider chain and
rewrites it. If bundles were lost, the RUNBOOK §1 ladder (milestone
tars + `rebuild --no-llm`) restores them; a coherent fleet needs NO
rebuild — verify with `status` before touching anything.

Naming note: doctor's "serve server" check refers to the KIT's own warm
oc-tool server (port 4096) — a separate thing from any ctxown project
server (4200+ ranges); ignore its not-running state unless you use
`oc-tool ask`.

Fresh clone (not this sandbox's paths): the repos are private — set
`OPENCODE_KIT_PAT` (env or kit `.env`) before `recover.sh`/clone.

## Deploying a corpus (the ladder)

A *project* is any directory containing `corpus/` with ownable docs
`corpus/docs/*.md` plus a shared `corpus/glossary.md` (term | meaning |
value owner table — the glossary's meanings and listed value owners are
authoritative), inside a **git repo**. Durable state (registry, bundles,
agent files) is git-versioned by design — commit it.

Learn on the toy first: `examples/demo/` in the layer repo is a 7-owner
"Aurora Station" corpus with a walkthrough README and 2 pre-authored
plants. A real deployment (README quickstart in the layer repo) is the
same ladder at scale.

```bash
CTXOWN=/home/z/my-project/context-ownership/ctxown.py
P=/home/z/my-project/myproject          # has corpus/docs/*.md + glossary.md
cd $P && git init -b main && git add -A && git commit -m "corpus v1"

# Assign the project's port range AT INIT (recorded in the registry;
# later commands need no env — they read the recorded range):
export COV_BASE_PORT=4700               # a FREE range (see "Ports" above)
python3 $CTXOWN --project $P init       # owners from the tree (root = manager)
git add -A && git commit -m registry    # registry.json must be tracked
python3 $CTXOWN --project $P check      # oversized/micro/unownable flags — fix now
python3 $CTXOWN --project $P build      # LLM per owner — DETACHED, see below
git add -A && git commit -m "fleet build"
python3 $CTXOWN --project $P status     # fleet_coherent: true before serving
python3 $CTXOWN --project $P serve start        # probe-driven provider
python3 $CTXOWN --project $P ask --owner <id> "question?"   # seconds
```

Corpus discipline: `check` flags oversized (>32K tokens → split
structurally) and micro (<300 tokens → merge) docs; append-only logs
(worklogs, changelogs) stay OUT of the owned tree. Glossary authoring
is real work: mine terms from the corpus, assign a value owner per
term, declare precedence.

## Long steps MUST run detached

The Bash tool kills whole process trees at ~590 s. Plain `&`,
`nohup &`, `setsid & disown` all die with the call — only the
double-fork (reparent to PID 1) survives. The layer ships a generic
runner:

```bash
python3 /home/z/my-project/context-ownership/scripts/detach.py \
  --log $P/build.log --cwd $P \
  -- python3 $CTXOWN --project $P build
tail $P/build.log              # poll; $P/build.log.rc appears on completion
```

What the log shows: build and eval stream progress on stderr
(`[build 3/7] POWER ...` per owner; `[eval] plant <id> — sharded arm:
DETECTED/miss` per plant and arm) — silence means the process died
before starting, not that it is thinking. The completion signal is the
`<log>.rc` sidecar file. `build` uses the same probe-driven model chain
as serving, and its final JSON reports the recorded provider selection.

`serve status` note: in the default **shared** mode there is ONE server
per project (on the project's base port) that all owners answer
through — per-owner rows in the status output are only meaningful with
`--per-owner-ports`; seeing them as `healthy: false` in shared mode is
presentation, not breakage. Check the shared server's own row.

Runs that proved this the hard way: a 590 s-killed smoke review left an
un-committed corpus; a first review-board run lost all output at a
toolcall boundary.

## Daily operations

- **Owners are named after their docs** (`owners` lists them): MISSION,
  POWER, ... — ask the owner whose doc owns the answer; `task "..."`
  routes automatically when you don't know.
- **Ask one owner**: `ask --owner <id> "<question>"` — per-inquiry
  session; each answer quotes document + passage (no quote, no finding).
- **Route a task**: `task "<description>"` — resolves target owners and
  the LCA manager, then executes.
- **Review a change**: `review --file docs/X.md --base <sha>` — the
  standing board broadcasts the diff to ALL owners (coverage is never
  sampled), dedups + ranks findings. `--concurrency 3` default; 12-way
  proven on OpenRouter, not on the keyless trickle.
- **Write path**: `write --owner <id> --instruction "..."` —
  owner-authorised edit ladder: decide → isolated executor → enforced
  confinement → self-invalidation → rebuild. Stale-dirty owners REFUSE
  to answer; that refusal is the model working.
- **Telemetry**: `telemetry` — grep-rate inside vs outside owned paths
  over oc-tool events.

Scale facts (measured): 63 concurrent asks = 25.8 s; a 63-owner board
= ~14 min at 12-way. 1,000 owners do NOT mean 1,000 processes — one
serve server per project, concurrency is a knob (see OPERATIONS.md).

## The planted-contradiction eval (§8.6)

The falsifiable test: plant contradictions into the corpus, then ask
three same-model systems to find them — the sharded owner fleet vs
cold-grep vs chunk-RAG. What it proved at real scale (64 owners, 339K
tokens): compiled-memory plants (values surviving ONLY in owners'
preserved derived layers) are caught by the fleet 3/3 and by both
baselines 0/3; bridged plants tie. Full story: ADOPTION-REPORT §6c.

Ground truth: `$P/eval/planted.json` — a **bare JSON list** (a
{"plants": [...]} wrapper crashes the loader — found live). Fields per
plant: `id` (unique), `file` (corpus-relative), `find` (must exist
VERBATIM in the corpus — verify with grep before launching),
`replace`, `category` (`bridged` | `semantic-conversion` |
`compiled-memory`), `note`, `expect`.

- `bridged`: the planted value is echoed verbatim elsewhere → grep
  crosses → all arms catch (positive control).
- `semantic-conversion`: planted restatement disagrees with the
  authority only across a unit conversion (runs/day vs hours) → grep on
  the planted phrasing cannot cross.
- `compiled-memory`: the authority value appears nowhere greppable in
  the live corpus — only in the owner's compiled derived layer.

Launch detached (it commits plants, rebuilds, runs boards, restores):

```bash
python3 /home/z/my-project/context-ownership/scripts/detach.py \
  --log $P/eval.log --cwd $P -- \
  python3 $CTXOWN --project $P eval --arms all
```

`--arms all` runs the full §8.6 three-system comparison (sharded,
coldgrep, rag); the default is `sharded,coldgrep`.

On **rc=5 (honest abort)**: nothing is broken. The corpus self-restored
(verify `git -C $P status` clean; the run's `eval-snapshot` commit is
the restore point — never reset to HEAD, it may contain plant commits).
Diagnose the provider (usually churn), resume with
`eval --plants <remaining,ids>`. Measured abort cost so far: $0.038
across two aborts; the decisive finish on free-tier: $0.000.

Per-plant evidence (`eval/plants/<id>.json` — the full per-owner review
JSON plus baseline answers) survives the restore **by design** and is
committed on top of the restored snapshot ("eval evidence (R15f)"
commit). The result line is the last JSON object in the run log.

## Gotchas index (each one found live)

| Symptom / trap | Fix |
|---|---|
| `opencode run` hangs with ZERO output | stdin is interactive-capable — oc-tool passes stdin=DEVNULL already; if calling opencode yourself add `< /dev/null` |
| Long run dies at ~590 s | run detached via `scripts/detach.py` (double-fork) |
| Build refuses: "corpus working tree is dirty" | correct safety behavior — commit registry/bundles; keep run logs OUT (template .gitignore: `*.log`, `*.pid`, `*.rc`) |
| Everything 401/402 at doctor | key dead or account out of credit — doctor says so and points at the keyless route; `serve probe` re-selects automatically |
| HTTP 500 on every model after a hard-killed review | server poisoned — verified restart: kill by port, wait for the port to free, fresh `serve start`, one-ask probe |
| Empty-200 answers after an agent hot-reload | restart the server (upstream anomalyco/opencode#46245) |
| Detached task ids lost after reboot | OC_STATE_DIR defaulted to /tmp (wiped) — set it to a durable path BEFORE launching |
| Corpus "dirty" with 65 modified files, 0 content changes | file-mode drift from container restore — `git config core.fileMode false` |
| eval crashes loading ground truth | planted.json must be a bare list; `find` must exist verbatim |
| Board wall-time ballooning | provider latency that night — check before touching concurrency |

## Where the deep docs live

| Need | Read |
|---|---|
| Kit reference: every oc-tool subcommand, MCP tools, patterns | `<kit>/SKILL.md` (comprehensive, ~90KB — consult by section) |
| Layer quickstart + adoption ladder L1–L5 | `<layer>/README.md` |
| Operating procedures (this skill's source) | `<layer>/RUNBOOK.md` |
| E2E system anatomy (components, flows, eval mechanics) | `<layer>/ARCHITECTURE.md` — also the user-facing PDF: `/home/z/my-project/download/oc-harness-context-ownership-e2e.pdf` |
| Scale strategy (sleep/live, 1k docs, doc cutting) | `<layer>/OPERATIONS.md` |
| Spec contract, every section's status + evidence | `<layer>/SPEC-COMPLIANCE.md` |
| What the experiments proved, honestly | `<layer>/ADOPTION-REPORT.md` (§6c = the decisive result) |
| Deterministic suite (78 checks) | `python3 <layer>/tests/test_ctxown.py` |
| Kit integration suite (35 asserts + stress) | `bash <kit>/tests/test_oc_tool.sh`, `bash <kit>/tests/test_stress.sh` |

Reference deployments: the flagship fleet is a 64-owner / 339K-token
corpus at `/home/z/my-project/adopt-scan/hermes-proj` (serve range
4200–4263 — keep new projects off it). The pilot (35 owners) lives in
the harness repo under `cov/`.
