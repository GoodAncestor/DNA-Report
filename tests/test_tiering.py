"""Tests for the job_tier boundary: which uploads run inline vs get queued.

Pins the light/heavy split the deployment relies on, and confirms DNA-Report
still runs everything inline when no queue backend is configured (standalone
behaviour unchanged).
"""
import os
import pytest
from dnareport.detect import InputKind
from dnareport.tiering import (
    job_tier, should_queue, queue_enabled, INLINE, QUEUED, LARGE_MARKER_SET,
)


# --- the heavy set: raw ingestion that must be normalized/piled-up/merged ---
def test_idat_is_heavy():
    assert job_tier(InputKind.IDAT) == QUEUED

def test_modbam_is_heavy():
    assert job_tier(InputKind.MODBAM) == QUEUED

def test_multi_sample_vcf_is_heavy():
    assert job_tier(InputKind.VCF, n_samples=6) == QUEUED

def test_uncapped_large_array_is_heavy():
    assert job_tier(InputKind.BETA_MATRIX, n_markers=LARGE_MARKER_SET + 1) == QUEUED
    assert job_tier(InputKind.BEDMETHYL, n_markers=900_000) == QUEUED


# --- the light set: lookups, capped/small annotation, genotype files ---
def test_single_vcf_is_light():
    assert job_tier(InputKind.VCF, n_samples=1) == INLINE

def test_twentythreeandme_is_light():
    assert job_tier(InputKind.TWENTYTHREE_AND_ME) == INLINE

def test_small_array_is_light():
    assert job_tier(InputKind.BETA_MATRIX, n_markers=50) == INLINE

def test_capped_array_is_light_even_when_large():
    # an explicit --max-markers cap keeps a big array on the inline path
    assert job_tier(InputKind.BETA_MATRIX, n_markers=900_000, max_markers=20) == INLINE

def test_unknown_is_light():
    assert job_tier(InputKind.UNKNOWN) == INLINE

def test_array_defaults_light_without_marker_count():
    # no marker count known yet (pre-parse) -> do not pre-emptively queue
    assert job_tier(InputKind.BETA_MATRIX) == INLINE


# --- the queue guard: heavy only queues when a backend is configured ---
def test_no_backend_never_queues(monkeypatch):
    monkeypatch.delenv("DNAREPORT_QUEUE_URL", raising=False)
    assert queue_enabled() is False
    # even a heavy job stays inline with no backend -> standalone unchanged
    assert should_queue(InputKind.MODBAM) is False
    assert should_queue(InputKind.IDAT) is False

def test_backend_queues_heavy_only(monkeypatch):
    monkeypatch.setenv("DNAREPORT_QUEUE_URL", "redis://queue:6379/0")
    assert queue_enabled() is True
    assert should_queue(InputKind.MODBAM) is True          # heavy -> queued
    assert should_queue(InputKind.VCF, n_samples=1) is False  # light -> inline
    assert should_queue(InputKind.TWENTYTHREE_AND_ME) is False
