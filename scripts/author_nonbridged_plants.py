#!/usr/bin/env python3
"""Author + mechanically verify the NON-BRIDGED plant set (the decisive
§1.4 experiment) for hermes.

Plant categories:
  A  semantic-conversion — live corpus authority exists but in a different
     unit (6h vs 4×/day): both arms have a path, grep(old-value) alone fails.
  B  compiled-memory — the value's ONLY other record is the owner bundle's
     preserved DERIVED layer (coldgrep is structurally blind; the sharded
     self-owner holds the pre-state via its compiled index).
  X  positive control — classic bridged plant (replicates prior eval).

Verification per plant:
  1. find-text exists VERBATIM in the target doc (eval hard requirement)
  2. replace-text differs and is unique
  3. uniqueness audit: old-value tokens must appear in NO other corpus doc
     (for category B), or only in unit-disagreed form (for A)
  4. derived-layer echo exists for category B (the catch path)
  5. scorer-feasibility: find-text has >=3 distinctive >=5-char tokens
"""
import json, re, sys
from pathlib import Path

PROJ = Path("/home/z/my-project/adopt-scan/hermes-proj")
CORPUS = PROJ / "corpus" / "docs"
BUNDLES = PROJ / "bundles"

PLANTS = [
    {
        "id": "NP-1-bridge-cadence-units",
        "file": "docs/TOKEN-AUDIT.md",
        "category": "semantic-conversion",
        "note": "TOKEN-AUDIT:172 states the job cadences in runs/day ('bridge 4x'); the authority lives in FLEET-GUIDE/glossary/HANDOFF as '(6h)' — grep('4x') and grep('6h') do not cross; the agent must convert 24h/6h = 4 runs/day != 2. Same-format restatement of the x-format exists nowhere else in the corpus.",
        "find": "(monitor 12× + digest 1× + bridge 4×)",
        "replace": "(monitor 12× + digest 1× + bridge 2×)",
        "expect": "both arms must convert units; coldgrep path = grep('bridge')/'worklog-bridge' -> 6h statements",
    },
    {
        "id": "NP-2-monitor-cadence-units",
        "file": "docs/TOKEN-AUDIT.md",
        "category": "semantic-conversion",
        "note": "monitor 12x/day vs '(2h)' authority — same unit-conversion structure as NP-1 on the sibling value of the same line.",
        "find": "(monitor 12× + digest 1× + bridge 4×)",
        "replace": "(monitor 6× + digest 1× + bridge 4×)",
        "expect": "conversion 2h -> 12 runs/day != 6; NOTE watchdog also runs 12x/day (E-O) — a real-world confusion hazard",
    },
    {
        "id": "NP-3-worklog-bridge-bridged-control",
        "file": "docs/FLEET-GUIDE.md",
        "category": "value-contradiction",
        "note": "POSITIVE CONTROL (classic bridged plant, replicates the prior eval's phenomenon): old value '6h' greppable -> glossary 'Cron job (6h)' + HANDOFF 'worklog-bridge 6h' + TOKEN-AUDIT 'bridge 4x' — direct same-unit restatements exist.",
        "find": "- **worklog-bridge** (6h): digests new worklog entries into memory (position-tracked).",
        "replace": "- **worklog-bridge** (12h): digests new worklog entries into memory (position-tracked).",
        "expect": "both arms catch (grep '6h' / 'worklog-bridge'); a miss here would be a red flag about the run itself",
    },
    {
        "id": "NP-4-digest-size-compiled-memory",
        "file": "docs/TOKEN-AUDIT.md",
        "category": "compiled-memory",
        "note": "The 3,405-char digest size appears in exactly ONE corpus line and in NO other doc (incl. .agents/SKILL.md and glossary). The only pre-state record outside the diff is TOKEN-AUDIT's own preserved DERIVED layer ('memory digest: 3,405 chars'). Coldgrep has nothing to grep: expected honest miss. The sharded self-owner reviews with bundle(core=planted + derived=old) + diff.",
        "find": "| memory digest | 3,405 chars | 3,405 chars | 0 |",
        "replace": "| memory digest | 2,405 chars | 2,405 chars | 0 |",
        "expect": "sharded: catch IF the self-owner consults its compiled index; coldgrep: structural miss. THE discriminating plant.",
    },
    {
        "id": "NP-5-statecopy-size-compiled-memory",
        "file": "docs/EXPERIMENTS-LOG.md",
        "category": "compiled-memory",
        "note": "E-0's 51 MB copy size: unique corpus line, no second statement anywhere; echoed only in EXPERIMENTS-LOG's derived layer. Same structure as NP-4 in a different owner.",
        "find": "state copied (51 MB incl. skills cache)",
        "replace": "state copied (15 MB incl. skills cache)",
        "expect": "sharded-only possible; coldgrep blind",
    },
    {
        "id": "NP-6-cappressure-compiled-memory",
        "file": "docs/EXPERIMENTS-LOG.md",
        "category": "compiled-memory",
        "note": "E-? 'hit 94% capacity' — measured cap-pressure fact, single corpus statement, derived-layer echo only. The semantic hook (memory cap 8000) is glossary-pinned but the 94% figure itself is not derivable from it.",
        "find": "acknowledged + memory write (hit 94% capacity — real-world cap pressure observed)",
        "replace": "acknowledged + memory write (hit 64% capacity — real-world cap pressure observed)",
        "expect": "sharded-only possible; coldgrep blind",
    },
]

