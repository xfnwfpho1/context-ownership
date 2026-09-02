#!/usr/bin/env python3
"""Launch the NON-BRIDGED decisive eval (§1.4) as a double-fork daemon.

Design notes (2026-09-02):
- 6 plants: 2 semantic-conversion + 1 bridged positive control + 3
  compiled-memory (the discriminating category — authority lives only in
  the owner bundle's preserved DERIVED layer).
- Arms: all three (sharded, coldgrep, rag).
- Cost accounting: OpenRouter /api/v1/key usage snapshot before/after —
  the measured dollar delta for THIS run (answers 'how much did it cost'
  with data instead of estimates).
- Heartbeat + result files under /home/sync/hermes-eval2/ (bash-limit
  immune).
"""
import json, os, subprocess, sys, time
from pathlib import Path

OUT = Path("/home/sync/hermes-eval2")
OUT.mkdir(parents=True, exist_ok=True)
HP = "/home/z/my-project/adopt-scan/hermes-proj"
CTXOWN = "/home/z/my-project/context-ownership/ctxown.py"
KEY = None
try:
    for line in Path("/home/z/my-project/opencode-zai-agent-kit/.env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            KEY = line.split("=", 1)[1].strip()
except Exception:
    pass

def usage_snapshot():
    if not KEY:
        return None
    try:
        r = subprocess.run(["curl", "-s", "-m", "20", "https://openrouter.ai/api/v1/key",
                            "-H", f"Authorization: Bearer {KEY}"],
                           capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout)["data"]
        return {"usage": d.get("usage"), "usage_daily": d.get("usage_daily"),
                "usage_monthly": d.get("usage_monthly"),
                "usage_weekly": d.get("usage_weekly"), "at": time.time()}
    except Exception as e:
        return {"error": str(e)[:200]}

def daemon_main():
    log = open(OUT / "eval.log", "ab", 0)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] non-bridged decisive eval: 6 plants, arms=all", flush=True)
    before = usage_snapshot()
    (OUT / "usage_before.json").write_text(json.dumps(before, indent=1))
    print(f"cost snapshot (before): {json.dumps(before)}", flush=True)
    env = dict(os.environ, CTXOWN_PROJECT=HP)
    r = subprocess.run([sys.executable, CTXOWN, "eval", "--arms", "all"],
                       cwd=HP, env=env, capture_output=True, text=True)
    after = usage_snapshot()
    (OUT / "usage_after.json").write_text(json.dumps(after, indent=1))
    (OUT / "result.json").write_text(r.stdout.strip() + "\n")
    (OUT / "stderr.log").write_text(r.stderr[-8000:])
    print(f"cost snapshot (after): {json.dumps(after)}", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] eval rc={r.returncode} wall={(time.time()-t0)/60:.1f}min", flush=True)
    if before and after and before.get("usage") is not None and after.get("usage") is not None:
        print(f"MEASURED COST OF THIS RUN: ${after['usage'] - before['usage']:.3f}", flush=True)
    (OUT / "DONE").write_text(f"rc={r.returncode} wall={(time.time()-t0)/60:.1f}min\n")

if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    sys.exit(0)
daemon_main()
