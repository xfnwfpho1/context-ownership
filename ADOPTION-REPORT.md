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

Corpus: 339K tokens / 64 owners — far beyond one competent flash-tier
window, and nobody hand-designed it for the model. 10 plants authored
against the corpus's REAL decisions/findings (RPO cadence, benchmark
counts, compaction thresholds, pin version, watchdog drop rate, cost
figures, tail-protection mechanics, free-model memory policy, LRU cache).
All three arms on openrouter/z-ai/glm-5.3-flash, both main arms model-
pinned, honest-abort semantics active.

**RESULT (2026-09-02, wall 6.2h, restored clean, FP 0):**

| arm | detections |
|---|---|
| sharded (63-owner board, hierarchical aggregation) | **10/10** |
| coldgrep (one agent + grep over the whole corpus) | **10/10** |
| chunk-RAG (BM25 top-8, no tools) | **8/10** |

Verdict: **TIE between the two full architectures; chunk-RAG loses 2.**
Per-plant wall 32-41 min (slow-provider day; the board itself ~14-18 min).
Finding volume per plant: 5-44 POST-aggregation findings — the
broadcast-to-all design surfaces massive redundancy; multiple owners flag
the same drift (e.g. the pin-version plant drew 44 findings).

**What this says about the model doc's §1.4 claim — honestly:**
- The detection edge did NOT manifest as a rate advantage here. On a real,
  cross-referenced, glossary-anchored corpus, realistic drift carries
  lexical bridges, and a grep-equipped 128K-context agent follows them.
  Every plant's authority was findable by grep; coldgrep found them all.
- The RAG arm's 2 misses (P-H01 RPO, P-H02 benchmark count) are the clean
  negative control: retrieval WITHOUT ownership knowledge does not know
  where authority lives. Lexical top-8 missed the contradicting chunks.
- The only sharded-unique catch on record remains the pilot's P17
  (absence plant with NO lexical bridge — missed by coldgrep twice). This
  round's absence attempt (P-H10, LRU) failed to be non-bridged: "LRU" is
  a greppable token. **The decisive §1.4 experiment now needs
  non-bridged plants** — semantic-only contradictions whose authority
  document shares no distinctive token with the planted text. That is a
  plant-authoring discipline, not a machinery gap.
- The ownership model's demonstrated value on real corpora so far is NOT
  raw detection rate — it is: authority locality (P17), the staleness
  guarantee (self-invalidation fired live), the write ladder, and bounded
  per-owner context economics. Detection-rate parity with a much simpler
  baseline is itself the honest headline for a 6x-scale real corpus.

## 6c. The non-bridged decisive eval — the arms finally separate (2026-09-02)

§6b's honest close was that the decisive §1.4 test needs plants whose
pre-state is NOT greppable anywhere in the corpus. This round authored
exactly that set against the same hermes fleet (339K tokens / 64 owners),
expectations pre-registered in the plant files before any run:

| plant | category | pre-state survives where |
|---|---|---|
| NP-1, NP-2 | semantic-conversion | authority docs, but in converted units (runs/day vs 6h) — grep(old) and grep(new) do not cross |
| NP-3 | bridged control | greppable same-unit statements in glossary/HANDOFF — a miss here indicts the run, not the arm |
| NP-4, NP-5, NP-6 | compiled-memory | ONLY the owning doc's preserved DERIVED layer — the corpus line being changed is the value's sole corpus occurrence |

**RESULT (final run: wall 19.6 min, measured cost $0.000 on a free-tier
model, all three arms pinned to that model, 63-owner board, hierarchical
aggregation, 0 false positives on the clean run, corpus restored clean
and verified on disk):**

| plant | category | sharded | coldgrep | chunk-RAG |
|---|---|---|---|---|
| NP-1 bridge-cadence-units | semantic-conversion | ✓ | ✓ | ✓ |
| NP-2 monitor-cadence-units | semantic-conversion | ✓ | ✓ | ✓ |
| NP-3 worklog-bridge control | value-contradiction | ✓ | ✓ | ✓ |
| NP-4 digest-size | compiled-memory | **✓** | ✗ | ✗ |
| NP-5 statecopy-size | compiled-memory | **✓** | ✗ | ✗ |
| NP-6 cappressure | compiled-memory | **✓** | ✗ | ✗ |
| **total** | | **6/6** | **3/6** | **3/6** |

