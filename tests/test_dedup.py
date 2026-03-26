"""Tests for near-duplicate clustering helpers."""

import numpy as np

from travelogue.analysis.embeddings import cosine_similarity
from travelogue.analysis.dedup import _phash_distance, _quality_score


def test_cosine_identical():
    v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_zero_vector():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(a, b) == 0.0


def test_quality_score_prefers_sharper():
    sharp = _quality_score(5000.0, 4000, 3000)
    blurry = _quality_score(100.0, 4000, 3000)
    assert sharp > blurry


def test_quality_score_prefers_larger():
    big = _quality_score(1000.0, 6000, 4000)
    small = _quality_score(1000.0, 800, 600)
    assert big > small


def test_phash_distance_identical():
    h = "aabbccddeeff0011"
    assert _phash_distance(h, h) == 0


def test_phash_distance_max():
    # Two hashes that differ in every bit should give distance 64
    a = "0000000000000000"
    b = "ffffffffffffffff"
    assert _phash_distance(a, b) == 64
