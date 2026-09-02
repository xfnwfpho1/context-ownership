#!/usr/bin/env python3
"""Hunt the hermes corpus for paraphrase pairs: lines whose stated fact is
pinned elsewhere in DIFFERENT surface form (no shared distinctive token).
These are the raw material for non-bridged plants (the decisive §1.4 test).

Method:
- authority values: known pinned facts (from glossary + plants' notes)
- for each, scan narrative docs for lines that paraphrase the same fact
- report candidate (line, doc) pairs with their distinctive-token overlap
  vs the authority passage (want: ZERO overlap)
"""
import re, sys
from pathlib import Path

CORPUS = Path("/home/z/my-project/adopt-scan/hermes-proj/corpus/docs")
STOP = set("""the a an of to in on for and or is are was were be been with by at as from
that this these those it its their our your each per not no we they he she which who
when where what how why all any some more most less least very much many few
can could should would will may might must shall do does did done have has had
than then so such only just also both either neither one two three four five six
seven eight nine ten hundred thousand million half quarter third first second
""".split())

def tokens(text, minlen=5):
    return {w.lower() for w in re.findall(r"[A-Za-z0-9_]{%d,}" % minlen, text)} - STOP

# authority passages: (label, file, regex or substring to locate passage)
AUTH = [
    ("RPO-30min", "FLEET-GUIDE.md", r"RPO of the whole system: 30 minutes"),
    ("bench-22q", "MEMORY-BENCHMARK.md", r"22 questions"),
    ("compaction-85", "docs-research/findings-A.md", r"85%"),
    ("memcap-8000", "docs-research/findings-A.md", r"memory_char_limit"),
    ("pin-0205", "README.md", r"v0\.20\.5"),
    ("drop-88", "DECISIONS.md", r"88%"),
    ("fleet-cost", "TOKEN-AUDIT.md", r"\$0\.17"),
    ("lru-128-1h", "RESEARCH-REPORT.md", r"LRU"),
    ("tail-clamp", "docs-research/findings-A.md", r"tail"),
    ("freemodel-mem", "DECISIONS.md", r"free model"),
]

# narrative docs to hunt in (not the authority doc itself)
HUNT = ["RESEARCH-REPORT.md", "EXPERIMENTS-LOG.md", "SPINE-FIT.md",
        "SPINE-ARCHITECTURE.md", "ha-vs-cc-matrix.md", "FLEET-GUIDE.md",
        "RESEARCH-BRIEF.md", "README.md", "PLAN.md", "DECISIONS.md",
        "TOKEN-AUDIT.md", "MEMORY-BENCHMARK.md",
        "docs-research/findings-A.md", "docs-research/findings-B.md",
        "docs-research/findings-C.md", "docs-research/findings-D.md",
        "docs-research/RESEARCH-BRIEF.md"]

# paraphrase probes: words that typically introduce a restated quantity
PROBE = re.compile(
    r"(half|twice|per hour|per day|per month|hourly|daily|monthly|nightly|"
    r"roughly|about |around |nearly |almost |approx|percent|"
    r"eighty|ninety|seventy|sixty|fifty|forty|thirty|twenty|dozen|"
    r"a nickel|five bucks|budget|burn|cap|limit|window|threshold|cadence|"
    r"every \w+|each \w+|timeout|ttl|ttl|cache|idle)", re.I)

for label, afile, pat in AUTH:
    ap = CORPUS / afile
    if not ap.exists():
        continue
    am = re.search(pat, ap.read_text())
    if not am:
        print(f"[skip] {label}: authority pattern not found in {afile}")
        continue
    apass = ap.read_text()[max(0, am.start()-200):am.end()+200]
    atoks = tokens(apass)
    print(f"\n=== {label} (authority: {afile}) ===")
    print(f"  authority passage: ...{apass[am.start()-max(0,am.start()-200):][:0]}{am.group(0)}...")
    for h in HUNT:
        if afile.endswith(Path(h).name):
            continue
        hp = CORPUS / h
        if not hp.exists():
            continue
        for i, line in enumerate(hp.read_text().splitlines(), 1):
            if not PROBE.search(line):
                continue
            ltoks = tokens(line)
            shared = ltoks & atoks
            # candidate: touches a quantity-ish concept, low overlap
            if ltoks and len(shared) <= 2:
                print(f"  {h}:{i}  overlap={len(shared)} {sorted(shared) if shared else ''}")
                print(f"    | {line.strip()[:150]}")
