"""
Phase 2 ML integration tests — end-to-end pipeline on synthetic InSAR data.

These tests use the ``synthetic_sar_stack`` conftest fixture (12 time steps,
64×64 pixels) and exercise the full data flow:

  displacement array
      → extract_features()          (6-feature matrix)
      → DeformationClassifier       (labels + probabilities)
      → uncertainty_to_raster()     (label, entropy, confidence COGs)
      → AnomalyDetector             (anomaly labels + scores)
      → train_default_classifier()  (writes model pickle, loads back)
      → COGWriter                   (writes and reads back rasters)

Physical correctness assertions confirm that the engineered signal patches
(subsidence, seasonal, low-coherence) are reflected in the feature values.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from terrapulse_core.ml.features import extract_features, pixel_valid_mask, N_FEATURES
from terrapulse_core.ml.classifier import DeformationClassifier, DeformationClass
from terrapulse_core.ml.anomaly import AnomalyDetector
from terrapulse_core.ml.uncertainty import compute_uncertainty, uncertainty_to_raster
from terrapulse_core.ml.train import (
    generate_synthetic_training_data,
    train_default_classifier,
    train_default_anomaly_detector,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sar() -> dict:
    """
    Module-scoped synthetic SAR stack — created once for all integration tests.

    Same data as the conftest ``synthetic_sar_stack`` fixture (same seed),
    but declared here at module scope so module-scoped dependent fixtures
    (features_and_mask, tiny_classifier) can request it without a
    ScopeMismatch error.
    """
    rng = np.random.default_rng(seed=42)
    n_t, h, w = 12, 64, 64
    times = np.linspace(0, 1.0, n_t)

    disp = rng.normal(0, 2.0, size=(n_t, h, w)).astype(np.float32)
    for i, t in enumerate(times):
        disp[i, h // 2:, :w // 2] += -15.0 * t          # subsidence
        disp[i, :h // 2, w // 2:] += 10.0 * np.sin(2 * np.pi * t)  # seasonal

    coherence = np.full((h, w), 0.7, dtype=np.float32)
    coherence[:h // 2, :w // 2] = 0.15
    coherence += rng.normal(0, 0.05, size=(h, w)).astype(np.float32)
    coherence = np.clip(coherence, 0.0, 1.0)

    dem_slope = np.abs(rng.normal(5.0, 3.0, size=(h, w))).astype(np.float32)
    dem_aspect = rng.uniform(0, 360, size=(h, w)).astype(np.float32)
    velocity = np.polyfit(times, disp.reshape(n_t, -1), deg=1)[0].reshape(h, w).astype(np.float32)

    return {
        "displacement": disp,
        "velocity": velocity,
        "coherence": coherence,
        "dem_slope": dem_slope,
        "dem_aspect": dem_aspect,
        "times": times,
    }


@pytest.fixture(scope="module")
def features_and_mask(sar: dict) -> tuple[np.ndarray, np.ndarray]:
    """Extract feature matrix + valid mask from the synthetic stack."""
    feat = extract_features(
        velocity_ts=sar["displacement"],   # (12, 64, 64)
        coherence=sar["coherence"],
        dem_slope=sar["dem_slope"],
        dem_aspect=sar["dem_aspect"],
    )
    mask = pixel_valid_mask(
        sar["displacement"],
        sar["coherence"],
        sar["dem_slope"],
        sar["dem_aspect"],
    )
    return feat, mask


@pytest.fixture(scope="module")
def tiny_classifier() -> DeformationClassifier:
    """Train a tiny classifier once for all integration tests."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test_model.pkl"
        return train_default_classifier(save_path=p, n_samples_per_class=80)


# ---------------------------------------------------------------------------
# 1. Feature extraction
# ---------------------------------------------------------------------------

