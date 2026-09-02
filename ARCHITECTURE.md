# Architecture — the E2E system, as it actually runs

This is the canonical end-to-end description of the OC-harness + context-ownership
deployment: what runs where, which processes exist, how a corpus goes from a
messy repo to a serving fleet, and how the falsifiable eval exercises it. It is
written for an operator or reviewer who wants the whole machine in one file.
Companion docs: [RUNBOOK.md](RUNBOOK.md) (procedures), [OPERATIONS.md](OPERATIONS.md)
(scale model), [SPEC-COMPLIANCE.md](SPEC-COMPLIANCE.md) (spec coverage),
[ADOPTION-REPORT.md](ADOPTION-REPORT.md) (deployment history + eval results).

**Where it runs: NOT on GitHub Actions.** All execution happens inside one
local sandboxed container (2 vCPU / 4 GiB RAM). GitHub's only roles are (a)
remote persistence for the repos — "git is our disk", because local disk is
recycled — and (b) the *subject matter* of the second corpus
(`cc-gha-exploration`, a research project about GHA). No workflow, runner, or
GHA job executes any part of this system.

**How many instances: 64 owners, not 64 processes.** The "64" is the hermes
fleet's owner count — 64 durable bundles on disk (63 leaf owners + the root
manager). An owner is a registry row + a bundle + a policy, never a running
process. At steady state the whole deployment is: 1 OpenCode serve server per
project (port 4200 for hermes, 4400+ for cc-gha), 1 keyless zai proxy (port
4570), 2 MCP servers, and short-lived controller invocations. Concurrency is
an execution knob (`review --concurrency`), not a fleet size: measured points
are 12-way review (856 s for a full 63-owner board), a one-off 63-way blast
(63/63 in 25.8 s), and eval boards at 6-way.

## 1. Component map

```
┌────────────────────────────────────────────────────────────────────┐
│ Sandbox container (2 vCPU / 4 GiB, no durable disk)                │
│                                                                    │
│  /home/z/my-project/                                               │
│  ├── opencode-zai-agent-kit/   OC harness (oc-tool.py v0.5.7,      │
│  │                             setup.sh, MCP servers, tests)       │
│  ├── context-ownership/        THE LAYER (ctxown.py, stdlib only,  │
│  │                             3.1K lines, 13 subcommands)         │
│  ├── adopt-scan/hermes-proj/   Flagship fleet: corpus/ (own git),  │
│  │                             bundles/, registry.json, eval/      │
│  ├── adopt-scan/cc-gha-*/      Second fleet (staged)               │
│  └── opencode-harness/         Spec + pilot deployment (cov/)      │
│                                                                    │
│  /home/sync/                   ossfs mount — survives recycles     │
│                                (milestone-*, hermes-eval2/, …)     │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ HTTPS egress
                               ▼
                 OpenRouter (paid key, glm-5.3-flash
                 → minimax fallback chain)   +   z.ai sandbox SDK
                 (keyless glm-4-plus via local proxy, last resort)
                               │
                               ▼
                 GitHub remotes (PAT): context-ownership,
                 opencode-zai-agent-kit, opencode-harness,
                 hermes-ctxown-corpus  — persistence, not compute
```

### Layer 0 — the OC harness (`opencode-zai-agent-kit`)

`oc-tool.py` is a terminal-invocable wrapper around OpenCode (v1.18+) that the
controller shells out to. What it contributes beyond raw OpenCode:

- **Structured results**: every call returns one-line JSON `{ok, text, model,
  cost, …}` parsed from the JSONL event stream — the controller never scrapes
  prose.
- **Survival**: `run`/`parallel`/`serve` all double-fork, so background work
  survives the Bash tool's process-tree teardown; long tasks use `--detach`
  + `wait`.
- **Resilience**: retry (2x) + model fallback chain + key swap, driven by a
  `doctor` health check. A dead model is surfaced as `ok:false`, never as a
  silent empty answer.
- **Serving**: `serve start` brings up a warm OpenCode HTTP server; `ask` /
  `result` / `sessions` are its client. One server per project port.
- **Tools for sub-agents**: `mcp-zai/server.js` exposes web search/fetch,
  image and vision tools; `mcp-browser` drives a headless browser.
- `scripts/zai_proxy.mjs` republishes the sandbox's keyless GLM access as an
  OpenAI-compatible endpoint (provider `zai`, glm-4-plus) — the free
  last-resort route with hard rate limits (~1 req/5 s sustained).

### Layer 1 — the context-ownership layer (`ctxown.py`)

