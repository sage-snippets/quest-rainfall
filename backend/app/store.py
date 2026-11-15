"""Read-only access to runs written by app.pipeline (manifest + compressed grids)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

RUN_ID = re.compile(r"^\d{10}$")   # YYYYMMDDHH, also guards against path traversal


class NotFound(LookupError):
    pass


class Store:
    def __init__(self, root: Path, cache_size: int = 48):
        self.root = Path(root)
        self._load = lru_cache(maxsize=cache_size)(self._load_uncached)
        self._manifest = lru_cache(maxsize=64)(self._manifest_uncached)
        self._window = lru_cache(maxsize=8)(self._window_uncached)

    # ---------------------------------------------------------------- runs
    def runs(self, model: str) -> list[str]:
        d = self.root / model
        if not d.is_dir():
            return []
        return sorted((p.name for p in d.iterdir()
                       if RUN_ID.match(p.name) and (p / "manifest.json").exists()), reverse=True)

    def latest(self, model: str) -> str:
        runs = self.runs(model)
        if not runs:
            raise NotFound(f"no ingested runs for model '{model}'")
        return runs[0]

    def manifest(self, model: str, run: str) -> dict:
        if not RUN_ID.match(run):
            raise ValueError("run must be YYYYMMDDHH")
        return self._manifest(model, run)

    def _manifest_uncached(self, model: str, run: str) -> dict:
        p = self.root / model / run / "manifest.json"
        if not p.exists():
            raise NotFound(f"run {run} not available for model '{model}'")
        return json.loads(p.read_text())

    # ---------------------------------------------------------------- arrays
    def _load_uncached(self, model: str, run: str, name: str) -> np.ndarray:
        p = self.root / model / run / name
        if not p.exists():
            raise NotFound(f"{name} missing for {model}/{run}")
        # Memory-mapped and read-only: the OS page cache holds hot grids, so RAM use stays
        # small on constrained hosts, and cached arrays can never be mutated by callers.
        return np.load(p, mmap_mode="r")

    def axes(self, model: str, run: str) -> tuple[np.ndarray, np.ndarray]:
        return (self._load(model, run, "lats.npy").astype(np.float64),
                self._load(model, run, "lons.npy").astype(np.float64))

    def interval(self, model: str, run: str, a: int, b: int) -> np.ndarray:
        return self._load(model, run, f"interval_{a}_{b}.npy")

    def cumulative(self, model: str, run: str, step: int) -> np.ndarray:
        if step == 0:
            lats, lons = self.axes(model, run)
            return np.zeros((lats.size, lons.size), dtype=np.float32)
        return self._load(model, run, f"cumulative_{step}.npy")

    def window(self, model: str, run: str, start: int, end: int) -> np.ndarray:
        """Precipitation (mm) that fell between forecast steps start and end.

        start == 0: the raw ECMWF accumulated field. Otherwise: the sum of the QC-checked
        native intervals in between (each >= 0, so the window total is >= 0).
        """
        return self._window(model, run, start, end)

    def _window_uncached(self, model: str, run: str, start: int, end: int) -> np.ndarray:
        steps = self.manifest(model, run)["steps"]
        if start not in steps or end not in steps:
            raise ValueError(f"steps must be among the available steps {steps}")
        if end <= start:
            raise ValueError("end_step must be greater than start_step")
        if start == 0:
            return self.cumulative(model, run, end)
        i, j = steps.index(start), steps.index(end)
        total = np.zeros_like(self.interval(model, run, steps[i], steps[i + 1]), dtype=np.float64)
        for a, b in zip(steps[i:j], steps[i + 1:j + 1]):
            total += self.interval(model, run, a, b)
        out = total.astype(np.float32)
        out.setflags(write=False)
        return out
