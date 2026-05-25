"""
QGIS message bar helper utilities.

Provides thin wrappers around ``iface.messageBar()`` so callers don't have
to import Qgis constants directly.  All functions are safe to call from the
main thread only (QGIS constraint).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qgis.gui import QgisInterface  # type: ignore[import]

logger = logging.getLogger(__name__)

# Duration constants (seconds)
DURATION_SHORT = 4
DURATION_MEDIUM = 8
DURATION_LONG = 0   # 0 = sticky (user must dismiss)


def push_info(
    iface: "QgisInterface",
    message: str,
    title: str = "TerraPulse",
    duration: int = DURATION_SHORT,
) -> None:
    """Show a blue information bar message."""
    try:
        from qgis.core import Qgis  # type: ignore[import]
        iface.messageBar().pushMessage(title, message, level=Qgis.Info, duration=duration)
    except Exception as exc:
        logger.warning("message_bar.push_info failed: %s", exc)


def push_success(
    iface: "QgisInterface",
    message: str,
    title: str = "TerraPulse",
    duration: int = DURATION_SHORT,
) -> None:
    """Show a green success bar message."""
    try:
        from qgis.core import Qgis  # type: ignore[import]
        iface.messageBar().pushMessage(title, message, level=Qgis.Success, duration=duration)
    except Exception as exc:
        logger.warning("message_bar.push_success failed: %s", exc)


def push_warning(
    iface: "QgisInterface",
    message: str,
    title: str = "TerraPulse",
    duration: int = DURATION_MEDIUM,
) -> None:
    """Show an orange warning bar message."""
    try:
        from qgis.core import Qgis  # type: ignore[import]
        iface.messageBar().pushMessage(title, message, level=Qgis.Warning, duration=duration)
    except Exception as exc:
        logger.warning("message_bar.push_warning failed: %s", exc)


def push_error(
    iface: "QgisInterface",
    message: str,
    title: str = "TerraPulse",
    duration: int = DURATION_LONG,
) -> None:
    """Show a red error bar message (sticky by default — user must dismiss)."""
    try:
        from qgis.core import Qgis  # type: ignore[import]
        iface.messageBar().pushMessage(title, message, level=Qgis.Critical, duration=duration)
    except Exception as exc:
        logger.warning("message_bar.push_error failed: %s", exc)


def push_task_complete(
    iface: "QgisInterface",
    run_id: str,
    success: bool,
    detail: str = "",
) -> None:
    """
    Post-task notification (success or failure).

    Parameters
    ----------
    iface:    QgisInterface
    run_id:   8-char abbreviated run identifier
    success:  True = green success, False = red error
    detail:   Optional extra text (error message or output path)
    """
    short_id = run_id[:8]
    if success:
        msg = f"Run {short_id} complete."
        if detail:
            msg += f" Output: {detail}"
        push_success(iface, msg, duration=DURATION_MEDIUM)
    else:
        msg = f"Run {short_id} failed."
        if detail:
            msg += f" {detail}"
        push_error(iface, msg)
