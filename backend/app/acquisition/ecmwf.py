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
from datetime import datetime, timezone
from pathlib import Path

from ecmwf.opendata import Client

log = logging.getLogger(__name__)

# Mirrors supported by the official client, tried in order.
SOURCES = ("ecmwf", "aws", "azure")

# Published forecast steps (hours), verified against data.ecmwf.int on 2026-11-15.
# IFS 00/12z (stream oper): 0-144 by 3 h, then 150-360 by 6 h.
IFS_STEPS = list(range(0, 145, 3)) + list(range(150, 361, 6))


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


def latest_run(model: str = "ifs", last_step: int = 144, source: str = "ecmwf") -> datetime:
    """Latest run whose `tp` at `last_step` is published (i.e. the run is complete up to it).

    ECMWF publishes steps progressively, so probing the *last* step we need
    avoids picking up a run that is still being disseminated.
    """
    run = _client(source, model).latest(type="fc", stream="oper", param="tp", step=last_step)
    return run.replace(tzinfo=timezone.utc)


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
    if model == "ifs":
        bad = sorted(set(steps) - set(IFS_STEPS))
        if bad:
            raise ValueError(f"steps not published for IFS: {bad}")
    if run.hour not in (0, 12):
        # 06/18z IFS runs are a different stream (scda) with a 90 h horizon; not used.
        raise ValueError("only 00z and 12z IFS runs are supported")

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
