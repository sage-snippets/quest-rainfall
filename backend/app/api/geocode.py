"""Place search via the Open-Meteo geocoding API (free, no key) with a small TTL cache."""
from __future__ import annotations

import logging
import time
from collections import OrderedDict

import httpx

log = logging.getLogger(__name__)


class GeocoderUnavailable(RuntimeError):
    pass


class Geocoder:
    def __init__(self, url: str, timeout_s: float, ttl_s: int, max_entries: int = 512,
                 transport: httpx.BaseTransport | None = None):
        self.url, self.ttl, self.max = url, ttl_s, max_entries
        self.client = httpx.Client(timeout=timeout_s, transport=transport,
                                   headers={"User-Agent": "quest-rainfall/1.0"})
        self._cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()

    def search(self, q: str, count: int = 8) -> tuple[list[dict], bool]:
        key = f"{q.strip().lower()}|{count}"
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            self._cache.move_to_end(key)
            return hit[1], True
        try:
            r = self.client.get(self.url, params={"name": q.strip(), "count": count,
                                                  "language": "en", "format": "json"})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("geocoder failed", extra={"error": f"{type(e).__name__}: {e}"})
            raise GeocoderUnavailable("geocoding service unavailable") from e
        results = [{
            "name": x.get("name"), "country": x.get("country"), "admin1": x.get("admin1"),
            "lat": x["latitude"], "lon": x["longitude"],
        } for x in data.get("results") or [] if "latitude" in x and "longitude" in x]
        self._cache[key] = (time.monotonic(), results)
        if len(self._cache) > self.max:
            self._cache.popitem(last=False)
        return results, False
