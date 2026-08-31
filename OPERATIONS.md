# Operating model — scale, sleep, and cutting

The question this file answers: **what changes at 100 docs? At 1,000?**
Grounded in measurements from the 35-owner reference deployment, not
speculation.

## The one-sentence answer

Ownership lanes scale with the corpus; **execution does not** — a fleet of
1,000 owners is 1,000 durable bundles on disk plus a bounded pool of ~3-10
live execution slots, and the review board stays correct at any size
because coverage is an ownership property, not a concurrency property.

## Sleep vs. live: ownership lanes ≠ execution slots

There is no such thing as a running owner. An owner is a registry row, a
bundle on disk, and a policy file. A "live instance" exists only for the
seconds an ask is in flight — every ask opens a fresh session from the
bundle baseline and closes it after (§7.4). The fleet is therefore
**asleep by default** and wakes per inquiry.

What this buys:

| Fleet size | Live slots | What actually runs |
|---|---|---|
| 35 owners (today) | 1 shared server, 3 concurrent asks | ~1-3 LLM rounds per owner review; full board = 470-530s wall |
| 100 owners | 1-2 servers, 3-6 concurrent asks | Same mechanics; wall grows ~linearly with owners/slots |
| 1,000 owners | 2-4 servers, 6-10 concurrent asks | Same mechanics; see pacing below |

`review --concurrency N` is the knob. The measured constraints on a 4 GiB
sandbox: one shared OpenCode server holds a 35-way concurrent blast
fine (35/35, wall 34s on OpenRouter); the binding limits are the
**provider's** concurrency/rate ceilings, not RAM. On the free zai-local
route the proxy drip-feeds at 0.05-0.15 req/s; on paid OpenRouter routes
3 concurrent asks are safe and fast.

**Rule: never size live slots to the fleet.** Size them to the provider's
comfortable sustained rate. Extra slots buy nothing when the upstream
rate-limits; fewer slots cost only wall-clock, never coverage.

## Dividing the doc set intelligently (100 → 1,000 docs)

Broadcast cost per review is O(owners) — that is §8.4's *design*, not a
flaw. The scaling moves are:

1. **Aggregation follows the tree (§8.4).** Owners are grouped into
   squads by directory; each squad's findings are compressed by a manager
   pass; the root combines squad outputs. The root's input stays bounded
   (squads, not owners) while every owner still reviews — the
   conservation check enforces "never sampling" mechanically. Auto-mode:
   hierarchical when leaves > `CTXOWN_AGG_THRESHOLD` (40). At 1,000 docs
   expect ~50-80 squads — one root merge over 50-80 compressed bundles is
   trivial.
2. **Fan-out is already wave-based.** The bounded thread pool paces the
   board through the provider's window; a 1,000-owner board at 3 slots ×
   ~8s/ask ≈ 45 min wall. Slower, but correct — and parallelizable by
   adding servers/keys, which is a provider-budget question, not an
   architecture one.
3. **Periphery rings keep each ask small.** Every owner's ask contains its
   own doc verbatim + its periphery, never other docs' bodies. Ask size is
   independent of fleet size; only count grows.
4. **Pre-warm only the hot owners (§7.5).** If a subset of docs churns
   (the spec's "10 changes a week" class), pin those owners' servers hot
   and leave the long tail asleep — cold wake costs one bundle prefill,
   which the measurements show is seconds, not minutes.

What does NOT scale and should be refused: any scheme that routes the
review only to "affected" owners. That is hierarchical *sampling* — §8.4
explicitly forbids it, and the eval's P17 result is the reason: the
plant's contradiction lived in a doc no router would have selected
(billing-dispute SLA vs operations-runbook §5.1), and the broadcast is
exactly what caught it.

## How thinly to cut a document (granularity)

The spec's answer (§2.1, §8.3-4) with measured numbers:

- **Default: one doc = one owner.** Doc boundaries are authored and
  semantic — the partition is handed to you.
- **A doc bigger than ~32K tokens (the flash-tier competence zone) is too
  coarse** — `check` flags it and the fix is structural subdivision: split
  by top-level section into child docs in a subdirectory, one owner each.
  Never "split by taste": push the cut one directory level deeper.
- **A doc smaller than ~300 tokens is too fine** — bundle overhead exceeds
  value; merge it into its parent topic. `check` flags these too.
- **Append-only tails are a split signal.** A runbook with a dated log
  accreting at the end is `marginal` — split the log out (it will classify
  append-only) and keep the runbook ownable.
- **Cut depth is one parameter for the whole corpus**, not per-owner
  judgement. Changing it is a re-init + rebuild, which is cheap and
  auditable.

## Unownable docs: the temporal class

Worklogs, changelogs, meeting minutes, audit trails — anything whose
content is an ever-growing log rather than a maintained specification —
are a **structurally poor fit** for durable ownership:

- Their staleness half-life is hours, not weeks, so the §8.3 premise
  ("change is slow and reviewed") is violated — the coherence machinery
  would spend its life invalidating them.
- Their value is temporal (what happened *lately*), which no fixed bundle
  captures; and "the whole thing" is unbounded, so verbatim inclusion rots.

`check` classifies them (name patterns + dated-header density) and the
adoption policy is: **exclude from ownership lanes**, and if the log
matters for owners, own a *derived rolling summary* instead — a small,
scoped doc regenerated per change-window, which is a normal ownable
artifact. The raw log stays what it is: an append-only event store.

See ADOPTION-REPORT.md for this applied to a real repo tree.

## Cost and pacing (measured)

| Quantity | OpenRouter (paid key) | zai-local (free) |
|---|---|---|
| Single-owner ask | ~2.5-8s, ~$0.001 | ~300ms + heavy rate window |
| Full 34-owner board | 470-530s wall | ~20 min at trickle pace |
| Eval v9 (7 plants + FP) | ~3h, ~$1.30 total | ~2-3h (rate-bound) |
| Rate behavior | 3 concurrent safe | 429s after ~100-150 req/45min; retries are traffic too |

Operational rules that fell out of measurement:

1. The controller raises oc-tool's sync budget to the full chain worst
   case — the interactive 100s guard silently detaches large one-shot
   calls (found live on the §7.7 executor).
2. On rate-limited routes, pace NEW requests AND retries through the
   token bucket; a retry storm keeps the 429 window hot (measured: 433
   retries in 4 min == self-inflicted outage).
3. Provider death must fail loudly and structurally (R14: probe → chain →
   honest abort), never as silent zeros — eval v8's entire sharded arm
   once recorded 0/34 on a dead key that looked like "no findings".
