#!/usr/bin/env python3
"""
ctxown.py — the context-ownership layer for OpenCode.

An incrementally adoptable implementation of the context-ownership model
(the spec is context-ownership-model.md in the opencode-harness repo):
- ADOPT L1 (compiler):      ctxown.py init + build   — bundles + manifests
- ADOPT L2 (standing board): ctxown.py review         — §8.4 fan-out, all owners
- ADOPT L3 (routing):        ctxown.py route/task     — §2.5 path resolution + LCA
- ADOPT L4 (write path):     ctxown.py write          — §7.7 owner-authorised edit
- ADOPT L5 (falsifiable):    ctxown.py eval           — §8.6 planted contradiction

Each layer is useful without the ones above it. The controller owns identity,
invalidation, model routing and session rotation (§13.4); OpenCode is the
swappable serving substrate, not the model.

Implements the context-ownership model:
  - Structural ownership: one doc/module per owner, enumerated from the tree
  - Durable bundles: core ring (owned artifact VERBATIM) + periphery ring
    (cross-references, deterministic) + derived layer (LLM-compiled) +
    manifest.json (coverage) + corpus.sha (coherence pin)
  - Owners as durable identities (address + bundle + policy), served by
    ephemeral per-inquiry sessions on `opencode serve`
  - Routing by path resolution (task -> paths -> owners by prefix; LCA rule)
  - Coherence states (valid / stale-clean / stale-dirty / invalid) with the
    hard rule: refuse to answer while stale
  - Standing review board: broadcast a change to ALL owners, fixed form,
    quotes mandatory, aggregator dedupes + ranks

Design decisions mapped to the doc:
  - Owners are NOT OpenCode built-in subagents (§13.4): the registry in
    registry.json is the source of identity; OpenCode custom agents
    (.opencode/agents/cov-<id>.md) are just the serving-layer policy carrier
    (system prompt = bundle, tool whitelist = policy).
  - Sessions are per-INQUIRY (§7.4): every `ask` creates a fresh session from
    the bundle baseline; multi-turn within one inquiry via --session.
  - Wake-time diff injection goes at the TAIL of the question (§6.1, §13.1).
  - corpus.sha advances ONLY on successful bundle rebuild (§6.1).

Usage: ctxown.py <command> ... (see main() or --help)
A PROJECT is any directory containing corpus/ (the owned doc tree).
Select it with --project DIR or $CTXOWN_PROJECT; default = this script's dir.
Extracted from the battle-tested opencode-harness cov pilot (56-test suite,
eval v9) and generalized; the pilot remains the reference deployment.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# --- Paths ---
# R10: KIT_DIR auto-discovery — fresh-clone scenarios leave the legacy
# /home/z/my-project/opencode-zai-agent-kit path empty. Try env, then a
# few well-known locations, then fall back to a PATH lookup of `oc-tool`.
def _discover_kit_dir():
    env = os.environ.get("OC_KIT_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    candidates = [
        Path("/home/z/my-project/opencode-zai-agent-kit"),
        Path("/home/z/kit"),
        Path("/home/z/opencode-zai-agent-kit"),
    ]
    for c in candidates:
        if (c / "oc-tool.py").is_file():
            return c
    # Fall back: search PATH for the `oc-tool` binary (installed by setup.sh)
    import shutil
    oc_bin = shutil.which("oc-tool")
    if oc_bin:
        # Resolve the kit dir from the binary's sibling oc-tool.py
        bin_dir = Path(oc_bin).resolve().parent
        for c in [bin_dir, bin_dir.parent, bin_dir.parent / "opencode-zai-agent-kit"]:
            if (c / "oc-tool.py").is_file():
                return c
    # Last-ditch: legacy hardcoded path (will fail with clear error if missing)
    return Path("/home/z/my-project/opencode-zai-agent-kit")

KIT_DIR = _discover_kit_dir()

# --- Project resolution (the layer is corpus-agnostic) ---
# A project is a directory with corpus/ in it. Resolution order:
#   1. $CTXOWN_PROJECT (or legacy $COV_PROJECT)
#   2. --project DIR global flag (parsed in main; sets this before use)
#   3. this script's own directory (back-compat: the pilot layout)
def _resolve_project_dir():
    env = (os.environ.get("CTXOWN_PROJECT") or os.environ.get("COV_PROJECT"))
    if env and (Path(env) / "corpus").is_dir():
        return Path(env)
    return Path(__file__).resolve().parent

PROJECT_DIR = _resolve_project_dir()

def set_project_dir(p):
    """Switch the project root (called by main() for --project). Rebinds
    every derived path. Must be called before any command runs."""
    global PROJECT_DIR, CORPUS_DIR, BUNDLES_DIR, REGISTRY_PATH, AGENTS_DIR
    global EVAL_DIR, SHARED_DIR, STATE_FILE, LOCK_FILE
    PROJECT_DIR = Path(p).resolve()
    CORPUS_DIR = PROJECT_DIR / "corpus"
    BUNDLES_DIR = PROJECT_DIR / "bundles"
    REGISTRY_PATH = PROJECT_DIR / "registry.json"
    AGENTS_DIR = PROJECT_DIR / ".opencode" / "agents"
    EVAL_DIR = PROJECT_DIR / "eval"
    SHARED_DIR = BUNDLES_DIR / "_shared"
    STATE_FILE = _state_file_for(PROJECT_DIR)
    LOCK_FILE = PROJECT_DIR / ".ctxown.lock"

def _state_file_for(project):
    # New projects use .ctxown-state.json; the pilot deployment keeps its
    # legacy .cov-state.json (read AND written) so its live state survives
    # the extraction. Whichever exists wins; new projects get the new name.
    legacy = project / ".cov-state.json"
    if legacy.exists():
        return legacy
    return project / ".ctxown-state.json"

CORPUS_DIR = PROJECT_DIR / "corpus"
BUNDLES_DIR = PROJECT_DIR / "bundles"
REGISTRY_PATH = PROJECT_DIR / "registry.json"
AGENTS_DIR = PROJECT_DIR / ".opencode" / "agents"
EVAL_DIR = PROJECT_DIR / "eval"
SHARED_DIR = BUNDLES_DIR / "_shared"

# --- Config ---
DEFAULT_MODEL = os.environ.get("COV_MODEL", "openrouter/z-ai/glm-5.3-flash")
BASE_PORT = int(os.environ.get("COV_BASE_PORT", "4200"))
OC_TOOL = os.environ.get("OC_TOOL", str(KIT_DIR / "oc-tool.py"))

STATE_FILE = _state_file_for(PROJECT_DIR)   # serving state (ports, pids)
LOCK_FILE = PROJECT_DIR / ".ctxown.lock"

# --- R14: provider-resilient serving layer ---
# The controller OWNS model routing (§13.4). It probes every (key, model)
# candidate and serves through the best live one, with a per-ask fallback
# chain. Found live (eval v8 post-mortem): the primary OpenRouter key EXPIRED
# mid-session and the cov server carried exactly one key + one model with no
# fallback — 34/34 asks returned empty 200s and the whole sharded arm
# recorded zeros while the run looked like a 'scalability' failure. The
# coldgrep arm survived only because oc-tool has its own retry/key-swap/
# model chain (F-07). This gives the serving layer the same resilience.
# Candidates, in preference order:
#   1. OpenRouter primary key  × [COV_MODEL, fallback models from .env]
#   2. OpenRouter fallback key × same model list
#   3. zai-local proxy (kit scripts/zai_proxy.mjs — the sandbox's keyless
#      GLM access exposed as an OpenAI-compatible endpoint; needs the proxy)
PROBE_MAX_TOKENS = int(os.environ.get("COV_PROBE_MAX_TOKENS", "32000"))
ZAI_PROXY_PORT = int(os.environ.get("COV_ZAI_PROXY_PORT", "4570"))
ZAI_PROXY_URL = os.environ.get("COV_ZAI_PROXY_URL",
                               f"http://127.0.0.1:{ZAI_PROXY_PORT}")
ZAI_MODEL = os.environ.get("COV_ZAI_MODEL", "zai/glm-4-plus")
ZAI_PROXY_SCRIPT = KIT_DIR / "scripts" / "zai_proxy.mjs"
ZAI_PROXY_LOG = f"/tmp/zai-proxy-{ZAI_PROXY_PORT}.log"


def out_json(obj):
    print(json.dumps(obj, ensure_ascii=False))


def fail(msg, code=1, **extra):
    obj = {"ok": False, "error": msg}
    obj.update(extra)
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def load_env():
    env_file = KIT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


# ---------------------------------------------------------------------------
# R14: provider probe + selection (the controller owns model routing, §13.4)
# ---------------------------------------------------------------------------

def _model_chain_from_env():
    """[primary, fallback1, fallback2] model ids, deduped. Reads .env lazily
    (module-level env reads happen before load_env() in subprocess entries)."""
    load_env()
    chain = [os.environ.get("COV_MODEL") or DEFAULT_MODEL]
    vals = [
        os.environ.get("OC_FALLBACK_MODEL")
        or os.environ.get("OPENCODE_FALLBACK_MODEL")
        or "openrouter/minimax/minimax-m3:free",
        os.environ.get("OC_FALLBACK_MODEL_2")
        or os.environ.get("OPENCODE_FALLBACK_MODEL_2")
        or "openrouter/dots-studio/dots-3-note-preview:free",
    ]
    for v in vals:
        if v and v not in chain:
            chain.append(v)
    return chain


def _provider_candidates():
    """Preference-ordered (key, model) candidates. OpenRouter pairs first
    (primary key preferred, primary model preferred), then the keyless
    zai-local proxy as the last resort."""
    load_env()
    or_models = _model_chain_from_env()
    cands = []
    for alias, envk in (("primary", "OPENROUTER_API_KEY"),
                        ("fallback", "OPENROUTER_API_KEY_FALLBACK")):
        key = (os.environ.get(envk) or "").strip()
        if not key:
            continue
        for m in or_models:
            cands.append({"provider": "openrouter", "key_alias": alias,
                          "api_key": key, "model": m})
    cands.append({"provider": "zai", "key_alias": None, "api_key": None,
                  "model": ZAI_MODEL})
    return cands


def probe_openrouter_pair(api_key, model):
    """Minimal DIRECT OpenRouter call mirroring a real ask: the same upfront
    affordability window (max_tokens=32000 — what opencode sends for
    flash-class models) that produced the live 402s, so a pair that probes OK
    cannot 402 on a real ask. Returns (ok, detail)."""
    mid = model[len("openrouter/"):] if model.startswith("openrouter/") else model
    body = {"model": mid,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": PROBE_MAX_TOKENS}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
            return True, "200"
    except urllib.error.HTTPError as e:
        try:
            detail = (e.read().decode(errors="replace") or "")[:200]
        except Exception:
            detail = ""
        m = re.search(r'"message"\s*:\s*"([^"]{0,120})', detail)
        return False, (f"HTTP {e.code} " + (m.group(1) if m else detail)).strip()[:140]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:140]


def zai_proxy_healthy():
    try:
        with urllib.request.urlopen(f"{ZAI_PROXY_URL}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_zai_proxy():
    """Double-fork the z-ai proxy daemon (survives this process). Idempotent."""
    if zai_proxy_healthy():
        return True
    if not ZAI_PROXY_SCRIPT.is_file():
        return False
    pid = os.fork()
    if pid > 0:
        for _ in range(30):
            time.sleep(1)
            if zai_proxy_healthy():
                return True
        return False
    os.setsid()
    if os.fork():
        os._exit(0)
    try:
        sys.stdout.flush(); sys.stderr.flush()
        lf = open(ZAI_PROXY_LOG, "ab")
        os.dup2(lf.fileno(), 1); os.dup2(lf.fileno(), 2)
        devnull = os.open("/dev/null", os.O_RDWR)
        os.dup2(devnull, 0)
        os.execvp("node", ["node", str(ZAI_PROXY_SCRIPT)])
    except Exception:
        os._exit(1)


def probe_zai_local():
    """Probe the zai proxy with a TOOL-CALLING request — verifies both the
    endpoint AND function-calling (owner agents need read/glob/grep).
    Returns (ok, detail)."""
    if not (zai_proxy_healthy() or start_zai_proxy()):
        return False, "proxy failed to start (node missing? script missing?)"
    body = {"model": ZAI_MODEL.split("/", 1)[1] if "/" in ZAI_MODEL else ZAI_MODEL,
            "messages": [{"role": "user", "content": "Reply with the word pong."}],
            "max_tokens": 16,
            "tools": [{"type": "function", "function": {
                "name": "noop", "description": "do nothing",
                "parameters": {"type": "object", "properties": {}}}}]}
    req = urllib.request.Request(
        f"{ZAI_PROXY_URL}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            json.loads(r.read().decode())
            return True, "200 (tool-calling OK)"
    except urllib.error.HTTPError as e:
        try:
            detail = (e.read().decode(errors="replace") or "")[:140]
        except Exception:
            detail = ""
        return False, f"HTTP {e.code} {detail}".strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:140]


_PROVIDER_SELECTION = None   # module cache (subprocesses re-read state)


def select_provider(force=False):
    """Probe all (key, model) candidates, pick the first live one in
    preference order. Builds the per-ask fallback chain from the probes:
      [selected model] + [other models OK on the SAME key] + [zai model]
    (the model is carried per-message, so chain retries need no restart; the
    zai provider is env-keyless and works on any server). Returns a selection
    dict; 'model' is None when EVERYTHING is down (fail honestly)."""
    global _PROVIDER_SELECTION
    if _PROVIDER_SELECTION and not force:
        return _PROVIDER_SELECTION
    probes = []
    ok_by_key = {}     # key_alias -> [models that probed OK]
    zai_ok = False
    selection = None
    for c in _provider_candidates():
        if c["provider"] == "zai":
            ok, detail = probe_zai_local()
            zai_ok = ok
        else:
            ok, detail = probe_openrouter_pair(c["api_key"], c["model"])
        probes.append({"provider": c["provider"], "key_alias": c["key_alias"],
                       "model": c["model"], "ok": ok, "detail": detail})
        if ok:
            ok_by_key.setdefault(c["key_alias"], []).append(c["model"])
            if selection is None:
                selection = c
    chain = []
    if selection:
        chain = list(ok_by_key.get(selection["key_alias"], []))
        if zai_ok and ZAI_MODEL not in chain:
            chain.append(ZAI_MODEL)   # probe-verified last resort
        chain = chain[:4]
    sel = {"provider": (selection or {}).get("provider"),
           "key_alias": (selection or {}).get("key_alias"),
           "api_key": (selection or {}).get("api_key"),
           "model": (selection or {}).get("model"),
           "chain": chain, "at": time.time(), "probes": probes}
    _PROVIDER_SELECTION = sel
    return sel


def current_selection():
    """The provider selection the serving layer should be using right now:
    module cache → state file → fresh probe (and record it)."""
    global _PROVIDER_SELECTION
    if _PROVIDER_SELECTION:
        return _PROVIDER_SELECTION
    st = load_state()
    p = st.get("provider") or {}
    if p.get("model") and p.get("chain"):
        _PROVIDER_SELECTION = p
        return p
    sel = select_provider(force=True)
    if sel.get("model"):
        st = load_state()
        st["provider"] = {k: sel[k] for k in
                          ("provider", "key_alias", "model", "chain", "at")}
        save_state(st)
    return sel


def provider_chain():
    """Per-ask model fallback chain (>=1 entry)."""
    sel = current_selection()
    return sel.get("chain") or [DEFAULT_MODEL]


def record_provider(sel):
    """Persist a selection into the serving state (subprocesses read it)."""
    st = load_state()
    st["provider"] = {k: sel[k] for k in
                      ("provider", "key_alias", "model", "chain", "at")}
    save_state(st)


def ensure_provider():
    """Probe + reconcile the serving layer with the best live provider:
      - selects (probe) the best (key, model), auto-starting the zai proxy
      - starts the shared server if none is running
      - restarts (verified) a running server whose key no longer matches the
        selection — mid-flight key death is exactly what killed eval v8
    Returns the selection dict; fails honestly if nothing is live."""
    sel = select_provider(force=True)
    if not sel.get("model"):
        fail("no live provider — every candidate failed probing:\n"
             + json.dumps(sel.get("probes"), indent=1)[:600], 8)
    record_provider(sel)
    running = []
    seen_ports = set()
    if server_healthy(BASE_PORT):
        running.append(("shared", BASE_PORT))
        seen_ports.add(BASE_PORT)
    for o in load_registry()["owners"]:
        if o["port"] in seen_ports:
            continue
        if server_healthy(o["port"]):
            running.append((o["id"], o["port"]))
            seen_ports.add(o["port"])
    if not running:
        r = start_server(BASE_PORT, f"/tmp/cov-serve-{BASE_PORT}.log",
                         api_key=sel.get("api_key"))
        st = load_state()
        st.setdefault("servers", {})["shared"] = BASE_PORT
        save_state(st)
        if not r:
            fail("ensure_provider: shared server failed to start", 8)
        return sel
    # server(s) already running — restart only if the recorded key is stale
    st = load_state()
    cur = st.get("provider") or {}
    key_matches = (cur.get("key_alias") == sel.get("key_alias")) \
        or (sel.get("provider") == "zai" and sel.get("key_alias") is None
            and cur.get("key_alias") is None)
    if not key_matches and sel.get("provider") == "openrouter":
        # unknown or stale key on a live server — verified restart with the
        # selected key (zai selections need no env key; no restart required)
        for name, port in running:
            old_pid = listener_pid(port)
            killed, detail = kill_listener(port)
            if not killed:
                fail(f"ensure_provider: could not free port {port} ({detail}) "
                     "for a provider-key restart", 8)
            Path(f"/tmp/cov-serve-{port}.pid").unlink(missing_ok=True)
            start_server(port, f"/tmp/cov-serve-{port}.log",
                         api_key=sel.get("api_key"))
            new_pid = listener_pid(port)
            if not (server_healthy(port) and new_pid and new_pid != old_pid):
                fail(f"ensure_provider: restart of {name} on :{port} not "
                     "verified — do not trust the serving layer", 8)
    return sel


def git(args, cwd=None):
    """Run git; returns (rc, stdout, stderr)."""
    try:
        r = subprocess.run(["git"] + args, cwd=cwd or str(CORPUS_DIR),
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 127, "", f"git failed: {e}"


def corpus_sha():
    rc, out, _ = git(["rev-parse", "HEAD"])
    return out if rc == 0 else None


def corpus_dirty_files(base_sha):
    """Files changed since base_sha: COMMITTED (base..HEAD) ∪ UNCOMMITTED
    worktree edits (base vs working tree) — R6/F2: uncommitted edits used to
    bypass the staleness rule entirely. Returns None when git itself fails
    (R6/F3: a corrupted repo or rewritten history must NEVER look 'valid').

    R13: paths are returned CORPUS-RELATIVE (e.g. 'docs/foo.md', 'glossary.md')
    to match owner['core_paths']. The previous version returned repo-relative
    paths (e.g. 'cov/corpus/docs/foo.md') which never matched core_paths —
    so the controller's stale-dirty refusal was silently never triggered
    (the agent's wake-time diff injection still worked, so v7 didn't surface
    this). Filter to corpus/ subdir so non-corpus changes (bundle rebuilds,
    code fixes) don't pollute the changed list either."""
    if not base_sha:
        return []
    rc, out, _ = git(["diff", "--name-only", "--relative", f"{base_sha}..HEAD"])
    if rc != 0:
        return None
    committed = {l.strip() for l in out.splitlines() if l.strip()}
    rc2, out2, _ = git(["diff", "--name-only", "--relative", base_sha])
    if rc2 != 0:
        return None
    worktree = {l.strip() for l in out2.splitlines() if l.strip()}
    return sorted(committed | worktree)


