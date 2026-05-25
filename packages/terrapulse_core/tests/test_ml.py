"""
Tests for terrapulse_core.ml — Phase 2 full implementations.

Covers:
  - DeformationClass enum, labels, colours
  - Feature extraction constants and _fit_seasonal helper
  - DeformationClassifier.predict() — shape / dtype contract
  - AnomalyDetector.fit / predict / anomaly_score / fit_predict
  - compute_uncertainty() edge-cases
  - uncertainty_to_raster() reshape contract
  - train.generate_synthetic_training_data() statistical sanity
"""

from __future__ import annotations

import numpy as np
import pytest

from terrapulse_core.ml.classifier import (
    CLASS_COLORS_HEX,
    CLASS_LABELS,
    DeformationClass,
    DeformationClassifier,
)
from terrapulse_core.ml.anomaly import AnomalyDetector
from terrapulse_core.ml.features import N_FEATURES, FEATURE_NAMES, _fit_seasonal
from terrapulse_core.ml.foundation import is_foundation_model_available
from terrapulse_core.ml.uncertainty import (
    ClassificationUncertainty,
    compute_uncertainty,
    uncertainty_to_raster,
)
from terrapulse_core.ml.train import generate_synthetic_training_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proba(n: int = 20, n_classes: int = 5) -> np.ndarray:
    """Random probability matrix that sums to 1 per row."""
    rng = np.random.default_rng(0)
    raw = rng.dirichlet(np.ones(n_classes), size=n)
    return raw.astype(np.float32)


def _train_tiny_clf() -> DeformationClassifier:
    """Train a tiny (50 samples/class) classifier for unit-test speed."""
    from terrapulse_core.ml.train import train_default_classifier
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "tiny_rf.pkl"
        return train_default_classifier(save_path=p, n_samples_per_class=50)


# ---------------------------------------------------------------------------
# DeformationClass enum
# ---------------------------------------------------------------------------

class TestDeformationClass:
    def test_enum_values(self) -> None:
        assert DeformationClass.STABLE == 0
        assert DeformationClass.ANOMALOUS == 4

    def test_all_classes_have_labels(self) -> None:
        for cls in DeformationClass:
            assert cls in CLASS_LABELS
            assert cls in CLASS_COLORS_HEX

    def test_colors_are_hex(self) -> None:
        for color in CLASS_COLORS_HEX.values():
            assert color.startswith("#")
            assert len(color) == 7


# ---------------------------------------------------------------------------
# Feature constants
# ---------------------------------------------------------------------------

class TestFeatureConstants:
    def test_n_features(self) -> None:
        assert N_FEATURES == 6

    def test_feature_names_length(self) -> None:
        assert len(FEATURE_NAMES) == N_FEATURES


# ---------------------------------------------------------------------------
# _fit_seasonal helper
# ---------------------------------------------------------------------------

class TestFitSeasonal:
    def test_pure_sinusoid(self) -> None:
        times = np.linspace(0, 1, 24)
        values = 10.0 * np.sin(2 * np.pi * times)
        amp = _fit_seasonal(times, values)
        assert abs(amp - 10.0) < 0.5

    def test_constant_signal(self) -> None:
        times = np.linspace(0, 1, 24)
        values = np.full(24, 5.0)
        amp = _fit_seasonal(times, values)
        assert amp < 1.0

    def test_too_few_points(self) -> None:
        amp = _fit_seasonal(np.array([0.0, 0.5]), np.array([0.0, 1.0]))
        assert amp == 0.0


# ---------------------------------------------------------------------------
# DeformationClassifier
# ---------------------------------------------------------------------------

