"""
ReportTask â€” Phase 3 report generation background task.

Orchestrates:
  1. OSM asset query (Overpass API)
  2. Risk ranking (RiskRanker)
  3. HTML + PDF report rendering (ReportRenderer)
  4. STAC item writing (STACItemWriter)

Emits:
    report_complete(success: bool, run_id: str)
    progress_message(message: str)
"""

from __future__ import annotations

import logging
from pathlib import Path

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

logger = logging.getLogger(__name__)


class ReportTask(QgsTask):
    """
    QgsTask that generates the full deformation analysis report.

    Steps
    -----
    1. Load recipe YAML to reconstruct run context.
    2. Query OSM assets within the AOI (Overpass API).
    3. Run RiskRanker to score each asset.
    4. Build NarrativeContext from recipe + ranking result.
    5. Render HTML report (+ optional PDF) via ReportRenderer.
    6. Write STAC item JSON for run discoverability.
    7. Update recipe with report paths.
    """

    report_complete = pyqtSignal(bool, str)   # (success, run_id)
    progress_message = pyqtSignal(str)

    def __init__(
        self,
        run_id: str,
        output_dir: Path,
        velocity_cog: Path | None = None,
        coherence_cog: Path | None = None,
        anthropic_api_key: str | None = None,
        generate_pdf: bool = False,
        parent: object | None = None,
    ) -> None:
        super().__init__(
            f"TerraPulse Report â€” {run_id[:8]}",
            QgsTask.CanCancel,
        )
        self._run_id = run_id
        self._output_dir = output_dir
        self._velocity_cog = velocity_cog
        self._coherence_cog = coherence_cog
        self._anthropic_api_key = anthropic_api_key
        self._generate_pdf = generate_pdf

        self._report_html: Path | None = None
        self._report_pdf: Path | None = None
        self._error_message: str | None = None

    # ------------------------------------------------------------------
    # QgsTask interface
    # ------------------------------------------------------------------

    def run(self) -> bool:  # noqa: C901
        """Execute report generation pipeline in a worker thread."""
        try:
            from terrapulse_core.provenance.recipe import RecipeWriter, RunRecipe
            from terrapulse_core.stac.models import BBox
            from terrapulse_core.risk.osm import OSMQuerier
            from terrapulse_core.risk.ranking import RiskRanker, RankingResult
            from terrapulse_core.reporting.renderer import ReportConfig, ReportRenderer
            from terrapulse_core.reporting.llm import (
                NarrativeContext,
                AnthropicNarrativeClient,
                TemplatedFallbackClient,
            )
            from terrapulse_core.provenance.stac_writer import STACItemWriter

            # ---- 1. Load recipe ----
            self._emit("Loading recipeâ€¦")
            self.setProgress(5)
            writer = RecipeWriter(self._output_dir)
            try:
                recipe = writer.load(self._run_id)
            except Exception as exc:
                logger.warning("Could not load recipe: %s â€” creating minimal stub", exc)
                recipe = RunRecipe(
                    run_id=self._run_id,
                    status="completed",
                    aoi_wkt="",
                    start_date="",
                    end_date="",
                    engine="pygmtsar",
                    mode="standard",
                )

            # ---- 2. Parse AOI ----
            aoi = _aoi_from_recipe(recipe)

            if self.isCanceled():
                return False

            # ---- 3. OSM query ----
            self._emit("Querying OpenStreetMap infrastructureâ€¦")
            self.setProgress(15)
            osm_assets = None
            if aoi is not None:
                try:
                    querier = OSMQuerier()
                    osm_assets = querier.query_assets(aoi)
                    self._emit(
                        f"Found {osm_assets.n_features} OSM features in AOI."
                    )
                except Exception as exc:
                    logger.warning("OSM query failed (continuing without): %s", exc)
                    self._emit(f"OSM query skipped ({exc})")

            if self.isCanceled():
                return False

            # ---- 4. Risk ranking ----
            self._emit("Scoring at-risk infrastructureâ€¦")
            self.setProgress(35)
            ranking_result = RankingResult()
            vel_cog = self._velocity_cog or (
                Path(recipe.velocity_cog) if recipe.velocity_cog else None
            )
            coh_cog = self._coherence_cog or (
                Path(recipe.coherence_cog) if recipe.coherence_cog else None
            )

            if osm_assets and vel_cog and coh_cog and vel_cog.exists() and coh_cog.exists():
                try:
                    ranker = RiskRanker()
                    ranking_result = ranker.rank(
                        velocity_cog=vel_cog,
                        coherence_cog=coh_cog,
                        osm_assets=osm_assets,
                    )
                    self._emit(
                        f"Risk scoring complete: {ranking_result.n_high_risk} high-risk assets."
                    )
                except Exception as exc:
                    logger.warning("Risk ranking failed: %s", exc)

            if self.isCanceled():
                return False

            # ---- 5. Build NarrativeContext ----
            self._emit("Building narrative contextâ€¦")
            self.setProgress(50)

            dominant_class = "Linear subsidence/uplift"
            max_vel = -10.0  # fallback
            mean_coh = 0.65

            ctx = NarrativeContext(
                aoi_description=_describe_aoi(aoi, recipe),
                time_window=f"{recipe.start_date} to {recipe.end_date}",
                n_scenes=len(recipe.scenes) if recipe.scenes else 0,
                dominant_class=dominant_class,
                max_subsidence_mm_yr=max_vel,
                n_high_risk_assets=ranking_result.n_high_risk,
                top_assets=[a.name for a in ranking_result.assets[:5]],
                mean_coherence=mean_coh,
                processing_mode=recipe.mode or "standard",
                engine_name=recipe.engine or "pygmtsar",
            )

            # ---- 6. Build LLM client ----
            llm_client = None
            if self._anthropic_api_key:
                llm_client = AnthropicNarrativeClient(self._anthropic_api_key)

            # ---- 7. Render report ----
            self._emit("Rendering HTML reportâ€¦")
            self.setProgress(65)

            recipe_yaml = self._output_dir / f"recipe_{self._run_id}.yaml"
            config = ReportConfig(
                output_dir=self._output_dir,
                title=f"TerraPulse â€” {ctx.aoi_description}",
                generate_html=True,
                generate_pdf=self._generate_pdf,
                llm_client=llm_client,
                run_id=self._run_id,
                engine_name=recipe.engine or "pygmtsar",
                processing_mode=recipe.mode or "standard",
                recipe_path=str(recipe_yaml),
            )
            renderer = ReportRenderer(config)
            report_output = renderer.render(ctx, ranking_result)

            if not report_output.success:
                raise RuntimeError("ReportRenderer.render() returned success=False")

            self._report_html = report_output.html_path
            self._report_pdf = report_output.pdf_path

            if self.isCanceled():
                return False

            # ---- 8. Update recipe ----
            self._emit("Updating provenance recipeâ€¦")
            self.setProgress(82)
            if report_output.html_path:
                recipe.report_html = str(report_output.html_path)
            if report_output.pdf_path:
                recipe.report_pdf = str(report_output.pdf_path)
            try:
                writer.write(recipe)
            except Exception as exc:
                logger.warning("Could not update recipe: %s", exc)

            # ---- 9. Write STAC item ----
            self._emit("Writing STAC itemâ€¦")
            self.setProgress(92)
            if aoi is not None:
                try:
                    stac_path = STACItemWriter().write(recipe, aoi, self._output_dir)
                    logger.info("STAC item: %s", stac_path)
                except Exception as exc:
                    logger.warning("STAC item writing failed: %s", exc)

            self.setProgress(100)
            self._emit("Report generation complete.")
            return True

        except Exception as exc:
            logger.exception("ReportTask failed")
            self._error_message = str(exc)
            return False

    def finished(self, result: bool) -> None:
        """Called on the main thread after run() completes."""
        self.report_complete.emit(result, self._run_id)
        if not result:
            logger.error("ReportTask failed: %s", self._error_message)

    def cancel(self) -> None:
        logger.info("ReportTask cancel requested.")
        super().cancel()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def report_html(self) -> Path | None:
        return self._report_html

    @property
    def report_pdf(self) -> Path | None:
        return self._report_pdf

    @property
    def error_message(self) -> str | None:
        return self._error_message

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, message: str) -> None:
        self.progress_message.emit(message)
        logger.debug("ReportTask: %s", message)


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, no QGIS dependency)
# ---------------------------------------------------------------------------

