"""Background ingestion: once at startup, then periodically.

Runs in a daemon thread so the API starts immediately. Each cycle asks ECMWF for the
latest run that is complete up to the configured horizon; if it is newer than what we
hold, it is ingested atomically (see pipeline.ingest). Any failure is logged and recorded
in the status, the last valid run keeps being served, and the next attempt is retried
with backoff. Nothing is ever synthesised.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .acquisition.ecmwf import latest_run
from .pipeline import ingest, prune, steps_up_to
from .store import Store

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Refresher:
    def __init__(self, store: Store, root: Path, model: str = "ifs", max_step: int = 72,
                 interval_s: int = 3600, retry_s: tuple[int, ...] = (60, 300, 900), keep_runs: int = 2):
        self.store, self.root, self.model = store, Path(root), model
        self.steps = steps_up_to(max_step)
        self.interval_s, self.retry_s, self.keep_runs = interval_s, retry_s, keep_runs
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.status: dict = {"state": "idle", "last_attempt": None, "last_success": None,
                             "last_error": None, "consecutive_failures": 0,
                             "horizon_h": self.steps[-1], "interval_s": interval_s}

    # ------------------------------------------------------------------ one cycle
    def run_once(self) -> bool:
        """Check for and ingest a newer run. Returns True if the store is up to date."""
        self.status.update(state="checking", last_attempt=_now())
        try:
            run = latest_run(self.model, last_step=self.steps[-1])
            run_id = run.strftime("%Y%m%d%H")
            have = self.store.runs(self.model)
            if have and have[0] >= run_id:
                log.info("forecast up to date", extra={"run": have[0]})
            else:
                self.status["state"] = "ingesting"
                log.info("ingesting new run", extra={"run": run_id, "steps": len(self.steps)})
                ingest(self.model, self.steps, run, self.root)
                removed = prune(self.model, self.keep_runs, self.root)
                if removed:
                    log.info("pruned old runs", extra={"removed": removed})
            self.status.update(state="idle", last_success=_now(), last_error=None, consecutive_failures=0)
            return True
        except Exception as e:  # network, ECMWF unavailable, validation failure, disk...
            self.status["consecutive_failures"] += 1
            self.status.update(state="error", last_error=f"{type(e).__name__}: {e}"[:500])
            log.exception("refresh failed; continuing to serve the last valid run",
                          extra={"failures": self.status["consecutive_failures"]})
            return False

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            ok = self.run_once()
            if ok:
                wait = self.interval_s
            else:
                n = self.status["consecutive_failures"]
                wait = self.retry_s[min(n - 1, len(self.retry_s) - 1)]
            self.status["next_check_in_s"] = wait
            self._stop.wait(wait)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="forecast-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