class TestDeformationClassifier:
    def test_load_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError, match="model"):
            DeformationClassifier.load()

    def test_predict_returns_correct_shapes(self) -> None:
        clf = _train_tiny_clf()
        features = np.zeros((30, 6), dtype=np.float32)
        labels, proba = clf.predict(features)
        assert labels.shape == (30,)
        assert proba.shape == (30, 5)
        assert labels.dtype == np.int32
        assert proba.dtype == np.float32

    def test_predict_labels_are_valid_classes(self) -> None:
        clf = _train_tiny_clf()
        features = np.zeros((10, 6), dtype=np.float32)
        labels, _ = clf.predict(features)
        valid_classes = {int(c) for c in DeformationClass}
        for lbl in labels:
            assert int(lbl) in valid_classes

    def test_predict_proba_sums_to_one(self) -> None:
        clf = _train_tiny_clf()
        features = np.zeros((15, 6), dtype=np.float32)
        _, proba = clf.predict(features)
        row_sums = proba.sum(axis=1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-5)

    def test_predict_wrong_shape_raises(self) -> None:
        clf = _train_tiny_clf()
        with pytest.raises(ValueError, match="6"):
            clf.predict(np.zeros((10, 4), dtype=np.float32))

    def test_predict_no_model_raises(self) -> None:
        clf = object.__new__(DeformationClassifier)
        clf._model = None  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError):
            clf.predict(np.zeros((10, 6), dtype=np.float32))


# ---------------------------------------------------------------------------
# AnomalyDetector
# ---------------------------------------------------------------------------

class TestAnomalyDetector:
    def test_default_contamination(self) -> None:
        det = AnomalyDetector()
        assert det._contamination == 0.05

    def test_invalid_contamination_raises(self) -> None:
        with pytest.raises(ValueError):
            AnomalyDetector(contamination=0.0)
        with pytest.raises(ValueError):
            AnomalyDetector(contamination=0.6)

    def test_fit_returns_self(self) -> None:
        det = AnomalyDetector()
        features = np.random.default_rng(0).random((50, 6)).astype(np.float32)
        result = det.fit(features)
        assert result is det

    def test_is_fitted_after_fit(self) -> None:
        det = AnomalyDetector()
        assert not det.is_fitted
        det.fit(np.random.default_rng(1).random((50, 6)).astype(np.float32))
        assert det.is_fitted

    def test_predict_output_shape_and_values(self) -> None:
        rng = np.random.default_rng(2)
        features = rng.random((100, 6)).astype(np.float32)
        det = AnomalyDetector(contamination=0.1)
        det.fit(features)
        labels = det.predict(features)
        assert labels.shape == (100,)
        assert set(np.unique(labels)).issubset({1, -1})

    def test_anomaly_score_shape_and_monotonicity(self) -> None:
        rng = np.random.default_rng(3)
        # Train on clean data, score a clear outlier
        clean = rng.normal(loc=0.5, scale=0.1, size=(200, 6)).astype(np.float32)
        outlier = np.ones((1, 6), dtype=np.float32) * 100.0  # extreme outlier
        det = AnomalyDetector(contamination=0.05)
        det.fit(clean)
        scores_clean = det.anomaly_score(clean)
        scores_outlier = det.anomaly_score(outlier)
        assert scores_clean.shape == (200,)
        # Outlier should score lower (more anomalous) than the mean clean score
        assert float(scores_outlier[0]) < float(scores_clean.mean())

    def test_fit_predict_convenience(self) -> None:
        features = np.random.default_rng(4).random((60, 6)).astype(np.float32)
        det = AnomalyDetector(contamination=0.1)
        labels = det.fit_predict(features)
        assert labels.shape == (60,)

    def test_predict_before_fit_raises(self) -> None:
        det = AnomalyDetector()
        with pytest.raises(RuntimeError, match="fit"):
            det.predict(np.zeros((5, 6), dtype=np.float32))

    def test_fit_requires_2d(self) -> None:
        det = AnomalyDetector()
        with pytest.raises(ValueError):
            det.fit(np.zeros(50, dtype=np.float32))

    def test_fit_requires_min_samples(self) -> None:
        det = AnomalyDetector()
        with pytest.raises(ValueError, match="10"):
            det.fit(np.zeros((5, 6), dtype=np.float32))


