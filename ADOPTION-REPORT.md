# Adoption report — mapping the layer onto the real project doc base

**Question answered:** can the context-ownership layer be mapped onto this
account's real, active, agent-harness repos — with their actual messiness
(worklogs that never stop growing, mirrored upstream docs, duplicate
filenames) — and what does that mapping look like?

**Method:** inventory every repo (GitHub API), classify every markdown
artifact with the layer's own doc-fit classifier, then a full dry-run
deployment (init → check → build → status, no LLM serving) on the largest
active research repo. All numbers below are measured, not estimated.

## 1. The account inventory (21 repos)

Active (pushed within ~8 days) and agent-harness-related, the adoption
candidates in priority order:

| Repo | Docs | Tokens | Verdict |
|---|---|---|---|
| **hermes-agent-spine-research** | 70 | ~361K | **Flagship target** — 6.5× the pilot, 9× a flash window; active research fleet |
| **cc-gha-exploration** | 47 | ~284K | Second target — parallel dogfooding track, heavy research docs |
| claudecode-harness-research | 7 | ~31K | Below the §1.4 threshold — one competent window holds it; **do not build** |
| cc-gha-public-executor | 1 | ~1.5K | Nothing to own |
| opencode-harness (cov pilot) | 34 | ~55K | Already deployed — the reference |
| tier0/1/2-demo, mirror kits, admin UI | few | small | Below threshold, or non-doc repos |

## 2. What the fit classifier found on real trees

| Repo | Ownable | Append-only | Notable |
|---|---|---|---|
| hermes-agent-spine-research | 64 | 6 | `worklog-session{,2,3,4-5,6}.md` + `bench/worklog.md` — session logs accreting as separate files |
| cc-gha-exploration | 46 | 1 | **`worklog.md` = 47,098 tokens — the single largest doc in the repo (16% of the corpus)** and still growing |
| claudecode-harness-research | 6 | 1 | `worklog.md` = 12.5K tokens, 40% of the repo |

The predicted messiness is real and quantified: in every agent-harness repo
the *largest single artifact* is a temporal log, not a maintained spec.
The policy from OPERATIONS.md applies mechanically: exclude append-only
docs from ownership lanes; own a derived rolling summary if owners need the
log's content.

## 3. The dry run: hermes-agent-spine-research, deployed for real

Assembled the corpus (64 ownable docs + a stub glossary — adoption step 1
is authoring the real one), then ran the layer end to end:

```
init     -> 64 owners enumerated from the tree (63 leaves + root manager)
check    -> 338,811 tokens total, 5,293 avg/owner (every owner well inside
            the flash-tier competence zone), 0 oversized, 4 micro, 0 drift
build    -> 64 bundles with periphery rings (7-8 cross-refs per doc)
status   -> 64/64 valid, fleet_coherent: true
```

**This is the §1.4 case in production form**: a corpus 9× past any
competent window, partitioned into owners that each hold ~5K tokens — the
pilot's detection-edge question, now answerable at real scale.

## 4. Real-tree defects the dry run exposed (and the fixes, already in)

1. **Owner-ID collision.** Real trees have `SKILL.md` at two depths; the
   bare filename as owner-id made the second owner unreachable
   (`owner_by_id` returns the first). Fixed: duplicate stems are
   disambiguated structurally with the parent-path prefix, deterministic
   and readable.
2. **Dot-directories are not corpus.** `.agents/SKILL.md` is harness
   config, not an owned document. Fixed: `init` excludes any path with a
   dot-directory component.
3. **The corpus must be assembled, not pointed.** Real repos mix code,
   config, logs, and docs in one tree. Adoption means curating
   `corpus/docs/` (a copy or symlink farm) + authoring `glossary.md` —
   the layer deliberately does not guess what counts as a document. A
   future `adopt` helper could generate the corpus skeleton from a repo.

## 5. The proposed ownership map (hermes, at repo scope)

The tree already suggests the squads (§8.4 aggregation units):

- **root fleet docs** (`DECISIONS`, `FLEET-GUIDE`, `PLAN`, `SPINE-*`,
  `README`, `SKILL`, `HANDOFF`) — 10 owners; the strategic layer
