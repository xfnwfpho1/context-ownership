# context-ownership

An incrementally adoptable implementation of the **context-ownership model**:
partition a doc corpus along its own structure — one document per owner —
compile each owner a durable **bundle** (the owned doc verbatim + a
deterministic periphery + a compiled derived layer), and serve every owner
from it. Comprehension cost moves from *per query* to *per change*, and an
unmeasurable search problem becomes an auditable reading problem.

**The spec is the contract.** This layer implements
[`context-ownership-model.md`](https://github.com/xfnwfpho1/opencode-harness/blob/main/context-ownership-model.md)
(github.com/xfnwfpho1/opencode-harness). Every section is tracked —
status, evidence, and what closes each gap — in
[**SPEC-COMPLIANCE.md**](SPEC-COMPLIANCE.md). The operational model for
large fleets lives in [**OPERATIONS.md**](OPERATIONS.md).

## The adoption ladder

Each layer is useful on its own; adopt only what you need.

| Rung | What you get | Commands |
|---|---|---|
| **L1 — compiler** | Durable, git-versioned bundles with coverage manifests + coherence pins | `init` · `build` · `status` · `rebuild` · `check` |
| **L2 — standing review board** | Broadcast every change to ALL owners; fixed 4-question review form; mandatory quotes; dedup + severity ranking | `review` |
| **L3 — routing** | Task → paths → owners by prefix; LCA determines the coordinating manager | `route` · `task` · `ask` |
| **L4 — write path** | Owner-authorised edits: decide → isolated executor → enforce confinement → self-invalidation → rebuild → verify | `write` |
| **L5 — the falsifiable test** | Planted-contradiction eval, three systems: coldgrep vs chunk-RAG vs sharded owners | `eval` |

Plus **telemetry** (§7.8): greps inside vs outside owned paths — the control
loop that tells the compiler what to rebuild.

## Quickstart

A *project* is any directory containing `corpus/` (with `corpus/docs/*.md`
and a shared `corpus/glossary.md`), inside a git repo.

```bash
# once: the OC-harness kit (serving substrate — OpenCode + oc-tool)
bash setup.sh   # from github.com/xfnwfpho1/opencode-zai-agent-kit

ctxown.py --project /path/to/myproject init          # owners from the tree
ctxown.py --project /path/to/myproject build         # compile bundles
ctxown.py --project /path/to/myproject check         # fit + drift + sizes
ctxown.py --project /path/to/myproject serve start   # probe-driven provider
ctxown.py --project /path/to/myproject review --file docs/some-spec.md
```

`$CTXOWN_PROJECT` can replace `--project`. The reference deployment (35
owners, ~55K-token corpus, 19 planted contradictions, eval v9) lives in
[github.com/xfnwfpho1/opencode-harness](https://github.com/xfnwfpho1/opencode-harness)
under `cov/` — that is the test fixture and the case study.

## What the controller owns (and why)

Per the spec's §13.4 decision, OpenCode is the **serving substrate**, not
the model: the controller owns identity (the registry, not OpenCode
subagents), invalidation (`corpus.sha` advances only on successful rebuild),
model routing (R14: probe-driven key/model selection with a per-ask
fallback chain), and session rotation (per-inquiry sessions; nothing
durable is a process).

Design rules that are load-bearing and enforced in code:

- **Coverage is never sampled.** The review board broadcasts to every
  owner; hierarchical aggregation must conserve every distinct quoted
  finding or it falls back to pass-through, loudly (§8.4).
- **Refusing to answer while stale-dirty is a hard rule** — including when
  the staleness is the owner's own write (§6.2, §7.7; the write path proves
  the refusal fires before it rebuilds).
- **No quote, no finding** — every finding quotes document and passage
  (§8.5); the aggregator drops anything unquoted.
- **Concurrency is an execution-model knob** (`review --concurrency`),
  fully separate from ownership lanes — 1,000 owners review through 3 live
  slots just as correctly as through 30 (see OPERATIONS.md).

## Tests

```bash
python3 tests/test_ctxown.py   # deterministic, no LLM calls (74 checks;
                               # +2 live-server checks when a server is up)
```

## Repository layout

```
ctxown.py               the controller (single file, stdlib only)
tests/test_ctxown.py    deterministic regression + capability suite
examples/pilot/         pointer to the reference deployment
ARCHITECTURE.md         the E2E system as it actually runs (components, flows, eval mechanics)
RUNBOOK.md              operating procedures: cold start, servers, build, eval, failure playbook
SPEC-COMPLIANCE.md      every spec section -> status -> evidence
OPERATIONS.md           scale strategy: sleep/live, 100-1k docs, doc cutting
```
