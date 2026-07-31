"""Resource guards for the headless browser.

Two responsibilities:

1. ``run_guarded`` -- run a browser coroutine under a hard wall-clock + memory
   ceiling. If it hangs or the Chrome process tree bloats past the limit, kill
   the tree and abort with a clear error instead of blocking forever / eating
   all RAM.
2. ``kill_stragglers`` -- reap orphaned automation-Chrome processes left behind
   by a previously crashed/killed run, without touching the user's real browser.

All process accounting is scoped to *this* Python process's descendants (the
Playwright node driver and the Chrome tree it spawns are our children), so the
watchdog can never kill an unrelated process.
"""

import os
import asyncio

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a declared dependency
    psutil = None


def _own_descendants():
    """All live descendant processes of this Python process (node driver +
    Chrome + renderers). Empty list if psutil is unavailable."""
    if psutil is None:
        return []
    try:
        return psutil.Process(os.getpid()).children(recursive=True)
    except Exception:
        return []


def tree_rss_mb():
    """Resident memory (MB) summed across this process's descendant tree."""
    total = 0
    for p in _own_descendants():
        try:
            total += p.memory_info().rss
        except Exception:
            continue
    return total / (1024 * 1024)


def kill_own_browsers():
    """Force-kill every descendant process (Chrome tree + node driver).

    Used to abort a hung/bloated run: killing Chrome unblocks any stuck
    Playwright call so the awaiting coroutine can unwind. Returns the number of
    processes signalled.
    """
    procs = _own_descendants()
    for p in procs:
        try:
            p.kill()
        except Exception:
            continue
    if procs:
        try:
            psutil.wait_procs(procs, timeout=5)
        except Exception:
            pass
    return len(procs)


async def run_guarded(coro, timeout_sec, mem_limit_mb, poll_sec=2):
    """Run ``coro`` under a wall-clock + memory watchdog.

    Returns the coroutine's result on success (re-raising any error it raised).
    If ``timeout_sec`` elapses or the descendant tree exceeds ``mem_limit_mb``,
    the browser tree is killed and a ``RuntimeError`` is raised describing why.
    A limit of 0/None disables that particular check.
    """
    task = asyncio.ensure_future(coro)
    elapsed = 0.0
    poll = max(0.5, float(poll_sec))

    while True:
        done, _ = await asyncio.wait({task}, timeout=poll)
        if task in done:
            return task.result()  # propagates the coroutine's own exceptions

        elapsed += poll
        breach = None
        if mem_limit_mb:
            rss = tree_rss_mb()
            if rss > mem_limit_mb:
                breach = f"memory {rss:.0f}MB exceeded {mem_limit_mb}MB limit"
        if breach is None and timeout_sec and elapsed >= timeout_sec:
            breach = f"timeout after {int(elapsed)}s (limit {timeout_sec}s)"

        if breach:
            killed = kill_own_browsers()  # unblocks the hung Playwright call
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            raise RuntimeError(f"Aborted: {breach}. Killed {killed} browser process(es).")


# --- Straggler reaping ------------------------------------------------------

# Distinctive markers of a Playwright/automation-launched Chrome. We only ever
# kill a process if it is clearly automation-controlled AND tied to this app's
# browsers -- never a user's normal Chrome (which has none of these).
_AUTOMATION_MARKERS = ("--remote-debugging-pipe", "--remote-debugging-port")
_APP_PROFILE_MARKERS = ("ms-playwright", "playwright_chromiumdev_profile", ".profile")


def find_straggler_pids(extra_profile_dir=None):
    """Return PIDs of orphaned automation-Chrome processes safe to reap.

    A process qualifies only if its command line shows automation control
    (remote-debugging pipe/port) *and* references a Playwright/app profile path.
    A user's normal browser has neither, so it is never matched.
    """
    if psutil is None:
        return []
    markers = list(_APP_PROFILE_MARKERS)
    if extra_profile_dir:
        markers.append(str(extra_profile_dir))
    own = {p.pid for p in _own_descendants()}
    pids = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid in own or proc.pid == os.getpid():
                continue
            cmd = " ".join(proc.info.get("cmdline") or [])
            if not cmd:
                continue
            name = (proc.info.get("name") or "").lower()
            if "chrome" not in name and "chromium" not in name:
                continue
            if not any(m in cmd for m in _AUTOMATION_MARKERS):
                continue
            if not any(m in cmd for m in markers):
                continue
            pids.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def kill_stragglers(extra_profile_dir=None):
    """Kill orphaned automation-Chrome stragglers. Returns the count killed."""
    if psutil is None:
        return 0
    pids = find_straggler_pids(extra_profile_dir)
    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            continue
    for p in procs:
        try:
            p.kill()
        except Exception:
            continue
    if procs:
        try:
            psutil.wait_procs(procs, timeout=5)
        except Exception:
            pass
    return len(procs)