def all_docs():
    for f in sorted(CORPUS.rglob("*.md")):
        yield f

def check():
    ok = True
    for p in PLANTS:
        target = CORPUS / Path(p["file"]).relative_to("docs")
        text = target.read_text()
        pid = p["id"]
        # 1. verbatim presence
        if p["find"] not in text:
            print(f"[FAIL] {pid}: find-text not verbatim in {p['file']}")
            ok = False
            continue
        # 2. single occurrence
        n = text.count(p["find"])
        if n != 1:
            print(f"[FAIL] {pid}: find-text occurs {n}x (must be 1)")
            ok = False
        # 5. scorer feasibility
        toks = {w for w in re.findall(r"[A-Za-z0-9_]{5,}", p["find"])}
        if len(toks) < 3:
            print(f"[WARN] {pid}: only {len(toks)} distinctive tokens for quote-overlap scoring")
        # 3. uniqueness audit for compiled-memory plants
        if p["category"] == "compiled-memory":
            old_vals = [v for v in re.findall(r"\d[\d,\.]*\s*(?:MB|%)?", p["find"])
                        if v.strip() not in ("0", "1", "2", "0 ") and len(v.strip()) >= 2]
            owner = Path(p["file"]).stem
            for v in old_vals:
                hits = [str(f) for f in all_docs() if v in f.read_text()]
                others = [h for h in hits if not h.endswith(p["file"])]
                if others:
                    print(f"[FAIL] {pid}: old value {v!r} also in {others}")
                    ok = False
                else:
                    print(f"[ok]   {pid}: {v!r} unique to {p['file']} in live corpus")
                # 4. derived echo
                dpath = BUNDLES / owner / "BUNDLE.md"
                if dpath.exists():
                    dtxt = dpath.read_text()
                    m = re.search(r"## DERIVED LAYER(.*)$", dtxt, re.S)
                    derived = m.group(1) if m else ""
                    if v in derived:
                        print(f"[ok]   {pid}: {v!r} echoed in {owner} DERIVED layer (catch path)")
                    else:
                        print(f"[WARN] {pid}: {v!r} NOT in {owner} derived layer — sharded catch path weak")
                        ok = False
        # category A: verify old value does not appear in same unit elsewhere
        if p["category"] == "semantic-conversion":
            v = re.search(r"(bridge \d+×|monitor \d+×)", p["find"]).group(0)
            hits = [str(f) for f in all_docs() if v in f.read_text()]
            others = [h for h in hits if not h.endswith(p["file"])]
            print(f"[ok]   {pid}: {v!r} in corpus: {hits} (other docs: {others or 'none'})")
            if others:
                print(f"[FAIL] {pid}: same-unit restatement exists elsewhere")
                ok = False
        print(f"[ok]   {pid}: verified ({p['category']})")
    return ok

if __name__ == "__main__":
    if check():
        out = PROJ / "eval" / "planted.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(PLANTS, indent=1))
        print(f"\nALL CHECKS PASS -> wrote {len(PLANTS)} plants to {out}")
    else:
        print("\nCHECKS FAILED — not writing planted.json")
        sys.exit(1)
