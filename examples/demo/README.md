# Aurora Station — demo corpus (a 5-minute walkthrough)

A toy context-ownership deployment: 6 ownable docs (~3K tokens total, measured 3024)
+ a shared glossary → 7 owners (6 leaves + the root manager). It exists
so you can learn the whole ladder — init → build → serve → ask → eval —
in minutes at $0 on the keyless/free provider route, before pointing
the layer at a real corpus.

The corpus is deliberately realistic in the one way that matters:
**values cross document boundaries** (MISSION.md owns cadences that
COMMS.md restates; SAFETY.md echoes POWER.md's reserve floor), and the
two demo plants exploit exactly that structure.

## Walkthrough

```bash
# 0. Prereq: the OC serving kit is up (oc-tool doctor exits 0).
#    CTXOWN = the layer's controller, e.g.
CTXOWN=/home/z/my-project/context-ownership/ctxown.py

# 1. Copy the template OUT of the layer repo and make it a git project
#    (a project is any dir with corpus/ inside a git repo). Choose a
#    FRESH directory name — don't reuse one from an earlier walkthrough.
cp -r /home/z/my-project/context-ownership/examples/demo \
      /home/z/my-project/aurora-demo
cd /home/z/my-project/aurora-demo
git init -b main
git config user.name "Demo Operator"   # only if you have no global
git config user.email "demo@local"     # git identity — the eval commits
git add -A && git commit -m "demo corpus v1"

# 2. Compile the fleet (probe-driven serving; free/keyless = $0).
#    Durable state is git-versioned by design: commit after init and
#    after build (the registry and bundles ARE the fleet).
#    Pick a FREE port range — the export matters ONLY for `init`, which
#    records the range in the registry; later commands read it back:
ss -ltn | awk '{print $4}' | grep -oE '[0-9]+$' | sort -n | uniq
export COV_BASE_PORT=4400               # e.g. — disjoint from what's listed
python3 $CTXOWN --project . init       # 7 owners; range recorded here
git add -A && git commit -q -m "registry"   # registry.json must be tracked
python3 $CTXOWN --project . check      # fit + drift + sizes
python3 $CTXOWN --project . build      # 7 LLM calls; minutes on free-tier
git add -A && git commit -q -m "fleet build" # bundles + agent files
python3 $CTXOWN --project . status     # expect: fleet_coherent true

# 3. Serve and interrogate one owner
python3 $CTXOWN --project . serve start
python3 $CTXOWN --project . ask --owner COMMS \
  "How many passes do we get and who owns that cadence?"

# 4. Run the planted-contradiction eval — the full §8.6 three-system
#    comparison (sharded owners vs cold-grep vs chunk-RAG).
#    Long steps run detached (double-fork — the Bash tool kills whole
#    process trees at ~590 s). detach.py is the layer's generic runner:
python3 /home/z/my-project/context-ownership/scripts/detach.py \
  --log eval.log --cwd . -- \
  python3 $CTXOWN --project . eval --arms all
tail eval.log                          # poll: eval.log.rc appears when done
ls eval/plants/run-*/                  # per-plant evidence (RUN-STAMPED: each
                                        # run gets its own run-<ts>/ dir —
                                        # re-runs never revert earlier
                                        # evidence) survives the restore by
                                        # design (R15f+)
```

## What the eval will show

- `DEMO-1-bridged-reserve-floor` — a **bridged** plant: the planted
  value is echoed verbatim elsewhere, so grep crosses and every
  architecture catches it (the positive control).
- `DEMO-2-semantic-pass-cadence` — a **semantic-conversion** plant: the
  planted restatement ("2 passes per day") disagrees with the authority
  ("every 8 hours") only across a unit conversion, so catching it
  requires comprehension, not grep.

Expect DEMO-1 (bridged) to be caught by every arm — it is the positive
control. DEMO-2 (semantic-conversion) is comprehension-dependent: on
the reference run (minimax-m3:free, `--arms all`) only the sharded arm
caught it — the comprehension edge shows up even at toy scale — but a
corpus this small does not guarantee separation; treat TIE as a normal
outcome. The demo teaches the mechanics (default arms are
`sharded,coldgrep`; `--arms all` adds chunk-RAG); the *detection edge*
that pre-registered (compiled-memory plants caught only by sharded
self-owners) needs a real corpus and compiled derived layers — see
ADOPTION-REPORT §6c in the layer repo.

## Notes

- Provider: `serve start` probes OpenRouter pairs first (paid, then
  free models — a $0-balance key still works for free models) and
  falls back to the keyless zai proxy. With no key at all, the keyless
  route serves everything at $0.
- Wall times: build ≈ 5–15 min, full 3-arm eval ≈ 5–10 min on free-tier
  OpenRouter (measured: 7 owners, 2 plants + FP = ~6 min); the keyless
  trickle route is slower — budget 25+ min. Anything past ~9 minutes must
  run detached (double-fork) — the Bash tool kills whole process trees at
  ~590 s.
- Cleanup: `serve stop`, then `rm -rf /home/z/my-project/aurora-demo`.
- The template ships a `.gitignore` for transient artifacts — run logs
  (`*.log`, `*.pid`, `*.rc`) and the machine-local serving state
  (`.ctxown-state.json`, `.ctxown.lock`; they change on every serve
  start/stop). Everything else the controller writes (registry,
  bundles, agent files) is durable and meant to be committed.