# ---------------------------------------------------------------------------
# compute_uncertainty
# ---------------------------------------------------------------------------

class TestComputeUncertainty:
    def test_returns_correct_types(self) -> None:
        uc = compute_uncertainty(_make_proba(20, 5))
        assert isinstance(uc, ClassificationUncertainty)
        assert uc.entropy.dtype == np.float32
        assert uc.confidence.dtype == np.float32
        assert uc.margin.dtype == np.float32

    def test_shapes(self) -> None:
        uc = compute_uncertainty(_make_proba(50, 5))
        assert uc.entropy.shape == (50,)
        assert uc.confidence.shape == (50,)
        assert uc.margin.shape == (50,)

    def test_perfectly_certain_pixel(self) -> None:
        """If one class has probability 1, entropy=0, confidence=1, margin=1."""
        proba = np.array([[1.0, 0.0, 0.0, 0.0, 0.0]], dtype=np.float32)
        uc = compute_uncertainty(proba)
        assert float(uc.entropy[0]) == pytest.approx(0.0, abs=1e-4)
        assert float(uc.confidence[0]) == pytest.approx(1.0, abs=1e-4)
        assert float(uc.margin[0]) == pytest.approx(1.0, abs=1e-4)

    def test_uniform_distribution_maximum_entropy(self) -> None:
        """Uniform over 5 classes → entropy = log2(5) ≈ 2.322 bits."""
        import math
        proba = np.full((1, 5), 0.2, dtype=np.float32)
        uc = compute_uncertainty(proba)
        assert float(uc.entropy[0]) == pytest.approx(math.log2(5), abs=0.01)
        assert float(uc.confidence[0]) == pytest.approx(0.2, abs=1e-4)

    def test_entropy_non_negative(self) -> None:
        uc = compute_uncertainty(_make_proba(100, 5))
        assert np.all(uc.entropy >= 0.0)

    def test_confidence_in_unit_interval(self) -> None:
        uc = compute_uncertainty(_make_proba(100, 5))
        assert np.all(uc.confidence >= 0.0)
        assert np.all(uc.confidence <= 1.0)

    def test_margin_non_negative(self) -> None:
        uc = compute_uncertainty(_make_proba(100, 5))
        assert np.all(uc.margin >= 0.0)

    def test_raises_on_1d_input(self) -> None:
        with pytest.raises(ValueError):
            compute_uncertainty(np.array([0.2, 0.8]))

    def test_raises_on_single_class(self) -> None:
        with pytest.raises(ValueError):
            compute_uncertainty(np.ones((10, 1), dtype=np.float32))


# ---------------------------------------------------------------------------
# uncertainty_to_raster
# ---------------------------------------------------------------------------

