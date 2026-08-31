# Spec compliance — every box, checked off

Status legend: **DONE** (implemented + tested + evidence) · **PARTIAL**
(implemented, gap remains) · **GAP** (not implemented) · **N/A** (not
applicable to this deployment). Evidence keys: [T#] = test in
`tests/test_ctxown.py`; [L] = validated live this session; [v9] = eval v9
(the 3-hour, 7-plant, model-pinned run); [R#] = fix series in the pilot's
README.

## Part I — the model

| § | Requirement | Status | Evidence / gap-closer |
|---|---|---|---|
| 2.1 | Structural ownership, fleet statically enumerable from the tree | **DONE** | `init` enumerates one owner per doc [T24]; 35-owner registry buildable in CI |
| 2.1 | Granularity = one parameter (cut depth), not per-owner judgement | **DONE** | cut_depth in registry; `check` flags docs that need a deeper cut (§8.3.4 below) |
| 2.2 | Co-change as diagnostic, diffed against the tree → refactoring backlog | **GAP** | Not implemented. Closure: mine `git log` co-change frequency, diff vs the structural map, report ranked candidates |
| 2.3 | Bundle two rings: core verbatim, periphery synthesised | **DONE** | Core included byte-identical [T1: tampering with it flips state to invalid]; periphery = deterministic cross-reference ring |
| 2.3 | Coverage manifest + pinned `corpus.sha` | **DONE** | manifest.json + corpus.sha per bundle; sha advances only on successful rebuild [T4, T5] |
| 2.4 | Owner = address + bundle + policy, not a process | **DONE** | registry.json is identity; OpenCode agents are just the policy carrier (§13.4); sessions are per-inquiry and disposable [T19] |
| 2.5 | Routing = path resolution; LCA escalation rule; decline-and-redirect | **DONE** | `route`/`task`: lexical + LLM task→path, prefix→owners, LCA manager synthesis [T8, T14] |
| 2.5 | Log every resolution | **PARTIAL** | route output is JSON (auditable) but no persistent resolution log — closure: append to a log file |
| 2.6 | Seams get owners (contract artifacts); high-fan-in hubs promoted | **PARTIAL** | glossary.md is the shared contract in every bundle's shared prefix; root manager owns the aggregated interface. No `.proto`-style contract freeze workflow yet — that is the code-deployment phase 4 story |
| 2.7 | Invariants: owner permanent/sessions ephemeral; bundle is prior not prison; owners may grep beyond bundle | **DONE** | Bundle agents carry read/glob/grep tools; per-inquiry sessions |
| 2.7 | Owners retain write authority; execution isolated | **DONE** | `write`: owner decides, disposable executor session applies, controller enforces confinement [L — full ladder validated live] |
| 2.7 | Deploy on docs first | **DONE** | This IS the doc deployment; code is future work |

## Part II — operating the model

| § | Requirement | Status | Evidence |
|---|---|---|---|
| 6.1 | Deterministic wake-time diff injection at the TAIL | **DONE** | diff_injection appends at the question tail [T15] |
| 6.1 | Notification ≠ validity; corpus.sha advances only on rebuild | **DONE** | [T4] |
| 6.2 | Coherence states: valid / stale-clean / stale-dirty / invalid / unverified | **DONE** | `status` computes all five [T1-T4, T12, T13] |
| 6.2 | Refuse to answer while stale — hard rule | **DONE** | stale_guard refuses on stale-dirty/unbuilt/unverified [T15; L — the §7.7 refusal fired live] |
| 6.2 | Disagreement (bundle vs live source) is reportable | **PARTIAL** | Owners are prompted to flag contradictions; not yet a structured escalation event — closure: a `contradiction_report` finding type routed to humans |
| 7.1 | Bundle is a prior, not a closed world; bundle-primed grep beats cold grep | **DONE** | Owner agents have tools; bundle carries priors (vocabulary, file map) |
| 7.2 | Three tiers: baseline (durable) / scratch (per inquiry) / promotions (to bundle source) | **PARTIAL** | Baseline+scratch: done (fresh session per inquiry). Promotions: telemetry reports what to promote [T-telemetry]; auto-write-back not implemented |
| 7.3 | Reset (new session from baseline) is the default; GC never; fork for branches | **DONE** | Per-inquiry fresh sessions; no transcript GC anywhere |
| 7.4 | Session scoped to the inquiry, never spanning unrelated inquiries | **DONE** | Fresh session per ask; `--session` continues one inquiry [T19] |
| 7.5 | Byte-stable cache-eligible prefix; session-specific content at tail | **PARTIAL** | Prompt layout is bundle-first, question-tail. **Measured: OpenRouter free-tier flash routes return `cache_read: 0`** — no provider caching on our routes (the §11 cache reality check, answered for this provider; zai unknown). Correctness does not depend on it (§5) |
| 7.6 | Long-inquiry handling (tail truncation, re-anchor, fork) | **N/A** | Doc owner inquiries are single-turn; no long inquiry has occurred. Closure when one does |
| 7.7 | Write path: owner decides, isolated execution, verify, one writer, self-invalidation terminates in rebuild | **DONE** | `write` ladder — validated live end-to-end: confinement enforced, self-stale refusal PROVEN, rebuild re-arms, owner answers from updated bundle [L] |
| 7.8 | Q&A log is compiler input: grep-rate split inside/outside; auto-promote periphery; declines are telemetry; contradictions escalate | **PARTIAL** | `telemetry` parses oc-tool events.jsonl into inside/outside greps + rebuild hints [T-evt]. Gaps: server-side asks expose no tool events via the OpenCode API (recorded, not hidden); auto-promotion is manual |
| 8.1-8.3 | Doc-set deployment: glossary in shared prefix, full text not summaries, citation mandatory, "not in my scope" first-class, model allocation | **DONE** | All five enforced in the bundle + review form [T11: no-quote-no-finding] |
| 8.4 | Standing review board: broadcast to ALL, fixed form, aggregator dedupes/ranks/drops-unquoted | **DONE** | `review`: ThreadPool fan-out to every leaf, 4-question form, aggregate_review [T11] |
| 8.4 | Hierarchical aggregation must never become hierarchical sampling | **DONE** | `--agg hier`: squad compression + controller-owned conservation check; violation → pass-through [T22, T23; L — conservation caught a real compressor merge live] |
| 8.5 | Build notes (glossary, full text, citation, scope-honesty, sha tracking, model allocation) | **DONE** | See 8.1-8.3 + flash-tier owners / strong-model builder split |
| 8.6 | Falsifiable test: ~20 planted contradictions; three systems | **DONE** | 19 plants; eval v9 ran sharded vs coldgrep (TIE 6-6, 0 FP) [v9]; arm (b) chunk-RAG implemented (BM25 chunks + one cold prompt) and smoke-validated on P13 [L]. **The full 3-arm run is the remaining decisive experiment** |
| 9 | Build sequence phases 1-4 | **PARTIAL** | Phase 1 (compiler + incremental path): DONE. Phase 2 (routing): DONE. Phase 3 (direct owner-to-owner): correctly not needed yet (no relay-demand in logs). Phase 4 (seam layer): GAP (code deployment) |
| 10 | Failure modes instrumented | **PARTIAL** | Instrumented: confident stale answers (stale_guard), self-stale (write ladder), under-invalidation (R13 path fix), registry drift (`check` [T24]), transport errors (structured, honest-abort), empty-200 poisoning (R14 chain). Gaps: silent build loss audit, seam drift, co-change diff |
| 11 | Validation experiments | see table below | |

### §11 experiment scoreboard

| Experiment | Status | Result |
|---|---|---|
| Bundle vs. cold grep (accuracy + tokens to caller) | **GAP** | Closure: 20-question QA benchmark, both arms |
| Planted-contradiction eval (doc set) | **DONE** | v9: TIE 6-6 on flash-tier, both arms pinned, 0 FP, corpus restored clean [v9] |
| Absence-query benchmark | **PARTIAL** | The review board IS an absence query (each owner: "does anything I own contradict this?"); a dedicated 10-question benchmark with ground truth not run |
| Amortisation check O(queries)→O(changes) | **GAP** | Closure: token ledger over 100 queries, both regimes |
| Build-loss audit (second model lists omissions) | **GAP** | Cheap to run; not yet |
| Bundle-vs-reality drift (monthly sample) | **GAP** | Not yet (fleet is young) |
| Grep-rate telemetry | **DONE** | `telemetry` [T-evt] |
| Task→path benchmark / LCA distribution | **GAP** | Router exists; the 50-task benchmark not run |
| Co-change vs. tree diff | **GAP** | See §2.2 |
| Seam stress test | **N/A** | Code deployment phase |
| Cache reality check | **DONE (partial)** | OpenRouter free flash: `cache_read: 0` on live asks — assume cold on free routes (§13.1's warning confirmed) |
| Head vs. tail injection | **DONE** | Diff at tail by construction; head-injection would break the prefix — not worth burning tokens to re-confirm what the provider already answers with `cache_read: 0` |
| Fresh vs. warm latency | **PARTIAL** | Measured incidentally: single-owner review ask ≈ 6.5s (one LLM round); full board ≈ 470-530s wall |
| Nesting probe / reaper test | **N/A** | Claude Code-specific / sessions are fresh-by-design |

## Part III — grounding and implementation

| § | Requirement | Status | Evidence |
|---|---|---|---|
| 13.1 | Prompt layout most-stable-first; append-only prefix discipline | **DONE** | Shared prefix → per-owner bundle → tail diff + question |
| 13.3 | OpenCode is viable only as the substrate of a custom harness | **DONE** | This layer IS that controller; owners are registry IDs, not built-in subagents |
| 13.4 | Controller owns model routing, identity, invalidation, session rotation | **DONE** | R14 provider chain (probe → selection → per-ask fallback; survives mid-session key death) [T18, T19; L — key rotation under load] |
| 14 | Open questions tracked honestly | **DONE** | This file; plus upstream issues filed (anomalyco/opencode#46245 agent hot-reload poisoning; empty-200 and stdin-hang already tracked upstream) |

## Untested dev scenarios — the honest list

What we have NOT run yet, ranked by value:

1. **The full 3-arm eval** (sharded vs coldgrep vs chunk-RAG over all 19
   plants) — completes §8.6 exactly as specified. The machinery is ready.
2. **Bundle-vs-coldgrep QA benchmark** — the §11 go/no-go experiment the
   contradiction eval does not cover.
3. **Build-loss audit** — the only way to see the failure serving owners
   are blind to (§3.4). One evening of work.
4. **A second real deployment** (Phase 2 of the project plan): the layer
   pointed at a real repo's doc tree, with the fit classifier deciding what
   is ownable (see ADOPTION-REPORT.md).
5. **Registry churn under live edits** — add/retire docs mid-flight, watch
   `check` catch drift and `init`+`rebuild` heal it ([T24] covers the
   mechanics; a live churn drill has not run).
6. **Co-change mining + tree diff** (§2.2) — the refactoring-backlog signal.

## Verdict

Per the spec's own falsifiability rule (§8.6): the model is **not yet
validated as a WIN** — eval v9 on flash-tier was a TIE (6-6) at ~55K
tokens, with the sharded fleet uniquely catching P17 (an
absence/inference plant both baselines missed twice). The corpus sits at
the edge of one competent flash-tier window, exactly where §1.4 says the
model starts to earn its complexity. The decisive experiments are queued
above, not blocked on machinery.
