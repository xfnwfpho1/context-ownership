#!/usr/bin/env python3
"""cov deterministic regression suite (R8).

No LLM calls — everything here is pure controller logic + git state.
Run: python3 tests/test_ctxown.py   (deterministic; uses the reference deployment as its fixture via $CTXOWN_TEST_PROJECT)

Covers (peer-review driven: 9-a code / 9-b tests+docs):
  T1   F-19  tampered bundle core -> coherence 'invalid' (content verification)
  T2   F-19  restored bundle -> 'valid' again
  T3   F-18  build refuses on dirty corpus tree (honest error, no build)
  T4   F-19  full circle — the LIVE bug: build --force on a dirty tree pins a
             lying sha; content verification catches it; rebuild heals it
  T5   R6b  identical rebuild does NOT rewrite agent files (mtime stable)
  T6   F-20 eval refuses on dirty corpus (exit 3, no run)
  T7   R6b  serve stop --owner scopes to one server
  T8         route decision unit tests (dominant / tie / zero / single)
  T9   F-19  bundle_core_matches: unparseable marker -> None
  T10        eval ground truth: every plant's find-text exists in its file
  T11        aggregate_review: dedupe, no-quote-no-finding, buckets, transport
  T12  F3    garbage pinned sha -> 'unverified', fleet fails CLOSED
  T13        reverse-dep invalidation (periphery change -> dependent invalid)
  T14        compute_lca unit test
  T15  §6.2  ask refusal on stale-dirty owner (needs live server; skips if none)

The suite REFUSES to run while an eval is in flight (it mutates the corpus),
and restores all state in a finally block even on unexpected failure.
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# The layer under test: repo-root/ctxown.py. The PROJECT under test is the
# reference deployment (opencode-harness/cov) — it supplies the corpus, git
# history, bundles and registry the deterministic tests need. Override with
# $CTXOWN_TEST_PROJECT to point the suite at another deployment.
LAYER = Path(__file__).resolve().parent.parent          # context-ownership/
CTXOWN = LAYER / "ctxown.py"
PROJECT = Path(os.environ.get("CTXOWN_TEST_PROJECT",
                              "/home/z/my-project/opencode-harness/cov"))
COV = PROJECT   # back-compat alias: existing tests use COV for project paths
sys.path.insert(0, str(LAYER))
os.environ["CTXOWN_PROJECT"] = str(PROJECT)   # module-level resolution must
# match the --project passed to subprocess calls (found via T9)

import importlib.util
spec = importlib.util.spec_from_file_location("ctxownmod", CTXOWN)
cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cov)

PASS, FAIL = 0, 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")

def run(*args, **kw):
    return subprocess.run([sys.executable, str(CTXOWN), "--project", str(PROJECT),
                           *args],
                           cwd=str(PROJECT), capture_output=True, text=True, **kw)

def git(*args):
    r = subprocess.run(["git", *args], cwd=str(COV / "corpus"),
                       capture_output=True, text=True)
    return r.returncode, r.stdout

def owner_state(oid):
    r = run("status")
    d = json.loads(r.stdout.strip().splitlines()[-1])
    return d["owners"][oid]["state"]

# ---- safety guard: never mutate the live corpus while an eval is in flight ----
for hb_path in sorted(glob.glob('/home/sync/eval-*/heartbeat.txt')):
    hb = Path(hb_path)
    if hb.exists():
        age = time.time() - hb.stat().st_mtime
        content = hb.read_text().strip()
        last = content.splitlines()[-1] if content else ''
        if age < 180 and 'DONE' not in last:
            print(f"REFUSED: eval in flight ({hb_path}, fresh heartbeat).")
            print("Re-run this suite after the eval completes.")
            sys.exit(2)

# ---- safety guard: refuse on a dirty corpus worktree (CodeRabbit) ----
# the finally-block restore would otherwise discard the caller's own
# uncommitted edits; refuse up front instead.
_rc, _st_out = git("status", "--porcelain")
if _rc == 0 and _st_out.strip():
    print("REFUSED: corpus has uncommitted changes — this suite mutates and")
    print("hard-restores the corpus tree; commit or stash first.")
    sys.exit(2)

# ---- snapshot state for guaranteed restore ----
ORIG_HEAD = cov.corpus_sha()
PO_DOC = COV / "corpus/docs/product-overview.md"
PO_BUNDLE = COV / "bundles/product-overview/BUNDLE.md"
ORIG_DOC = PO_DOC.read_text()
ORIG_BUNDLE = PO_BUNDLE.read_text()

try:
    print("== T1: F-19 tampered bundle core -> invalid ==")
    rate_row = next((l for l in ORIG_DOC.splitlines()
                     if "API rate limit" in l and "|" in l), "")
    assert rate_row and rate_row in ORIG_BUNDLE, "rate-limit row not in bundle"
    tampered = ORIG_BUNDLE.replace(rate_row, rate_row.replace("0", "1", 1), 1)
    assert tampered != ORIG_BUNDLE
    PO_BUNDLE.write_text(tampered)
    check("tampered core detected as invalid", owner_state("product-overview") == "invalid")

    print("== T2: F-19 restored bundle -> valid ==")
    PO_BUNDLE.write_text(ORIG_BUNDLE)
    check("restored core -> valid", owner_state("product-overview") == "valid")

    print("== T3: F-18 build refuses on dirty tree ==")
    PO_DOC.write_text(ORIG_DOC + "\n<!-- residue -->\n")
    r = run("build", "--owner", "api-spec", "--no-llm")
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    d = json.loads(out) if out.startswith("{") else {}
    check("build refused", d.get("ok") is False)
    check("refusal names the dirty file", "product-overview.md" in json.dumps(d))

    print("== T4: F-19 full circle — build --force on dirty tree, pin lies, caught ==")
    # this is the EXACT live bug: bundle compiled from dirty bytes, pinned clean sha
    r = run("build", "--owner", "product-overview", "--no-llm", "--force")
    d = json.loads(r.stdout.strip().splitlines()[-1])
    check("forced build ran", d.get("ok") is True)
    # restore the doc (the pin now describes a tree the bundle didn't see)
    PO_DOC.write_text(ORIG_DOC)
    git("add", "-A"); git("commit", "-m", "t4-restore-doc", "--allow-empty")
    st = owner_state("product-overview")
    check("lying pin caught -> invalid", st == "invalid", f"state={st}")
    r = run("rebuild", "--no-llm")
    check("rebuild heals", owner_state("product-overview") == "valid")

    print("== T5: R6b identical rebuild leaves agent files untouched ==")
    agent_file = COV / ".opencode/agents/cov-api-spec.md"
    before = agent_file.stat().st_mtime_ns
    r = run("build", "--owner", "api-spec", "--no-llm")
    after = agent_file.stat().st_mtime_ns
    d = json.loads(r.stdout.strip().splitlines()[-1])
    check("no agent rewrite on identical content", before == after)
    check("agents_changed empty in output", "agents_changed" not in d)
    print("== T5b: F24 rebuild IDEMPOTENCE (derived layer stable) ==")
    b1 = (COV / "bundles/api-spec/BUNDLE.md").read_text()
    r = run("build", "--owner", "api-spec", "--no-llm")
    b2 = (COV / "bundles/api-spec/BUNDLE.md").read_text()
    check("two consecutive no-llm builds are byte-identical", b1 == b2)
    hdr = "(compiled index — convenience only; the CORE ring above wins on any conflict)"
    check("no duplicated derived-header tails", b2.count(hdr) == 1,
          f"count={b2.count(hdr)}")

    print("== T6: F-20 eval refuses on dirty corpus ==")
    PO_DOC.write_text(ORIG_DOC + "\n<!-- residue -->\n")
    r = run("eval", "--quick")
    PO_DOC.write_text(ORIG_DOC)
    git("checkout", "--", "docs/product-overview.md")
    check("eval refused with exit 3", r.returncode == 3,
          f"rc={r.returncode} out={r.stdout[:120]}")
    check("refusal mentions --clean-start", "--clean-start" in r.stdout)

    print("== T7: R6b scoped serve stop ==")
    r = run("serve", "stop", "--owner", "api-spec")
    d = json.loads(r.stdout.strip().splitlines()[-1])
    stopped_ids = [s["server"] for s in d.get("stopped", [])]
    check("scoped stop touches only api-spec",
          stopped_ids in ([], ["api-spec"]), f"stopped={stopped_ids}")
    try:
        with urllib.request.urlopen("http://127.0.0.1:4200/global/health", timeout=2) as h:
            check("shared server survived scoped stop", h.status == 200)
    except Exception:
        print("  (note: no shared server running — health check skipped)")

    print("== T8: route decision unit tests (pure function) ==")
    check("dominant top-1 wins",
          cov.resolve_route_owners([("a", 9), ("b", 3), ("c", 1)]) == (["a"], "lexical-dominant"))
    check("tie falls through to LLM",
          cov.resolve_route_owners([("a", 4), ("b", 3), ("c", 1)]) == (None, None))
    check("2x boundary NOT dominant (strict >)",
          cov.resolve_route_owners([("a", 6), ("b", 3)]) == (None, None))
    check("all-zero falls through",
          cov.resolve_route_owners([("a", 0), ("b", 0)]) == (None, None))
    check("single owner with score wins",
          cov.resolve_route_owners([("a", 2)]) == (["a"], "lexical-dominant"))
    check("single owner with zero falls through",
          cov.resolve_route_owners([("a", 0)]) == (None, None))

    print("== T9: bundle_core_matches unparseable -> None ==")
    tmp = PO_BUNDLE.read_text()
    PO_BUNDLE.write_text("# no markers here")
    ov = [o for o in cov.load_registry()["owners"] if o["id"] == "product-overview"][0]
    check("unparseable bundle -> None", cov.bundle_core_matches(ov) is None)
    PO_BUNDLE.write_text(tmp)

    print("== T10: eval ground truth integrity ==")
    gt = json.loads((COV / "eval/planted.json").read_text())
    # Phase 3 grew the corpus from 12 plants (P01-P12, the 6-doc pilot) to 19
    # (P01-P19, with P13-P19 the inference-level additions for the >1-window
    # extended corpus). The count is intentionally not pinned to a specific
    # number — any addition is fine as long as every plant's find-text is
    # present in the corpus (the next assertion).
    check("ground truth has at least 12 plants", len(gt) >= 12,
          f"got {len(gt)}")
    drift = []
    for p in gt:
        f = COV / "corpus" / p["file"]
        if not f.exists():
            drift.append(f"{p['id']}: file missing")
        elif p["find"] not in f.read_text():
            drift.append(f"{p['id']}: find-text drift")
    check(f"all {len(gt)} plants' find-texts present", not drift, "; ".join(drift))

    print("== T11: aggregate_review unit tests (synthetic) ==")
    mk = lambda owner, in_scope, findings, notes="", terr=False: {
        "owner": owner, "in_scope": in_scope, "findings": findings,
        "notes": notes, "transport_error": terr}
    results = [
        mk("a", True, [{"type": "contradiction", "severity": "high",
                        "quote_mine": "Q1 alpha beta", "quote_change": "x",
                        "explanation": "e"}]),
        mk("b", True, [{"type": "contradiction", "severity": "medium",
                        "quote_mine": "Q1 alpha beta",  # duplicate quote -> deduped
                        "quote_change": "x", "explanation": "e"}]),
        mk("c", True, [{"type": "contradiction", "severity": "high",
                        "quote_mine": "",  # no quote -> dropped (§8.5)
                        "explanation": "e"}]),
        mk("d", False, []),
        mk("e", None, [], notes="SELF_STALE — refused"),
        mk("f", None, [], notes="UNPARSEABLE (x)"),
        mk("g", None, [], terr=True),   # transport error: never answered
    ]
    rep = cov.aggregate_review(results)
    check("dedupe by quote (2->1)", rep["finding_count"] == 1, json.dumps(rep["findings"]))
    check("no-quote-no-finding", all(f["quote_mine"] for f in rep["findings"]))
    check("owners_in_scope", rep["owners_in_scope"] == ["a", "b", "c"])
    check("self_stale bucket", rep["owners_self_stale"] == ["e"])
    check("unparseable excludes self-stale", rep["owners_unparseable"] == ["f", "g"])
    check("transport errors counted", rep["owners_transport_error"] == ["g"])

    print("== T12: F3 garbage pinned sha -> unverified, fleet fails closed ==")
    sha_file = COV / "bundles/architecture/corpus.sha"
    orig_sha = sha_file.read_text()
    sha_file.write_text("0" * 40)
    r = run("status")
    d = json.loads(r.stdout.strip().splitlines()[-1])
    check("architecture unverified", d["owners"]["architecture"]["state"] == "unverified")
    check("fleet_coherent false", d["fleet_coherent"] is False)
    check("warning_unverified surfaced", "warning_unverified" in d)
    sha_file.write_text(orig_sha)

    print("== T13: reverse-dep invalidation (periphery change -> dependent invalid) ==")
    PO_DOC.write_text(ORIG_DOC + "\n<!-- reverse-dep probe: see `api-spec.md` -->\n")
    git("add", "-A"); git("commit", "-m", "t13-reverse-dep-probe", "--allow-empty")
    check("core owner stale-dirty", owner_state("product-overview") == "stale-dirty")
    check("dependent invalid (periphery)", owner_state("api-spec") == "invalid")
    git("reset", "--hard", "HEAD~1")
    r = run("rebuild", "--no-llm")
    check("fleet re-cohered", owner_state("product-overview") == "valid"
          and owner_state("api-spec") == "valid")

    print("== T14: compute_lca unit test ==")
    reg = cov.load_registry()
    lca = cov.compute_lca(reg, ["api-spec", "data-model"])
    check("LCA of two leaves = root manager", lca == "root", f"lca={lca}")

    print("== T15: §6.2 ask refusal on stale-dirty owner (live server) ==")
    server_up = False
    try:
        with urllib.request.urlopen("http://127.0.0.1:4200/global/health", timeout=2) as h:
            server_up = (h.status == 200)
    except Exception:
        pass
    if server_up:
        PO_DOC.write_text(ORIG_DOC + "\n<!-- staleness probe -->\n")
        git("add", "-A"); git("commit", "-m", "t15-stale-probe", "--allow-empty")
        r = run("ask", "--owner", "product-overview", "What is the Free plan rate limit?")
        d = json.loads(r.stdout.strip().splitlines()[-1])
        check("stale-dirty ask refused without LLM",
              d.get("refused") is True and "stale-dirty" in json.dumps(d).lower(),
              json.dumps(d)[:200])
        git("reset", "--hard", "HEAD~1")
        run("rebuild", "--no-llm")
    else:
        print("  (note: no live server — refusal test skipped)")

    print("== T16: R10 KIT_DIR auto-discovery ==")
    # R10: fresh-clone scenarios leave the legacy path empty; auto-discovery
    # must find the kit via PATH lookup or well-known locations.
    discovered = cov.KIT_DIR
    check("KIT_DIR resolves to a real directory", discovered.is_dir(),
          f"KIT_DIR={discovered}")
    check("discovered KIT_DIR contains oc-tool.py",
          (discovered / "oc-tool.py").is_file(),
          f"looking for {discovered}/oc-tool.py")
    # The OC_TOOL path also depends on this — verify both are consistent
    check("OC_TOOL path resolves to an existing file",
          Path(cov.OC_TOOL).is_file(), f"OC_TOOL={cov.OC_TOOL}")

    print("== T17: R10 unverified-rebuilds (the fresh-clone recovery) ==")
    # R10: a fresh clone leaves bundles 'unverified' (pinned shas got squashed
    # in the merge); rebuild MUST recover them, not no-op.
    # Simulate by writing garbage to one bundle's corpus.sha (the same state
    # T12 produces for the whole fleet, but for one owner only).
    # NOTE: writing to corpus.sha (a tracked file) makes the tree dirty, so
    # the rebuild's dirty-tree check refuses unless --force is used. The R10
    # recovery path runs identically under --force — the build still pins
    # HEAD's sha and content-verifies via bundle_core_matches.
    sha_file = cov.BUNDLES_DIR / "api-spec" / "corpus.sha"
    orig_sha_t17 = sha_file.read_text().strip()
    sha_file.write_text("0" * 40)  # garbage sha
    pre_state = owner_state("api-spec")
    check("garbage sha surfaces as unverified",
          pre_state == "unverified", f"pre_state={pre_state}")
    r = run("rebuild", "--no-llm", "--force")
    post_state = owner_state("api-spec")
    check("rebuild recovered the unverified bundle",
          post_state in ("valid", "stale-clean"), f"post_state={post_state}")
    # Restore (the finally block also runs but be explicit)
    sha_file.write_text(orig_sha_t17)

    print("== T18: R14 provider probe + selection ordering ==")
    # The selection must follow preference order (primary key first, primary
    # model first, zai last) and the chain must contain only PROBE-VERIFIED
    # candidates. All probe transports are mocked — no network here.
    real_probe_or = cov.probe_openrouter_pair
    real_probe_zai = cov.probe_zai_local
    real_sel_cache = cov._PROVIDER_SELECTION
    cands = cov._provider_candidates()
    models = cov._model_chain_from_env()
    primary_key = next((c["api_key"] for c in cands
                        if c["key_alias"] == "primary"), "k1")
    fallback_key = next((c["api_key"] for c in cands
                         if c["key_alias"] == "fallback"), "k2")
    try:
        # (a) every OpenRouter pair dead, zai alive -> zai is selected
        cov.probe_openrouter_pair = lambda k, m: (False, "HTTP 401 dead")
        cov.probe_zai_local = lambda: (True, "200")
        cov._PROVIDER_SELECTION = None
        sel = cov.select_provider(force=True)
        check("all-OpenRouter-dead selects zai",
              sel.get("provider") == "zai" and sel.get("model") == cov.ZAI_MODEL,
              f"sel={sel.get('provider')}/{sel.get('model')}")
        check("zai selection chain is [zai]",
              sel.get("chain") == [cov.ZAI_MODEL], f"chain={sel.get('chain')}")

        # (b) primary key + primary model alive -> preferred over everything
        def probe_b(k, m):
            return (k == primary_key and m == models[0]), "200"
        cov.probe_openrouter_pair = probe_b
        cov._PROVIDER_SELECTION = None
        sel = cov.select_provider(force=True)
        check("primary+primary-model wins",
              sel.get("key_alias") == "primary" and sel.get("model") == models[0],
              f"sel={sel.get('key_alias')}/{sel.get('model')}")
        check("chain: selected model first, zai last resort last",
              sel.get("chain") == [models[0], cov.ZAI_MODEL],
              f"chain={sel.get('chain')}")

        # (c) primary key fully dead, fallback key alive on models[1:] ->
        #     selection = fallback+models[1], chain = fallback's OK models + zai
        def probe_c(k, m):
            if k == fallback_key:
                ok_models = {models[1], models[2]} if len(models) > 2 else {models[1]}
                return m in ok_models, "200"
            return False, "HTTP 401 dead"
        cov.probe_openrouter_pair = probe_c
        cov._PROVIDER_SELECTION = None
        sel = cov.select_provider(force=True)
        check("dead primary key falls through to fallback key",
              sel.get("key_alias") == "fallback" and sel.get("model") == models[1],
              f"sel={sel.get('key_alias')}/{sel.get('model')}")
        ok_models = [m for m in models[1:] if m]
        expected_chain = ok_models + [cov.ZAI_MODEL]   # zai mock still True here
        check("chain holds only probe-OK models on the selected key + zai",
              sel.get("chain") == expected_chain,
              f"chain={sel.get('chain')} expected={expected_chain}")

        # (d) total outage -> model None, chain [] (fail honestly, never fake)
        cov.probe_openrouter_pair = lambda k, m: (False, "HTTP 401 dead")
        cov.probe_zai_local = lambda: (False, "proxy down")
        cov._PROVIDER_SELECTION = None
        sel = cov.select_provider(force=True)
        check("total outage -> model None, empty chain (honest)",
              sel.get("model") is None and sel.get("chain") == [],
              f"sel={sel.get('model')}/{sel.get('chain')}")

        # (e) zai probe FAILED -> zai must NOT be appended to an OpenRouter
        #     chain (chain contains only probe-verified candidates)
        cov.probe_openrouter_pair = probe_b
        cov.probe_zai_local = lambda: (False, "proxy down")
        cov._PROVIDER_SELECTION = None
        sel = cov.select_provider(force=True)
        check("failed zai probe is excluded from the chain",
              sel.get("chain") == [models[0]], f"chain={sel.get('chain')}")
    finally:
        cov.probe_openrouter_pair = real_probe_or
        cov.probe_zai_local = real_probe_zai
        cov._PROVIDER_SELECTION = real_sel_cache

    print("== T19: R14 ask retries the model chain on poisoned responses ==")
    # The v8 failure mode: every ask returned an empty 200 with zero output
    # tokens (a swallowed 401). R14: the ask walks the chain — empty response
    # on model N retries on model N+1 with a FRESH session; if every model
    # fails, the error is structured and names the whole chain. Transport is
    # fully mocked (no live server, no LLM).
    real_http_json = cov.http_json
    real_owner_port = cov.owner_port
    real_stale_guard = cov.stale_guard
    real_provider_chain = cov.provider_chain
    calls = []
    try:
        def fake_http_json(method, url, body=None, timeout=120):
            if url.endswith("/session"):
                calls.append(("session",))
                return {"id": f"ses-fake-{len(calls)}"}
            mid = body["model"]["modelID"]
            calls.append(("message", mid))
            if mid == "dead-model":
                # the poisoned signature: 200 OK, empty text, zero tokens
                return {"parts": [{"type": "text", "text": ""}],
                        "info": {"tokens": {"output": 0}}}
            return {"parts": [{"type": "text", "text": "REAL ANSWER"}],
                    "info": {"tokens": {"output": 5}}}
        cov.http_json = fake_http_json
        cov.owner_port = lambda registry, oid: 4200
        cov.stale_guard = lambda owner: ("valid", [])
        cov.provider_chain = lambda: ["dead-model", "live-model"]
        reg = cov.load_registry()
        r = cov.cov_ask_owner("api-spec", "test question", registry=reg)
        check("ask recovers via chain retry",
              r.get("ok") is True and r.get("text") == "REAL ANSWER",
              f"r={r}")
        check("answer came from the live model",
              r.get("model") == "live-model", f"model={r.get('model')}")
        check("fresh session per chain attempt (failed attempt not reused)",
              len([c for c in calls if c[0] == "session"]) == 2,
              f"calls={calls}")

        # all models dead -> structured failure naming the chain
        calls.clear()
        cov.provider_chain = lambda: ["dead-model"]
        r2 = cov.cov_ask_owner("api-spec", "q", registry=reg)
        check("all-dead chain -> structured failure + empty_response flag",
              r2.get("ok") is False and r2.get("empty_response") is True
              and r2.get("model_chain") == ["dead-model"],
              f"r2={r2}")

        # caller-supplied session: continuation semantics preserved (all
        # attempts in the ONE session — no fresh sessions mid-inquiry)
        calls.clear()
        cov.provider_chain = lambda: ["dead-model", "live-model"]
        r3 = cov.cov_ask_owner("api-spec", "q", session="ses-given", registry=reg)
        check("given session is reused across chain attempts",
              r3.get("ok") is True and r3.get("session_id") == "ses-given"
              and len([c for c in calls if c[0] == "session"]) == 0,
              f"r3={r3} calls={calls}")
    finally:
        cov.http_json = real_http_json
        cov.owner_port = real_owner_port
        cov.stale_guard = real_stale_guard
        cov.provider_chain = real_provider_chain

    # ===========================================================================
    # T20-T26: the LAYER's new capabilities (write path §7.7, hierarchical
    # aggregation §8.4, doc-fit §8.3/phase-2, telemetry §7.8, chunk-RAG §8.6(b))
    # ===========================================================================
    print("\n== T20: doc-fit classification (ownable / append-only / marginal) ==")
    ownable = cov.classify_doc_fit("docs/api-spec.md", "# API\n\nRate limit is 100 rpm.")
    check("plain spec is ownable", ownable["fit"] == "ownable", str(ownable))
    wl = cov.classify_doc_fit("docs/worklog.md",
                              "# Worklog\n\n## 2026-08-30\n\ndid a thing\n\n## 2026-08-29\n\ndid another\n\n## 2026-08-28\n\nmore")
    check("worklog with dated headers is append-only", wl["fit"] == "append-only", str(wl))
    cl = cov.classify_doc_fit("docs/changelog.md", "# Changelog\n\nv1.2: fixed")
    check("changelog name alone flags append-only", cl["fit"] == "append-only", str(cl))
    mg = cov.classify_doc_fit("docs/runbook.md",
                              "# Runbook\n\nsteps...\n\n## 2026-08-30\n\none dated entry")
    check("single dated header is marginal (split candidate)", mg["fit"] == "marginal", str(mg))

    print("\n== T21: chunk corpus + BM25 retrieval determinism/sanity ==")
    chunks = cov.chunk_corpus()
    check("corpus chunks: every doc, nonempty", len(chunks) > 30 and all(c["text"] for c in chunks),
          f"{len(chunks)} chunks")
    gt_for_retrieval = json.loads((COV / "eval/planted.json").read_text())
    probe_plant = gt_for_retrieval[0]
    top = cov.bm25_topk(f"{probe_plant['file']} {probe_plant['find']}", chunks, k=8)
    check("BM25 top-k deterministic", top == cov.bm25_topk(f"{probe_plant['file']} {probe_plant['find']}", chunks, k=8))
    check("BM25 surfaces the plant's own file",
          any(c["doc"] == probe_plant["file"] for c in top),
          " | ".join(c["doc"] for c in top))

    print("\n== T22: squad compression conservation (never-sample enforcement) ==")
    findings_a = [
        {"owners": ["api-spec"], "type": "contradiction", "severity": "high",
         "quote_mine": "Rate limit is 100 requests per minute", "quote_change": "x",
         "explanation": "e1"},
        {"owners": ["auth-service"], "type": "duplicate", "severity": "low",
         "quote_mine": "Soft-deleted tenants are purged after 30 days", "quote_change": "y",
         "explanation": "e2"},
    ]
    good_stub = lambda q, timeout=240: (True, json.dumps({"findings": [
        {"owners": ["api-spec"], "type": "contradiction", "severity": "high",
         "quote_mine": "Rate limit is 100 requests per minute", "quote_change": "x",
         "explanation": "merged"},
        {"owners": ["auth-service"], "type": "duplicate", "severity": "low",
         "quote_mine": "Soft-deleted tenants are purged after 30 days", "quote_change": "y",
         "explanation": "merged2"}]}), {})
    r_good = cov.compress_group("docs", findings_a, oc_run_fn=good_stub)
    check("faithful compressor accepted", r_good["compressed"] is True and len(r_good["findings"]) == 2, str(r_good)[:120])
    drop_stub = lambda q, timeout=240: (True, json.dumps({"findings": [
        {"owners": ["api-spec"], "type": "contradiction", "severity": "high",
         "quote_mine": "Rate limit is 100 requests per minute", "quote_change": "x",
         "explanation": "dropped the other one"}]}), {})
    r_drop = cov.compress_group("docs", findings_a, oc_run_fn=drop_stub)
    check("dropping compressor -> conservation violation + pass-through",
          r_drop["compressed"] is False and r_drop.get("conservation_violation") is True
          and len(r_drop["findings"]) == 2, str(r_drop)[:160])

    print("\n== T23: hierarchical aggregation end-to-end (stubbed managers) ==")
    def _mk(owner, parent, quote, sev="high", typ="contradiction"):
        return {"owner": owner, "state": "valid", "in_scope": True,
                "findings": [{"type": typ, "severity": sev, "quote_mine": quote,
                              "quote_change": "chg", "explanation": "exp"}],
                "notes": "", "transport_error": False, "parent": parent}
    results_e2e = [
        _mk("api-spec", "docs", "Rate limit is 100 requests per minute"),
        _mk("auth-service", "docs/services", "Tokens expire after 24 hours"),
        _mk("billing-policy", "docs/services", "Invoices retain for 7 years"),
    ]
    def keepall_stub(q, timeout=240):
        import re as _re
        quotes = _re.findall(r'"quote_mine":\s*"([^"]+)"', q)
        return (True, json.dumps({"findings": [
            {"owners": ["x"], "type": "contradiction", "severity": "high",
             "quote_mine": qq, "quote_change": "c", "explanation": "kept"}
            for qq in quotes]}), {})
    hier = cov.hierarchical_aggregate(results_e2e, cov.load_registry(), oc_run_fn=keepall_stub)
    hq = {f["quote_mine"] for f in hier["findings"]}
    check("hier: every distinct quote survives the tree",
          hq == {"Rate limit is 100 requests per minute", "Tokens expire after 24 hours",
                 "Invoices retain for 7 years"}, str(hq))
    check("hier: squad audit present, never_sampled intact",
          bool(hier.get("hierarchy", {}).get("squads")) and hier["hierarchy"]["never_sampled"] is True,
          str(hier.get("hierarchy"))[:120])

    print("\n== T24: corpus check on a synthetic fixture (registry drift + fit) ==")
    import tempfile, shutil
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td)
        (fx / "corpus" / "docs").mkdir(parents=True)
        (fx / "corpus" / "docs" / "alpha.md").write_text("# Alpha\n\nSome spec content here.\n")
        (fx / "corpus" / "docs" / "worklog.md").write_text(
            "# Worklog\n\n## 2026-08-30\n\na\n\n## 2026-08-29\n\nb\n\n## 2026-08-28\n\nc\n")
        (fx / "corpus" / "glossary.md").write_text("# Glossary\n\n- Tenant: the unit\n")
        subprocess.run(["git", "init", "-q"], cwd=str(fx), capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=str(fx), capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
                       cwd=str(fx), capture_output=True)
        r = subprocess.run([sys.executable, str(CTXOWN), "--project", str(fx), "init"],
                           capture_output=True, text=True)
        j = json.loads(r.stdout.strip().splitlines()[-1])
        check("fixture init enumerates 2 leaves + root manager",
              j.get("owners") == 3 and "alpha" in j.get("ids", [])
              and "worklog" in j.get("ids", []) and "root" in j.get("ids", []),
              r.stdout[-200:])
        r = subprocess.run([sys.executable, str(CTXOWN), "--project", str(fx), "check"],
                           capture_output=True, text=True)
        j = json.loads(r.stdout.strip().splitlines()[-1])
        check("check flags the worklog as append-only",
              any(u["doc"].endswith("worklog.md") for u in j.get("unownable_docs", [])), r.stdout[-300:])
        check("check: no drift right after init", j.get("registry_drift") is False, str(j.get("registry_drift")))
        (fx / "corpus" / "docs" / "beta.md").write_text("# Beta\n\nNew doc\n")
        r = subprocess.run([sys.executable, str(CTXOWN), "--project", str(fx), "check"],
                           capture_output=True, text=True)
        j = json.loads(r.stdout.strip().splitlines()[-1])
        check("check detects registry drift for a new doc",
              j.get("registry_drift") is True and any(d.endswith("beta.md")
              for d in j.get("registry_drift_new_docs", [])), r.stdout[-300:])

    print("\n== T25: write-path confinement (the §7.7 enforcement step) ==")
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td)
        (fx / "corpus" / "docs").mkdir(parents=True)
        (fx / "corpus" / "docs" / "alpha.md").write_text("# Alpha\n\nVersion 2.0 is current.\n")
        (fx / "corpus" / "docs" / "other.md").write_text("# Other\n\nUnrelated.\n")
        subprocess.run(["git", "init", "-q"], cwd=str(fx), capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=str(fx), capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
                       cwd=str(fx), capture_output=True)
        subprocess.run([sys.executable, str(CTXOWN), "--project", str(fx), "init"],
                       capture_output=True, text=True)
        subprocess.run([sys.executable, str(CTXOWN), "--project", str(fx), "build", "--no-llm"],
                       capture_output=True, text=True)
        cov.set_project_dir(fx)
        reg = cov.load_registry()
        alpha = next(o for o in reg["owners"] if o["id"] == "alpha")
        doc_path = cov.CORPUS_DIR / alpha["path"]
        orig = doc_path.read_text()
        doc_path.write_text(orig.replace("2.0", "3.0"))
        changed = cov.git_changed_tracked()
        check("confined edit recognized (untracked registry.json excluded, "
              "repo-root paths normalized)",
              changed == [alpha["path"]], str(changed))
        (cov.CORPUS_DIR / "docs/other.md").write_text("# Other\n\nTampered.\n")
        changed = cov.git_changed_tracked()
        check("stray second edit detected as NOT confined", changed != [alpha["path"]], str(changed))
        (cov.CORPUS_DIR / "docs/other.md").write_text("# Other\n\nUnrelated.\n")
        doc_path.write_text(orig)
        cov.set_project_dir(PROJECT)

    print("\n== T26: rag_detects scoring (quote-overlap on synthetic answers) ==")
    plant_t26 = {"id": "T26", "file": "docs/api-spec.md",
                 "find": "Rate limit is 100 requests per minute",
                 "replace": "Rate limit is 1000 requests per minute"}
    hit = {"ok": True, "contradicts": True,
           "quote": "The API rate limit of 100 requests per minute applies per tenant"}
    miss = {"ok": True, "contradicts": True, "quote": "Invoices are retained for 7 years"}
    fp_shape = {"ok": True, "contradicts": False, "quote": ""}
    check("rag_detects: overlapping quote counts", cov.rag_detects(hit, plant_t26) is True)
    check("rag_detects: non-overlapping quote does not count", cov.rag_detects(miss, plant_t26) is False)
    check("rag_detects: no-contradiction answer is not a detection", cov.rag_detects(fp_shape, plant_t26) is False)

finally:
    print("\n== restoring state ==")
    try:
        PO_DOC.write_text(ORIG_DOC)
        PO_BUNDLE.write_text(ORIG_BUNDLE)
        git("reset", "--hard", ORIG_HEAD)
        git("clean", "-fd")
        subprocess.run([sys.executable, str(CTXOWN), "--project", str(PROJECT),
                        "rebuild", "--no-llm"],
                       cwd=str(PROJECT), capture_output=True, text=True)
        ok_state = owner_state("product-overview") == "valid"
        print(f"  restored: corpus @ {cov.corpus_sha()[:8]} "
              f"(orig {ORIG_HEAD[:8]}), product-overview valid={ok_state}")
    except Exception as e:
        print(f"  RESTORE ERROR: {e}")
        FAIL += 1

print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
