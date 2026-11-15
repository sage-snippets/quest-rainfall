# QUEST Rainfall: ECMWF Precipitation Forecast Application

Precipitation forecast web application built on genuine **ECMWF Open Data** (IFS and AIFS),
developed for the QUEST Lab Full Stack AI Pre-doctoral application (IISc Bangalore).

> Status: **M0, project scaffold.** The full documentation will be written as milestones land.

## Layout

```
backend/            FastAPI service + ECMWF acquisition and GRIB2 processing
  app/acquisition/  ECMWF Open Data retrieval (ecmwf-opendata client)
  app/processing/   GRIB2 decoding, accumulated→interval precipitation, stats
  app/api/          HTTP routes
  scripts/          Verification utilities (e.g. verify_grib.py)
  tests/            pytest suite; tests/fixtures holds a real ECMWF GRIB2 sample
frontend/           React + TypeScript + MapLibre UI (from M5)
docs/               Technical notes
```

## Backend quick start

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on Linux/macOS
pip install -r requirements-dev.txt
pytest
python scripts/verify_grib.py --steps 0 6 12   # live ECMWF download + decode check
```

## Data attribution

Forecast data: © ECMWF, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
All values are **model forecasts**, not observations.