def single_writer_lock():
    """R8/F23: advisory flock on <project>/.ctxown.lock for build/rebuild/eval — the
    corpus/bundles/agents are single-writer state machines; concurrent runs
    interleave writes and stack restart storms (found by peer review #7).
    Returns the held lock fd or exits with a structured error."""
    LOCK = PROJECT_DIR / ".ctxown.lock"
    fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        fail("another build/rebuild/eval holds the writer lock (.ctxown.lock) — "
             "wait for it or remove a stale lock after checking no run is live", 6)
    return fd


def corpus_tree_clean():
    """F-18: is the corpus working tree clean vs HEAD (incl. untracked)?
    A build on a dirty tree pins corpus.sha=HEAD over FOREIGN bytes — the
    pinned sha then lies about what the bundle contains (found live: bundles
    carried planted values while coherence reported 'valid'). Returns
    True/False, or None when git itself fails (treat as dirty)."""
    rc, out, _ = git(["status", "--porcelain"])
    if rc != 0:
        return None
    return not out.strip()


def bundle_core_matches(owner, registry=None, docs=None):
    """F-19: content-verify the bundle's verbatim CORE ring against the live
    corpus, independent of git state. The git-based coherence check trusts the
    pinned sha to describe the compiled tree — this closes that hole: verbatim
    is verbatim. Returns [] on match, [path...] on mismatch, None when the
    bundle can't be parsed (treat as unverified)."""
    bfile = BUNDLES_DIR / owner["id"] / "BUNDLE.md"
    if not bfile.exists():
        return None
    text = bfile.read_text()
    marker = f"<!-- core: {owner['path']} -->"
    i = text.find(marker)
    j = text.find("\n## PERIPHERY RING", i)
    if i < 0 or j < 0:
        return None
    embedded = text[i + len(marker):j].strip()
    if owner["kind"] == "manager":
        registry = registry or load_registry()
        current = render_manager_core(registry, docs or all_docs(registry))
    else:
        p = CORPUS_DIR / owner["path"]
        if not p.exists():
            return None
        current = p.read_text()
    return [] if embedded == current.strip() else [owner["path"]]


def oc_run(prompt, timeout=90, model=None, no_fallback=False):
    """One-shot builder call via oc-tool (strong model at build time, §3.3).
    Returns (ok, text, raw_dict).
    R14: model pins the call to the probe-selected provider (fairness: the
    eval's two arms must run the SAME model); no_fallback disables oc-tool's
    own chain so a pinned call can't silently degrade to a different model.
    R14b: stdin=DEVNULL — `opencode run` blocks on an interactive stdin
    (found live: foreground calls hung with zero output until killed)."""
    cmd = ["python3", OC_TOOL, "run", prompt, "--timeout", str(timeout)]
    if model:
        cmd += ["--model", model]
    if no_fallback:
        cmd += ["--no-fallback"]
    # oc-tool protects interactive callers with a ~100s SYNC BUDGET: a call
    # whose wait exceeds it detaches and returns a structured error (found
    # live: the §7.7 executor call — a large mechanical-edit prompt —
    # exceeded it while the same prompt standalone raced under it). The
    # controller is NOT an interactive caller: it must be able to wait out
    # the FULL CHAIN worst case (oc-tool retries the next model on failure,
    # so the wait can legitimately be N x timeout), not just one attempt.
    # Budget = timeout * chain-length + slack; capped by subprocess timeout.
    chain_len = 1 if no_fallback else 6
    budget = timeout * chain_len + 90
    env = dict(os.environ)
    env.setdefault("OC_SYNC_BUDGET", str(budget))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=budget + 30,
                           stdin=subprocess.DEVNULL, env=env)
        d = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        return d.get("ok", False), (d.get("text") or ""), d
    except Exception as e:
        return False, f"oc_run failed: {e}", {}


# ---------------------------------------------------------------------------
# Registry (structural ownership — one doc per owner, read off the tree §2.1)
# ---------------------------------------------------------------------------

def cmd_init(args):
    """Enumerate owners straight off the corpus tree and write registry.json."""
    if not CORPUS_DIR.is_dir():
        fail(f"corpus dir not found: {CORPUS_DIR}")
    docs_root = CORPUS_DIR / "docs"
    if not docs_root.is_dir():
        fail(f"docs/ dir not found under corpus: {docs_root_dir_hint()}")

    # Real-tree hygiene (found via the hermes-agent-spine-research dry run):
    # 1. dot-directories (.agents/, .github/, .opencode/...) are agent/harness
    #    config, not corpus documents — they are not ownable artifacts.
    # 2. real trees have duplicate filenames (SKILL.md at two depths) — a bare
    #    stem collides as an owner id, making the second owner unreachable.
    #    Disambiguate structurally: prefix the parent path (readable,
    #    still tree-derived, still deterministic).
    all_docs = [md for md in sorted(docs_root.rglob("*.md"))
                if not any(part.startswith(".")
                           for part in md.relative_to(CORPUS_DIR).parts[:-1])]
    stem_counts = {}
    for md in all_docs:
        stem_counts[md.stem] = stem_counts.get(md.stem, 0) + 1

    owners = []
    idx = 0
    seen_ids = set()
    for md in all_docs:
        rel = md.relative_to(CORPUS_DIR).as_posix()
        oid = md.stem  # structural: file name is the owner id
        if stem_counts[md.stem] > 1:
            parent = os.path.dirname(rel)
            prefix = parent.replace("docs/", "").replace("docs", "").replace("/", "-").strip("-")
            oid = f"{prefix}-{md.stem}" if prefix else md.stem
        base, k = oid, 2
        while oid in seen_ids:
            oid = f"{base}-{k}"
            k += 1
        seen_ids.add(oid)
        owners.append({
            "id": oid,
            "name": oid.replace("-", " ").replace("_", " ").title(),
            "path": rel,
            "core_paths": [rel],
            "parent": os.path.dirname(rel),   # LCA = common parent dir (§2.5)
            "kind": "leaf",
            "port": BASE_PORT + 1 + idx,      # per-owner port (serve mode B)
            "title": first_heading(md),
        })
        idx += 1
    # Root manager (LCA of everything; aggregated-interfaces bundle §2.5)
    owners.append({
        "id": "root",
        "name": "Fleet Root Manager",
        "path": "docs",
        "core_paths": ["glossary.md"],
        "parent": "",
        "kind": "manager",
        "port": BASE_PORT,
        "title": "Aggregated fleet interface (manager)",
    })
    registry = {
        "corpus_dir": str(CORPUS_DIR),
        "cut_depth": 1,
        "shared": {"glossary": "glossary.md"},
        "base_port": BASE_PORT,
        "model": DEFAULT_MODEL,
        "owners": owners,
        "created_at": time.time(),
    }
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    out_json({"ok": True, "registry": str(REGISTRY_PATH),
              "owners": len(owners), "ids": [o["id"] for o in owners]})


def docs_root_dir_hint():
    return f"{CORPUS_DIR} (expected layout: corpus/docs/*.md + corpus/glossary.md)"


def first_heading(md_path):
    try:
        for line in md_path.read_text().splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except Exception:
        pass
    return md_path.stem


def load_registry():
    if not REGISTRY_PATH.exists():
        fail("registry.json not found — run: cov.py init", 2)
    # R8/F22: malformed registry is a structured error, never a traceback
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except json.JSONDecodeError as e:
        fail(f"registry corrupted ({e}) — re-run `cov.py init` or repair it", 2)


def owner_by_id(registry, oid):
    for o in registry["owners"]:
        if o["id"] == oid:
            return o
    return None


# ---------------------------------------------------------------------------
# Bundle compiler (§2.3, §9 Phase 1) — core verbatim + periphery + derived
# ---------------------------------------------------------------------------

PROTOCOL_TEXT = """You are a documentation OWNER agent in a context-ownership fleet.
OPERATING PROTOCOL (all owners share this):
1. Your BUNDLE below contains your owned document VERBATIM (core ring) plus
   cross-references from other documents (periphery ring) and a derived index.
2. Answer from the bundle first. You may read/grep the live corpus at
   {corpus_dir} ONLY to verify or complete something YOUR bundle already
   references (e.g. a periphery pointer, a glossary term). The bundle is a
   prior, not a prison — but it is YOUR prior: fetch only what it points at.
3. QUOTES ARE MANDATORY for findings: every claim about a document must quote
   the document and section. No quote, no finding.
4. "Not in my scope" is a FIRST-CLASS answer, and it is REQUIRED when the
   question is about ANOTHER owner's owned document and your bundle (core,
   periphery, glossary) does not already contain the answer. In that case
   reply exactly NOT_IN_SCOPE plus the one-line reason and which owner doc
   the question belongs to. Do NOT grep the live corpus to answer another
   owner's question out-of-band — that bypasses the fleet's routing and
   audit trail. Never speculate outside your bundle.
5. If the wake-time diff (appended at the tail of the question) shows YOUR OWN
   document changed since your bundle was built, REFUSE to answer: reply
   exactly STALE_REFUSED and instruct the caller to rebuild your bundle
   (cov.py rebuild). Answering from a stale bundle is the worst failure mode.
6. If you find LIVE corpus content that contradicts your bundle, do NOT
   silently reconcile — report it as BUNDLE_CONTRADICTION (highest-signal
   error the system can emit).
7. Term meanings follow the shared glossary. Value ownership/precedence
   follows the glossary precedence table.
"""


def periphery_for(owner, registry, all_docs_text):
    """Deterministic periphery ring (§2.3): passages in OTHER docs that
    reference this owner's doc, plus glossary rows whose value-owner is this
    doc. Auditable — every entry carries its source citation."""
    refs = []
    my_stem = Path(owner["path"]).stem
    for doc in all_docs_text:
        if doc["path"] == owner["path"]:
            continue
        for i, line in enumerate(doc["lines"], 1):
            # reference = explicit doc-name mention or backtick stem mention
            if (my_stem in line) or (f"`{owner['path']}`" in line):
                refs.append({"source": f"{doc['path']}#L{i}", "line": line.strip()})
    glossary_rows = []
    glossary = all_docs_text_glossary(all_docs_text)
    for row in glossary:
        if my_stem in row.get("owner_file", ""):
            glossary_rows.append(row)
    return refs, glossary_rows


def all_docs(registry):
    docs = []
    for o in registry["owners"]:
        p = CORPUS_DIR / o["path"]
        if o["kind"] == "leaf" and p.exists():
            docs.append({"path": o["path"], "lines": p.read_text().splitlines()})
    gl = CORPUS_DIR / "glossary.md"
    if gl.exists():
        docs.append({"path": "glossary.md", "lines": gl.read_text().splitlines()})
    return docs


