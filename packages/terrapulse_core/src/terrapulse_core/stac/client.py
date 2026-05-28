"""
STAC client for querying Sentinel-1 SLC scenes from the
Copernicus Data Space Ecosystem (CDSE).

CDSE STAC endpoint: https://catalogue.dataspace.copernicus.eu/stac/
Collection:         SENTINEL-1
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pystac_client

from terrapulse_core.stac.models import BBox, SceneStack, SentinelScene

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import pystac

logger = logging.getLogger(__name__)

CDSE_STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/"
SENTINEL1_COLLECTION = "sentinel-1-slc"  # lowercase-hyphen naming as of 2024+

# Maximum AOI area before we warn loudly (km²)
_WARN_AREA_KM2 = 10_000.0
_MAX_AREA_KM2 = 25_000.0

# Retry policy for transient CDSE/STAC errors (502/503/504, connection drops)
_RETRY_DELAYS_S: tuple[int, ...] = (5, 20, 60)  # 3 attempts after initial try
_TRANSIENT_HTTP_CODES: frozenset[int] = frozenset({502, 503, 504})
# Match HTTP error codes that may appear inside pystac_client error messages.
_HTTP_CODE_RE = re.compile(r"\b(5\d{2})\b")


class STACQueryError(Exception):
    """Raised when a STAC query fails or returns no usable results."""


def _is_transient(exc: BaseException) -> bool:
    """
    Return True if ``exc`` looks like a transient upstream failure that's
    worth retrying (502/503/504, connection reset, gateway timeout).
    """
    msg = str(exc)
    # Common CDSE outage signatures
    if "Bad Gateway" in msg or "Gateway Timeout" in msg or "Service Unavailable" in msg:
        return True
    if "Connection reset" in msg or "Connection aborted" in msg or "RemoteDisconnected" in msg:
        return True
    # HTTP code in the message body (pystac_client wraps requests errors as text)
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
    Thin wrapper around pystac-client for CDSE Sentinel-1 SLC queries.

    All heavy I/O (SLC downloads) is NOT done here — this class only
    queries metadata and returns typed ``SentinelScene`` objects.
    """

    def __init__(self, catalog_url: str = CDSE_STAC_URL) -> None:
        self._catalog_url = catalog_url
        self._client: pystac_client.Client | None = None

    def _get_client(self) -> pystac_client.Client:
        if self._client is None:
            logger.debug("Opening STAC catalog: %s", self._catalog_url)
            self._client = _with_retry(
                "stac.open",
                lambda: pystac_client.Client.open(self._catalog_url),
            )
        return self._client

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

        Parameters
        ----------
        aoi:
            WGS-84 bounding box.
        start_date / end_date:
            Inclusive time window.
        orbit_direction:
            ``"ascending"`` or ``"descending"``.
        max_scenes:
            Hard cap on the number of scenes returned.

        Returns
        -------
        List of ``SentinelScene`` objects sorted by acquisition date (oldest first).

        Raises
        ------
        STACQueryError
            If no scenes are found or the query fails.
        """
        area = aoi.area_km2
        if area > _MAX_AREA_KM2:
            raise STACQueryError(
                f"AOI area {area:.0f} km² exceeds the maximum allowed "
                f"{_MAX_AREA_KM2:.0f} km². Reduce your AOI."
            )
        if area > _WARN_AREA_KM2:
            logger.warning(
                "AOI area %.0f km² is large. Downloads may be slow.", area
            )

        client = self._get_client()

        time_range = f"{start_date.isoformat()}Z/{end_date.isoformat()}Z"
        orbit_upper = orbit_direction.upper()  # CDSE stores "ASCENDING" / "DESCENDING"

        logger.info(
            "Searching STAC: bbox=%s, time=%s, orbit=%s",
            aoi.as_list(),
            time_range,
            orbit_upper,
        )

        # Fetch without server-side query extension (avoids CDSE case-sensitivity
        # issues and deprecated query-extension support). Filter client-side instead.
        # The whole fetch is wrapped in _with_retry so a transient 502/503/504
        # midway through pagination triggers a clean restart with backoff.
        def _do_search() -> list[pystac.Item]:
            search = client.search(
                collections=[SENTINEL1_COLLECTION],
                bbox=aoi.as_list(),
                datetime=time_range,
                max_items=max_scenes * 4,  # fetch extra so client-side filter has room
                sortby="+datetime",
            )
            collected: list[pystac.Item] = []
            for item in search.items():
                item_orbit = str(item.properties.get("sat:orbit_state", "")).upper()
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
            # Non-transient errors get a friendly wrap too (strip HTML, etc.)
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
        Convenience method: query scenes and wrap in a ``SceneStack``.
        Picks the most common relative orbit to ensure a coherent stack.
        """
        scenes = self.search_scenes(
            aoi=aoi,
            start_date=start_date,
            end_date=end_date,
            orbit_direction=orbit_direction,
            max_scenes=max_scenes,
        )

        # Pick dominant relative orbit (most common in results)
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

    @staticmethod
    def _item_to_scene(item: pystac.Item) -> SentinelScene:
        """Map a raw pystac Item to a typed SentinelScene."""
        props = item.properties

        # CDSE uses sat:orbit_state; fall back to unknown
        orbit_dir = str(props.get("sat:orbit_state", "ascending")).lower()
        if orbit_dir not in ("ascending", "descending"):
            orbit_dir = "ascending"

        polarisation = str(props.get("sar:polarizations", ["VV"])[0]).upper()
        if polarisation not in ("VV", "VH", "VV+VH", "HH", "HV"):
            polarisation = "VV"

        assets = {
            key: asset.href
            for key, asset in item.assets.items()
            if asset.href
        }

        bbox_list = item.bbox or [0.0, 0.0, 0.0, 0.0]

        return SentinelScene(
            scene_id=item.id,
            datetime=item.datetime or datetime.utcnow(),
            bbox=BBox(
                west=bbox_list[0],
                south=bbox_list[1],
                east=bbox_list[2],
                north=bbox_list[3],
            ),
            orbit_direction=orbit_dir,  # type: ignore[arg-type]
            relative_orbit=int(props.get("sat:relative_orbit", 0)),
            polarisation=polarisation,  # type: ignore[arg-type]
            processing_level="L1",
            assets=assets,
            estimated_size_bytes=int(props.get("filesize", 0)),
        )

    def iter_scenes(
        self,
        aoi: BBox,
        start_date: datetime,
        end_date: datetime,
    ) -> Iterator[SentinelScene]:
        """Streaming iterator — use when you don't want to load all scenes at once."""
        client = self._get_client()
        time_range = f"{start_date.isoformat()}Z/{end_date.isoformat()}Z"
        search = client.search(
            collections=[SENTINEL1_COLLECTION],
            bbox=aoi.as_list(),
            datetime=time_range,
            sortby="+datetime",
        )
        for item in search.items():
            yield self._item_to_scene(item)
