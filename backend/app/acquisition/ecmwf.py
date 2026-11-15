"""ECMWF Open Data retrieval of total precipitation (`tp`).

Uses the official `ecmwf-opendata` client, which reads the per-file `.index`
and downloads only the `tp` GRIB messages via HTTP byte ranges (~0.3 MB/step
instead of ~15 MB). The files are global; there is no server-side spatial
subsetting in ECMWF Open Data, so regional filtering happens after decoding.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from ecmwf.opendata import Client

log = logging.getLogger(__name__)

# Mirrors supported by the official client, tried in order.
SOURCES = ("ecmwf", "aws", "azure")

# Published forecast steps (hours) and runs, verified against data.ecmwf.int on 2026-11-15.
# IFS 00/12z (stream oper): 0-144 by 3 h, then 150-360 by 6 h. IFS 06/18z is a different
#   stream (scda) that stops at 90 h, so it is not used.
# AIFS-single 00/06/12/18z (all stream oper): 0-360 by 6 h.
IFS_STEPS = list(range(0, 145, 3)) + list(range(150, 361, 6))
AIFS_STEPS = list(range(0, 361, 6))
MODEL_SPECS = {
    "ifs": {"steps": IFS_STEPS, "run_hours": (0, 12), "label": "00/12z"},
    "aifs-single": {"steps": AIFS_STEPS, "run_hours": (0, 6, 12, 18), "label": "00/06/12/18z"},
}


def spec(model: str) -> dict:
    if model not in MODEL_SPECS:
        raise ValueError(f"unknown model '{model}'")
    return MODEL_SPECS[model]


class AcquisitionError(RuntimeError):
    pass


@dataclass
class Retrieval:
    model: str
    run: datetime           # forecast initialisation time (UTC)
    steps: list[int]
    path: str
    source: str
    urls: list[str]
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["run"] = self.run.isoformat()
        return d


def _client(source: str, model: str) -> Client:
    return Client(source=source, model=model, resol="0p25")


INDEX_URL = ("https://data.ecmwf.int/forecasts/{d:%Y%m%d}/{d:%H}z/{model}/0p25/oper/"
             "{d:%Y%m%d%H}0000-{step}h-oper-fc.index")


def latest_run(model: str = "ifs", last_step: int = 144, lookback_h: int = 96,
               now: datetime | None = None, client: httpx.Client | None = None) -> datetime:
    """Latest run (of the model's supported run hours) whose `tp` index for `last_step` is
    published, i.e. the run is complete up to the horizon we need.

    The official client's latest() also returns 06/18z runs, which for IFS are a different
    stream (scda) that stops at 90 h, so we probe the supported (stream oper) runs ourselves,
    newest first, within ECMWF's ~4-day retention window.
    """
    hours = spec(model)["run_hours"]
    step_h = 24 // len(hours)                 # 12 h for IFS, 6 h for AIFS
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    t = now.replace(hour=max(h for h in hours if h <= now.hour), minute=0, second=0, microsecond=0)
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=True)
    try:
        while t >= now - timedelta(hours=lookback_h):
            url = INDEX_URL.format(d=t, model=model, step=last_step)
            r = client.head(url)
            if r.status_code == 200:
                return t
            if r.status_code not in (403, 404):
                r.raise_for_status()          # 5xx etc.: let the caller retry later
            t -= timedelta(hours=step_h)
    finally:
        if own:
            client.close()
    raise AcquisitionError(f"no complete {spec(model)['label']} {model} run up to step {last_step} "
                           f"in the last {lookback_h} h")


def fetch_tp(
    run: datetime,
    steps: list[int],
    target: Path,
    model: str = "ifs",
    retries: int = 3,
    backoff_s: float = 2.0,
) -> Retrieval:
    """Download `tp` for `steps` of `run` into `target` (one multi-message GRIB2 file).

    Tries each mirror with exponential backoff. Raises AcquisitionError if all fail.
    """
    sp = spec(model)
    bad = sorted(set(steps) - set(sp["steps"]))
    if bad:
        raise ValueError(f"steps not published for {model}: {bad}")
    if run.hour not in sp["run_hours"]:
        hours = " and ".join(f"{h:02d}z" for h in sp["run_hours"])
        raise ValueError(f"only {hours} {model} runs are supported")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    errors: list[str] = []
    for source in SOURCES:
        for attempt in range(1, retries + 1):
            try:
                res = _client(source, model).retrieve(
                    date=run.strftime("%Y%m%d"), time=run.hour, type="fc", stream="oper",
                    param="tp", step=steps, target=str(tmp),
                )
                tmp.replace(target)  # atomic: never leave a half-written file in place
                log.info("fetched tp %s %s steps=%s from %s", model, run, steps, source)
                return Retrieval(model, run, list(steps), str(target), source,
                                 [u for u, _ in res.urls])
            except Exception as e:  # network errors, 404s, partial content
                msg = f"{source} attempt {attempt}: {type(e).__name__}: {e}"
                errors.append(msg)
                log.warning("tp download failed: %s", msg)
                if attempt < retries:
                    time.sleep(backoff_s * 2 ** (attempt - 1))
    tmp.unlink(missing_ok=True)
    raise AcquisitionError("all sources failed:\n" + "\n".join(errors))