A single stdlib-only Python file is the controller. Per the spec's §13.4
decision it owns identity, invalidation, model routing and session rotation;
OpenCode is only the serving substrate. CLI surface (`--project` or
`$CTXOWN_PROJECT` scopes every command):

| Command | Purpose |
|---|---|
| `init` | Enumerate owners from the corpus tree (one doc = one owner; dot-dirs and duplicate stems handled structurally) |
| `build` / `rebuild` | Compile bundles; `--workers N` parallelizes; `rebuild --no-llm` refreshes core+periphery but **preserves derived layers** |
| `status` | Fleet coherence: valid / stale-clean / stale-dirty / invalid / unverified per owner |
| `check` | Corpus health: oversized (>32K tok) / micro (<300 tok) docs, doc-fit classification (append-only detection), registry drift |
| `serve` | Probe-driven provider selection; server start/status/restart with verification |
| `ask` / `route` / `task` | Per-inquiry owner Q&A, task→path→owners resolution with LCA manager synthesis |
| `review` | The standing review board: broadcast a diff to ALL owners, aggregate findings |
| `write` | Owner-authorised write ladder (§7.7) |
| `telemetry` | Grep-rate inside vs outside owned paths (§7.8) |
| `eval` | The planted-contradiction experiment (§8.6) — see §5 below |

**Bundle anatomy** (one per owner, under `bundles/<owner>/`):

- **Core**: the owned document, byte-identical. Tampering flips the state to
  invalid (tested).
- **Periphery**: a deterministic cross-reference ring (typically 7–8 refs on
  hermes) — what the owner should know lives *next door*.
- **Derived layer**: an LLM-compiled cross-doc memory (the expensive part;
  420 s timeout and 120K-char truncation ceilings, both corpus-scale
  parameters after R15).
- **Manifest + `corpus.sha` pin**: coverage manifest; the pin advances only
  on successful rebuild, and a *stale-dirty* owner refuses to answer (hard
  rule, proven live on the write ladder).

**The model chain** (R14): `serve probe` walks a candidate table — OpenRouter
(key, model) pairs first, keyless zai last — and the selection is re-checked
per ask with fallback. The eval pins both arms to the *same* current model so
architecture, not model tier, is the variable.

### Layer 2 — the corpora

| Fleet | Owners | Tokens | State |
|---|---|---|---|
| `hermes-agent-spine-research` (flagship) | 64 (63 leaves + root) | 339K | Live: glossary (70+ terms), 64/64 LLM bundles, evaluated twice, non-bridged eval in flight |
| `cc-gha-exploration` | 45 | 228K | Staged: glossary (48 terms), deterministic build done, LLM build queued |
| pilot (`opencode-harness/cov/`) | 35 | ~55K | Reference: 19 plants, eval v9 (TIE 6-6, P17 sharded-unique) |

A *project* is any directory containing `corpus/docs/*.md` +
`corpus/glossary.md` inside a git repo. The corpus is **assembled, not
pointed at**: the layer never guesses what counts as a document; adoption
means curating `corpus/docs/` and authoring the glossary. Append-only logs
(worklogs, changelogs) are classified out of ownership lanes — on cc-gha the
single largest artifact is `worklog.md` at 47K tokens (16% of that repo);
excluding it is policy, not oversight. The hermes corpus is itself a git repo
(`hermes-ctxown-corpus` on GitHub) so eval mutation/restore is disciplined by
git, not by filesystem hope.

## 2. Adoption flow (repo → serving fleet)

1. **Inventory + fit scan** (GitHub API): classify every markdown artifact;
   pick targets above the §1.4 threshold (one competent window ≈ 40K tokens —
   below that, do not build).
2. **Assemble the corpus**: copy/symlink ownable docs into `corpus/docs/`;
   leave append-only tails out.
3. **Author the glossary** — the highest-leverage artifact (§8.5): terms,
   value owners, precedence rules, extracted from the corpus itself. Shared
   prefix of every bundle.
4. `init` → owners enumerated from the tree; `check` → sizes, fit, drift.
5. `build --workers 2` → LLM compiles 64 derived layers (measured: 121.5 min,
   ~370K in + 256K out tokens, ≈ $0.09 at flash-tier pricing).
6. `serve start` → probe picks the provider; `status` → 64/64 valid,
   `fleet_coherent: true`.

## 3. Steady-state flows

- **Ask** (`ask --owner X "…"`): fresh session from the bundle baseline,
  question at the tail; diff injection is deterministic and appended at the
  tail (§6.1). Stale-dirty owners refuse. Nothing durable is a process.
