"""
Deformation layer loader — full Phase 1 implementation.

Loads the velocity COG into QGIS as a QgsRasterLayer and applies the
TerraPulse diverging colour ramp (blue = uplift, white = stable, red = subsidence).

Style application priority:
  1. Load ``velocity.qml`` from the plugin resources directory (preferred).
  2. Fall back to a programmatically built SingleBandPseudoColor renderer
     using ``_VELOCITY_BREAKPOINTS`` (if QML file is missing or fails to load).
"""

from __future__ import annotations

import logging
from pathlib import Path

from qgis.core import (
    QgsColorRampShader,
    QgsProject,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor

logger = logging.getLogger(__name__)

# QML style file — delivered with the plugin
_STYLE_PATH = Path(__file__).parent.parent / "resources" / "styles" / "velocity.qml"

# Velocity colour ramp (mm/yr → hex colour)
# Diverging ramp: blue (uplift) ↔ white (stable) ↔ red (subsidence)
_VELOCITY_BREAKPOINTS: list[tuple[float, str]] = [
    (-50.0, "#053061"),
    (-20.0, "#2166AC"),
    (-10.0, "#4393C3"),
    (-5.0,  "#92C5DE"),
    (-2.0,  "#D1E5F0"),
    (0.0,   "#F7F7F7"),
    (2.0,   "#FDDBC7"),
    (5.0,   "#F4A582"),
    (10.0,  "#D6604D"),
    (20.0,  "#B2182B"),
    (50.0,  "#67001F"),
]


class DeformationLayerError(Exception):
    """Raised when a velocity COG cannot be loaded."""


def load_velocity_layer(
    velocity_cog: Path,
    layer_name: str = "LOS Velocity (mm/yr)",
    add_to_map: bool = True,
    group_name: str = "TerraPulse",
) -> QgsRasterLayer:
    """
    Load the velocity COG as a styled QGIS raster layer.

    Parameters
    ----------
    velocity_cog:
        Path to a COG GeoTIFF containing band 1 = LOS velocity in mm/yr.
    layer_name:
        Name shown in the QGIS Layers panel.
    add_to_map:
        If True, adds the layer to the current QGIS project and triggers
        a map canvas refresh.
    group_name:
        Name of the layer tree group to add the layer into.
        Created if it doesn't already exist.

    Returns
    -------
    Loaded and styled ``QgsRasterLayer``.

    Raises
    ------
    DeformationLayerError
        If the raster file is not valid or cannot be loaded.
    """
    if not velocity_cog.exists():
        raise DeformationLayerError(
            f"Velocity COG not found: {velocity_cog}\n"
            "Ensure the processing run completed successfully."
        )

    layer = QgsRasterLayer(str(velocity_cog), layer_name)
    if not layer.isValid():
        raise DeformationLayerError(
            f"QGIS could not load raster: {velocity_cog}\n"
            "Error: " + layer.error().message()
        )

    _apply_velocity_style(layer)

    if add_to_map:
        _add_layer_to_project(layer, group_name)

    logger.info("Velocity layer loaded: %s", layer_name)
    return layer


def load_coherence_layer(
    coherence_cog: Path,
    layer_name: str = "Temporal Coherence",
    add_to_map: bool = True,
    group_name: str = "TerraPulse",
) -> QgsRasterLayer:
    """
    Load the mean temporal coherence COG as a QGIS raster layer.
    Styled with a white→green sequential ramp [0, 1].
    """
    if not coherence_cog.exists():
        raise DeformationLayerError(f"Coherence COG not found: {coherence_cog}")

    layer = QgsRasterLayer(str(coherence_cog), layer_name)
    if not layer.isValid():
        raise DeformationLayerError(
            f"QGIS could not load coherence raster: {coherence_cog}"
        )

    _apply_coherence_style(layer)

    if add_to_map:
        _add_layer_to_project(layer, group_name)

    logger.info("Coherence layer loaded: %s", layer_name)
    return layer


# ------------------------------------------------------------------
# Style helpers
# ------------------------------------------------------------------

def _apply_velocity_style(layer: QgsRasterLayer) -> None:
    """
    Apply the TerraPulse velocity colour ramp to a QgsRasterLayer.

    Tries QML file first; falls back to programmatic renderer.
    """
    if _STYLE_PATH.exists():
        _, style_msg = layer.loadNamedStyle(str(_STYLE_PATH))
        if style_msg == "":
            logger.debug("Velocity QML style applied from %s", _STYLE_PATH)
            layer.triggerRepaint()
            return
        logger.warning("QML style load returned: %s — using programmatic fallback.", style_msg)

    # Programmatic fallback
    _apply_pseudocolor_renderer(
        layer,
        breakpoints=_VELOCITY_BREAKPOINTS,
        min_val=-50.0,
        max_val=50.0,
    )
    logger.debug("Velocity programmatic style applied.")


def _apply_coherence_style(layer: QgsRasterLayer) -> None:
    """Apply a white→green sequential ramp for coherence [0, 1]."""
    _apply_pseudocolor_renderer(
        layer,
        breakpoints=[
            (0.0,  "#FFFFFF"),
            (0.25, "#C7E9C0"),
            (0.5,  "#74C476"),
            (0.75, "#238B45"),
            (1.0,  "#00441B"),
        ],
        min_val=0.0,
        max_val=1.0,
    )


def _apply_pseudocolor_renderer(
    layer: QgsRasterLayer,
    breakpoints: list[tuple[float, str]],
    min_val: float,
    max_val: float,
    band: int = 1,
) -> None:
    """Build and assign a SingleBandPseudoColor renderer from breakpoint list."""
    color_ramp_items = [
        QgsColorRampShader.ColorRampItem(
            value, QColor(hex_color), str(value)
        )
        for value, hex_color in breakpoints
    ]

    ramp_shader = QgsColorRampShader(min_val, max_val)
    ramp_shader.setColorRampType(QgsColorRampShader.Interpolated)
    ramp_shader.setColorRampItemList(color_ramp_items)

    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(ramp_shader)

    renderer = QgsSingleBandPseudoColorRenderer(
        layer.dataProvider(), band, raster_shader
    )
    renderer.setClassificationMin(min_val)
    renderer.setClassificationMax(max_val)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _add_layer_to_project(layer: QgsRasterLayer, group_name: str) -> None:
    """Add layer to the QGIS project layer tree inside a named group."""
    root = QgsProject.instance().layerTreeRoot()

    # Find or create the TerraPulse group
    group = root.findGroup(group_name)
    if group is None:
        group = root.insertGroup(0, group_name)

    QgsProject.instance().addMapLayer(layer, addToLegend=False)
    group.addLayer(layer)
