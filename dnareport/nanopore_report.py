# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Compose same-sample native sequencing measurements and variant interpretation."""
from __future__ import annotations

from dataclasses import replace

from .detect import InputKind


PLATFORM_NOTE = (
    "Native ONT 5mC read fractions are shown at mapped CpG sites. "
    "Array-trained age clocks and reference-group classifications are withheld: "
    "this platform conversion has not been validated for those comparisons."
)


def _annotate_native(path, result, sample):
    from .orchestrate import _run_methylask
    findings, statuses, _clocks = _run_methylask(
        path, InputKind.BEDMETHYL, tissue=result.tissue, age=result.age,
        max_markers=None, notes=result.notes, nanopore_sample=sample,
    )
    from methylask.aggregate import aggregate_by_trait
    findings = aggregate_by_trait(findings, result.tissue)
    for finding in findings:
        finding.detail = {
            **(finding.detail or {}),
            "measurement_platform": "ONT",
            "modality": "methylome",
            "measurement_type": "5mC valid-read fraction",
            "platform_validation": "unvalidated_cross_platform",
            "your reading": sample.betas.get(finding.marker),
            "methylation_coverage": sample.coverage.get(finding.marker),
        }
    result.findings.extend(findings)
    result.provider_status.extend(statuses)
    result.notes.extend(sample.warnings)
    result.notes.append(PLATFORM_NOTE)
    return {
        **sample.stats,
        "modification": "5mC",
        "annotation_scope": "mapped CpG sites with sufficient coverage; local reference databases only",
        "array_clocks": "withheld_pending_platform_validation",
        "reference_groups": "withheld_pending_platform_validation",
        "provenance": sample.provenance,
    }


def _finish_native(path, result, metadata, genome_stats=None):
    from .orchestrate import finish, _scan_stats
    genome_stats = genome_stats or {}
    result.scan_stats["markers_scanned"] = (
        metadata["methylation"].get("mapped_probes", 0)
        + genome_stats.get("markers_scanned", 0)
    )
    finish(result)
    for finding in result.findings:
        if (finding.detail or {}).get("measurement_platform") == "ONT" and finding.interpretation:
            depth = finding.detail.get("methylation_coverage")
            fraction = finding.detail.get("your reading")
            if fraction is not None and depth is not None:
                finding.interpretation.found += f" Native 5mC fraction: {fraction:.3f}, from {depth} valid reads."
            finding.interpretation.how_sure += (
                " Your reading comes from native ONT sequencing; agreement with "
                "array-based study measurements has not been established for this run."
            )
        if ((finding.detail or {}).get("sequencing_platform") == "ONT"
                and (finding.detail or {}).get("clinical_significance") and finding.interpretation):
            # Generic VCF copy assumes a validated WGS assay. State this run's
            # actual evidence without inferring accuracy from its file format.
            stars = (finding.detail or {}).get("gold_stars")
            review = f"ClinVar classification: {finding.detail['clinical_significance']}. "
            if stars is not None:
                review += f"ClinVar reports {stars} of 4 review stars. "
            finding.interpretation.how_sure = (
                review + ("This variant was read from a supplied variant file. "
                          if metadata.get("preparation", {}).get("variant_source") == "supplied VCF"
                          else "This variant was called from native ONT reads. ")
                + "The run's filtering and coverage are recorded in the sequencing summary. "
                "Clinical accuracy has not been established for this run."
            )
    result.scan_stats.update(_scan_stats(path, result))
    result.scan_stats["nanopore"] = metadata
    result.notes = list(dict.fromkeys(result.notes))
    return result


def analyze_native(path, result, *, config=None, reference_fasta=None,
                   reference_build=None, sample_id=None, min_coverage=5,
                   vcf_path=None, combined_strands=False, trait_table=None):
    from methylask.ingest.nanopore import read_bedmethyl
    if isinstance(min_coverage, bool) or not isinstance(min_coverage, int) or min_coverage < 1:
        raise ValueError("Minimum methylation coverage must be a positive integer")
    if result.kind == InputKind.BEDMETHYL:
        if vcf_path:
            raise ValueError("A supplied VCF requires the source BAM to validate sample and reference consistency")
        sample = read_bedmethyl(
            path, reference_build=reference_build, min_coverage=min_coverage,
            combined_strands=combined_strands,
        )
        result.engines = ("methylask",)
        metadata = {
            "sample_id": sample_id,
            "reference_build": "GRCh38",
            "input": "bedMethyl",
            "variants": {"status": "not_supplied", "scope": "No variant calls were supplied."},
            "methylation": _annotate_native(path, result, sample),
        }
        result.notes.append("This bedMethyl input supplies methylation measurements only; no genome variant screen ran.")
        return _finish_native(path, result, metadata)

    from .nanopore import NanoporeConfig, prepare_nanopore
    from .orchestrate import _run_geneask
    config = config or NanoporeConfig.from_env()
    overrides = {}
    if reference_fasta is not None:
        overrides["reference_fasta"] = reference_fasta
    if reference_build is not None:
        overrides["reference_build"] = "GRCh38" if reference_build == "hg38" else reference_build
    if overrides:
        config = replace(config, **overrides)
    with prepare_nanopore(path, config=config, sample_id=sample_id, vcf_path=vcf_path) as prepared:
        sample = read_bedmethyl(
            prepared.bedmethyl_path, reference_build=config.reference_build,
            min_coverage=min_coverage, combined_strands=True,
        )
        result.engines = ("nanopore", "methylask", "geneask")
        result.notes.extend(prepared.notes)
        methylation = _annotate_native(prepared.bedmethyl_path, result, sample)
        genome_stats = {}
        findings, notes, limits = _run_geneask(
            prepared.vcf_path, InputKind.VCF, trait_table=trait_table,
            scan_stats=genome_stats, statuses=result.provider_status,
            offline_only=True, allow_diplotypes=False,
        )
        for finding in findings:
            finding.detail = {**(finding.detail or {}), "sequencing_platform": "ONT"}
        result.findings.extend(findings)
        result.notes.extend(n for n in notes if not n.startswith("No carried variants were read from this VCF"))
        if not genome_stats.get("markers_scanned"):
            result.notes.append("No carried variants remained for interpretation after the stated call filters. This does not establish a reference genotype or a negative screen.")
        result.notes.append("Pharmacogenomic diplotypes are withheld: this filtered variant-only callset does not establish callable reference sites, structural variants, or complete gene coverage.")
        if limits:
            result.scan_stats["limits"] = limits
        metadata = {
            "sample_id": getattr(prepared, "sample_id", None) or prepared.provenance.get("sample_id") or sample_id,
            "reference_build": config.reference_build,
            "input": result.kind.value,
            "variants": {"status": "interpreted", "records_scanned": genome_stats.get("markers_scanned"),
                         "scope": prepared.provenance.get("variant_scope", "See preparation provenance for caller scope and exclusions.")},
            "methylation": methylation,
            "preparation": prepared.provenance,
        }
        return _finish_native(path, result, metadata, genome_stats)
