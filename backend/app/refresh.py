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
    """Keeps the newest run of each enabled model. Models are ingested one after another
    (never concurrently), so peak memory stays that of a single ingest."""

    def __init__(self, store: Store, root: Path, models=("ifs",), max_step: int = 72,
                 interval_s: int = 3600, retry_s: tuple[int, ...] = (60, 300, 900), keep_runs: int = 2):
        self.store, self.root, self.models = store, Path(root), list(models)
        self.steps = {m: steps_up_to(max_step, m) for m in self.models}
        self.interval_s, self.retry_s, self.keep_runs = interval_s, retry_s, keep_runs
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.by_model: dict[str, dict] = {m: {
            "state": "idle", "last_attempt": None, "last_success": None, "last_error": None,
            "consecutive_failures": 0, "horizon_h": self.steps[m][-1], "interval_s": interval_s,
        } for m in self.models}

    @property
    def status(self) -> dict:
        """Status of the primary (first) model; per-model detail is in `by_model`."""
        return self.by_model[self.models[0]]

    # ------------------------------------------------------------------ one cycle
    def run_model(self, model: str) -> bool:
        """Check for and ingest a newer run of one model. True if its store is up to date."""
        st, steps = self.by_model[model], self.steps[model]
        st.update(state="checking", last_attempt=_now())
        try:
            run = latest_run(model, last_step=steps[-1])
            run_id = run.strftime("%Y%m%d%H")
            have = self.store.runs(model)
            if have and have[0] >= run_id:
                log.info("forecast up to date", extra={"model": model, "run": have[0]})
            else:
                st["state"] = "ingesting"
                log.info("ingesting new run", extra={"model": model, "run": run_id, "steps": len(steps)})
                ingest(model, steps, run, self.root)
                removed = prune(model, self.keep_runs, self.root)
                if removed:
                    log.info("pruned old runs", extra={"model": model, "removed": removed})
            st.update(state="idle", last_success=_now(), last_error=None, consecutive_failures=0)
            return True
        except Exception as e:  # network, ECMWF unavailable, validation failure, disk...
            st["consecutive_failures"] += 1
            st.update(state="error", last_error=f"{type(e).__name__}: {e}"[:500])
            log.exception("refresh failed; continuing to serve the last valid run",
                          extra={"model": model, "failures": st["consecutive_failures"]})
            return False

    def run_once(self) -> bool:
        """One cycle over all models; a failure in one model never blocks the others."""
        results = [self.run_model(m) for m in self.models]
        return all(results)

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            ok = self.run_once()
            if ok:
                wait = self.interval_s
            else:
                n = max(st["consecutive_failures"] for st in self.by_model.values())
                wait = self.retry_s[min(n - 1, len(self.retry_s) - 1)]
            for st in self.by_model.values():
                st["next_check_in_s"] = wait
            self._stop.wait(wait)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="forecast-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