- **`docs-research/raw/`** — 20+ owners of *mirrored upstream
  hermes-agent docs* (frozen snapshots: stable, ownable, and the perfect
  periphery corpus — contradictions between the fleet's *findings-\**
  docs and upstream behavior live exactly here)
- **`docs-research/findings-*`** — the research conclusions; the
  highest-value review-board participants
- **`bench/`** — benchmark methodology + results
- **excluded**: the 6 session worklogs (append-only)

Standing review-board use case on day one: a change to `SPINE-FIT.md` or a
new `findings-*.md` broadcast to all 63 owners — "does this contradict
anything we've measured or decided?" — is precisely the absence question
(§1.2) that no grep over 361K tokens can answer with coverage.

## 6a. Execution log — the real deployments, driven live (2026-09-01)

**Hermes: from dry run to full production deployment.**
- Real glossary authored (70+ terms extracted from the corpus itself, value
  owners + precedence rules) — the single highest-leverage artifact; every
  bundle now carries shared vocabulary.
- Full LLM build: **64/64 derived layers compiled clean in 121.5 min at 2
  parallel workers** (found + fixed R15/R15b first — see below).
- Serving + stress at 2x pilot scale: **63/63 concurrent owner asks in
  25.8s wall**; full 63-owner review board (benign diff) **856s at 12-way
  concurrency, 0 transport errors, 0 false positives, self-invalidation
  refusal fired correctly**.
- The 3-arm planted eval (sharded vs coldgrep vs chunk-RAG, 10 plants
  authored against the corpus's real facts) — results in §6b.

**cc-gha-exploration: the second real corpus, staged.**
- 45 owners / 228K tokens (worklog.md, mcp-web/vendor/**, dot-dirs excluded),
  ports 4400+, corpus check clean. One real-messiness artifact found: an
  EMPTY doc (`review/round-2/cc-self-review.md`, 0 bytes) — flagged by the
  micro-doc check, still owned (correct: it is in the tree).
- Real glossary authored (48 terms, including the corpus's OWN stated
  precedence rule: design docs defer to validation docs).
- Deterministic build done; LLM build queued (one evening of provider budget).

**Defects only real scale could surface (all fixed + regression-tested):**
- R15: 120s derived-layer timeout clipped legitimately-slow compiles into
  retry churn (the pilot's 1.6K-token docs never hit it; hermes' 5-25K-token
  docs did). Timeout + truncation are now corpus-scale parameters.
- R15b: `--workers N` parallel build (per-owner mutation surfaces are
  disjoint; the build holds the single-writer lock for the whole command).
- R15c: the eval's review board now scales wall-clock and worker count with
  fleet size (a fixed 2400s/3-worker pair would clip a 64-owner board).
- R15d: squad compressions in hierarchical aggregation parallelized.
- R15e: an extraction leftover (COV_DIR) killed the layer's FIRST-ever eval
  run — subprocess-argv paths need tests that resolve every global (T27).

## 6b. The hermes 3-arm eval — the §1.4 detection-edge experiment at 6x scale

(corpus: 339K tokens / 64 owners — far beyond one competent flash-tier
window, and nobody hand-designed it for the model; 10 plants authored
against the corpus's real decisions/findings)

(RESULTS PENDING — eval in flight)

## 6. Recommended sequencing

1. **Author the real glossary** for hermes (the highest-leverage artifact,
   §8.5) — terms like spine/fleet/session/GHA currently resolve by
   context, which is exactly the synonymy problem the glossary kills.
2. **Full LLM build** on hermes (64 derived layers, one evening of
   provider budget) → `serve start` → the standing review board is live.
3. **Run the planted-contradiction eval on hermes** (author ~10 plants
   against its real decisions/findings) — the detection-edge experiment
   at 6× the pilot's scale, on a corpus nobody hand-designed for the
   model.
4. cc-gha-exploration second (its `worklog.md` excluded; its `research/`
   A2A docs are the ownable core).
5. claudecode-harness-research: leave alone (§1.4 — it fits one window);
   revisit if it grows past ~40K tokens.
