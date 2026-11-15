"""Ingest the latest (or a given) IFS run into the local store and print a QC summary.

Usage: python scripts/ingest.py [--steps 0 3 6 ...] [--run 2026-11-15T00]
"""
import argparse, logging, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import ingest  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="ifs")
ap.add_argument("--steps", type=int, nargs="+", default=list(range(0, 49, 3)))
ap.add_argument("--run", help="YYYY-MM-DDTHH (UTC); default: latest complete run")
a = ap.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
run = datetime.strptime(a.run, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if a.run else None
m = ingest(a.model, a.steps, run)
print(f"run {m['run']}  steps {m['steps']}  source {m['retrieval']['source']}")
for iv in m["intervals"]:
    print(f"  {iv['start_step']:>3}-{iv['end_step']:<3}h max {iv['max_mm']:7.1f} mm  "
          f"clamped {iv['n_clamped_negatives']:6d} (min raw {iv['min_raw_diff_mm']:+.4f}, tol {iv['packing_tolerance_mm']:.4f})")