def _aoi_from_recipe(recipe: object) -> object | None:
    """Parse BBox from recipe.aoi_wkt, or return None on failure."""
    try:
        from terrapulse_core.stac.models import BBox
        wkt: str = recipe.aoi_wkt  # type: ignore[attr-defined]
        if not wkt:
            return None
        # Parse simple POLYGON((w s, e s, e n, w n, w s)) WKT
        coords_str = wkt.replace("POLYGON((", "").replace("))", "").strip()
        pairs = [p.strip().split() for p in coords_str.split(",")]
        xs = [float(p[0]) for p in pairs]
        ys = [float(p[1]) for p in pairs]
        return BBox(
            west=min(xs), south=min(ys),
            east=max(xs), north=max(ys),
        )
    except Exception as exc:
        logger.debug("Could not parse AOI from recipe: %s", exc)
        return None


def _describe_aoi(aoi: object | None, recipe: object) -> str:
    """Return a human-readable AOI description."""
    if aoi is None:
        return "Unknown AOI"
    try:
        cx = (aoi.west + aoi.east) / 2  # type: ignore[attr-defined]
        cy = (aoi.south + aoi.north) / 2  # type: ignore[attr-defined]
        return f"AOI ({cy:.2f}Â°N, {cx:.2f}Â°E)"
    except Exception:
        return "Unknown AOI"