- **Review** (`review --file F --base SHA`): the standing board. Every leaf
  owner receives the changed file's diff against base plus its own bundle and
  answers a fixed 4-question form; findings must quote document + passage
  (no quote, no finding); the aggregator dedupes and severity-ranks. Above 40
  leaves, aggregation goes hierarchical: directory squads are compressed in
  parallel (R15d) and the root merges squad outputs under a **conservation
  check** — if compression loses a distinct quoted finding, it falls back to
  pass-through, loudly. Never sampling: coverage is an ownership property,
  not a concurrency property.
- **Write** (`write`): owner decides → disposable executor session applies →
  controller enforces confinement → self-invalidation fires (the owner
  refuses to answer until rebuilt) → rebuild re-arms the owner from its
  updated bundle. Validated live end-to-end.

## 4. The eval engine (§8.6), mechanically

Ground truth is `eval/planted.json` — a bare JSON list of plants
`{id, file, find, replace, category, …}`. The scorer counts a detection only
when a finding's quoted passage overlaps the plant's original text by ≥30%
token overlap (no keyword soup). Per plant, in order:

1. Plant the contradiction (single `find→replace`), `git commit` in the
   corpus repo.
2. `rebuild --no-llm` for the affected owner — core+periphery refresh,
   **derived layer preserved** (this is what makes compiled-memory plants
   possible: the owner's compiled index can still hold the pre-state value).
3. **Sharded arm**: the full review board (63 owners) against the pre-plant
   `clean_sha`.
4. **coldgrep arm**: one agentic sub-agent with grep/read tools over the
   whole corpus, *model-pinned to the same model*, fallback disabled — this
   is the A/B baseline ("local sub-agent without context-ownership").
5. **chunk-RAG arm**: BM25 top-8 chunks + one cold prompt, no tools.
6. Persist raw evidence (`eval/plants/<id>.json`: plant, full review JSON,
   coldgrep transcript, RAG retrieval, verdicts) — R15f, after the hermes
   round lost everything but scores.
7. Restore immediately; after all plants, a benign no-op edit to the
   glossary measures the false-positive baseline.

Honest-abort semantics: a failed measurement must be distinguishable from a
measured miss. ALL-owners transport errors, failed per-plant rebuilds,
unverified server restarts, or a dead coldgrep provider abort with rc=5 —
they never score as zeros. A `finally` block guarantees corpus restore
(`git reset --hard clean_sha` + `clean -fd` + rebuild) even on abort, and
`--clean-start` wipes residue from a hard-killed run. `--plants ID,ID` re-runs
a subset — the resume path after an abort.

## 5. Where the numbers live

Every number quoted in the companion docs is measured: build 121.5 min at 2
workers; 63-way blast 25.8 s; full 63-owner board 856 s at 12-way; eval v9
(pilot) ~3 h ≈ $1.30; hermes 10-plant eval 6.2 h wall, ~$1.50 model cost; key
spend for the whole production round $4.01 (lifetime $6.65 of a $10 credit).
Per-run dollar deltas are now snapshotted from OpenRouter's key-usage
endpoint before/after every eval launch (`usage_before/after.json` under
`/home/sync/hermes-eval2/`), so cost is a measurement, not an estimate. The
one known telemetry gap: per-request token usage is not persisted (session
storage dies with container recycles); the usage-endpoint delta closes it at
run granularity.

## 6. What the evals said (honest scoreboard)

- Pilot v9 (55K tokens, 7 plants + FP, model-pinned): **TIE 6-6**, 0 FP, and
  the one architecture-discriminating datapoint on record — P17, an
  *absence* plant with no lexical bridge, caught by sharded twice, missed by
  coldgrep twice.
- Hermes round 1 (339K tokens, 10 plants + FP): **sharded 10/10, coldgrep
  10/10 (TIE), chunk-RAG 8/10, 0 FP**, corpus restored clean. All 10 plants
  were lexically bridged — realistic drift carries tokens grep can follow.
  The RAG arm's 2 misses are the clean negative control: retrieval without
  ownership knowledge doesn't know where authority lives.
- Non-bridged round (in flight, 6 plants: 2 semantic-conversion, 1 bridged
  control, 3 compiled-memory): NP-1 (semantic conversion) was caught by all
  three arms; the run then hit a transient provider 404, aborted honestly
  (rc=5, corpus self-restored, measured cost $0.033) and was resumed with
  `--plants` for the remaining five. The discriminating category is
  compiled-memory: values that exist only in the owner's preserved derived
  layer, where coldgrep is structurally blind.
- The model's demonstrated value so far is **not** raw detection rate on
  bridged drift — it is authority locality (P17), the staleness guarantee,
  the write ladder, and bounded per-owner context economics. The §1.4
  detection-rate question stays open until the non-bridged round lands.
