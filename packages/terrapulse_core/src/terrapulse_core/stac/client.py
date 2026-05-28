"""
STAC client for querying Sentinel-1 SLC scenes from the
Copernicus Data Space Ecosystem (CDSE).

CDSE STAC endpoint: https://catalogue.dataspace.copernicus.eu/stac/
Collection:         sentinel-1-slc

Implementation note
-------------------
This client deliberately does NOT depend on ``pystac-client``. The STAC search
endpoint is just a JSON HTTP POST, and avoiding pystac-client means the QGIS
plugin process can call STAC search directly without installing 30+ wheels into
QGIS's bundled Python. ``requests`` is the only external dependency, and it
ships with QGIS.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import requests

from terrapulse_core.stac.models import BBox, SceneStack, SentinelScene

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

CDSE_STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/"
SENTINEL1_COLLECTION = "sentinel-1-slc"  # lowercase-hyphen naming as of 2024+

# Maximum AOI area before we warn loudly (km²)
_WARN_AREA_KM2 = 10_000.0
_MAX_AREA_KM2 = 25_000.0

# Retry policy for transient CDSE/STAC errors (502/503/504, connection drops)
_RETRY_DELAYS_S: tuple[int, ...] = (5, 20, 60)  # 3 attempts after initial try
_TRANSIENT_HTTP_CODES: frozenset[int] = frozenset({502, 503, 504})
# Match HTTP error codes that may appear inside error messages.
_HTTP_CODE_RE = re.compile(r"\b(5\d{2})\b")

# Network timeouts
_CONNECT_TIMEOUT_S = 15
_READ_TIMEOUT_S = 60
# Page size for /search pagination. CDSE caps at 1000; 100 is a fine default.
_PAGE_LIMIT = 100


class STACQueryError(Exception):
    """Raised when a STAC query fails or returns no usable results."""


def _is_transient(exc: BaseException) -> bool:
    """
    Return True if ``exc`` looks like a transient upstream failure that's
    worth retrying (502/503/504, connection reset, gateway timeout).
    """
    msg = str(exc)
    if "Bad Gateway" in msg or "Gateway Timeout" in msg or "Service Unavailable" in msg:
        return True
    if "Connection reset" in msg or "Connection aborted" in msg or "RemoteDisconnected" in msg:
        return True
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    m = _HTTP_CODE_RE.search(msg)
    return bool(m and int(m.group(1)) in _TRANSIENT_HTTP_CODES)


def _friendly_error(exc: BaseException) -> str:
    """
    Produce a clean, user-facing string for an upstream STAC error. Strips HTML
    error pages (CDSE's nginx returns ``<html><head><title>502 Bad Gateway``…)
    and substitutes a one-line explanation.
    """
    msg = str(exc).strip()
    if "<html" in msg.lower() or "<head" in msg.lower():
        m = _HTTP_CODE_RE.search(msg)
        code = m.group(1) if m else "5xx"
        return (
            f"Copernicus Data Space is temporarily unavailable (HTTP {code}). "
            "This is a CDSE-side outage, not a plugin bug. "
            "Wait a few minutes and try again, or check "
            "https://dataspace.copernicus.eu/news for status updates."
        )
    return msg


def _with_retry(label: str, fn: Callable[[], Any]) -> Any:
    """
    Call ``fn()`` and retry on transient failures with exponential backoff
    (5s, 20s, 60s). Non-transient failures propagate immediately.
    """
    last_exc: BaseException | None = None
    for attempt, delay in enumerate((0, *_RETRY_DELAYS_S)):
        if delay:
            logger.warning(
                "%s: transient failure, retry %d/%d in %ds — %s",
                label, attempt, len(_RETRY_DELAYS_S), delay, last_exc,
            )
            time.sleep(delay)
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_transient(exc):
                raise
    assert last_exc is not None
    raise STACQueryError(_friendly_error(last_exc)) from last_exc


class STACClient:
    """
    CDSE Sentinel-1 SLC STAC search via direct HTTP — no pystac-client dependency.

    All heavy I/O (SLC downloads) is NOT done here — this class only
    queries metadata and returns typed ``SentinelScene`` objects.
    """

    def __init__(self, catalog_url: str = CDSE_STAC_URL) -> None:
        self._catalog_url = catalog_url.rstrip("/") + "/"
        self._session: requests.Session | None = None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "terrapulse-core/STAC-client",
                "Accept": "application/json",
            })
        return self._session

    def close(self) -> None:
        if self._session is not None:
            with contextlib.suppress(Exception):
                self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_scenes(
        self,
        aoi: BBox,
        start_date: datetime,
        end_date: datetime,
        orbit_direction: str = "ascending",
        max_scenes: int = 30,
    ) -> list[SentinelScene]:
        """
        Query CDSE STAC for Sentinel-1 SLC scenes intersecting ``aoi``
        within the given time window.

        Returns a list of ``SentinelScene`` sorted oldest-first.

        Raises ``STACQueryError`` on network/server errors, or if no scenes match.
        """
        area = aoi.area_km2
        if area > _MAX_AREA_KM2:
            raise STACQueryError(
                f"AOI area {area:.0f} km² exceeds the maximum allowed "
                f"{_MAX_AREA_KM2:.0f} km². Reduce your AOI."
            )
        if area > _WARN_AREA_KM2:
            logger.warning("AOI area %.0f km² is large. Downloads may be slow.", area)

        time_range = f"{start_date.isoformat()}Z/{end_date.isoformat()}Z"
        orbit_upper = orbit_direction.upper()  # CDSE stores "ASCENDING" / "DESCENDING"

        logger.info(
            "Searching STAC: bbox=%s, time=%s, orbit=%s",
            aoi.as_list(), time_range, orbit_upper,
        )

        # Filter client-side for orbit (avoids CDSE query-extension quirks).
        # The whole fetch is wrapped in _with_retry so a transient 502/503/504
        # midway through pagination triggers a clean restart with backoff.
        target = max_scenes * 4  # fetch extra for client-side filter headroom

        def _do_search() -> list[dict[str, Any]]:
            collected: list[dict[str, Any]] = []
            for item in self._iter_search_items(
                bbox=aoi.as_list(),
                datetime_range=time_range,
                target_count=target,
            ):
                item_orbit = str(item.get("properties", {}).get("sat:orbit_state", "")).upper()
                if item_orbit and item_orbit != orbit_upper:
                    continue
                collected.append(item)
                if len(collected) >= max_scenes:
                    break
            return collected

        try:
            items = _with_retry("stac.search", _do_search)
        except STACQueryError:
            raise
        except BaseException as exc:  # noqa: BLE001
            raise STACQueryError(_friendly_error(exc)) from exc

        if not items:
            raise STACQueryError(
                f"No Sentinel-1 SLC scenes found for AOI={aoi.as_list()}, "
                f"time={time_range}, orbit={orbit_direction}.\n"
                "Try: (1) switch orbit to Descending, "
                "(2) use an older date range (e.g. 2023-01-01 → 2024-01-01), "
                "(3) draw a larger AOI."
            )

        logger.info("Found %d scenes after client-side filtering.", len(items))
        return [self._item_to_scene(item) for item in items]

    def build_stack(
        self,
        aoi: BBox,
        start_date: datetime,
        end_date: datetime,
        orbit_direction: str = "ascending",
        max_scenes: int = 30,
    ) -> SceneStack:
        """
        Query scenes and wrap them in a ``SceneStack`` of the dominant relative orbit.
        """
        scenes = self.search_scenes(
            aoi=aoi,
            start_date=start_date,
            end_date=end_date,
            orbit_direction=orbit_direction,
            max_scenes=max_scenes,
        )

        orbit_counts: dict[int, int] = {}
        for s in scenes:
            orbit_counts[s.relative_orbit] = orbit_counts.get(s.relative_orbit, 0) + 1
        dominant_orbit = max(orbit_counts, key=lambda k: orbit_counts[k])

        filtered = [s for s in scenes if s.relative_orbit == dominant_orbit]
        total_bytes = sum(s.estimated_size_bytes for s in filtered)

        return SceneStack(
            scenes=filtered,
            aoi=aoi,
            orbit_direction=orbit_direction,  # type: ignore[arg-type]
            relative_orbit=dominant_orbit,
            total_size_bytes=total_bytes,
        )

    def iter_scenes(
        self,
        aoi: BBox,
        start_date: datetime,
        end_date: datetime,
    ) -> Iterator[SentinelScene]:
        """Streaming iterator — use when you don't want all scenes in memory."""
        time_range = f"{start_date.isoformat()}Z/{end_date.isoformat()}Z"
        for item in self._iter_search_items(
            bbox=aoi.as_list(),
            datetime_range=time_range,
            target_count=None,
        ):
            yield self._item_to_scene(item)

    # ------------------------------------------------------------------
    # HTTP search + pagination
    # ------------------------------------------------------------------

    def _iter_search_items(
        self,
        bbox: list[float],
        datetime_range: str,
        target_count: int | None,
    ) -> Iterator[dict[str, Any]]:
        """
        Iterate STAC Items from POST /search, following ``rel=next`` links until
        ``target_count`` items have been yielded or the catalog is exhausted.

        ``target_count=None`` means "iterate forever" (the caller stops early).
        """
        session = self._get_session()
        search_url = f"{self._catalog_url}search"

        body: dict[str, Any] = {
            "collections": [SENTINEL1_COLLECTION],
            "bbox": bbox,
            "datetime": datetime_range,
            "limit": _PAGE_LIMIT,
            "sortby": [{"field": "datetime", "direction": "asc"}],
        }

        yielded = 0
        method = "POST"
        url: str | None = search_url
        next_body: dict[str, Any] | None = body

        while url is not None:
            if method == "POST":
                resp = session.post(
                    url,
                    json=next_body,
                    timeout=(_CONNECT_TIMEOUT_S, _READ_TIMEOUT_S),
                )
            else:
                resp = session.get(
                    url,
                    timeout=(_CONNECT_TIMEOUT_S, _READ_TIMEOUT_S),
                )

            if resp.status_code != 200:
                raise STACQueryError(
                    f"STAC search returned HTTP {resp.status_code}: {resp.text[:300]}"
                )

            page = resp.json()
            for feature in page.get("features", []):
                yield feature
                yielded += 1
                if target_count is not None and yielded >= target_count:
                    return

            # Follow rel=next link if present (STAC API spec)
            url, method, next_body = _next_link(page)

    # ------------------------------------------------------------------
    # Item → SentinelScene mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _item_to_scene(item: dict[str, Any]) -> SentinelScene:
        """Map a raw STAC Item dict to a typed SentinelScene."""
        props = item.get("properties", {}) or {}

        orbit_dir = str(props.get("sat:orbit_state", "ascending")).lower()
        if orbit_dir not in ("ascending", "descending"):
            orbit_dir = "ascending"

        polarisations = props.get("sar:polarizations") or ["VV"]
        polarisation = str(polarisations[0]).upper() if polarisations else "VV"
        if polarisation not in ("VV", "VH", "VV+VH", "HH", "HV"):
            polarisation = "VV"

        assets_raw = item.get("assets", {}) or {}
        assets = {
            key: asset.get("href", "")
            for key, asset in assets_raw.items()
            if isinstance(asset, dict) and asset.get("href")
        }

        bbox_list = item.get("bbox") or [0.0, 0.0, 0.0, 0.0]

        # datetime from properties (ISO-8601) → datetime obj
        dt_raw = props.get("datetime") or item.get("datetime")
        try:
            scene_dt = (
                datetime.fromisoformat(str(dt_raw).replace("Z", "+00:00"))
                if dt_raw else datetime.utcnow()
            )
        except (TypeError, ValueError):
            scene_dt = datetime.utcnow()

        return SentinelScene(
            scene_id=item.get("id", "unknown"),
            datetime=scene_dt,
            bbox=BBox(
                west=float(bbox_list[0]),
                south=float(bbox_list[1]),
                east=float(bbox_list[2]),
                north=float(bbox_list[3]),
            ),
            orbit_direction=orbit_dir,  # type: ignore[arg-type]
            relative_orbit=int(props.get("sat:relative_orbit", 0)),
            polarisation=polarisation,  # type: ignore[arg-type]
            processing_level="L1",
            assets=assets,
            estimated_size_bytes=int(props.get("filesize", 0)),
        )


def _next_link(page: dict[str, Any]) -> tuple[str | None, str, dict[str, Any] | None]:
    """
    Extract the next-page URL + method + body from a STAC FeatureCollection.

    STAC API spec: pagination uses a ``links`` entry with ``"rel": "next"``.
    For POST searches the next link includes ``"method": "POST"`` and a
    ``"body"`` field with the query to repeat.
    """
    for link in page.get("links", []) or []:
        if not isinstance(link, dict):
            continue
        if link.get("rel") != "next":
            continue
        href = link.get("href")
        if not href:
            continue
        method = str(link.get("method", "GET")).upper()
        body = link.get("body") if method == "POST" else None
        return href, method, body
    return None, "GET", None