class TestUncertaintyToRaster:
    def _setup(self, ny: int = 8, nx: int = 8, n_valid: int = 40) -> tuple:
        n_total = ny * nx
        rng = np.random.default_rng(5)
        valid_mask = np.zeros(n_total, dtype=bool)
        valid_mask[:n_valid] = True
        rng.shuffle(valid_mask)

        labels = rng.integers(0, 5, size=n_valid).astype(np.int32)
        proba = rng.dirichlet(np.ones(5), size=n_valid).astype(np.float32)
        return labels, proba, valid_mask, ny, nx

    def test_output_shapes(self) -> None:
        labels, proba, mask, ny, nx = self._setup()
        lr, er, cr = uncertainty_to_raster(labels, proba, mask, (ny, nx))
        assert lr.shape == (ny, nx)
        assert er.shape == (ny, nx)
        assert cr.shape == (ny, nx)

    def test_label_nodata_is_minus_one(self) -> None:
        labels, proba, mask, ny, nx = self._setup(n_valid=10)
        lr, _, _ = uncertainty_to_raster(labels, proba, mask, (ny, nx))
        invalid = lr[~mask.reshape(ny, nx)]
        assert np.all(invalid == -1)

    def test_entropy_nodata_is_nan(self) -> None:
        labels, proba, mask, ny, nx = self._setup(n_valid=10)
        _, er, _ = uncertainty_to_raster(labels, proba, mask, (ny, nx))
        invalid = er[~mask.reshape(ny, nx)]
        assert np.all(np.isnan(invalid))

    def test_valid_pixels_populated(self) -> None:
        labels, proba, mask, ny, nx = self._setup(n_valid=40)
        lr, er, cr = uncertainty_to_raster(labels, proba, mask, (ny, nx))
        valid_labels = lr[mask.reshape(ny, nx)]
        assert np.all(valid_labels != -1)

    def test_wrong_mask_length_raises(self) -> None:
        labels = np.zeros(10, dtype=np.int32)
        proba = np.ones((10, 5), dtype=np.float32) / 5
        with pytest.raises(ValueError, match="valid_mask"):
            uncertainty_to_raster(labels, proba, np.ones(99, dtype=bool), (8, 8))

    def test_wrong_labels_length_raises(self) -> None:
        mask = np.array([True] * 20 + [False] * 44, dtype=bool)
        labels = np.zeros(10, dtype=np.int32)   # should be 20
        proba = np.ones((10, 5), dtype=np.float32) / 5
        with pytest.raises(ValueError, match="labels length"):
            uncertainty_to_raster(labels, proba, mask, (8, 8))


# ---------------------------------------------------------------------------
# generate_synthetic_training_data
# ---------------------------------------------------------------------------

class TestGenerateSyntheticData:
    def test_output_shapes(self) -> None:
        X, y = generate_synthetic_training_data(n_samples_per_class=50)
        assert X.shape == (250, 6)
        assert y.shape == (250,)

    def test_dtypes(self) -> None:
        X, y = generate_synthetic_training_data(n_samples_per_class=20)
        assert X.dtype == np.float32
        assert y.dtype == np.int32

    def test_all_classes_present(self) -> None:
        _, y = generate_synthetic_training_data(n_samples_per_class=50)
        assert set(np.unique(y)) == {0, 1, 2, 3, 4}

    def test_coherence_in_unit_interval(self) -> None:
        X, _ = generate_synthetic_training_data(n_samples_per_class=100)
        assert np.all(X[:, 3] >= 0.0)
        assert np.all(X[:, 3] <= 1.0)

    def test_slope_non_negative(self) -> None:
        X, _ = generate_synthetic_training_data(n_samples_per_class=100)
        assert np.all(X[:, 4] >= 0.0)

    def test_anomalous_class_low_coherence(self) -> None:
        """ANOMALOUS pixels should have systematically lower mean coherence."""
        X, y = generate_synthetic_training_data(n_samples_per_class=300)
        coh_stable = X[y == 0, 3].mean()
        coh_anomalous = X[y == 4, 3].mean()
        assert coh_anomalous < coh_stable - 0.3

    def test_seasonal_class_high_amplitude(self) -> None:
        """SEASONAL pixels should have higher mean seasonal amplitude."""
        X, y = generate_synthetic_training_data(n_samples_per_class=300)
        amp_stable = X[y == 0, 2].mean()
        amp_seasonal = X[y == 2, 2].mean()
        assert amp_seasonal > amp_stable + 5.0

    def test_reproducible_with_same_seed(self) -> None:
        X1, y1 = generate_synthetic_training_data(random_state=99)
        X2, y2 = generate_synthetic_training_data(random_state=99)
        np.testing.assert_array_equal(X1, X2)
        np.testing.assert_array_equal(y1, y2)


# ---------------------------------------------------------------------------
# Foundation model availability
# ---------------------------------------------------------------------------

class TestFoundationModelAvailability:
    def test_returns_bool(self) -> None:
        result = is_foundation_model_available()
        assert isinstance(result, bool)
