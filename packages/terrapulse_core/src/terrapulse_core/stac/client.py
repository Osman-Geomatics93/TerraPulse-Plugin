"""
STAC client for querying Sentinel-1 SLC scenes from the
Copernicus Data Space Ecosystem (CDSE).

CDSE STAC endpoint: https://catalogue.dataspace.copernicus.eu/stac/
Collection:         SENTINEL-1
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterator

import pystac
import pystac_client

from terrapulse_core.stac.models import BBox, SentinelScene, SceneStack

logger = logging.getLogger(__name__)

CDSE_STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/"
SENTINEL1_COLLECTION = "sentinel-1-slc"  # lowercase-hyphen naming as of 2024+

# Maximum AOI area before we warn loudly (km²)
_WARN_AREA_KM2 = 10_000.0
_MAX_AREA_KM2 = 25_000.0


class STACQueryError(Exception):
    """Raised when a STAC query fails or returns no usable results."""


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
            self._client = pystac_client.Client.open(self._catalog_url)
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
        search = client.search(
            collections=[SENTINEL1_COLLECTION],
            bbox=aoi.as_list(),
            datetime=time_range,
            max_items=max_scenes * 4,  # fetch extra so client-side filter has room
            sortby="+datetime",
        )

        items: list[pystac.Item] = []
        for item in search.items():
            props = item.properties

            # Filter by orbit direction (case-insensitive)
            item_orbit = str(props.get("sat:orbit_state", "")).upper()
            if item_orbit and item_orbit != orbit_upper:
                continue

            # (collection is already SLC-only; no product_type filter needed)

            items.append(item)
            if len(items) >= max_scenes:
                break

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