def all_docs_text_glossary(docs=None):
    """Parse glossary table rows into {term, meaning, owner_file}."""
    docs = docs if docs is not None else all_docs(load_registry())
    rows = []
    for doc in docs:
        if doc["path"] != "glossary.md":
            continue
        for line in doc["lines"]:
            m = re.match(r"^\|\s*\*?\*(.+?)\*?\*\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
            if m and not m.group(1).startswith("Term"):
                term, meaning, owner = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                rows.append({"term": term, "meaning": meaning, "owner_file": owner})
    return rows


def derived_layer_prompt(owner, core_text):
    return f"""You are a CONTEXT COMPILER (builder role). Read the following owned document
completely, then produce a compact DERIVED LAYER for it. Output ONLY markdown with
exactly these three sections:

## Index
A table of every section with a one-line gist (section -> what it establishes).

## Key facts
Every QUANTITATIVE or NORMATIVE fact (limits, SLOs, roles, retention windows,
prices, precedence rules, invariants). One bullet per fact, each with the
section it came from. Include numbers EXACTLY as written.

## External assumptions
Bullets: claims this document makes about OTHER documents (e.g. "assumes
api-spec.md §4 enforces the rate limits defined here"). Each with a pointer.
If none, write "- none".

DOCUMENT ({owner['path']}) BEGINS
{core_text}
DOCUMENT ENDS"""


def build_owner_bundle(owner, registry, docs, use_llm=True):
    """Compile one owner's bundle: Segment A (protocol) is rendered at agent
    generation time; here we build Segment B (core verbatim + periphery +
    derived) and the manifest. Returns manifest dict.
    R6/F6: with use_llm=False, the PRIOR bundle's DERIVED LAYER is preserved
    (a --no-llm rebuild used to replace it with a placeholder and then pin
    corpus.sha over the degraded bundle — §6.1 violation). Builder failures
    flag derived_ok:false in the manifest instead of silently pinning."""
    bdir = BUNDLES_DIR / owner["id"]
    bdir.mkdir(parents=True, exist_ok=True)
    core_path = CORPUS_DIR / owner["path"]
    if owner["kind"] == "manager":
        core_text = render_manager_core(registry, docs)
    else:
        # R8/F22: registry-listed doc deleted — structured error, not a traceback
        if not core_path.exists():
            fail(f"corpus file missing for owner {owner['id']}: {owner['path']} "
                 f"— restore it or re-run `cov.py init`", 3)
        core_text = core_path.read_text()

    refs, glossary_rows = periphery_for(owner, registry, docs)

    derived = None
    derived_ok = True
    if use_llm:
        # R15 (found on the real hermes corpus): the pilot's 120s timeout
        # clipped LEGITIMATELY-SLOW large-doc compiles mid-generation — the
        # chain then retried as "failures" (owner churn: 3 x 120s SIGTERM
        # before a fallback landed). A full ~25K-token doc compiles in
        # ~150-250s on flash-tier. 420s headroom; truncation raised 60K->
        # 120K chars (three real docs silently lost their derived coverage
        # at 60K; 120K chars ~ 30K tokens still fits the 64K window with
        # max_tokens headroom).
        ok, text, _ = oc_run(derived_layer_prompt(owner, core_text[:120000]),
                             timeout=420)
        if ok and text.strip():
            derived = text.strip()
        else:
            derived_ok = False
    if derived is None:
        # preserve the prior derived layer when rebuilding without the LLM
        # (R6/F6); fall back to an honest placeholder + derived_ok flag.
        prior = _prior_derived_layer(bdir)
        if prior:
            derived = prior
        else:
            derived = "(derived layer not yet compiled — run a full `cov.py build` to generate it; the CORE ring above is verbatim and authoritative)"
            derived_ok = False

    parts = [f"# BUNDLE — owner `{owner['id']}` ({owner['title']})",
             ""]
    parts.append("## CORE RING (owned artifact, VERBATIM, authoritative)\n")
    parts.append(f"<!-- core: {owner['path']} -->\n")
    parts.append(core_text)
    parts.append("\n## PERIPHERY RING (references TO this owner from other docs — "
                 "lossy synthesis is acceptable here; quotes are citations)\n")
    if refs:
        for r in refs[:200]:
            parts.append(f"- `{r['source']}`: {r['line']}")
    else:
        parts.append("- (no inbound references found)")
    if glossary_rows:
        parts.append("\n### Glossary rows this owner's doc is value-owner of\n")
        for g in glossary_rows:
            parts.append(f"- **{g['term']}** — {g['meaning'][:160]}")
    parts.append("\n## DERIVED LAYER (compiled index — convenience only; "
                 "the CORE ring above wins on any conflict)\n")
    parts.append(derived)
    bundle_md = "\n".join(parts)
    (bdir / "BUNDLE.md").write_text(bundle_md)

    manifest = {
        "owner": owner["id"],
        "path": owner["path"],
        "kind": owner["kind"],
        "core_inputs": ([owner["path"]] if owner["kind"] == "leaf"
                        else [o["path"] for o in registry["owners"] if o["kind"] == "leaf"] + ["glossary.md"]),
        "periphery_inputs": sorted({r["source"].split("#")[0] for r in refs} | {"glossary.md"}),  # R6/F17: glossary is ALWAYS an input
        "derived_ok": derived_ok,
        "bundle_bytes": len(bundle_md.encode()),
        "core_bytes": len(core_text.encode()),
        "built_at": time.time(),
    }
    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _prior_derived_layer(bdir):
    """Extract the DERIVED LAYER CONTENT from a previously built bundle.
    R8/F24: the old extraction returned everything after the '## DERIVED LAYER'
    marker — including the rest of the HEADER LINE — so every --no-llm rebuild
    re-prepended the header and duplicated its tail (found live: 13 copies
    accumulated in a bundle over one eval). Now skips the header line and
    strips any duplicates the old bug left behind (self-healing)."""
    prior = bdir / "BUNDLE.md"
    if not prior.exists():
        return None
    try:
        text = prior.read_text()
        marker = "## DERIVED LAYER"
        idx = text.find(marker)
        if idx < 0:
            return None
        rest = text[idx + len(marker):]
        nl = rest.find("\n")
        content = rest[nl + 1:].strip() if nl >= 0 else rest.strip()
        header_tail = ("(compiled index — convenience only; the CORE ring "
                       "above wins on any conflict)")
        lines = content.splitlines()
        while lines and lines[0].strip() == header_tail:
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
        return "\n".join(lines).strip() if lines else None
    except Exception:
        return None


def render_manager_core(registry, docs):
    """A manager's bundle is NOT everything below it — it is the aggregated
    public interface of its children (§2.5): titles + one-paragraph gists +
    their key norms, generated deterministically from the headings."""
    parts = ["# Fleet root — aggregated interface of child owners\n",
             "This is a MANAGER bundle: aggregated child interfaces, not full text.",
             "For details, the router should send you to the leaf owner.\n"]
    for o in registry["owners"]:
        if o["kind"] != "leaf":
            continue
        gist = []
        try:
            grab = False
            for line in (CORPUS_DIR / o["path"]).read_text().splitlines():
                if line.startswith("## "):
                    grab = True
                    gist.append(line[3:].strip())
                elif grab and line.strip() and not line.startswith("#"):
                    gist.append(line.strip())
                    if len(gist) >= 3:
                        break
        except Exception:
            pass
        parts.append(f"## Child owner: `{o['id']}` ({o['path']})")
        parts.append("\n".join(f"- {g}" for g in gist[:6]))
        parts.append("")
    return "\n".join(parts)


def write_shared():
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    gl = CORPUS_DIR / "glossary.md"
    if gl.exists():
        (SHARED_DIR / "glossary.md").write_text(gl.read_text())


def generate_owner_agent(owner):
    """Render the serving-layer OpenCode agent for an owner: system prompt =
    Segment A (byte-stable across the fleet) + Segment B (byte-stable per
    owner). Cache-eligible prefix per §13.1. Regenerated on every build;
    returns True when the file content changed (R6b: no change, no write)."""
    protocol = PROTOCOL_TEXT.replace("{corpus_dir}", str(CORPUS_DIR))
    glossary_text = (SHARED_DIR / "glossary.md").read_text() if (SHARED_DIR / "glossary.md").exists() else ""
    bundle = (BUNDLES_DIR / owner["id"] / "BUNDLE.md").read_text()
    body = (protocol
            + "\n## SHARED GLOSSARY (all owners share these term meanings)\n\n"
            + glossary_text
            + "\n\n---\n\n# YOUR BUNDLE (Segment B — core ring verbatim + periphery + derived)\n\n"
            + bundle)
    tools = {"read": True, "glob": True, "grep": True,
             "write": False, "edit": False, "bash": False,
             "todowrite": False, "webfetch": False, "task": False,
             "list": False, "skill": False, "websearch": False}
    tools_yaml = "\n".join(f"  {k}: {str(v).lower()}" for k, v in tools.items())
    agent_md = (f"---\n"
                f"description: \"Context-ownership owner agent for {owner['id']} (serving policy: read-only)\"\n"
                f"mode: primary\n"
                f"temperature: 0.2\n"
                f"tools:\n{tools_yaml}\n"
                f"---\n\n"
                f"<system-prompt>\n{body}\n</system-prompt>\n")
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_DIR / f"cov-{owner['id']}.md"
    # R6b: byte-compare before write — an unconditional rewrite bumps mtime,
    # OpenCode's watcher hot-reloads the agent mid-flight, and asks start
    # returning EMPTY text (found live: 5 silent server reboots). Identical
    # bytes → no write → no reload. Returns True when the file changed.
    if path.exists() and path.read_text() == agent_md:
        return False
    path.write_text(agent_md)
    return True


def cmd_build(args):
    """Compile bundles for --owner (default all). --no-llm skips the derived
    layer builder call (core+periphery still verbatim/deterministic).
    F-18: refuses to build on a dirty corpus working tree unless --force —
    the bundle pins corpus.sha=HEAD but compiles working-tree bytes."""
    clean = corpus_tree_clean()
    if clean is not True and not getattr(args, "force", False):
        rc, st, _ = git(["status", "--porcelain"])
        untracked_hint = ""
        rcu, outu, _ = git(["ls-files", "--others", "--exclude-standard"])
        if rcu == 0 and outu.strip():
            untracked_hint = ("\n(untracked files present — a NEW doc needs `cov.py init` "
                              "to become an owner; untracked scratch files should be "
                              "ignored or removed)")
        fail("corpus working tree is dirty — a build would pin HEAD's sha over "
             "foreign bytes. Commit/restore the corpus first (or --force; the "
             "bundle will still be content-verified by `status`).\n" + (st or "")[:400]
             + untracked_hint, 4)
    _lock = single_writer_lock()
    registry = load_registry()
    docs = all_docs(registry)
    write_shared()
    targets = [owner_by_id(registry, args.owner)] if args.owner else registry["owners"]
    if args.owner and not targets[0]:
        fail(f"unknown owner: {args.owner} (see cov.py owners)", 3)
    built = []
    agents_changed = []

    def build_one(o):
        m = build_owner_bundle(o, registry, docs, use_llm=not args.no_llm)
        # corpus.sha advances ONLY after a successful rebuild (§6.1)
        (BUNDLES_DIR / o["id"] / "corpus.sha").write_text(corpus_sha() or "")
        if generate_owner_agent(o):
            agents_changed.append(o["id"])
        return {"owner": o["id"], "core_bytes": m["core_bytes"],
                "bundle_bytes": m["bundle_bytes"],
                "periphery_refs": len(m["periphery_inputs"])}

    # R15b: optional build parallelism (--workers). The build holds the
    # single-writer lock for the WHOLE command; per-owner mutation surfaces
    # (bundles/<id>/, agents/cov-<id>.md, corpus.sha) are disjoint per owner,
    # so N workers just overlap the oc_run waits (cold-process latency, not
    # shared state). list.append under the GIL is atomic. map() preserves
    # order. No live server may be running while agents are rewritten —
    # hot-reload poisoning (restart_running_servers at the end handles the
    # safe case: it verifies a fresh, unpoisoned listener).
    workers = int(getattr(args, "workers", 1) or 1)
    if workers > 1 and len(targets) > 1 and not args.no_llm:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            built = list(pool.map(build_one, targets))
    else:
        built = [build_one(o) for o in targets]
    # R6b: changed agent files under a live server = hot-reload poison; restart
    restarted = restart_running_servers() if agents_changed else []
    out_json({"ok": True, "built": built, "corpus_sha": corpus_sha(),
              **({"agents_changed": agents_changed,
                  "servers_restarted": restarted} if agents_changed else {})})


# ---------------------------------------------------------------------------
# Coherence (§6) — valid / stale-clean / stale-dirty / invalid
# ---------------------------------------------------------------------------

def coherence_state(owner):
    """Deterministic coherence state for one owner (§6.2). R6/F3: git failure
    or a pinned sha that no longer resolves yields 'unverified' — never a
    false 'valid' (a rebase/rewrite of corpus history must blind nothing)."""
    bdir = BUNDLES_DIR / owner["id"]
    sha_file = bdir / "corpus.sha"
    bundle_file = bdir / "BUNDLE.md"
    if not bundle_file.exists() or not sha_file.exists():
        return "unbuilt", None, []
    base = sha_file.read_text().strip() or None
    changed = corpus_dirty_files(base)
    if changed is None:
        return "unverified", base, []
    core_changed = [f for f in changed if f in owner["core_paths"]]
    manifest_path = bdir / "manifest.json"
    periphery_inputs = []
    if manifest_path.exists():
        # R8/F22: a truncated manifest degrades to invalid, never crashes status
        try:
            periphery_inputs = json.loads(manifest_path.read_text()).get("periphery_inputs", [])
        except json.JSONDecodeError:
            return "invalid", base, ["(manifest corrupted)"]
    periphery_changed = [f for f in changed if f in periphery_inputs]
    if core_changed:
        return "stale-dirty", base, core_changed
    if periphery_changed:
        return "invalid", base, periphery_changed
    if changed:
        return "stale-clean", base, changed
    # F-19: git says clean, but git only sees COMMITS — the bundle was
    # compiled from working-tree BYTES. Content-verify the verbatim CORE ring
    # so a bundle built on a dirty tree (pinned sha lying about its content)
    # can never report 'valid'.
    cv = bundle_core_matches(owner)
    if cv is None:
        return "unverified", base, []
    if cv:
        return "invalid", base, cv
    return "valid", base, []


def cmd_status(args):
    registry = load_registry()
    states = {}
    for o in registry["owners"]:
        state, base, changed = coherence_state(o)
        states[o["id"]] = {"state": state, "corpus_sha": base,
                           "changed_inputs": changed,
                           "port": o["port"], "kind": o["kind"]}
    # R6/F3: 'unverified' (git failure / unresolvable pinned sha) must fail
    # the fleet — a corpus history rewrite must never look coherent.
    fleet_valid = all(s["state"] in ("valid", "stale-clean") for s in states.values())
    unverified = [k for k, s in states.items() if s["state"] == "unverified"]
    out_json({"ok": True, "corpus_sha": corpus_sha(), "fleet_coherent": fleet_valid,
              "owners": states,
              **({"warning_unverified": unverified,
                  "hint": "git could not diff some bundles against their pinned sha — rebuild the fleet or repair corpus history"} if unverified else {})})


def cmd_rebuild(args):
    """Incremental rebuild: git diff -> path prefix -> core owners (stale-dirty)
    + reverse-dep periphery (invalid). Deterministic graph traversal (§9 P1).
    F-18: same dirty-tree refusal as build (the eval's per-plant rebuild runs
    with the plant COMMITTED, so it is unaffected)."""
    clean = corpus_tree_clean()
    if clean is not True and not getattr(args, "force", False):
        rc, st, _ = git(["status", "--porcelain"])
        fail("corpus working tree is dirty — refusing to rebuild (pin would lie "
             "about content). Commit/restore first (or --force).\n" + (st or "")[:400], 4)
    _lock = single_writer_lock()
    registry = load_registry()
    write_shared()   # R8/#6: rebuild must refresh the shared glossary too —
                     # agents embed it; a stale copy survives rebuilds otherwise
    rebuilt = []
    agents_changed = []
    for o in registry["owners"]:
        state, base, changed = coherence_state(o)
        # R10: unverified must also rebuild — a fresh clone (where the pinned
        # sha was squashed in merge) leaves every bundle 'unverified'; rebuild
        # is the documented recovery path and must actually recover.
        if state in ("stale-dirty", "invalid", "unverified", "unbuilt"):
            m = build_owner_bundle(o, registry, all_docs(registry), use_llm=not args.no_llm)
            (BUNDLES_DIR / o["id"] / "corpus.sha").write_text(corpus_sha() or "")
            if generate_owner_agent(o):
                agents_changed.append(o["id"])
            rebuilt.append({"owner": o["id"], "was": state, "changed": changed})
    # R6b: restart live servers so they pick up changed agents cleanly
    restarted = restart_running_servers() if agents_changed else []
    out_json({"ok": True, "rebuilt": rebuilt,
              **({"agents_changed": agents_changed,
                  "servers_restarted": restarted} if agents_changed else {}),
              **({} if rebuilt else {"hint": "fleet already coherent"})})


# ---------------------------------------------------------------------------
# Serving layer (§7, §13.4) — owners are controller-managed sessions on
# `opencode serve`. Mode A: one shared server, owner selected per-message via
# the `agent` param. Mode B (--per-owner-ports): one server per owner port.
# ---------------------------------------------------------------------------

def mem_available_mb():
    """Free memory (MB) — the serve layer refuses to start servers under
    pressure (the kit's OOM lesson: 8 agents + serve + Chrome killed the box).
    Fails CLOSED (returns 0) when /proc/meminfo is unreadable."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def start_server(port, log_path, api_key=None):
    """Double-forked `opencode serve` with cwd=PROJECT_DIR (agent discovery).
    R14: api_key forces the PROBE-SELECTED OpenRouter key into the server env
    (None = keep whatever .env carries; zai selections need no key).
    Returns pid or None."""
    pid = os.fork()
    if pid > 0:
        # parent: wait briefly for server readiness in the child's stub below
        for _ in range(30):
            time.sleep(1)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/global/health", timeout=1) as r:
                    if r.status == 200:
                        return read_pid(port) or True
            except Exception:
                pass
        return None
    # child
    os.setsid()
    if os.fork():
        os._exit(0)
    # grandchild
    try:
        sys.stdout.flush(); sys.stderr.flush()
        lf = open(log_path, "ab")
        os.dup2(lf.fileno(), 1); os.dup2(lf.fileno(), 2)
        devnull = os.open("/dev/null", os.O_RDWR)
        os.dup2(devnull, 0)
        os.chdir(str(PROJECT_DIR))
        os.environ["PATH"] = f"{Path.home()}/.npm-global/bin:" + os.environ.get("PATH", "")
        # R8/P0-2: the server MUST carry the provider key even when the parent
        # process imported this module without running main() (found live: a
        # module-import restart exec'd a KEYLESS opencode serve — every eval
        # ask 401'd 'Missing Authentication header' and returned empty text).
        # Keys are FORCED from .env (setdefault alone can't fix a stale parent
        # value, and an empty-string parent value would survive it).
        load_env()
        env_file = KIT_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().startswith("OPENROUTER_API_KEY"):
                        os.environ[k.strip()] = v.strip()
        # R14: the probe-selected key WINS over whatever .env carries — keys
        # expire mid-flight and .env is not refreshed on key rotation (found
        # live: eval v8's whole sharded arm was empty-200s on a dead key).
        if api_key:
            os.environ["OPENROUTER_API_KEY"] = api_key
        Path(f"/tmp/cov-serve-{port}.pid").write_text(str(os.getpid()))
        os.execvp("opencode", ["opencode", "serve", "--port", str(port), "--hostname", "127.0.0.1"])
    except Exception:
        Path(f"/tmp/cov-serve-{port}.pid").unlink(missing_ok=True)
        os._exit(1)


def read_pid(port):
    pf = Path(f"/tmp/cov-serve-{port}.pid")
    if pf.exists():
        try:
            return int(pf.read_text().strip())
        except Exception:
            return None
    return None


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def server_healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/global/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def listener_pid(port):
    """Resolve the ACTUAL process holding 127.0.0.1:<port> via ss.
    R8/P0: the pid file is written pre-exec by the launcher and goes stale
    whenever a start fails to bind (found live: 15 ServeErrors while
    'restarted: true' was reported every time — kills were skipping a dead
    pid while the old server held the port).
    Requires iproute2 (`ss`); returns None when unavailable — callers degrade
    honestly (restarts report not-verified; evals abort rather than trust an
    unverifiable serving layer)."""
    try:
        r = subprocess.run(["ss", "-tlnp", f"sport = :{port}"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        m = re.search(r"pid=(\d+)", r.stdout or "")
        return int(m.group(1)) if m else None
    except Exception:
        return None


def wait_port_free(port, timeout_s=20):
    """True once nothing listens on <port> (health AND ss both silent)."""
    for _ in range(timeout_s * 2):
        if listener_pid(port) is None and not server_healthy(port):
            return True
        time.sleep(0.5)
    return False


def kill_listener(port, timeout_s=15):
    """Kill whatever REALLY holds <port> (pid file first, then ss resolution),
    SIGTERM -> SIGKILL, then wait for the port to be free. Returns
    (killed: bool, detail: str)."""
    pids = []
    fp = read_pid(port)
    lp = listener_pid(port)
    if fp and pid_alive(fp):
        pids.append(fp)
    if lp and lp not in pids and pid_alive(lp):
        pids.append(lp)
    if not pids:
        # nothing to kill by pid — port may still be held by an unresolvable
        # process; report honestly instead of pretending
        if listener_pid(port) is None:
            return True, "no listener"
        return False, f"port {port} held by unresolvable pid"
    for pid in pids:
        try:
            os.kill(pid, 15)
        except Exception:
            pass
    for _ in range(timeout_s * 2):
        if listener_pid(port) is None:
            return True, f"SIGTERM {pids}"
        time.sleep(0.5)
    for pid in pids:
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    if wait_port_free(port, 10):
        return True, f"SIGKILL {pids}"
    return False, f"port {port} still held after SIGKILL {pids}"


def restart_running_servers():
    """R6b/R8: agent files changed under a LIVE server — OpenCode's watcher
    hot-reloads agents mid-flight and asks silently return empty text (found
    live: eval v5's 0/12). The reliable pickup is a clean restart of exactly
    the servers that were running (shared 4200 and/or per-owner ports).
    R8/P0: every restart is now VERIFIED — the real listener is resolved via
    ss (not the trust-me pid file), the port must actually go free, the new
    server must be a DIFFERENT pid, and failures are reported honestly so a
    poisoned eval aborts instead of recording empty findings.
    R14: each restart RE-PROBES the provider chain and forces the currently
    selected key — per-plant rebuild restarts during an eval are exactly
    where a mid-flight key death gets healed (v8's failure mode)."""
    sel = None
    try:
        sel = select_provider(force=True)
        if sel.get("model"):
            record_provider(sel)
    except Exception:
        sel = None
    registry = load_registry()
    running = []
    seen_ports = set()
    if server_healthy(BASE_PORT):
        running.append(("shared", BASE_PORT))   # shared mode serves ALL owners
        seen_ports.add(BASE_PORT)
    for o in registry["owners"]:
        # dedupe: root's port IS BASE_PORT — already covered as 'shared' above
        if o["port"] in seen_ports:
            continue
        if server_healthy(o["port"]):
            running.append((o["id"], o["port"]))
            seen_ports.add(o["port"])
    out = []
    for name, port in running:
        old_pid = listener_pid(port)
        killed, detail = kill_listener(port)
        if not killed:
            out.append({"server": name, "port": port, "restarted": False,
                        "error": f"could not free port: {detail}"})
            continue
        Path(f"/tmp/cov-serve-{port}.pid").unlink(missing_ok=True)
        r = start_server(port, f"/tmp/cov-serve-{port}.log",
                         api_key=(sel or {}).get("api_key"))
        new_pid = listener_pid(port)
        verified = bool(r) and server_healthy(port) and new_pid is not None \
            and new_pid != old_pid
        out.append({"server": name, "port": port,
                    "restarted": bool(r), "verified": verified,
                    "old_pid": old_pid, "new_pid": new_pid,
                    "provider": (sel or {}).get("provider"),
                    "model": (sel or {}).get("model"),
                    **({} if verified else
                       {"error": "restart not verified (pid unchanged or "
                                 "port not healthy) — serving agents may be "
                                 "poisoned; do not trust this server"})})
    return out


def cmd_serve(args):
    registry = load_registry()
    state = load_state()
    action = args.serve_action
    if action == "probe":
        # R14: manual provider probe — shows every (key, model) candidate's
        # live status and what the controller WOULD select right now.
        sel = select_provider(force=True)
        if sel.get("model"):
            record_provider(sel)
        out_json({"ok": True, "selection":
                  {k: sel.get(k) for k in ("provider", "key_alias", "model",
                                           "chain")},
                  "probes": sel.get("probes"),
                  "zai_proxy_healthy": zai_proxy_healthy()})
        return
    if action == "start":
        targets = []
        if args.per_owner_ports:
            targets = [(o["id"], o["port"]) for o in registry["owners"]]
        else:
            targets = [("shared", BASE_PORT)]
        if args.owner:
            o = owner_by_id(registry, args.owner)
            if not o:
                fail(f"unknown owner {args.owner}", 3)
            targets = [(o["id"], o["port"])]
        # R14: probe BEFORE starting so the server is born with a live key
        sel = select_provider(force=True)
        if sel.get("model"):
            record_provider(sel)
        started = []
        for name, port in targets:
            if server_healthy(port):
                started.append({"server": name, "port": port, "already_running": True})
                continue
            need_mb = 500
            if mem_available_mb() < need_mb:
                started.append({"server": name, "port": port, "error":
                                f"insufficient memory ({mem_available_mb()}MB avail, need ~{need_mb}MB) — stop other servers first"})
                continue
            log = f"/tmp/cov-serve-{port}.log"
            r = start_server(port, log, api_key=sel.get("api_key"))
            pid = read_pid(port)
            started.append({"server": name, "port": port,
                            "started": bool(r), "pid": pid, "log": log})
            time.sleep(1)
        state["servers"] = {n: p for n, p in targets}
        save_state(state)
        any_up = any(s.get("started") or s.get("already_running") for s in started)
        out_json({"ok": any_up,
                  "mode": "per-owner-ports" if args.per_owner_ports else "shared",
                  "provider": {k: (load_state().get("provider") or {}).get(k)
                               for k in ("provider", "key_alias", "model", "chain")},
                  "servers": started})
        if not any_up:
            sys.exit(6)   # R8/#5: 'serve start && ask' must not proceed on failure
    elif action == "status":
        servers = []
        for o in registry["owners"]:
            healthy = server_healthy(o["port"])
            servers.append({"owner": o["id"], "port": o["port"], "healthy": healthy,
                            "pid": read_pid(o["port"])})
        out_json({"ok": True, "servers": servers,
                  "shared_port": BASE_PORT,
                  "shared_healthy": server_healthy(BASE_PORT),
                  "provider": load_state().get("provider"),
                  "zai_proxy_healthy": zai_proxy_healthy()})
    elif action == "stop":
        # R6b: --owner scopes the stop to ONE server (this flag used to parse
        # then stop EVERYTHING — killed a live eval mid-run; eval v3's 0/12).
        targets = registry["owners"] + [{"id": "shared", "port": BASE_PORT}]
        if args.owner:
            if args.owner == "shared":
                targets = [{"id": "shared", "port": BASE_PORT}]
            else:
                o = owner_by_id(registry, args.owner)
                if not o:
                    fail(f"unknown owner {args.owner} (or 'shared')", 3)
                targets = [o]
        stopped = []
        for o in targets:
            killed, detail = kill_listener(o["port"])
            Path(f"/tmp/cov-serve-{o['port']}.pid").unlink(missing_ok=True)
            stopped.append({"server": o["id"], "port": o["port"],
                            "stopped": killed,
                            **({} if killed else {"error": detail})})
        state.pop("servers", None)
        save_state(state)
        out_json({"ok": True, "stopped": stopped})


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Inquiry API (§7.4) — per-inquiry sessions; agent param selects the owner
# ---------------------------------------------------------------------------

def owner_port(registry, oid):
    o = owner_by_id(registry, oid)
    if not o:
        fail(f"unknown owner: {oid}", 3)
    # prefer the owner's own port if healthy, else the shared server
    if server_healthy(o["port"]):
        return o["port"]
    if server_healthy(BASE_PORT):
        return BASE_PORT
    fail("no serve server running — start with: cov.py serve start", 7)


def stale_guard(owner):
    """Hard protocol rule (§6.2): refuse to answer while stale-dirty.
    R6/F13: also refuse UNBUILT owners (asking would 404 on a nonexistent
    agent) and surface 'unverified' instead of silently proceeding."""
    state, base, changed = coherence_state(owner)
    return state, changed


def diff_injection(owner, changed):
    """Segment C — wake-time diff at the TAIL, never the head (§13.1)."""
    return ("\n\n[wake-time diff: files changed since your bundle was built — "
            f"{', '.join(changed)}. If any is YOUR OWN core document, you must "
            "refuse per protocol rule 5.]")


def http_json(method, url, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def split_model_id(model):
    """'openrouter/z-ai/glm-5.3-flash' -> ('openrouter', 'z-ai/glm-5.3-flash').
    R6/F11: hardcoded providerID broke any non-openrouter COV_MODEL."""
    if model.startswith("openrouter/"):
        return "openrouter", model[len("openrouter/"):]
    if "/" in model:
        prov, mid = model.split("/", 1)
        return prov, mid
    return "openrouter", model


def cov_ask_owner(oid, question, session=None, timeout=120, registry=None,
                  enforce_staleness=True, extra_tail="", model_chain=None):
    """Send one inquiry to one owner. Returns dict {ok, text, session_id,
    state, refused}. R6/F12: all transport failures return structured JSON
    errors (never a traceback) per the harness's error contract.
    R14: the ask walks the provider's MODEL CHAIN — the model is carried
    per-message, so a dead model/key is retried on the next candidate without
    a server restart. A caller-supplied session keeps continuation semantics
    (all chain attempts in that one session); fresh asks get a fresh session
    per attempt so a half-failed attempt can't poison the retry."""
    registry = registry or load_registry()
    owner = owner_by_id(registry, oid)
    port = owner_port(registry, oid)
    state, changed = stale_guard(owner)
    if enforce_staleness and state in ("stale-dirty", "unbuilt", "unverified"):
        if state == "stale-dirty":
            return {"ok": False, "refused": True, "state": state,
                    "error": (f"owner {oid} is STALE-DIRTY (core changed: {changed}) — "
                              "answering is forbidden; run: cov.py rebuild")}
        if state == "unbuilt":
            return {"ok": False, "refused": True, "state": state,
                    "error": f"owner {oid} has no bundle built yet — run: cov.py build"}
        return {"ok": False, "refused": True, "state": state,
                "error": (f"owner {oid} coherence is UNVERIFIED (git could not diff "
                          "against its pinned sha) — repair corpus history or rebuild")}
    tail = diff_injection(owner, changed) if changed else ""
    tail += extra_tail

    # R14: walk the provider's model chain (selection comes from the serving
    # state written by serve start / probe / ensure_provider / restarts).
    chain = list(model_chain or provider_chain())
    last_err = None
    for attempt, model_id in enumerate(chain):
        sid = session
        try:
            if not sid:
                sess = http_json("POST", f"http://127.0.0.1:{port}/session",
                                 {"title": f"cov-{oid}-{int(time.time())}-{attempt}"},
                                 timeout=10)
                sid = sess["id"]
            prov, mid = split_model_id(model_id)
            resp = http_json("POST", f"http://127.0.0.1:{port}/session/{sid}/message",
                             {"agent": f"cov-{oid}",
                              "model": {"providerID": prov, "modelID": mid},
                              "parts": [{"type": "text", "text": question + tail}]},
                             timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode(errors="replace")[:300]
            except Exception:
                pass
            last_err = f"HTTP {e.code} {detail} [model {model_id}]"
            continue   # next model in the chain
        except Exception as e:
            last_err = f"{e} [model {model_id}]"
            continue
        text = "\n".join(p.get("text", "") for p in resp.get("parts", [])
                         if p.get("type") == "text")
        # R8/F21: an empty 200 with zero output tokens is the silent failure
        # mode (agent hot-reload under a live server, or a swallowed 401/402
        # — found live: a whole eval recorded ok:true/text:"" everywhere).
        # R14: retry the next model instead of giving up — that empty-200 is
        # exactly what a dead provider pair looks like through the server.
        toks = (resp.get("info") or {}).get("tokens") or {}
        if not text.strip() and not (toks.get("output") or 0):
            last_err = ("EMPTY text with zero output tokens on model "
                        f"{model_id} — serving layer poisoned or provider dead")
            continue
        refused = "STALE_REFUSED" in text or "NOT_IN_SCOPE" in text
        return {"ok": True, "text": text.strip(), "session_id": sid,
                "state": state, "refused": refused, "model": model_id,
                "tokens": (resp.get("info") or {}).get("tokens")}
    return {"ok": False, "state": state,
            "empty_response": ("EMPTY text" in (last_err or "")),
            "model_chain": chain,
            "error": (f"owner ask failed on every model in the chain "
                      f"({', '.join(chain)}): {last_err}"),
            "hint": "provider layer down? run: cov.py serve probe"}


def cmd_ask(args):
    registry = load_registry()
    r = cov_ask_owner(args.owner, args.prompt, session=args.session,
                      timeout=args.timeout, registry=registry)
    out_json(r)


# ---------------------------------------------------------------------------
# Router (§2.5) — task -> paths -> owners by prefix; LCA rule
# ---------------------------------------------------------------------------

STOPWORDS = set(("a an the is are of for to in on at what which how does do can "
                 "where when who whom this that these those and or not with from by as it its "
                 "their there be been was were will would should could plan add change update "
                 "fix remove delete create make need want tell me about describe explain").split())


def lexical_scores(task, registry):
    """Deterministic first pass: score each doc by term overlap."""
    words = [w for w in re.findall(r"[a-z0-9_\-]+", task.lower()) if w not in STOPWORDS]
    scores = {}
    for o in registry["owners"]:
        if o["kind"] != "leaf":
            continue
        try:
            text = (CORPUS_DIR / o["path"]).read_text().lower()
        except Exception:
            continue
        s = sum(1 for w in words if w in text)
        scores[o["id"]] = s
    return sorted(scores.items(), key=lambda kv: -kv[1])


def resolve_route_owners(scores):
    """Pure lexical-dominant decision: ([owner], method) or (None, None) when
    the task must fall through to the LLM router. Extracted from cmd_route for
    unit testing (peer review 9-b #1: end-to-end route tests hit lexical ties,
    fell to the LLM router, and flaked)."""
    top = scores[:3]
    if top and top[0][1] > 0 and (len(top) < 2 or top[0][1] > 2 * top[1][1]):
        return [top[0][0]], "lexical-dominant"
    return None, None


def cmd_route(args):
    registry = load_registry()
    task = args.task
    scores = lexical_scores(task, registry)
    top = scores[:3]
    owners, method = resolve_route_owners(scores)
    if owners is None:
        # router LLM resolves task -> paths (the only semantic step §2.5)
        listing = "\n".join(f"- {o['id']} ({o['path']}): {o['title']}" for o in registry["owners"] if o["kind"] == "leaf")
        prompt = (f"Router: given the task, pick the AFFECTED documents (1-3) from this list. "
                  f"Reply with ONLY a JSON array of owner ids, e.g. [\"api-spec\"].\n"
                  f"Task: {task}\nDocuments:\n{listing}")
        ok, text, _ = oc_run(prompt, timeout=60)
        m = re.search(r"\[.*?\]", text, re.S)
        try:
            owners = json.loads(m.group(0)) if m else []
        except Exception:
            owners = []
        owners = [o for o in owners if owner_by_id(registry, o)]
        method = "llm"
        if not owners:  # fallback to lexical top-2
            owners = [k for k, v in top[:2] if v > 0]
            method = "lexical-fallback"
    lca = compute_lca(registry, owners)
    out_json({"ok": True, "task": task, "method": method,
              "lexical_top": top, "owners": owners,
              "lca": lca, "coordination": "direct" if len(owners) == 1 else f"manager:{lca}"})


def compute_lca(registry, owner_ids):
    """LCA = deepest common parent directory of affected paths (§2.5).
    With a flat docs/ tree, ANY 2+ distinct owners share docs/ as parent →
    the fleet root manager coordinates. Deeper trees would walk parent dirs."""
    if not owner_ids:
        return None
    parents = {owner_by_id(registry, oid)["parent"]
               for oid in owner_ids if owner_by_id(registry, oid)}
    if len(parents) == 1 and "" not in parents:
        # single non-root parent level — that level's manager (only 'root'
        # exists in this registry's depth)
        return "root"
    return "root"


def cmd_task(args):
    """Route then execute: single leaf -> direct ask; multi -> ask each
    affected owner + LCA manager synthesizes (fan-out intelligence, single
    coherent answer). NOTE (R6/F15): the manager synthesis call exempts the
    root manager from the staleness guard — its inputs are the FRESH child
    answers composed at ask time; documented deviation from §6.2."""
    registry = load_registry()
    try:
        task_proc = subprocess.run(
            [sys.executable, __file__, "--project", str(PROJECT_DIR),
             "route", args.task],
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        fail("router timed out after 120s (LLM router unreachable?)", 5)
    try:
        route = json.loads(task_proc.stdout.strip().splitlines()[-1])
    except Exception:
        fail(f"router failed: {task_proc.stdout[-300:]} {task_proc.stderr[-300:]}", 3)
    owners = route.get("owners") or []
    if not owners:
        fail("router could not resolve any owner for this task", 3, route=route)
    answers = []
    for oid in owners:
        r = cov_ask_owner(oid, args.task, timeout=args.timeout, registry=registry)
        answers.append({"owner": oid, "ok": r.get("ok"), "text": r.get("text", "")[:4000],
                        "state": r.get("state")})
    if len(answers) == 1:
        out_json({"ok": True, "route": route, "answers": answers,
                  "synthesis": None})
        return
    # LCA synthesis (manager composes child answers)
    synth_q = ("As the coordinating manager, synthesize ONE answer to the task "
               f"from these child-owner answers. Cite owners. Task: {args.task}\n\n"
               + "\n\n".join(f"--- owner {a['owner']} ---\n{a['text']}" for a in answers))
    r = cov_ask_owner(route["lca"], synth_q, timeout=args.timeout, registry=registry,
                      enforce_staleness=False)
    out_json({"ok": True, "route": route, "answers": answers,
              "synthesis": {"manager": route["lca"], "text": r.get("text", "")[:6000]}})


# ---------------------------------------------------------------------------
# Standing review board (§8.4) — broadcast change to ALL owners, fixed form,
# quotes mandatory, aggregator dedupes + ranks. No routing: coverage IS the point.
# ---------------------------------------------------------------------------

REVIEW_FORM = """A change has been proposed to the corpus. Review it against YOUR OWN owned
document (and periphery) ONLY.

CHANGED FILE: {changed_file}
BASE SHA: {base}
THE DIFF (or new content):
```diff
{diff}
```

Answer with ONLY a JSON object (no prose outside it):
{{
  "findings": [
    {{
      "type": "contradiction|invalidates_assumption|term_differs|duplicate",
      "severity": "high|medium|low",
      "quote_mine": "EXACT quote from YOUR document that the change conflicts with",
      "quote_change": "exact phrase from the change that conflicts",
      "explanation": "one sentence"
    }}
  ],
  "in_scope": true|false,
  "notes": "anything else worth flagging, or empty"
}}

Rules: no quote, no finding. "Not in my scope" = {{"findings": [], "in_scope": false, "notes": ""}}.
Term meanings follow the shared glossary; value ownership follows the precedence table."""


def parse_review_json(text):
    """Owners must return strict JSON; tolerate code fences."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def cmd_review(args):
    registry = load_registry()
    changed_file = args.file
    # R6/F9: path-traversal guard — --file must resolve INSIDE the corpus
    resolved = (CORPUS_DIR / changed_file).resolve()
    if not str(resolved).startswith(str(CORPUS_DIR.resolve()) + os.sep):
        fail(f"--file must be a corpus-relative path (got: {changed_file})", 3)
    # R6/F8: --owner must exist
    if args.owner and not owner_by_id(registry, args.owner):
        fail(f"unknown owner: {args.owner} (see cov.py owners)", 3)
    base = args.base or last_commit_sha_for(changed_file)
    # R6/F4: include UNCOMMITTED worktree edits — reviewing only the last
    # commit's diff while the working tree carries newer edits silently
    # reviews the WRONG change. Diff base-vs-worktree, not base..HEAD.
    if base:
        rc, diff, _ = git(["diff", base, "--", changed_file])
    else:
        rc, diff, _ = git(["status"])
        diff = f"(new/uncommitted file)\n{resolved.read_text()[:4000]}" if resolved.exists() else ""
    if not diff.strip():
        fail(f"empty diff for {changed_file} against base {base}", 3)

    form = REVIEW_FORM.format(changed_file=changed_file, base=base or "none",
                              diff=diff[:12000])
    owners = [o for o in registry["owners"] if o["kind"] == "leaf"] if not args.owner else [owner_by_id(registry, args.owner)]

    def review_one(o):
        """One owner's review; NOTE: the changed doc's OWN owner reviews too
        (self-consistency), but its bundle is stale for its own doc — the
        §7.7 self-invalidation case; we ask it with the diff at the tail."""
        try:
            r = cov_ask_owner(o["id"], form, timeout=args.timeout, registry=registry,
                              enforce_staleness=False)
            parsed = parse_review_json(r.get("text", "")) if r.get("ok") else None
            err = None if r.get("ok") else (r.get("error") or "")[:200]
        except Exception as e:
            r, parsed, err = {}, None, f"owner ask failed: {e}"[:200]
        if parsed is None:
            raw = (r.get("text") or "") if isinstance(r, dict) else ""
            if "STALE_REFUSED" in raw:
                parsed = {"findings": [], "in_scope": None,
                          "notes": "SELF_STALE — owner refused per protocol rule 5 (its own doc changed); rebuild before it can self-review"}
            else:
                parsed = {"findings": [], "in_scope": None,
                          "notes": f"UNPARSEABLE ({err or raw[:150]})"}
        return {"owner": o["id"], "state": (r.get("state") if isinstance(r, dict) else None),
                "in_scope": parsed.get("in_scope"),
                "findings": parsed.get("findings", []),
                "notes": parsed.get("notes", ""),
                "transport_error": (not r.get("ok")) if isinstance(r, dict) else True,
                "ask_error": err}

    # §8.4 fan-out: all owners in parallel waves (bounded — RAM and provider
    # concurrency are real). CONCURRENCY IS AN EXECUTION-MODEL KNOB, fully
    # separate from the ownership lanes: every owner receives the change no
    # matter how many live execution slots exist (the sleep-vs-live model in
    # OPERATIONS.md). 3 live slots on a 4 GiB box is the calibrated default.
    conc = getattr(args, "concurrency", 3) or 3
    from concurrent.futures import ThreadPoolExecutor
    results = []
    with ThreadPoolExecutor(max_workers=conc) as pool:
        for r in pool.map(review_one, owners):
            results.append(r)

    # Aggregation follows the tree (§8.4): flat for small fleets, hierarchical
    # (squad-manager compression + conservation check) when the fleet is big
    # enough that the root would otherwise drown. 'auto' = hierarchical when
    # leaf count exceeds AGG_THRESHOLD. Never sampling — enforced.
    agg_mode = getattr(args, "agg", "auto") or "auto"
    n_leaves = sum(1 for o in owners if o.get("kind") == "leaf")
    use_hier = (agg_mode == "hier") or (agg_mode == "auto" and n_leaves > AGG_THRESHOLD)
    if use_hier:
        report = hierarchical_aggregate(results, registry)
    else:
        report = aggregate_review(results)
    out_json({"ok": True, "changed_file": changed_file, "base": base,
              "owners_queried": len(results), "aggregation": ("hierarchical" if use_hier else "flat"),
              "report": report,
              "raw": results})


def last_commit_sha_for(path):
    rc, out, _ = git(["log", "-2", "--format=%H", "--", path])
    shas = out.splitlines()
    if len(shas) >= 2:
        return shas[1]  # parent of HEAD's latest change to this file
    return None


def aggregate_review(results):
    """Dedupe by quoted passage, rank by severity (§8.4 step 4)."""
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    type_rank = {"contradiction": 0, "invalidates_assumption": 1,
                 "term_differs": 2, "duplicate": 3}
    seen_quotes = {}
    findings = []
    for r in results:
        for f in (r.get("findings") or []):
            # R9 (round-2): a parseable-but-malformed findings item (string,
            # dict-of-other-shape, scalar) must skip, never traceback
            if not isinstance(f, dict):
                continue
            q = (f.get("quote_mine") or "").strip()
            if not q:
                continue  # no quote, no finding (§8.5)
            key = q[:120].lower()
            if key in seen_quotes:
                seen_quotes[key]["owners"].append(r["owner"])
                continue
            entry = {"owners": [r["owner"]], "type": f.get("type"),
                     "severity": f.get("severity", "low"),
                     "quote_mine": q[:400], "quote_change": (f.get("quote_change") or "")[:200],
                     "explanation": (f.get("explanation") or "")[:300]}
            seen_quotes[key] = entry
            findings.append(entry)
    findings.sort(key=lambda f: (sev_rank.get(f["severity"], 9),
                                 type_rank.get(f["type"], 9)))
    in_scope = [r["owner"] for r in results if r.get("in_scope")]
    out_of_scope = [r["owner"] for r in results if r.get("in_scope") is False]
    unparseable = [r["owner"] for r in results
                   if r.get("in_scope") is None and "SELF_STALE" not in (r.get("notes") or "")]
    self_stale = [r["owner"] for r in results if "SELF_STALE" in (r.get("notes") or "")]
    transport_errors = [r["owner"] for r in results if r.get("transport_error")]
    return {"findings": findings, "finding_count": len(findings),
            "high_severity": sum(1 for f in findings if f["severity"] == "high"),
            "owners_in_scope": in_scope, "owners_out_of_scope": out_of_scope,
            "owners_unparseable": unparseable, "owners_self_stale": self_stale,
            "owners_transport_error": transport_errors}


# ---------------------------------------------------------------------------
# Corpus health check — §8.3.4 (oversized docs must be structurally
# subdivided), §10 (registry drift), and DOC-FIT classification: temporal,
# append-only artifacts (worklogs, changelogs) are a structurally poor fit
# for durable ownership because their staleness half-life is measured in
# hours and their content is a log, not a maintained spec (§8.3 reason 6
# assumes "change is slow and reviewed" — a log violates that premise).
# ---------------------------------------------------------------------------
OVERSIZED_TOKENS = int(os.environ.get("CTXOWN_OVERSIZED_TOKENS", "32000"))
MICRO_TOKENS = int(os.environ.get("CTXOWN_MICRO_TOKENS", "300"))

# name-level signals of append-only / temporal artifacts
_UNOWNABLE_NAME_RE = re.compile(
    r"(worklog|work-log|changelog|change-log|history|journal|diary|"
    r"meeting-?notes|minutes|activity|audit-?trail|scrapbook)", re.I)
# content-level signal: dense dated headers (## 2026-08-30 / ### 2026-08-30)
_DATED_HEADER_RE = re.compile(r"^#{1,3}\s*\d{4}-\d{2}-\d{2}", re.M)


def estimate_tokens(text):
    """Chars/4 — the corpus is English prose; calibrated against the
    tokenizer on the pilot corpus within ~10%."""
    return max(1, len(text) // 4)


def classify_doc_fit(rel_path, text):
    """Ownable vs append-only vs marginal. Ownable = scoped, actively
    maintained, semantically bounded (a spec, runbook, policy, contract).
    Append-only = temporal log whose tail grows forever. Marginal = mixed
    signals (ownable head + log tail, e.g. a runbook with a dated log at
    the end) — usually fixed by SPLITTING the doc (§8.3.4)."""
    reasons = []
    name_hit = _UNOWNABLE_NAME_RE.search(Path(rel_path).stem)
    dated = len(_DATED_HEADER_RE.findall(text))
    if name_hit:
        reasons.append(f"name pattern '{name_hit.group(0)}' is append-only/temporal")
    if dated >= 3:
        reasons.append(f"{dated} dated headers — content is a log, not a maintained spec")
    if name_hit or dated >= 3:
        kind = "append-only"
    elif dated == 1 or dated == 2:
        kind = "marginal"
        reasons.append(f"{dated} dated header(s) — check whether a log section is "
                       f"accreting at the tail; consider splitting it out")
    else:
        kind = "ownable"
    return {"fit": kind, "reasons": reasons}


def cmd_check(args):
    """Corpus health: oversized / micro docs, doc-fit classification, and
    registry drift (a doc in the tree with no owner, or an owner whose doc
    vanished — §10 'registry drift'). Pure local, no LLM calls."""
    registry = load_registry()
    leaves = [o for o in registry["owners"] if o["kind"] == "leaf"]
    owned_paths = {o["path"] for o in leaves}
    tree_docs = {str(p.relative_to(CORPUS_DIR).as_posix())
                 for p in (CORPUS_DIR / "docs").rglob("*.md")}
    oversized, micro, unownable, marginal = [], [], [], []
    sizes = {}
    for doc in sorted(tree_docs):
        text = (CORPUS_DIR / doc).read_text(errors="replace")
        tok = estimate_tokens(text)
        sizes[doc] = tok
        if tok > OVERSIZED_TOKENS:
            oversized.append({"doc": doc, "tokens": tok,
                              "action": "structurally subdivide (split by top-level "
                                        "section into child docs — §8.3.4)"})
        if tok < MICRO_TOKENS:
            micro.append({"doc": doc, "tokens": tok,
                          "action": "consider merging into its parent topic — a "
                                    "bundle per doc is overhead below ~300 tokens"})
        fit = classify_doc_fit(doc, text)
        if fit["fit"] == "append-only":
            unownable.append({"doc": doc, **fit,
                              "action": "exclude from ownership lanes (or own only a "
                                        "rolling head-summary); logs violate the "
                                        "'change is slow and reviewed' premise (§8.3)"})
        elif fit["fit"] == "marginal":
            marginal.append({"doc": doc, **fit})
    drift_new = sorted(tree_docs - owned_paths)
    drift_gone = sorted(owned_paths - tree_docs)
    total_tokens = sum(sizes.values())
    out_json({
        "ok": True, "docs": len(tree_docs), "total_tokens": total_tokens,
        "per_owner_avg_tokens": total_tokens // max(1, len(tree_docs)),
        "oversized": oversized, "micro": micro,
        "unownable_docs": unownable, "marginal_docs": marginal,
        "registry_drift_new_docs": drift_new,
        "registry_drift_gone_docs": drift_gone,
        "registry_drift": bool(drift_new or drift_gone),
        "hints": ([f"registry drifted ({len(drift_new)} new / {len(drift_gone)} gone) — "
                   f"run: ctxown.py init && ctxown.py rebuild"] if (drift_new or drift_gone)
                  else []) +
                 ([f"{len(oversized)} doc(s) exceed {OVERSIZED_TOKENS} tokens — "
                   f"subdivide before adding owners (§8.3.4)"] if oversized else []),
    })


# ---------------------------------------------------------------------------
# Write path (§7.7) — owners retain authority; execution stays isolated;
# self-invalidation is mandatory and verified, not assumed.
# The doc's own known-weak-points list says "The write path (§7.7) has never
# been run." This runs it, as a controller-enforced ladder:
#   1. DECIDE   — the owner (bundle-primed, its coherence verified) states
#                 intent + acceptance criteria + a draft revision.
#   2. EXECUTE  — a DISPOSABLE executor session (fresh oc_run process, never
#                 the owner's serving session) applies the plan mechanically.
#                 The executor's output is what lands — write-context never
#                 pollutes the reusable serving context (§7.2/§7.7).
#   3. ENFORCE  — the controller verifies the change is confined to the
#                 owner's core_paths (one writer per artifact, structural).
#   4. INVALIDATE — the owner's own bundle is now stale-dirty BY ITS OWN
#                 ACTION; the controller PROVES the hard rule fires (ask is
#                 refused) before any rebuild is allowed to paper over it.
#   5. REBUILD  — incremental rebuild of exactly that owner; the owner then
#                 answers from the UPDATED bundle (verified: it must know
#                 the new content, proving the rebuild actually took).
# ---------------------------------------------------------------------------
def git_changed_tracked():
    """Tracked-file modifications, CORPUS-RELATIVE. git() runs with
    cwd=CORPUS_DIR but prints repo-root-relative paths; normalize before
    comparing with owner paths ('docs/x.md'). Untracked files (a fresh
    registry.json, scratch notes) are deliberately excluded — only real
    edits to tracked corpus files count as writes."""
    rc, out, _ = git(["rev-parse", "--show-toplevel"])
    repo_root = Path(out) if rc == 0 else CORPUS_DIR
    rc, out, _ = git(["diff", "--name-only"])
    changed = []
    for l in out.splitlines():
        if not l.strip():
            continue
        try:
            changed.append(str((repo_root / l).resolve()
                               .relative_to(CORPUS_DIR.resolve())))
        except ValueError:
            changed.append(l)
    return changed


def git_mut(*args, _where="ctxown"):
    """A mutating git call whose failure must abort the caller — a silently
    uncommitted change shifts every later base and poisons the methodology
    while the run looks normal (R8/#8)."""
    rc, out, err = git(list(args))
    if rc != 0:
        fail(f"{_where}: git {' '.join(args)} failed (rc={rc}): "
             f"{(err or out)[:200]}", 5)
    return out


def cmd_write(args):
    registry = load_registry()
    owner = owner_by_id(registry, args.owner)
    if not owner:
        fail(f"unknown owner: {args.owner}", 3)
    if owner["kind"] != "leaf":
        fail(f"owner {args.owner} is a {owner['kind']}, not a leaf — only leaf "
             f"owners own a writable artifact (§2.1)", 3)
    doc_path = CORPUS_DIR / owner["path"]
    original = doc_path.read_text(errors="replace")
    ladder = {"owner": args.owner, "steps": []}

    # -- 1. DECIDE (owner answers from its verified bundle) ----------------
    decide_q = (f"You own this document (it is in your bundle verbatim). Draft a "
                f"revision that carries out this instruction:\n"
                f"  INSTRUCTION: {args.instruction}\n\n"
                f"Answer with ONLY a JSON object:\n"
                f'{{"intent": "one sentence", "acceptance_criteria": ["...", "..."], '
                f'"revised_document": "the COMPLETE new text of the document"}}\n'
                f"Rules: keep every unrelated section byte-identical; change only what "
                f"the instruction requires; the document must remain self-consistent.")
    dec = cov_ask_owner(args.owner, decide_q, timeout=args.timeout, registry=registry)
    ladder["steps"].append({"step": "decide", "ok": dec.get("ok"),
                            "state": dec.get("state"), "refused": dec.get("refused")})
    if not dec.get("ok"):
        fail("owner decision failed — write aborted before any edit", 4, ladder=ladder)
    plan = parse_review_json(dec.get("text") or "")
    if not (isinstance(plan, dict) and plan.get("revised_document")):
        fail("owner decision unparseable / missing revised_document — aborted", 4, ladder=ladder)
    ladder["steps"][0]["intent"] = (plan.get("intent") or "")[:200]
    ladder["steps"][0]["acceptance_criteria"] = plan.get("acceptance_criteria") or []

    # -- 2. EXECUTE (disposable executor session; never the owner's) -------
    if args.executor == "direct":
        revised = plan["revised_document"]
        ladder["steps"].append({"step": "execute", "mode": "direct",
                                "note": "executor bypassed (--executor direct) — "
                                        "only for controlled tests"})
    else:
        ex_q = (f"Mechanical edit task. Apply EXACTLY this revision; output ONLY the "
                f"final document text, nothing else (no fences, no commentary).\n\n"
                f"ORIGINAL:\n<<<DOC\n{original}\nDOC\n\n"
                f"REVISED (from the plan; trust it, but reproduce it faithfully):\n"
                f"<<<DOC\n{plan['revised_document']}\nDOC")
        sel = current_selection() if hasattr(current_selection, "__call__") else {}
        ex_ok, ex_text, ex_raw = oc_run(ex_q, timeout=args.timeout + 90,
                                        model=(sel or {}).get("model"),
                                        no_fallback=True)
        revised = (ex_text or "").strip()
        ladder["steps"].append({"step": "execute", "mode": "isolated-session",
                                "ok": ex_ok, "chars": len(revised),
                                "error": ((ex_raw or {}).get("error")
                                          if isinstance(ex_raw, dict) else None),
                                "stderr": (((ex_raw or {}).get("stderr") or "")[:300]
                                           if isinstance(ex_raw, dict) else None),
                                "exit_code": (ex_raw.get("exit_code")
                                              if isinstance(ex_raw, dict) else None)})
        if not ex_ok or not revised:
            fail("executor session failed — write aborted (corpus untouched)", 4, ladder=ladder)

    # -- 3. ENFORCE (structural: only the owned artifact may change) -------
    if revised == original:
        ladder["steps"].append({"step": "enforce", "changed": False,
                                "note": "revision is a no-op"})
        out_json({"ok": True, "ladder": ladder, "written": False,
                  "note": "no-op write — nothing applied"})
        return
    doc_path.write_text(revised)
    # -- 3. ENFORCE (structural: only the owned artifact may change) -------
    changed_files = git_changed_tracked()
    confined = changed_files == [owner["path"]]
    ladder["steps"].append({"step": "enforce", "changed_files": changed_files,
                            "confined_to_core": confined})
    if not confined:
        doc_path.write_text(original)   # revert: one writer per artifact (§7.7)
        fail("ENFORCEMENT: the edit touched files outside the owner's core "
             f"({changed_files}) — reverted; one writer per artifact (§7.7)",
             5, ladder=ladder)
    if args.dry_run:
        doc_path.write_text(original)   # dry-run never persists
        out_json({"ok": True, "ladder": ladder, "written": False,
                  "dry_run": True, "note": "dry-run: edit verified then reverted"})
        return

    # -- 4. INVALIDATE (prove the hard rule, don't assume it) -------------
    state, changed = stale_guard(owner)
    refusal = cov_ask_owner(args.owner, "What does your owned document say?",
                                timeout=args.timeout, registry=registry)
    refused = bool(refusal.get("refused")) or "STALE" in (refusal.get("error") or "")
    ladder["steps"].append({"step": "self-invalidate", "state": state,
                                "changed": changed, "ask_refused": refused})
    if state != "stale-dirty" or not refused:
            fail("SELF-INVALIDATION FAILED: owner must be stale-dirty and must refuse "
                 "to answer after its own edit (§7.7) — this is the most embarrassing "
                 "failure available; investigate before proceeding", 5, ladder=ladder)

    # -- 5. REBUILD + verify the owner now serves the new content ------
    # rebuild has no --owner flag (it rebuilds every STALE owner — after a
    # confined write that is exactly the written owner); --no-llm gives fast
    # protocol termination: the verbatim core + corpus.sha refresh is what
    # the §7.7 'refuse until rebuilt' rule needs. A full LLM derived-layer
    # rebuild can follow at the operator's leisure.
    if not args.skip_rebuild:
        # the write's integration point: commit BEFORE rebuild (rebuild
        # refuses dirty trees by design — T3; the eval's plant flow commits
        # first for the same reason). One writer per artifact, serialized
        # integration, then the bundle rebuild that re-arms the owner.
        git_mut("add", "-A", _where="write path")
        git_mut("commit", "-m",
                f"write: {args.owner} — {args.instruction[:60]}", "--allow-empty",
                _where="write path")
        subprocess.run([sys.executable, __file__, "--project", str(PROJECT_DIR),
                        "rebuild", "--no-llm"],
                       capture_output=True, text=True, cwd=str(PROJECT_DIR),
                       stdin=subprocess.DEVNULL, timeout=1800)
        state2, _ = stale_guard(owner)
        verify = cov_ask_owner(args.owner,
                               f"Quote the exact passage in your owned document "
                               f"that reflects this instruction: {args.instruction}",
                               timeout=args.timeout, registry=registry)
        vtext = (verify.get("text") or "")
        knew_it = bool(verify.get("ok")) and len(vtext) > 20
        ladder["steps"].append({"step": "rebuild", "state": state2,
                                "verify_ok": knew_it,
                                "verify_excerpt": vtext[:200]})
    out_json({"ok": True, "ladder": ladder, "written": True})


# ---------------------------------------------------------------------------
# Hierarchical aggregation (§8.4): "transport and aggregation can follow the
# tree: every leaf reviews for coverage, parent managers deduplicate and
# compress child findings, and the root sees a bounded result set.
# Hierarchical aggregation must never become hierarchical sampling — all
# owners still receive the change."
# The controller enforces the never-sample clause as a CONSERVATION CHECK:
# every distinct quoted finding from the raw pass must survive compression
# (or the group falls back to pass-through, loudly). Compression failing
# open would silently turn coverage into sampling — the one thing §8.4
# forbids.
# ---------------------------------------------------------------------------
AGG_THRESHOLD = int(os.environ.get("CTXOWN_AGG_THRESHOLD", "40"))


def compress_group(parent, findings, oc_run_fn=None):
    """One manager compression pass over a squad's findings. Returns
    {"findings": [...]} with every distinct raw quote preserved. The
    controller — not the model — verifies conservation."""
    oc_run_fn = oc_run_fn or oc_run
    if not findings:
        return {"findings": [], "compressed": False}
    payload = json.dumps([{"owner": f["owners"], "type": f["type"],
                           "severity": f["severity"], "quote_mine": f["quote_mine"],
                           "quote_change": f.get("quote_change", ""),
                           "explanation": f["explanation"]}
                          for f in findings], ensure_ascii=False, indent=1)
    q = (f"You are the manager of the '{parent}' squad reviewing child findings "
         f"from a doc-change review board. Compress and deduplicate them:\n"
         f"- MERGE ONLY IF THE QUOTED PASSAGES overlap (same sentence/clause "
         f"quoted by multiple owners) — keep the union of owners and the "
         f"strongest wording.\n"
         f"- Two findings about the same TOPIC but quoting DIFFERENT passages "
         f"are SEPARATE findings — output BOTH, verbatim quotes.\n"
         f"- PRESERVE every distinct quoted passage — dropping one is forbidden "
         f"(coverage is the entire point; you dedupe, you never sample).\n"
         f"- Re-rank: contradictions before assumption-breaks before term clashes.\n"
         f"Answer with ONLY a JSON object: {{\"findings\": [...]}} — same finding "
         f"shape as the input.\n"
         f"CHILD FINDINGS:\n{payload}")
    ok, text, raw_res = oc_run_fn(q, timeout=240)
    parsed = parse_review_json(text) if ok else None
    if isinstance(parsed, list):      # a bare JSON array IS the findings list
        parsed = {"findings": parsed}
    compressed = parsed if isinstance(parsed, dict) and "findings" in parsed else None
    out = []
    if isinstance(compressed, dict):
        for f in (compressed.get("findings") or []):
            if isinstance(f, dict) and (f.get("quote_mine") or "").strip():
                out.append({"owners": f.get("owners") or ["manager:" + parent],
                            "type": f.get("type"), "severity": f.get("severity", "low"),
                            "quote_mine": (f.get("quote_mine") or "").strip()[:400],
                            "quote_change": (f.get("quote_change") or "")[:200],
                            "explanation": (f.get("explanation") or "")[:300]})
    # conservation check (controller-owned, never trusted to the model).
    # A raw quote survives if a compressed quote shares >=30% of its
    # distinctive tokens (or a 40-char contiguous slice) — tolerant to the
    # compressor stripping markdown emphasis, strict against actual drops.
    def _quote_tokens(qt):
        return {w.lower() for w in re.findall(r"[A-Za-z0-9_]{4,}", qt or "")}
    def _survives(raw_quote, hay):
        rt = _quote_tokens(raw_quote)
        if not rt:
            return False
        for h in hay:
            hq = h.get("quote_mine") or ""
            if len(_quote_tokens(hq) & rt) / len(rt) >= 0.3:
                return True
            if any((raw_quote or "")[i:i+40] in hq
                   for i in range(0, max(1, len(raw_quote) - 39))):
                return True
        return False
    raw_quotes = [(f["quote_mine"] or "") for f in findings]
    surviving = sum(1 for rq in raw_quotes if _survives(rq, out))
    conservation_ok = surviving == len(raw_quotes)
    if not out or not conservation_ok:
        return {"findings": findings, "compressed": False,
                "conservation_violation": not conservation_ok,
                "raw_quotes": len(raw_quotes), "surviving_quotes": surviving,
                "compressor_output_quotes": [h.get("quote_mine", "")[:120] for h in out],
                "compressor_raw": (text or "")[:400],
                "compressor_error": ((raw_res or {}).get("error")
                                     if isinstance(raw_res, dict) else None)}
    return {"findings": out, "compressed": True,
            "conservation_violation": False,
            "raw_quotes": len(raw_quotes), "surviving_quotes": surviving}


def hierarchical_aggregate(results, registry, oc_run_fn=None):
    """Leaf results -> squad (parent-dir) compression -> root combine.
    Returns the aggregate_review-shaped report plus a hierarchy audit."""
    # re-run the flat dedupe first to get distinct-quote findings per squad
    base = aggregate_review(results)
    squads = {}
    by_owner = {r["owner"]: r for r in results}
    for f in base["findings"]:
        owner_id = f["owners"][0]
        parent = by_owner.get(owner_id, {}).get("parent") or "docs"
        squads.setdefault(parent, []).append(f)
    audit = {"squads": {}, "never_sampled": True}
    final_findings = []
    for parent, finds in sorted(squads.items()):
        comp = compress_group(parent, finds, oc_run_fn)
        audit["squads"][parent] = {
            "raw": len(finds), "out": len(comp["findings"]),
            "compressed": comp.get("compressed", False),
            "conservation_violation": comp.get("conservation_violation", False),
            "compressor_output_quotes": comp.get("compressor_output_quotes"),
            "compressor_raw": comp.get("compressor_raw")}
        if comp.get("conservation_violation"):
            audit["squads"][parent]["note"] = (
                "compression REJECTED by conservation check — pass-through "
                "(every owner's distinct quote preserved un-compressed)")
            audit["never_sampled"] = False   # fell back to pass-through (safe)
        final_findings.extend(comp["findings"])
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    type_rank = {"contradiction": 0, "invalidates_assumption": 1,
                 "term_differs": 2, "duplicate": 3}
    final_findings.sort(key=lambda f: (sev_rank.get(f["severity"], 9),
                                       type_rank.get(f["type"], 9)))
    report = dict(base)
    report["findings"] = final_findings
    report["finding_count"] = len(final_findings)
    report["high_severity"] = sum(1 for f in final_findings if f["severity"] == "high")
    report["hierarchy"] = audit
    return report


# ---------------------------------------------------------------------------
# Grep-rate telemetry (§7.8) — the Q&A log is compiler input, not memory.
# Greps INSIDE owned paths => the derived layer is under-built; greps OUTSIDE
# => the periphery ring is too thin (or the module is more coupled than the
# tree admits). Two diagnoses, two fixes — scope itself is never the variable.
# Sources: oc-tool events.jsonl (router/coldgrep/executor runs). Server-side
# owner asks do not expose tool events through the OpenCode session API —
# recorded honestly as a telemetry gap, not papered over.
# ---------------------------------------------------------------------------

def _events_tool_calls(events_path):
    """Extract tool calls from an oc-tool events.jsonl. Real schema (verified
    against live events): {"type": "tool_use", "part": {"tool": NAME,
    "state": {"input": {...}}}}. Target = the most identifying input field."""
    calls = []
    try:
        for line in Path(events_path).read_text(errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") != "tool_use":
                continue
            part = ev.get("part") or {}
            name = part.get("tool") or ""
            inp = ((part.get("state") or {}).get("input")) or {}
            target = ""
            for k in ("pattern", "query", "filePath", "path", "file",
                      "command", "url", "content"):
                v = inp.get(k)
                if isinstance(v, str) and v:
                    target = v
                    break
            if not target and inp:
                target = json.dumps(inp)[:120]
            if name:
                calls.append({"tool": name, "target": target})
    except Exception:
        pass
    return calls


def cmd_telemetry(args):
    registry = load_registry()
    owner = owner_by_id(registry, args.owner) if args.owner else None
    events_files = []
    if args.events:
        events_files = [Path(args.events)]
    else:
        import glob as _glob
        events_files = [Path(p) for p in
                        sorted(_glob.glob("/tmp/oc-tool-*/events.jsonl"))]
    if not events_files:
        out_json({"ok": True, "owner": args.owner, "events_files": 0,
                  "note": "no events.jsonl found — telemetry covers oc-tool "
                          "runs (router/coldgrep/executor); server asks do "
                          "not expose tool events via the API"})
        return
    inside = outside = 0
    by_tool = {}
    samples = []
    core = owner["core_paths"] if owner else []
    for ef in events_files:
        for c in _events_tool_calls(ef):
            t = (c["target"] or "").lower()
            is_search = c["tool"].lower() in ("grep", "glob", "find", "search",
                                              "list", "read", "webfetch")
            if not is_search:
                continue
            by_tool[c["tool"]] = by_tool.get(c["tool"], 0) + 1
            in_core = any(p.lower() in t for p in core) if core else None
            if in_core:
                inside += 1
            elif in_core is False:
                outside += 1
            if len(samples) < 20:
                samples.append({"tool": c["tool"], "target": c["target"][:120],
                                "inside_owned": in_core})
    hints = []
    if owner and inside > 0:
        hints.append(f"{inside} search(es) INSIDE owned paths — the derived layer "
                     f"is under-built; rebuild the owner's bundle (§7.8)")
    if owner and outside >= 3:
        hints.append(f"{outside} searches OUTSIDE owned paths — the periphery ring "
                     f"is too thin, or the doc is more coupled than the tree "
                     f"admits (refactoring candidate, §2.2)")
    out_json({"ok": True, "owner": args.owner, "events_files": len(events_files),
              "searches_inside_owned": inside, "searches_outside_owned": outside,
              "by_tool": by_tool, "samples": samples, "hints": hints,
              "coverage_note": "server-side owner asks expose no tool events "
                               "via the OpenCode session API — this is a "
                               "telemetry gap, recorded not hidden"})


# ---------------------------------------------------------------------------
# Chunk-retrieval baseline (§8.6 system (b)) — the eval's missing third arm.
# The spec's falsifiable test names THREE systems: (a) one agent with grep,
# (b) chunk-based RAG, (c) sharded preloaded owners. v9 ran (a) vs (c).
# This adds (b) as BM25 chunk retrieval feeding ONE cold prompt — no tools,
# no bundle, exactly the "chunk-based retrieval lags" configuration §1.3
# describes. Honest substitution, documented: no embedding endpoint exists
# in this sandbox, so the chunk scorer is lexical BM25 — the STRONGER lexical
# baseline, which biases AGAINST us (if BM25-chunks lose, embedding-RAG's
# recall problem is worse on synonym-dense prose, §8.2).
# ---------------------------------------------------------------------------
def chunk_corpus(chunk_chars=6000):
    """Split every corpus doc into ~1500-token chunks on heading boundaries;
    each chunk carries (doc, heading_path, text). Coverage manifest for the
    retrieval baseline: every chunk is enumerable, so recall is auditable."""
    chunks = []
    for doc in sorted((CORPUS_DIR / "docs").rglob("*.md")):
        rel = str(doc.relative_to(CORPUS_DIR).as_posix())
        text = doc.read_text(errors="replace")
        sections, heading = [], "_top"
        buf = [f"# {doc.stem}"]
        for line in text.splitlines():
            m = re.match(r"^(#{1,3})\s+(.*)", line)
            if m and len(m.group(1)) <= 2:
                if len("\n".join(buf)) > 200:
                    sections.append((heading, "\n".join(buf)))
                heading, buf = m.group(2).strip(), [line]
            else:
                buf.append(line)
        if buf:
            sections.append((heading, "\n".join(buf)))
        # pack sections into ~chunk_chars chunks
        cur, cur_h = [], None
        cur_len = 0
        for h, s in sections:
            if cur and cur_len + len(s) > chunk_chars:
                chunks.append({"doc": rel, "heading": cur_h or h,
                               "text": "\n".join(cur)})
                cur, cur_len = [], 0
            cur.append(s)
            cur_len += len(s)
            cur_h = cur_h or h
        if cur:
            chunks.append({"doc": rel, "heading": cur_h or "_top",
                           "text": "\n".join(cur)})
    return chunks


def bm25_topk(query, chunks, k=8):
    """Plain BM25 over the chunk corpus. Deterministic, no LLM."""
    import math
    tok = lambda s: re.findall(r"[a-z0-9]{2,}", s.lower())
    q = tok(query)
    docs_toks = [tok(c["text"]) for c in chunks]
    N = len(chunks)
    avgdl = (sum(len(d) for d in docs_toks) / max(1, N)) or 1
    df = {}
    for d in docs_toks:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    scored = []
    for i, d in enumerate(docs_toks):
        tf = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = max(0.0, math.log((N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)))
            s += idf * (tf[t] * 2.2) / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avgdl))
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    return [dict(chunks[i], score=round(s, 3)) for s, i in scored[:k]]


def rag_baseline(plant, model=None, oc_run_fn=None):
    """§8.6 arm (b): chunk retrieval + one cold review prompt, no tools."""
    oc_run_fn = oc_run_fn or oc_run
    query = f"{plant.get('file', '')} {plant.get('find', '')} {plant.get('replace', '')}"
    chunks = chunk_corpus()
    top = bm25_topk(query, chunks, k=8)
    retrieved = "\n\n".join(f"[CHUNK {i+1}] {c['doc']} :: {c['heading']}\n{c['text']}"
                            for i, c in enumerate(top))
    prompt = f"""You are reviewing a proposed change to a documentation corpus.
You have NO tools. You see ONLY the retrieved chunks below (a lexical search
over the corpus). Judge from those chunks alone.

The change modifies {plant['file']} replacing:
  OLD: {plant['find']}
  NEW: {plant['replace']}

Does this change CONTRADICT anything in the retrieved chunks? Answer with ONLY
a JSON object: {{"contradicts": true|false, "quote": "the exact contradicting
passage, or empty", "source": "file it came from", "explanation": "one sentence"}}

RETRIEVED CHUNKS:
{retrieved}"""
    ok, text, raw = oc_run_fn(prompt, timeout=600, model=model, no_fallback=True)
    if not ok:
        return {"ok": False, "error": (text or "transport failure")[:200]}
    parsed = parse_review_json(text)
    if not isinstance(parsed, dict):
        parsed = {"contradicts": False, "quote": "", "source": "",
                  "explanation": "unparseable"}
    return {"ok": True, "contradicts": bool(parsed.get("contradicts")),
            "quote": (parsed.get("quote") or "")[:400],
            "source": (parsed.get("source") or "")[:120],
            "explanation": (parsed.get("explanation") or "")[:200],
            "retrieved": [{"doc": c["doc"], "heading": c["heading"],
                           "score": c["score"]} for c in top]}


def rag_detects(result, plant):
    """Arm (b) detection: same quote-overlap rule as the other arms, applied
    to the RAG answer's quote. A retrieved-chunk hit whose quote doesn't
    overlap the planted passage is a false positive, not a detection."""
    if not result or not result.get("ok") or not result.get("contradicts"):
        return False
    target = (plant.get("find") or "").strip()
    q = result.get("quote") or ""
    if not target or not q:
        return False
    toks = {w.lower() for w in re.findall(r"[A-Za-z0-9_]{5,}", target)
            if w.lower() not in STOPWORDS}
    qtoks = {w.lower() for w in re.findall(r"[A-Za-z0-9_]{5,}", q)}
    if not toks or not qtoks:
        return False
    overlap = len(toks & qtoks) / max(1, len(toks))
    contiguous = any(target[i:i+40] in q for i in range(0, max(1, len(target) - 39)))
    return overlap >= 0.3 or contiguous


# ---------------------------------------------------------------------------
# Planted-contradiction eval (§8.6) — the falsifiable test
# ---------------------------------------------------------------------------

def cmd_eval(args):
    """Planted-contradiction eval (§8.6) on the LIVE corpus with guaranteed
    restore. R6 fixes:
    - F1: refuse to start on a dirty corpus; snapshot+restore in a finally
      block; assert clean_sha on exit (a crash can no longer poison the
      corpus/bundles/pins).
    - F5: --quick actually limits to the first 4 plants.
    - F16: (a) per-plant rebuild preserves derived layers (no mid-experiment
      bundle degradation); (b) detection requires the finding's quote to
      overlap the plant's ORIGINAL text (no keyword soup); (c) a clean-run
      false-positive baseline: review an untouched file — any finding there
      is a false positive.
    """
    gt_path = EVAL_DIR / "planted.json"
    if not gt_path.exists():
        fail(f"ground truth not found: {gt_path} (create it first)", 3)
    try:
        ground_truth = json.loads(gt_path.read_text())
    except json.JSONDecodeError as e:
        fail(f"ground truth corrupted: {e} — repair {gt_path}", 3)
    if args.quick:
        ground_truth = ground_truth[:4]
    # R11: --plants for focused evals (e.g. only the new inference-level plants)
    if getattr(args, 'plants', None):
        wanted = {p.strip() for p in args.plants.split(',') if p.strip()}
        before = len(ground_truth)
        ground_truth = [p for p in ground_truth if p.get('id') in wanted]
        missing = wanted - {p.get('id') for p in ground_truth}
        if missing:
            fail(f"--plants referenced unknown IDs: {sorted(missing)} "
                 f"(check eval/planted.json)", 3)
        print(f"--plants: selected {len(ground_truth)} of {before} plants")
    registry = load_registry()

    _lock = single_writer_lock()

    def git_mut(*args):
        """R8/#8: a mutating git call whose failure must abort the eval — an
        uncommitted plant silently shifts every later base and poisons the
        methodology while the run looks normal."""
        rc, out, err = git(list(args))
        if rc != 0:
            fail(f"eval aborted: git {' '.join(args)} failed (rc={rc}): "
                 f"{(err or out)[:200]}", 5)
        return out

    def rebuild_now():
        """Per-plant rebuild subprocess — releases the writer lock around it
        (the rebuild takes the lock itself), re-acquires after."""
        nonlocal _lock
        os.close(_lock)
        try:
            return subprocess.run([sys.executable, __file__, "rebuild", "--no-llm"],
                                  capture_output=True, text=True)
        finally:
            _lock = single_writer_lock()

    # refuse to start on a dirty corpus (F1); --clean-start (F20) wipes
    # residue from a HARD-KILLED eval (finally never ran) — found live after
    # a daemon abort left a written-but-uncommitted plant in the tree.
    rc, status_out, _ = git(["status", "--porcelain"])
    if rc == 0 and status_out.strip():
        if getattr(args, "clean_start", False):
            git_mut("reset", "--hard", "HEAD")
            git_mut("clean", "-fd")
            rc, status_out, _ = git(["status", "--porcelain"])
        if rc == 0 and status_out.strip():
            fail(f"corpus has uncommitted changes — commit or stash before eval "
                 f"(or --clean-start to wipe killed-eval residue):\n{status_out[:400]}", 3)
    git_mut("add", "-A")
    git_mut("commit", "-m", f"eval-snapshot-{int(time.time())}", "--allow-empty")
    clean_sha = corpus_sha()
    originals = {}

    # R14: probe + reconcile the provider AFTER the deterministic refusals
    # (dirty-corpus etc. must fire first — a dead provider must never mask
    # them; found via T6) but BEFORE planting anything — the eval must start
    # on a live (key, model) and both arms must stay pinned to the SAME model
    # (v7/v8 could silently diverge: sharded arm on the server's model,
    # coldgrep on whatever oc-tool's chain landed on). Mid-eval key death is
    # healed by the per-plant rebuild restarts (they re-probe).
    provider_sel = ensure_provider()
    eval_models = set()
    arms_raw = (getattr(args, "arms", None) or "sharded,coldgrep").strip()
    arms = ["sharded", "coldgrep", "rag"] if arms_raw == "all" else \
           [a.strip() for a in arms_raw.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ("sharded", "coldgrep", "rag")]
    if unknown:
        fail(f"unknown arms: {unknown} — valid: sharded,coldgrep,rag (or 'all')", 3)

    detections_sharded = {}
    detections_coldgrep = {}
    detections_rag = {}
    details = []
    fp_findings = 0
    try:
        for p in ground_truth:
            f = CORPUS_DIR / p["file"]
            if not f.exists():
                details.append({"id": p["id"], "error": "file missing"})
                continue
            original = f.read_text()
            if p["find"] not in original:
                details.append({"id": p["id"], "error": "find-text not present (ground truth drift)"})
                continue
            originals[p["file"]] = original
            # plant the contradiction
            f.write_text(original.replace(p["find"], p["replace"], 1))
            git_mut("add", "-A")
            git_mut("commit", "-m", f"plant {p['id']}", "--allow-empty")

            # rebuild ONLY the affected owner (derived layer preserved now).
            # R8: an unverified restart means serving agents may be poisoned
            # (empty-text mode) — abort the eval instead of recording garbage.
            rb = rebuild_now()
            try:
                rbd = json.loads(rb.stdout.strip().splitlines()[-1]) if rb.stdout.strip() else {}
            except Exception:
                rbd = {}
            # R9/N1: a FAILED per-plant rebuild (lock race, git error) used to
            # be silently absorbed — the plant then got reviewed against
            # un-rebuilt bundles while the run looked normal
            if rb.returncode != 0 or rbd.get("ok") is not True:
                fail("eval aborted: per-plant rebuild failed (rc=" + str(rb.returncode)
                     + "): " + (rb.stdout or rb.stderr or "")[-300:], 5)
            bad_restarts = [s for s in rbd.get("servers_restarted", [])
                            if not s.get("verified")]
            if bad_restarts:
                fail("eval aborted: server restart not verified after per-plant "
                     "rebuild (serving agents possibly poisoned) — "
                     + json.dumps(bad_restarts)[:400], 5)

            # review board (sharded owners)
            review = run_review_quiet(registry, p["file"], clean_sha)
            # R8: if EVERY owner hit a transport error, the serving layer is
            # poisoned — abort rather than record a guaranteed-false 0.
            terr = ((review or {}).get("report", {}) or {}).get("owners_transport_error") or []
            queried = (review or {}).get("owners_queried") or 0
            if queried and len(terr) >= queried:
                fail("eval aborted: ALL owners hit transport errors on plant "
                     f"{p['id']} — serving layer poisoned; restart serve and "
                     f"re-run (transport owners: {terr[:6]})", 5)
            hit = review_detects(review, p)
            detections_sharded[p["id"]] = hit

            # cold-grep baseline — R14: pinned to the CURRENT selection (the
            # per-plant rebuild restart may have healed a dead key and moved
            # the selection; both arms re-read the same state, so they stay
            # pinned together per plant)
            sel_now = current_selection()
            cold = (run_coldgrep_baseline(p, model=sel_now.get("model"))
                    if "coldgrep" in arms else None)
            if isinstance(cold, dict):
                fail(f"eval aborted: plant {p['id']} — {cold.get('error')} "
                     "(a measured miss must not be indistinguishable from a "
                     "failed measurement)", 5)
            if "coldgrep" in arms:
                detections_coldgrep[p["id"]] = cold
                eval_models.add(sel_now.get("model"))

            # §8.6 arm (b): chunk-retrieval baseline (BM25 chunks -> one
            # cold prompt, no tools, no bundle). Same honest-abort rule.
            if "rag" in arms:
                rag_res = rag_baseline(p, model=sel_now.get("model"))
                if not (isinstance(rag_res, dict) and rag_res.get("ok")):
                    fail(f"eval aborted: plant {p['id']} — rag arm transport "
                         f"failure: {(rag_res or {}).get('error')}", 5)
                detections_rag[p["id"]] = rag_detects(rag_res, p)

            details.append({"id": p["id"], "file": p["file"], "category": p.get("category"),
                            "sharded": hit,
                            "coldgrep": cold if "coldgrep" in arms else None,
                            "rag": (detections_rag.get(p["id"])
                                    if "rag" in arms else None),
                            "model": sel_now.get("model"),
                            "sharded_findings": len(review.get("report", {}).get("findings", [])) if review else 0})
            # restore immediately (also covered by the finally block)
            f.write_text(original)
            git_mut("add", "-A")
            git_mut("commit", "-m", f"restore {p['id']}", "--allow-empty")

        # F16(c)/R8: false-positive baseline — review a BENIGN no-op change
        # (trailing-whitespace edit to glossary) against clean_sha. The old
        # probe (unchanged glossary vs clean_sha) had an EMPTY diff, so
        # cmd_review always refused and the FP metric was structurally dead —
        # reported 0 forever without ever measuring anything.
        gl = CORPUS_DIR / "glossary.md"
        if gl.exists():
            gl_text = gl.read_text()
            gl.write_text(gl_text + "\n")
            git_mut("add", "-A")
            git_mut("commit", "-m", "fp-baseline-noop", "--allow-empty")
            fp_review = run_review_quiet(registry, "glossary.md", clean_sha)
            if not fp_review:
                fail("eval aborted: false-positive-baseline review failed to run "
                     "(transport) — a measured 0 must not be indistinguishable "
                     "from a failed measurement", 5)
            fp_findings = fp_review.get("report", {}).get("finding_count", 0)
            gl.write_text(gl_text)  # reverted by the finally reset anyway
    finally:
        # F1: guaranteed restore — reset files, rewind to clean_sha, rebuild.
        # The writer lock is released around the rebuild subprocess (it takes
        # its own lock); if re-acquisition fails the eval is already exiting.
        for fname, text in originals.items():
            try:
                (CORPUS_DIR / fname).write_text(text)
            except Exception:
                pass
        try:
            os.close(_lock)
        except Exception:
            pass
        git(["reset", "--hard", clean_sha])
        git(["clean", "-fd"])
        subprocess.run([sys.executable, __file__, "--project", str(PROJECT_DIR),
                        "rebuild", "--no-llm"],
                       capture_output=True, text=True)

    restored_ok = (corpus_sha() == clean_sha)
    n = len(detections_sharded)
    score = {"planted": n,
             "arms": arms,
             "sharded_detected": sum(1 for v in detections_sharded.values() if v),
             "coldgrep_detected": sum(1 for v in detections_coldgrep.values() if v),
             "rag_detected": (sum(1 for v in detections_rag.values() if v)
                              if detections_rag else None),
             "false_positive_findings_clean_run": fp_findings,
             "models_used": sorted(m for m in eval_models if m),
             "provider": {k: provider_sel.get(k) for k in
                          ("provider", "key_alias", "model")},
             "per_plant": details}
    out_json({"ok": True, "eval": score, "restored_clean": restored_ok,
              "model_mixed": len([m for m in eval_models if m]) > 1,
              "verdict": ("SHARDED_WINS" if score["sharded_detected"] > score["coldgrep_detected"]
                          else "TIE" if score["sharded_detected"] == score["coldgrep_detected"]
                          else "COLDGREP_WINS"),
              "verdict_3arm": (
                  None if score["rag_detected"] is None else
                  ("SHARDED_WINS" if score["sharded_detected"] > max(score["coldgrep_detected"], score["rag_detected"])
                   else "TIE" if score["sharded_detected"] == max(score["coldgrep_detected"], score["rag_detected"])
                   else "BASELINES_WIN"))})


def run_review_quiet(registry, changed_file, base, review_timeout=2400,
                    per_ask_timeout=300):
    """R12: bounded review — subprocess.run with a hard wall-clock cap so a
    hung review can never block the eval forever (the previous version had
    no subprocess timeout, so a stuck LLM call would freeze the eval).

    R14 recalibration (measured, not guessed): a real 34-owner review-board
    broadcast takes ~473s wall on OpenRouter (35-way concurrent asks show
    8.5-34.3s latencies) and ~530s on the rate-limited zai-local provider.
    The old 600s/30s pair clipped BOTH — the 30s per-ask fired on the
    latency tail mid-request, the abandoned requests piled up server-side,
    and the 600s wall then killed every plant. Current: 2400s wall, 300s
    per-ask — owner asks are multi-round agent loops (measured up to ~10
    LLM rounds per owner), and the zai proxy's token bucket paces upstream
    requests at ~0.3/s, so a full board review legitimately takes minutes."""
    try:
        r = subprocess.run([sys.executable, __file__, "--project", str(PROJECT_DIR),
                            "review", "--file", changed_file,
                            "--base", base, "--timeout", str(per_ask_timeout)],
                           capture_output=True, text=True, cwd=str(COV_DIR),
                           timeout=review_timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"review timed out after {review_timeout}s",
                "report": {"findings": [], "owners_transport_error": ["REVIEW_TIMEOUT"]}}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def review_detects(review, plant):
    """R6/F16(b): a plant counts as detected only when a finding's quoted
    passage (from the OWNER's doc) meaningfully overlaps the plant's ORIGINAL
    text — the exact passage the change contradicts. Keyword soup no longer
    counts.
    R9 (round-2 review): token sets are now CASE-FOLDED (owners quote
    sentence-start capitals, plants quote mid-sentence lowercase — 3 of 4
    eval-v6 sharded misses had real findings rejected on case alone) and the
    contiguous-window range is inclusive of the final position (the old
    `len(target)-40` skipped the last window)."""
    if not review:
        return False
    target = (plant.get("find") or "").strip()
    if not target:
        return False
    # distinctive tokens of the original passage (len>=5, minus stopwords),
    # case-folded so 'Soft-deleted' matches 'soft-deleted'
    toks = {w.lower() for w in re.findall(r"[A-Za-z0-9_]{5,}", target)
            if w.lower() not in STOPWORDS}
    for f in review.get("report", {}).get("findings", []):
        if not isinstance(f, dict):
            continue
        q = (f.get("quote_mine") or "")
        qtoks = {w.lower() for w in re.findall(r"[A-Za-z0-9_]{5,}", q)}
        if not qtoks:
            continue
        # overlap ratio: >=30% of the plant's tokens appear in the quote, or
        # a >=40-char contiguous slice of the target appears in the quote
        overlap = len(toks & qtoks) / max(1, len(toks))
        contiguous = any(target[i:i+40] in q
                         for i in range(0, max(1, len(target) - 39)))
        if overlap >= 0.3 or contiguous:
            return True
    return False


def run_coldgrep_baseline(plant, model=None):
    """Baseline (a): one cold agent with grep over the live corpus — same
    information as the review board gets (the diff), but NO bundle.
    R14: model pins the arm to the probe-selected provider with oc-tool's
    own fallback chain DISABLED — the two eval arms must run the same model
    (v7/v8 could silently diverge mid-eval: the sharded arm on the server's
    model, coldgrep on whatever oc-tool's chain landed on). Returns True /
    False / {"error": ...} — a transport failure is NOT a miss (a measured 0
    must never be indistinguishable from a failed measurement)."""
    prompt = f"""You are reviewing a proposed change to a documentation corpus at {CORPUS_DIR}.
You may use grep/read tools on the corpus (files: docs/*.md, glossary.md).

The change modifies {plant['file']} replacing:
  OLD: {plant['find']}
  NEW: {plant['replace']}

Does this change CONTRADICT anything else in the corpus? Answer with ONLY a
JSON object: {{"contradicts": true|false, "quote": "the exact contradicting
passage from another doc, or empty", "source": "file it came from", "explanation": "one sentence"}}"""
    ok, text, raw = False, "", {}
    # one retry on transport failure — a flaky paced-proxy window must not
    # kill a multi-hour eval (a clean 'no' is never retried, only failures)
    for _attempt in range(2):
        ok, text, raw = oc_run(prompt, timeout=900, model=model, no_fallback=True)
        if ok:
            break
        time.sleep(30)
    if not ok:
        # oc-tool's error field is a dict OR a plain string (found live: a
        # per-attempt timeout kill produced a string and crashed the eval)
        err = ""
        if isinstance(raw, dict):
            e = raw.get("error")
            if isinstance(e, dict):
                err = str(e.get("message") or e.get("name") or "")[:200]
            elif e is not None:
                err = str(e)[:200]
            if not err:
                err = str(raw.get("text") or "")[:200]
        if not err:
            err = str(text)[:200]
        return {"error": f"coldgrep transport failure: {err or 'unknown (no error detail)'}"}
    try:
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0))
        return bool(d.get("contradicts")) and bool(d.get("quote"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_owners(args):
    registry = load_registry()
    rows = []
    for o in registry["owners"]:
        state, _, _ = coherence_state(o)
        bundle = (BUNDLES_DIR / o["id"] / "BUNDLE.md")
        rows.append({"id": o["id"], "kind": o["kind"], "path": o["path"],
                     "port": o["port"], "coherence": state,
                     "bundle_bytes": bundle.stat().st_size if bundle.exists() else 0})
    out_json({"ok": True, "owners": rows})


def main():
    # --project may appear anywhere on the command line; resolve it BEFORE
    # argparse so every derived path binds to the right project root.
    for i, a in enumerate(sys.argv):
        if a == "--project" and i + 1 < len(sys.argv):
            set_project_dir(sys.argv[i + 1])
            sys.argv = sys.argv[:i] + sys.argv[i + 2:]
            break
        if a.startswith("--project="):
            set_project_dir(a.split("=", 1)[1])
            sys.argv.remove(a)
            break
    p = argparse.ArgumentParser(prog="ctxown",
                                 description="context-ownership layer controller "
                                             "(spec: context-ownership-model.md)")
    p.add_argument("--project", default=None,
                   help="project dir containing corpus/ (default: $CTXOWN_PROJECT "
                        "or this script's dir)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="enumerate owners from the corpus tree")

    b = sub.add_parser("build", help="compile bundles")
    b.add_argument("--owner", default=None)
    b.add_argument("--no-llm", action="store_true", help="skip derived-layer builder call")
    b.add_argument("--force", action="store_true", help="allow building on a dirty corpus tree (status still content-verifies)")
    b.add_argument("--workers", type=int, default=1,
                   help="parallel build workers (per-owner oc_run overlap; 1 = sequential, the default)")

    sub.add_parser("status", help="fleet coherence states")
    r = sub.add_parser("rebuild", help="incremental rebuild of stale bundles")
    r.add_argument("--no-llm", action="store_true")
    r.add_argument("--force", action="store_true", help="allow rebuilding on a dirty corpus tree")

    o = sub.add_parser("owners", help="list owners")

    s = sub.add_parser("serve", help="serving-layer server management")
    s.add_argument("serve_action", choices=["start", "status", "stop", "probe"])
    s.add_argument("--owner", default=None)
    s.add_argument("--per-owner-ports", action="store_true",
                   help="one opencode serve per owner (isolation demo; watch RAM)")

    a = sub.add_parser("ask", help="inquiry to one owner (per-inquiry session)")
    a.add_argument("--owner", required=True)
    a.add_argument("prompt")
    a.add_argument("--session", default=None, help="continue an inquiry")
    a.add_argument("--timeout", type=int, default=120)

    rt = sub.add_parser("route", help="resolve task -> owners -> LCA (no execution)")
    rt.add_argument("task")

    t = sub.add_parser("task", help="route + execute (direct or manager-synthesized)")
    t.add_argument("task")
    t.add_argument("--timeout", type=int, default=120)

    rv = sub.add_parser("review", help="standing review board on a change")
    rv.add_argument("--file", required=True, help="changed corpus file (repo-relative)")
    rv.add_argument("--base", default=None, help="base sha (default: last change to this file)")
    rv.add_argument("--owner", default=None, help="single-owner review (debug)")
    rv.add_argument("--timeout", type=int, default=120)
    rv.add_argument("--agg", choices=["auto", "flat", "hier"], default="auto",
                    help="aggregation mode (§8.4): flat | hierarchical | auto "
                         "(hier when leaves > CTXOWN_AGG_THRESHOLD=40)")
    rv.add_argument("--concurrency", type=int, default=3,
                    help="live execution slots for the fan-out — an execution-model "
                         "knob, fully separate from the number of ownership lanes")

    ck = sub.add_parser("check", help="corpus health: oversized/micro docs, "
                                        "doc-fit classification, registry drift")
    w = sub.add_parser("write", help="owner-authorised write path (§7.7)")
    w.add_argument("--owner", required=True)
    w.add_argument("--instruction", required=True,
                   help="what the owner should change in its owned document")
    w.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="verify the ladder through enforcement, then revert")
    w.add_argument("--executor", choices=["isolated", "direct"], default="isolated",
                   help="isolated: disposable executor session applies the edit "
                        "(§7.7 default); direct: apply the owner's draft as-is")
    w.add_argument("--skip-rebuild", action="store_true", dest="skip_rebuild",
                   help="stop after self-invalidation (manual rebuild later)")
    w.add_argument("--timeout", type=int, default=180)
    tl = sub.add_parser("telemetry", help="grep-rate telemetry (§7.8): searches "
                                          "inside vs outside owned paths")
    tl.add_argument("--owner", default=None)
    tl.add_argument("--events", default=None,
                    help="path to a specific events.jsonl (default: scan /tmp/oc-tool-*)")
    e = sub.add_parser("eval", help="planted-contradiction eval (§8.6)")
    e.add_argument("--quick", action="store_true", help="first 4 plants only")
    e.add_argument("--plants", default=None,
                   help="comma-separated list of plant IDs to run (e.g. P13,P14). Default: all plants in planted.json")
    e.add_argument("--arms", default="sharded,coldgrep",
                   help="arms to run: sharded,coldgrep,rag — or 'all' for the "
                        "full §8.6 three-system comparison (default sharded,coldgrep)")
    e.add_argument("--clean-start", action="store_true", dest="clean_start",
                   help="wipe uncommitted residue from a hard-killed eval (reset --hard HEAD + clean), then start")

    args = p.parse_args()
    load_env()
    if args.command == "init":
        cmd_init(args)
    elif args.command == "build":
        cmd_build(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "rebuild":
        cmd_rebuild(args)
    elif args.command == "owners":
        cmd_owners(args)
    elif args.command == "serve":
        cmd_serve(args)
    elif args.command == "ask":
        cmd_ask(args)
    elif args.command == "route":
        cmd_route(args)
    elif args.command == "task":
        cmd_task(args)
    elif args.command == "review":
        cmd_review(args)
    elif args.command == "eval":
        cmd_eval(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "write":
        cmd_write(args)
    elif args.command == "telemetry":
        cmd_telemetry(args)


if __name__ == "__main__":
    main()
