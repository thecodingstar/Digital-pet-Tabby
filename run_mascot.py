#!/usr/bin/env python3
"""Auto-restarting launcher for the Tabby taskbar mascot.

Runs taskbar_mascot_cat.py as a child process and watches it (plus the sprite
folder) for changes. Edit the mascot and save -> it relaunches automatically.

Start it with start_mascot.bat, or:  python run_mascot.py
Stop:  close the console window, or Ctrl+C.
"""
import os
import sys
import glob
import time
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
TARGET = HERE / "taskbar_mascot_cat.py"


def _can_run_mascot(py):
    """True if interpreter `py` exists, is spawnable, and has PyQt5."""
    try:
        r = subprocess.run([py, "-c", "import PyQt5"],
                           capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def find_interpreter():
    """Locate a real python(w).exe that can run the mascot. The interpreter
    running this script may be a uv shim that cannot spawn a child, so we
    search PATH and the standard Windows install dirs, preferring pythonw
    (no console window). Falls back to sys.executable."""
    cands = []
    for n in ("pythonw", "python"):
        w = shutil.which(n)
        if w:
            cands.append(w)
    base = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python")
    cands += sorted(glob.glob(os.path.join(base, "Python3*", "pythonw.exe")), reverse=True)
    cands += sorted(glob.glob(os.path.join(base, "Python3*", "python.exe")), reverse=True)
    cands.append(sys.executable)
    seen = set()
    for c in cands:
        key = os.path.normcase(os.path.abspath(c))
        if key in seen:
            continue
        seen.add(key)
        if _can_run_mascot(c):
            return c
    return sys.executable


INTERP = find_interpreter()
# files/dirs whose changes trigger a restart
WATCH = [TARGET, HERE / "brain.py", HERE / "chatter.py", HERE / "cat4_slice.py",
         HERE / ".env", HERE / "cat_config.json"]
WATCH_DIRS = [HERE / "cat4_states"]
POLL = 1.0  # seconds


def snapshot():
    """Newest mtime across all watched files."""
    latest = 0.0
    paths = list(WATCH)
    for d in WATCH_DIRS:
        if d.is_dir():
            paths += list(d.glob("*.png"))
    for p in paths:
        try:
            latest = max(latest, p.stat().st_mtime)
        except OSError:
            pass
    return latest


def launch():
    # reuse the interpreter already running this script (handles uv shims,
    # venvs, etc.); hide the child console via a Windows creation flag so we
    # don't depend on a pythonw.exe existing.
    print(f"[run_mascot] starting {TARGET.name} via {INTERP}", flush=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen([INTERP, str(TARGET)],
                            cwd=str(HERE), creationflags=flags)


def stop(proc):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


CRASH_WINDOW = 3.0     # exit sooner than this after launch == a crash
MAX_FAILS = 5          # consecutive fast crashes before we pause relaunching


def main():
    if not TARGET.exists():
        sys.exit(f"[run_mascot] not found: {TARGET}")
    stop_file = HERE / ".mascot_stop"
    stop_file.unlink(missing_ok=True)        # clear any stale sentinel
    proc = launch()
    started = time.time()
    last = snapshot()
    fails = 0
    try:
        while True:
            time.sleep(POLL)
            if stop_file.exists():           # Quit from the cat's menu
                print("[run_mascot] stop requested", flush=True)
                stop_file.unlink(missing_ok=True)
                break

            cur = snapshot()
            if cur != last:                  # code changed -> restart, clear backoff
                print("[run_mascot] change detected -> restarting", flush=True)
                stop(proc)
                proc = launch()
                started = time.time()
                last = cur
                fails = 0
                continue

            if proc is not None and proc.poll() is not None:
                uptime = time.time() - started
                fails = fails + 1 if uptime < CRASH_WINDOW else 0
                if fails >= MAX_FAILS:
                    print(f"[run_mascot] mascot crashed {fails}x fast; pausing "
                          f"relaunch. Edit a watched file to retry.", flush=True)
                    proc = None              # stop relaunching until a file change
                    continue
                backoff = min(30, 2 ** fails) if fails else 0
                print(f"[run_mascot] mascot exited (up {uptime:.1f}s) -> "
                      f"relaunch in {backoff}s (fail {fails})", flush=True)
                time.sleep(backoff)
                proc = launch()
                started = time.time()
    except KeyboardInterrupt:
        print("\n[run_mascot] stopping")
    finally:
        stop(proc)


if __name__ == "__main__":
    main()
