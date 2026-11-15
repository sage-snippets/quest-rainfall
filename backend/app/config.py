"""Runtime configuration from environment variables (prefix QR_). No secrets required."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QR_", env_file=".env", extra="ignore")

    data_root: Path = Path(__file__).resolve().parents[1] / "data" / "store"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    log_level: str = "INFO"
    max_grid_cells: int = 250_000          # ~ India box is 15k cells; global is 1.04M
    array_cache_size: int = 48
    tile_cache_size: int = 4096            # rendered PNG tiles kept in RAM (typ. 1-10 kB)             # decoded 0.25° grids kept in RAM (~4 MB each)
    static_dir: Path | None = None         # built frontend (index.html); served at / if set
    refresh_enabled: bool = False          # background ECMWF ingest at startup + periodically
    refresh_interval_s: int = 3600
    ingest_max_step: int = 72              # forecast horizon to ingest (h); IFS publishes to 360
    keep_runs: int = 2
    geocode_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    geocode_timeout_s: float = 5.0
    geocode_cache_ttl_s: int = 86_400