Verdict: **SHARDED_WINS** — in exactly the pre-registered shape. The
control validated the run; the semantic conversions were caught by every
architecture on both models tested; the compiled-memory plants were caught
by nobody but the fleet: 3/3 sharded-only.

The mechanism, verbatim from the run's own evidence (the discriminating
findings quote the owners' compiled memory, not corpus text; raw evidence
preserved by R15f at `hermes-proj/eval/plants/NP-*.json`):

- NP-5, owner EXPERIMENTS-LOG (its only finding): "The derived layer (key
  facts) and the diff disagree on the migrated state size: 51 MB vs 15 MB."
- NP-4, owner TOKEN-AUDIT (5 findings): the 3,405-char digest survives
  only in TOKEN-AUDIT's compiled key-facts; the planted table says 2,405.
- NP-6, owners EXPERIMENTS-LOG + MEMORY-BENCHMARK (2 findings, both
  high): the 94%-capacity measurement vs the planted 64%, corroborated
  from MEMORY-BENCHMARK's own owned benchmark trajectory.

The baselines' misses are structural, not model failures: after the
plant, the old value appears nowhere in the corpus (grep has no target)
and BM25 top-8 retrieves the changed doc with nothing left to contradict
it. The pre-state's only surviving record is the owner's compiled memory
— the §1.4 claim, confirmed in its precise form.

**Honest caveats:**
- NP-1 was measured on glm-5.3-flash before the provider's paid credit
died mid-experiment; NP-2..NP-6 finished on minimax-m3:free after the R14
chain rotated the selection. Each run was internally model-pinned
(model_mixed false), and all three discriminating compiled-memory plants
ran on one single model — the 3/3-vs-0/3-vs-0/3 separation is a clean
same-model comparison. The semantic-conversion result replicates across
both models.
- The mid-run credit death cost two honest aborts ($0.033 + $0.005
measured; corpus self-restored both times; no false misses recorded) and
exposed a harness lesson queued as R16: the provider probe's tiny ping
passes while real 32K-token asks return 402 — probe affordability must
match real-ask max_tokens.
- Total spend across all four launches: **$0.042 measured** ($0.004
  crash + $0.038 aborts + $0.000 decisive finish). The decisive run
  itself was free-tier.
- Compiled-memory plants drew 1-5 findings from exactly the owners whose
memory holds the pre-state (vs 5-44 findings on §6b's bridged plants) —
non-bridged drift is invisible outside the owning bundle, which is the
point.

**What this establishes — and what it does not:**
- Established: the fleet detects a class of real drift (single-statement
facts whose only other record is compiled memory) that a same-model grep
agent and a same-model chunk-RAG agent CANNOT detect, at zero marginal
build cost — the derived layers already existed.
- Bounded: bridged drift remains parity (§6b, 10/10 both arms); semantic
unit-conversion is parity on both models tested (NP-1/NP-2). The edge is
real exactly where authority is not lexically mirrored, and nowhere
else. That is §1.4, validated in both directions on a real corpus.

## 6. Recommended sequencing

1. ~~**Author the real glossary**~~ **DONE** — authored before the
   production build; the 64-owner fleet runs on it (§6a/§6b).
2. ~~**Full LLM build**~~ **DONE** — 64 derived layers built and serving;
   the §6c compiled-memory plants exercise them directly.
3. ~~**Run the planted-contradiction eval on hermes**~~ **DONE, both
   rounds** — bridged: TIE 10/10 (§6b); non-bridged: SHARDED_WINS with
   compiled-memory plants caught 3/3 by the fleet alone (§6c).
4. cc-gha-exploration second (its `worklog.md` excluded; its `research/`
   A2A docs are the ownable core).
5. claudecode-harness-research: leave alone (§1.4 — it fits one window);
   revisit if it grows past ~40K tokens.
