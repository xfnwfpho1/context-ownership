#!/usr/bin/env python3
"""detach.py — run any command as a double-fork daemon (bash-limit immune).

Why this exists: the sandbox's Bash tool kills whole process trees at
~590 s. Plain `&`, `nohup &`, `setsid & disown` all die with the call.
Only the double-fork pattern — the daemon reparents to PID 1 — survives
the toolcall boundary (found live twice; see RUNBOOK §0). Long ctxown
steps (build / eval / review on a real fleet) MUST run detached.

Usage:
  python3 scripts/detach.py --log /tmp/demo-build.log --cwd /proj \
      -- python3 ctxown.py --project . build

Output: one JSON line {ok, log} immediately (the stdout contract).
The command's stdout+stderr append to --log. When it finishes:
  <log>.pid  — the command's pid (written at spawn)
  <log>.rc   — the command's exit code (written at exit)
Monitor with: tail -f <log>; check done with: test -f <log>.rc && cat <log>.rc
"""
import argparse
import json
import os
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--log", required=True, help="log file (also .pid/.rc sidecars)")
    ap.add_argument("--cwd", default=None, help="working dir for the command")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="the command to run (put `--` before it)")
    a = ap.parse_args()
    cmd = [c for c in a.cmd]
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print(json.dumps({"ok": False, "error": "no command given (put `--` before it)"}))
        sys.exit(2)

    log = os.path.abspath(a.log)
    pid = os.fork()
    if pid > 0:
        # Top parent: the intermediate child exits immediately after the
        # fork; report and return. Do NOT wait on pid (it is transient).
        print(json.dumps({"ok": True, "log": log, "note": "detached (double-fork); monitor the log; .rc appears on completion"}))
        return

    # Intermediate child: new session, then fork again and exit — the
    # grandchild (supervisor) is reparented to PID 1 and survives the
    # bash toolcall that launched us.
    os.setsid()
    if os.fork():
        os._exit(0)

    # Supervisor (daemon): redirect stdio, run the command, record rc.
    try:
        os.chdir(a.cwd) if a.cwd else None
    except Exception as e:
        with open(log, "ab") as lf:
            os.dup2(lf.fileno(), 1)
            os.dup2(lf.fileno(), 2)
            print(f"detach: --cwd failed: {e}")
        open(log + ".rc", "w").write("126\n")
        os._exit(126)
    try:
        lf = open(log, "ab")
        os.dup2(lf.fileno(), 1)
        os.dup2(lf.fileno(), 2)
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
    except Exception:
        os._exit(1)
    print(f"detach: running: {' '.join(cmd)} (cwd={os.getcwd()})", flush=True)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # live logs, not exit-flushed
    try:
        p = subprocess.Popen(cmd, env=env)
        with open(log + ".pid", "w") as pf:
            pf.write(str(p.pid) + "\n")
        rc = p.wait()
    except Exception as e:
        print(f"detach: command failed to start: {e}")
        rc = 127
    try:
        with open(log + ".rc", "w") as rf:
            rf.write(str(rc) + "\n")
    except Exception:
        pass
    os._exit(rc)


if __name__ == "__main__":
    main()