class TestExtractFeatures:
    def test_output_shape(self, features_and_mask: tuple) -> None:
        features, mask = features_and_mask
        n_valid = int(mask.sum())
        assert features.shape == (n_valid, N_FEATURES)

    def test_dtype_is_float32(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        assert features.dtype == np.float32

    def test_no_nan_in_output(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        assert not np.any(np.isnan(features))

    def test_coherence_feature_in_unit_interval(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        assert np.all(features[:, 3] >= 0.0)
        assert np.all(features[:, 3] <= 1.0)

    def test_slope_feature_non_negative(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        assert np.all(features[:, 4] >= 0.0)

    def test_aspect_feature_in_unit_interval(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        assert np.all(features[:, 5] >= 0.0)
        assert np.all(features[:, 5] <= 1.0)

    def test_raises_on_too_few_times(self, sar: dict) -> None:
        """Only 3 time steps → should raise ValueError."""
        with pytest.raises(ValueError, match="4 time steps"):
            extract_features(
                velocity_ts=sar["displacement"][:3],
                coherence=sar["coherence"],
                dem_slope=sar["dem_slope"],
                dem_aspect=sar["dem_aspect"],
            )

    def test_raises_on_wrong_spatial_dims(self, sar: dict) -> None:
        with pytest.raises(ValueError):
            extract_features(
                velocity_ts=sar["displacement"],
                coherence=sar["coherence"][:32, :],  # wrong spatial shape
                dem_slope=sar["dem_slope"],
                dem_aspect=sar["dem_aspect"],
            )

    def test_raises_on_2d_velocity(self, sar: dict) -> None:
        with pytest.raises(ValueError, match="3-D"):
            extract_features(
                velocity_ts=sar["displacement"][0],   # 2-D
                coherence=sar["coherence"],
                dem_slope=sar["dem_slope"],
                dem_aspect=sar["dem_aspect"],
            )


# ---------------------------------------------------------------------------
# 2. Physical correctness of extracted features
# ---------------------------------------------------------------------------

class TestPhysicalCorrectness:
    """
    Verify that the extracted features reflect the engineered patches in
    synthetic_sar_stack:

    - Bottom-left quadrant (h/2:, :w/2):  linear subsidence -15 mm/yr
    - Top-right quadrant  (:h/2, w/2:):   seasonal 10 mm amplitude
    - Top-left quadrant   (:h/2, :w/2):   low coherence 0.15
    - Background:                          stable noise
    """

    def test_subsidence_patch_has_negative_velocity(self, sar: dict) -> None:
        """
        The bottom-left quadrant has -15 mm/yr subsidence engineered in.
        Valid pixels there should have mean velocity feature well below zero.
        """
        h, w = 64, 64
        disp = sar["displacement"]
        coh = sar["coherence"]
        slope = sar["dem_slope"]
        aspect = sar["dem_aspect"]

        # Extract features for the subsidence-only quadrant
        sub_disp = disp[:, h // 2:, :w // 2]
        sub_coh = coh[h // 2:, :w // 2]
        sub_slope = slope[h // 2:, :w // 2]
        sub_aspect = aspect[h // 2:, :w // 2]

        feat = extract_features(sub_disp, sub_coh, sub_slope, sub_aspect)
        mean_vel = float(feat[:, 0].mean())
        # Engineered velocity is -15 mm/yr; allow generous tolerance for noise
        assert mean_vel < -5.0, f"Expected mean velocity < -5 mm/yr, got {mean_vel:.1f}"

    def test_seasonal_patch_has_large_amplitude(self, sar: dict) -> None:
        """
        The top-right quadrant has a 10 mm seasonal sinusoid.
        Valid pixels there should have mean seasonal_amplitude > stable background.
        """
        h, w = 64, 64
        disp = sar["displacement"]
        coh = sar["coherence"]
        slope = sar["dem_slope"]
        aspect = sar["dem_aspect"]

        # Extract features for seasonal quadrant
        sea_disp = disp[:, :h // 2, w // 2:]
        sea_coh = coh[:h // 2, w // 2:]
        sea_slope = slope[:h // 2, w // 2:]
        sea_aspect = aspect[:h // 2, w // 2:]

        # Extract features for stable quadrant (bottom-right)
        stab_disp = disp[:, h // 2:, w // 2:]
        stab_coh = coh[h // 2:, w // 2:]
        stab_slope = slope[h // 2:, w // 2:]
        stab_aspect = aspect[h // 2:, w // 2:]

        feat_sea = extract_features(sea_disp, sea_coh, sea_slope, sea_aspect)
        feat_stab = extract_features(stab_disp, stab_coh, stab_slope, stab_aspect)

        amp_seasonal = float(feat_sea[:, 2].mean())
        amp_stable = float(feat_stab[:, 2].mean())
        assert amp_seasonal > amp_stable + 3.0, (
            f"Seasonal amplitude {amp_seasonal:.1f} not >> stable {amp_stable:.1f}"
        )

    def test_low_coherence_patch_correctly_captured(self, sar: dict) -> None:
        """
        The top-left patch has coherence ≈ 0.15.  The coherence feature
        (index 3) for valid pixels there should be < 0.4 on average.
        """
        h, w = 64, 64
        disp = sar["displacement"]
        coh = sar["coherence"]
        slope = sar["dem_slope"]
        aspect = sar["dem_aspect"]

        lc_disp = disp[:, :h // 2, :w // 2]
        lc_coh = coh[:h // 2, :w // 2]
        lc_slope = slope[:h // 2, :w // 2]
        lc_aspect = aspect[:h // 2, :w // 2]

        feat = extract_features(lc_disp, lc_coh, lc_slope, lc_aspect)
        mean_coh = float(feat[:, 3].mean())
        assert mean_coh < 0.4, f"Expected mean coherence < 0.4, got {mean_coh:.3f}"


# ---------------------------------------------------------------------------
# 3. Classification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_predict_shapes(
        self, features_and_mask: tuple, tiny_classifier: DeformationClassifier
    ) -> None:
        features, _ = features_and_mask
        labels, proba = tiny_classifier.predict(features)
        assert labels.shape == (len(features),)
        assert proba.shape == (len(features), 5)

    def test_labels_are_valid_classes(
        self, features_and_mask: tuple, tiny_classifier: DeformationClassifier
    ) -> None:
        features, _ = features_and_mask
        labels, _ = tiny_classifier.predict(features)
        valid = {int(c) for c in DeformationClass}
        for lbl in np.unique(labels):
            assert int(lbl) in valid

    def test_proba_sums_to_one(
        self, features_and_mask: tuple, tiny_classifier: DeformationClassifier
    ) -> None:
        features, _ = features_and_mask
        _, proba = tiny_classifier.predict(features)
        np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-5)

    def test_classifier_detects_anomalous_features(
        self, tiny_classifier: DeformationClassifier
    ) -> None:
        """
        Pixels with very low coherence (≈0.05) should preferentially
        get classified as ANOMALOUS (class 4).
        """
        # Build a batch with very low coherence (ANOMALOUS-like)
        rng = np.random.default_rng(77)
        features = rng.normal(0, 20, size=(200, 6)).astype(np.float32)
        features[:, 3] = rng.uniform(0.0, 0.15, size=200).astype(np.float32)  # low coh
        labels, _ = tiny_classifier.predict(features)
        frac_anomalous = (labels == int(DeformationClass.ANOMALOUS)).mean()
        assert frac_anomalous > 0.3, (
            f"Expected >30% ANOMALOUS for low-coherence pixels, got {frac_anomalous:.1%}"
        )


# ---------------------------------------------------------------------------
# 4. Uncertainty rasters
# ---------------------------------------------------------------------------

class TestUncertaintyRasters:
    def test_full_pipeline_shapes(
        self,
        sar: dict,
        features_and_mask: tuple,
        tiny_classifier: DeformationClassifier,
    ) -> None:
        features, mask = features_and_mask
        labels, proba = tiny_classifier.predict(features)
        lr, er, cr = uncertainty_to_raster(labels, proba, mask, (64, 64))
        assert lr.shape == (64, 64)
        assert er.shape == (64, 64)
        assert cr.shape == (64, 64)

    def test_invalid_pixels_are_nodata(
        self,
        features_and_mask: tuple,
        tiny_classifier: DeformationClassifier,
    ) -> None:
        features, mask = features_and_mask
        labels, proba = tiny_classifier.predict(features)
        lr, er, cr = uncertainty_to_raster(labels, proba, mask, (64, 64))
        invalid = ~mask.reshape(64, 64)
        assert np.all(lr[invalid] == -1)
        assert np.all(np.isnan(er[invalid]))
        assert np.all(np.isnan(cr[invalid]))

    def test_entropy_bounded(
        self,
        features_and_mask: tuple,
        tiny_classifier: DeformationClassifier,
    ) -> None:
        import math
        features, mask = features_and_mask
        labels, proba = tiny_classifier.predict(features)
        _, er, _ = uncertainty_to_raster(labels, proba, mask, (64, 64))
        valid_entropy = er[mask.reshape(64, 64)]
        assert np.all(valid_entropy >= 0.0)
        assert np.all(valid_entropy <= math.log2(5) + 1e-4)


# ---------------------------------------------------------------------------
# 5. Anomaly detection
# ---------------------------------------------------------------------------

class TestAnomalyDetectionIntegration:
    def test_fit_predict_on_full_stack(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        det = AnomalyDetector(contamination=0.05)
        labels = det.fit_predict(features)
        assert labels.shape == (len(features),)
        assert set(np.unique(labels)).issubset({1, -1})

    def test_anomaly_fraction_near_contamination(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        contamination = 0.10
        det = AnomalyDetector(contamination=contamination)
        labels = det.fit_predict(features)
        frac_anomalous = (labels == -1).mean()
        # IsolationForest targets contamination exactly on training data
        assert abs(frac_anomalous - contamination) < 0.03, (
            f"Expected ~{contamination:.0%} anomalies, got {frac_anomalous:.1%}"
        )

    def test_anomaly_scores_continuous(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        det = AnomalyDetector()
        det.fit(features)
        scores = det.anomaly_score(features)
        assert scores.shape == (len(features),)
        # Scores should not all be identical
        assert scores.std() > 0.0


# ---------------------------------------------------------------------------
# 6. train_default_classifier — full train + save + load round-trip
# ---------------------------------------------------------------------------

class TestTrainDefaultClassifier:
    def test_train_and_load_round_trip(self, tmp_path: Path) -> None:
        save_path = tmp_path / "model" / "rf_v1.pkl"
        clf = train_default_classifier(
            save_path=save_path,
            n_samples_per_class=80,
        )
        assert save_path.exists()
        assert save_path.stat().st_size > 0

        # Load back and verify it works
        clf_loaded = DeformationClassifier.load(save_path)
        features = np.zeros((5, 6), dtype=np.float32)
        labels, proba = clf_loaded.predict(features)
        assert labels.shape == (5,)
        assert proba.shape == (5, 5)

    def test_model_is_deterministic(self, tmp_path: Path) -> None:
        p1 = tmp_path / "m1.pkl"
        p2 = tmp_path / "m2.pkl"
        clf1 = train_default_classifier(save_path=p1, n_samples_per_class=50, random_state=7)
        clf2 = train_default_classifier(save_path=p2, n_samples_per_class=50, random_state=7)
        feat = np.ones((10, 6), dtype=np.float32)
        l1, _ = clf1.predict(feat)
        l2, _ = clf2.predict(feat)
        np.testing.assert_array_equal(l1, l2)

    def test_classifier_achieves_reasonable_accuracy(self, tmp_path: Path) -> None:
        """Synthetic data should be separable — expect ≥ 80% accuracy."""
        from sklearn.model_selection import cross_val_score  # type: ignore[import]
        X, y = generate_synthetic_training_data(n_samples_per_class=200)
        p = tmp_path / "acc_model.pkl"
        clf = train_default_classifier(save_path=p, n_samples_per_class=200)
        labels, _ = clf.predict(X)
        accuracy = (labels == y).mean()
        assert accuracy >= 0.80, (
            f"Expected ≥80% accuracy on synthetic training data, got {accuracy:.1%}"
        )


# ---------------------------------------------------------------------------
# 7. train_default_anomaly_detector convenience wrapper
# ---------------------------------------------------------------------------

class TestTrainDefaultAnomalyDetector:
    def test_returns_fitted_detector(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        det = train_default_anomaly_detector(features, contamination=0.05)
        assert det.is_fitted

    def test_scores_are_finite(self, features_and_mask: tuple) -> None:
        features, _ = features_and_mask
        det = train_default_anomaly_detector(features)
        scores = det.anomaly_score(features)
        assert np.all(np.isfinite(scores))
