"""
Report renderer: Jinja2 HTML → optional WeasyPrint PDF.

Produces both an interactive HTML report and a print-ready PDF
from the same Jinja2 template.  WeasyPrint is optional; if it is not
installed (or fails) only the HTML is written.

Usage::

    config = ReportConfig(output_dir=Path("/output"), generate_pdf=False)
    renderer = ReportRenderer(config)
    output = renderer.render(narrative_ctx, ranking_result)
    print(output.html_path)
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terrapulse_core.reporting.llm import LLMNarrativeClient, NarrativeContext
    from terrapulse_core.risk.ranking import RankingResult

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# TerraPulse semantic version embedded in reports
_VERSION = "0.1.0"


@dataclass
class ReportConfig:
    """Configuration for report generation."""

    output_dir: Path
    title: str = "TerraPulse Deformation Analysis"
    logo_path: Path | None = None
    include_interactive_charts: bool = False   # requires CDN access in viewer
    generate_pdf: bool = False                 # requires weasyprint
    generate_html: bool = True
    llm_client: LLMNarrativeClient | None = None
    run_id: str = ""
    engine_name: str = "pygmtsar"
    processing_mode: str = "standard"
    recipe_path: str = "recipe.yaml"


@dataclass
class ReportOutput:
    """Paths to generated report files."""

    html_path: Path | None = None
    pdf_path: Path | None = None
    success: bool = False
    warnings: list[str] = field(default_factory=list)


class ReportRenderer:
    """
    Renders the TerraPulse deformation analysis report.

    Steps
    -----
    1. Generate LLM (or templated) narrative.
    2. Encode velocity image to base64 (if provided).
    3. Render Jinja2 template → HTML string.
    4. Write HTML to ``config.output_dir / report.html``.
    5. Optionally convert to PDF via WeasyPrint.
    """

    def __init__(self, config: ReportConfig) -> None:
        self._config = config

    def render(
        self,
        narrative_context: NarrativeContext,
        ranking_result: RankingResult,
        velocity_png: Path | None = None,
        classification_png: Path | None = None,
    ) -> ReportOutput:
        """
        Render HTML (and optionally PDF) report.

        Parameters
        ----------
        narrative_context:
            Structured analysis metadata for the LLM / template.
        ranking_result:
            Risk-ranked OSM assets from ``RiskRanker.rank()``.
        velocity_png:
            Optional PNG image of the velocity map (embedded as base64).
        classification_png:
            Optional PNG of the classification layer (embedded as base64).

        Returns
        -------
        ``ReportOutput`` with paths to written files and any warnings.
        """
        warnings: list[str] = []
        self._config.output_dir.mkdir(parents=True, exist_ok=True)

        # ---- 1. Narrative text ----
        summary, recommendations = self._generate_narrative(
            narrative_context, warnings
        )

        # ---- 2. Encode images ----
        vel_b64 = _encode_image(velocity_png)
        cls_b64 = _encode_image(classification_png)

        # ---- 3. Render Jinja2 template ----
        try:
            from jinja2 import (  # type: ignore[import]
                Environment,
                FileSystemLoader,
                select_autoescape,
            )
        except ImportError as exc:
            raise ImportError("jinja2 is required for ReportRenderer") from exc

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )

        template_name = "report.html.j2"
        try:
            template = env.get_template(template_name)
        except Exception as exc:
            warnings.append(f"Template {template_name!r} not found, using plain fallback: {exc}")
            template = env.get_template("report_plain.html.j2")

        generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        html_str = template.render(
            title=self._config.title,
            aoi_description=narrative_context.aoi_description,
            time_window=narrative_context.time_window,
            generated_at=generated_at,
            n_scenes=narrative_context.n_scenes,
            max_subsidence_mm_yr=narrative_context.max_subsidence_mm_yr,
            dominant_class=narrative_context.dominant_class,
            n_high_risk_assets=narrative_context.n_high_risk_assets,
            mean_coherence=narrative_context.mean_coherence,
            summary_text=summary,
            recommendations_text=recommendations,
            top_assets=ranking_result.assets[:20],
            run_id=self._config.run_id,
            engine_name=self._config.engine_name,
            processing_mode=self._config.processing_mode,
            terrapulse_version=_VERSION,
            recipe_path=self._config.recipe_path,
            include_plotly=self._config.include_interactive_charts,
            velocity_image_b64=vel_b64,
            classification_image_b64=cls_b64,
            plotly_ts_json="{}",
        )

        # ---- 4. Write HTML ----
        html_path: Path | None = None
        if self._config.generate_html:
            html_path = self._config.output_dir / "report.html"
            html_path.write_text(html_str, encoding="utf-8")
            logger.info("HTML report written: %s", html_path)

        # ---- 5. PDF via WeasyPrint ----
        pdf_path: Path | None = None
        if self._config.generate_pdf:
            pdf_path = _render_pdf(html_str, self._config.output_dir, warnings)

        return ReportOutput(
            html_path=html_path,
            pdf_path=pdf_path,
            success=True,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_narrative(
        self,
        ctx: NarrativeContext,
        warnings: list[str],
    ) -> tuple[str, str]:
        """Return (summary_text, recommendations_text) from LLM or template."""
        from terrapulse_core.reporting.llm import TemplatedFallbackClient

        client = self._config.llm_client or TemplatedFallbackClient()
        try:
            summary = client.generate_summary(ctx)
        except Exception as exc:
            warnings.append(f"LLM summary failed, using template: {exc}")
            summary = TemplatedFallbackClient().generate_summary(ctx)

        try:
            recommendations = client.generate_recommendations(ctx)
        except Exception as exc:
            warnings.append(f"LLM recommendations failed, using template: {exc}")
            recommendations = TemplatedFallbackClient().generate_recommendations(ctx)

        return summary, recommendations


def _encode_image(path: Path | None) -> str | None:
    """Return base64-encoded PNG string, or None if path is missing/invalid."""
    if path is None or not path.exists():
        return None
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception as exc:
        logger.warning("Cannot encode image %s: %s", path, exc)
        return None


def _render_pdf(
    html_str: str,
    output_dir: Path,
    warnings: list[str],
) -> Path | None:
    """Convert HTML string to PDF via WeasyPrint. Returns None on failure."""
    try:
        from weasyprint import HTML  # type: ignore[import]

        pdf_path = output_dir / "report.pdf"
        HTML(string=html_str).write_pdf(str(pdf_path))
        logger.info("PDF report written: %s", pdf_path)
        return pdf_path
    except ImportError:
        warnings.append("weasyprint not installed — PDF skipped.")
        logger.warning("WeasyPrint not available — PDF generation skipped.")
        return None
    except Exception as exc:
        warnings.append(f"PDF generation failed: {exc}")
        logger.warning("WeasyPrint PDF generation failed: %s", exc)
        return None
