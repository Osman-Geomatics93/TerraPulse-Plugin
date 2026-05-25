"""
Classification layer loader — Phase 2.

Loads the ML classification raster (label COG), entropy COG, and confidence
COG into QGIS using a 5-class categorical palette matching the
``CLASS_COLORS_HEX`` scheme defined in ``terrapulse_core.ml.classifier``.

Style priority:
  1. Apply the programmatic 5-class palette (always available, no QML needed).
  2. Entropy / confidence layers get a sequential white→purple ramp.
"""

from __future__ import annotations

import logging
from pathlib import Path

from qgis.core import (
    QgsColorRampShader,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor

logger = logging.getLogger(__name__)

# 5-class palette — matches terrapulse_core.ml.classifier.CLASS_COLORS_HEX
# and DeformationClass enum values (0–4)
_CLASS_PALETTE: list[tuple[int, str, str]] = [
    (0,  "#2ECC71", "Stable"),
    (1,  "#F39C12", "Linear subsidence/uplift"),
    (2,  "#3498DB", "Seasonal deformation"),
    (3,  "#E74C3C", "Accelerating deformation"),
    (4,  "#95A5A6", "Anomalous / incoherent"),
]

# nodata label value used by ClassifyTask
_NODATA_LABEL = -1


def load_classification_layer(
    label_cog: Path,
    layer_name: str = "Deformation Classification",
    add_to_map: bool = True,
    group_name: str = "TerraPulse — ML",
) -> QgsRasterLayer:
    """
    Load the classification label COG as a 5-class categorical QGIS raster.

    Parameters
    ----------
    label_cog:
        Path to the classification COG written by ``ClassifyTask``.
        Band 1 = integer class labels (0–4, or -1 for nodata).
    layer_name:
        Name displayed in the QGIS Layers panel.
    add_to_map:
        If True, adds the layer to the current QGIS project.
    group_name:
        Layer tree group (created if missing).

    Returns
    -------
    Loaded and styled ``QgsRasterLayer``.

    Raises
    ------
    ValueError
        If the raster file is not valid.
    """
    if not label_cog.exists():
        raise ValueError(f"Classification COG not found: {label_cog}")

    layer = QgsRasterLayer(str(label_cog), layer_name)
    if not layer.isValid():
        raise ValueError(
            f"QGIS could not load classification raster: {label_cog}\n"
            + layer.error().message()
        )

    _apply_classification_style(layer)

    if add_to_map:
        _add_layer_to_project(layer, group_name)

    logger.info("Classification layer loaded: %s", layer_name)
    return layer


def load_entropy_layer(
    entropy_cog: Path,
    layer_name: str = "Classification Entropy (bits)",
    add_to_map: bool = True,
    group_name: str = "TerraPulse — ML",
) -> QgsRasterLayer:
    """
    Load the Shannon entropy COG — styled with a white→purple sequential ramp.

    Entropy of 0 = perfectly confident; log2(5) ≈ 2.32 bits = maximum uncertainty.
    """
    if not entropy_cog.exists():
        raise ValueError(f"Entropy COG not found: {entropy_cog}")

    layer = QgsRasterLayer(str(entropy_cog), layer_name)
    if not layer.isValid():
        raise ValueError(f"QGIS could not load entropy raster: {entropy_cog}")

    _apply_entropy_style(layer)

    if add_to_map:
        _add_layer_to_project(layer, group_name)

    logger.info("Entropy layer loaded: %s", layer_name)
    return layer


def load_confidence_layer(
    confidence_cog: Path,
    layer_name: str = "Classification Confidence",
    add_to_map: bool = True,
    group_name: str = "TerraPulse — ML",
) -> QgsRasterLayer:
    """
    Load the confidence (max class probability) COG — styled with a red→green ramp.
    """
    if not confidence_cog.exists():
        raise ValueError(f"Confidence COG not found: {confidence_cog}")

    layer = QgsRasterLayer(str(confidence_cog), layer_name)
    if not layer.isValid():
        raise ValueError(f"QGIS could not load confidence raster: {confidence_cog}")

    _apply_confidence_style(layer)

    if add_to_map:
        _add_layer_to_project(layer, group_name)

    logger.info("Confidence layer loaded: %s", layer_name)
    return layer


# ------------------------------------------------------------------
# Style helpers
# ------------------------------------------------------------------

def _apply_classification_style(layer: QgsRasterLayer) -> None:
    """Apply a 5-class categorical palette to a classification label raster."""
    classes = [
        QgsPalettedRasterRenderer.Class(
            value=cls_val,
            color=QColor(hex_color),
            label=label,
        )
        for cls_val, hex_color, label in _CLASS_PALETTE
    ]
    renderer = QgsPalettedRasterRenderer(
        layer.dataProvider(),
        bandNumber=1,
        classes=classes,
    )
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _apply_entropy_style(layer: QgsRasterLayer) -> None:
    """White → purple sequential ramp for Shannon entropy [0, 2.32 bits]."""
    import math
    max_entropy = math.log2(5)  # ≈ 2.32 bits for 5 classes
    _apply_pseudocolor(
        layer,
        breakpoints=[
            (0.0,        "#FFFFFF"),
            (max_entropy * 0.25, "#C994C7"),
            (max_entropy * 0.50, "#DF65B0"),
            (max_entropy * 0.75, "#980043"),
            (max_entropy,        "#49006A"),
        ],
        min_val=0.0,
        max_val=max_entropy,
    )


def _apply_confidence_style(layer: QgsRasterLayer) -> None:
    """Red → green ramp for confidence probability [0, 1]."""
    _apply_pseudocolor(
        layer,
        breakpoints=[
            (0.0,  "#D73027"),  # red — uncertain
            (0.25, "#FC8D59"),
            (0.50, "#FEE090"),
            (0.75, "#91CF60"),
            (1.0,  "#1A9850"),  # green — very confident
        ],
        min_val=0.0,
        max_val=1.0,
    )


def _apply_pseudocolor(
    layer: QgsRasterLayer,
    breakpoints: list[tuple[float, str]],
    min_val: float,
    max_val: float,
    band: int = 1,
) -> None:
    """Generic SingleBandPseudoColor renderer from a breakpoint list."""
    color_ramp_items = [
        QgsColorRampShader.ColorRampItem(v, QColor(c), str(v))
        for v, c in breakpoints
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
    """Add layer to the QGIS project inside a named group."""
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(group_name)
    if group is None:
        group = root.insertGroup(0, group_name)
    QgsProject.instance().addMapLayer(layer, addToLegend=False)
    group.addLayer(layer)
