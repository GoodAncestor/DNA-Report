"""Job tiering — decide which uploads run inline vs get queued to a worker.

The boundary the deployment hinges on. Reviewers hit a light front door
(Hetzner, mild CPU); anything compute- or memory-heavy is handed to the
batch worker pool (co-located Mac Studios reading from the NAS). This module
owns the *decision* of what is heavy — the app knows its own cost profile.
The *mechanism* of queuing (queue backend, upload handoff, worker lease) lives
in the deployment layer (dna-report-deploy), not here.

Tier is derived from the detected InputKind plus two refinements the kind alone
does not carry:
  - a VCF is light for single-sample interpretation but heavy when several raw
    per-sample VCFs must be merged/consensus-called first (n_samples >= 2);
  - a beta-matrix / bedMethyl is light for a handful of markers but heavy when a
    full array (~10^5-10^6 probes) is annotated live with no cap.

HEAVY (queued):
  IDAT                      -> methylprep normalization (raw array -> betas)
  MODBAM                    -> whole-methylome MM/ML pileup
  VCF with n_samples >= 2   -> bcftools merge/consensus before compare
  BETA_MATRIX / BEDMETHYL   -> only when annotated uncapped over a large marker set

LIGHT (inline): everything else -- single-VCF lookups, 23andMe genotype lookups,
capped/small array annotation, epigenetic clocks, compare on an already-merged
VCF, tiering, and HTML render. All arithmetic + cache reads (GDC included: the
request path reads the small precomputed summary, never the 294 GB mirror).
"""
from __future__ import annotations
import os
from .detect import InputKind

INLINE = "inline"
QUEUED = "queued"

# above this many markers, a live per-marker annotation loop is a batch job
# unless the caller has capped it (--max-markers).
LARGE_MARKER_SET = 5000


def job_tier(kind: InputKind, *, n_samples: int = 1,
             n_markers: int | None = None, max_markers: int | None = None) -> str:
    """Classify an upload into 'inline' or 'queued'. Pure function -- no I/O,
    no queue awareness, so it is trivially testable and safe to call anywhere."""
    # raw ingestion that must be normalized/piled-up first: always heavy
    if kind == InputKind.IDAT:
        return QUEUED
    if kind == InputKind.MODBAM:
        return QUEUED
    # several raw per-sample VCFs -> merge/consensus is heavy; one VCF is a lookup
    if kind == InputKind.VCF:
        return QUEUED if n_samples >= 2 else INLINE
    # array annotation: heavy only when a large marker set is annotated uncapped
    if kind in (InputKind.BETA_MATRIX, InputKind.BEDMETHYL):
        if max_markers is not None:          # caller capped it -> inline
            return INLINE
        if n_markers is not None and n_markers > LARGE_MARKER_SET:
            return QUEUED
        return INLINE
    # 23andMe genotype file, UNKNOWN, anything else -> light
    return INLINE


def queue_enabled() -> bool:
    """True only when a queue backend is configured (deployment sets this).
    Absent it, DNA-Report runs every job inline -- standalone behaviour is
    unchanged, which is what the CLI and the test suite exercise."""
    return bool(os.environ.get("DNAREPORT_QUEUE_URL"))


def should_queue(kind: InputKind, **kw) -> bool:
    """The orchestrator's actual gate: queue a job only if it is heavy AND a
    queue backend exists. No backend -> always inline, never blocks."""
    return queue_enabled() and job_tier(kind, **kw) == QUEUED
