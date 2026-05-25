"""
AOI rubber-band drawing map tool — full Phase 1 implementation.

User interaction:
  Left-click  → add vertex
  Right-click → close polygon (same as double-click)
  Escape      → cancel, clear rubber band
  Enter/Return → close polygon if ≥ 3 vertices

Emits ``aoi_captured(wkt_string)`` with the AOI polygon in WGS-84 (EPSG:4326).
The tool reprojects from the current map canvas CRS automatically.

Usage::

    tool = AOIMapTool(iface.mapCanvas())
    tool.aoi_captured.connect(on_aoi_ready)   # on_aoi_ready(wkt: str)
    iface.mapCanvas().setMapTool(tool)
    # User draws polygon…
    # on_aoi_ready is called with the WGS-84 WKT
"""

from __future__ import annotations

import logging

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas, QgsMapToolEmitPoint, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeyEvent, QMouseEvent

logger = logging.getLogger(__name__)

_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# Style constants
_FILL_COLOR = QColor(26, 107, 74, 60)    # brand green, 24% opacity
_STROKE_COLOR = QColor(26, 107, 74, 220)  # brand green, opaque
_STROKE_WIDTH = 2


class AOIMapTool(QgsMapToolEmitPoint):
    """
    Interactive polygon AOI drawing tool.

    Signals
    -------
    aoi_captured(wkt: str)
        Emitted once after the user closes the polygon.
        ``wkt`` is a POLYGON in WGS-84 (EPSG:4326).
    aoi_cancelled()
        Emitted if the user presses Escape or deactivates without completing.
    """

    aoi_captured = pyqtSignal(str)
    aoi_cancelled = pyqtSignal()

    def __init__(self, canvas: QgsMapCanvas) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._points: list[QgsPointXY] = []
        self._cancelled = False

        # Polygon rubber band (filled)
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setFillColor(_FILL_COLOR)
        self._rubber_band.setStrokeColor(_STROKE_COLOR)
        self._rubber_band.setWidth(_STROKE_WIDTH)

        # Line rubber band for the moving edge (tip → cursor)
        self._tip_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._tip_band.setStrokeColor(QColor(26, 107, 74, 160))
        self._tip_band.setWidth(1)
        self._tip_band.setLineStyle(Qt.DashLine)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def canvasPressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._add_vertex(event)
        elif event.button() == Qt.RightButton:
            self._finalise()

    def canvasMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Update the tip rubber band to show the live edge to the cursor."""
        if not self._points:
            return
        cursor_pt = self.toMapCoordinates(event.pos())
        self._tip_band.reset(QgsWkbTypes.LineGeometry)
        self._tip_band.addPoint(self._points[-1])
        self._tip_band.addPoint(cursor_pt, True)

    def canvasDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Double-click finalises the polygon."""
        # Add the vertex at the double-click position (the first click already added it)
        self._finalise()

    # ------------------------------------------------------------------
    # Keyboard events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if key == Qt.Key_Escape:
            self._cancel()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._finalise()
        elif key == Qt.Key_Backspace and self._points:
            # Remove last vertex
            self._points.pop()
            self._redraw_rubber_band()
        else:
            super().keyPressEvent(event)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Tool lifecycle
    # ------------------------------------------------------------------

    def deactivate(self) -> None:
        """Called by QGIS when another map tool is activated."""
        self._reset()
        super().deactivate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_vertex(self, event: QMouseEvent) -> None:
        """Add a vertex at the mouse position."""
        pt = self.toMapCoordinates(event.pos())
        self._points.append(pt)
        self._redraw_rubber_band()
        logger.debug("AOI vertex added: (%.4f, %.4f) — %d total", pt.x(), pt.y(), len(self._points))

    def _redraw_rubber_band(self) -> None:
        """Re-render the rubber band from the current points list."""
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for pt in self._points:
            self._rubber_band.addPoint(pt, False)  # False = don't update yet
        if self._points:
            self._rubber_band.addPoint(self._points[0], True)  # close + update

    def _finalise(self) -> None:
        """Close the polygon and emit ``aoi_captured`` with WGS-84 WKT."""
        if len(self._points) < 3:
            logger.warning("AOI needs at least 3 vertices (got %d). Keep drawing.", len(self._points))
            return

        # Build closed polygon geometry in canvas CRS
        closed = self._points + [self._points[0]]
        geom = QgsGeometry.fromPolygonXY([closed])

        if not geom or geom.isEmpty():
            logger.error("Failed to build polygon geometry from vertices.")
            self._cancel()
            return

        # Reproject to WGS-84
        canvas_crs = self._canvas.mapSettings().destinationCrs()
        if canvas_crs != _WGS84:
            transform = QgsCoordinateTransform(
                canvas_crs,
                _WGS84,
                QgsProject.instance(),
            )
            success = geom.transform(transform)
            if success != 0:  # 0 = Qgis.GeometryOperationResult.Success
                logger.error(
                    "CRS reprojection failed (canvas=%s → EPSG:4326). "
                    "Emitting WKT in canvas CRS as fallback.",
                    canvas_crs.authid(),
                )

        wkt = geom.asWkt(precision=7)
        logger.info("AOI captured: %s…", wkt[:80])

        self._reset()
        self.aoi_captured.emit(wkt)

        # Restore the previous map tool
        self._canvas.unsetMapTool(self)

    def _cancel(self) -> None:
        """Discard the in-progress polygon."""
        logger.info("AOI drawing cancelled.")
        self._reset()
        self.aoi_cancelled.emit()
        self._canvas.unsetMapTool(self)

    def _reset(self) -> None:
        """Clear all rubber bands and vertex list."""
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        self._tip_band.reset(QgsWkbTypes.LineGeometry)
        self._points.clear()
