# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Shared, measurement-first native sequencing summary for report exports."""
from __future__ import annotations
import html
import json


def summary_rows(result):
    meta = (result.scan_stats or {}).get("nanopore")
    if not meta:
        return []
    methyl = meta.get("methylation", {})
    variants = meta.get("variants", {})
    prep = meta.get("preparation", {})
    qc = prep.get("variant_qc", {})
    rows = [
        ("Sample label", meta.get("sample_id") or "Not supplied"),
        ("Reference assembly", meta.get("reference_build", "Not supplied")),
        ("Variant screen", variants.get("scope", variants.get("status", "Not supplied"))),
        ("CpG probes with sufficient coverage", methyl.get("mapped_probes", 0)),
        ("Minimum valid calls per CpG", methyl.get("min_coverage", "Not recorded")),
        ("Probes below coverage threshold", methyl.get("low_coverage_probes", 0)),
        ("Probes without measurements", methyl.get("missing_probes", 0)),
    ]
    if qc:
        rows.append(("Variant records retained after filtering", qc.get("reported_records", 0)))
    return rows


CAUTIONS = (
    "Missing measurements are not zero methylation. Missing variants are not evidence of a reference genotype or a negative disease screen. "
    "The variant workflow covers autosomal small germline variants; X, Y, mitochondrial DNA, structural variants, copy number, and repeat expansions are outside its scope. "
    "Native 5mC fractions have not been calibrated to methylation arrays: age clocks and reference-group predictions are withheld. "
    "This run has not established clinical accuracy."
)


def summary_html(result):
    rows = summary_rows(result)
    if not rows:
        return ""
    items = "".join(f"<div><dt style='font-size:13px;color:var(--mut,#666)'>{html.escape(key)}</dt><dd style='margin:4px 0 0;font-weight:600'>{html.escape(str(value))}</dd></div>" for key, value in rows)
    provenance = html.escape(json.dumps(result.scan_stats["nanopore"], indent=2, default=str))
    demo = result.scan_stats["nanopore"].get("demo", {})
    introduction = ""
    if demo.get("synthetic"):
        introduction = ("<aside class='synthetic-demo-notice' style='padding:16px;color:var(--ink,#1b1c18);background:var(--accent-soft,#e6efe9);border:1px solid var(--accent,#2b6a5b);border-radius:6px'>"
                        "<strong>Synthetic Nanopore demo</strong><p>" + html.escape(demo["description"]) + "</p>"
                        + ("<p>" + html.escape(demo["finding_scope"]) + "</p>" if demo.get("finding_scope") else "") +
                        "<p>Download: <a href='/demo/nanopore?format=json'>report JSON</a> · "
                        "<a href='/demo/nanopore?format=markdown'>report Markdown</a> · "
                        "<a href='/demo/nanopore/files/calls.vcf'>VCF</a> · "
                        "<a href='/demo/nanopore/files/calls.bedmethyl'>bedMethyl</a> · "
                        "<a href='/demo/nanopore/files/README.md'>fixture guide</a></p></aside>")
        measurements = result.scan_stats["nanopore"].get("demo_measurements", [])
        introduction += "<p>" + html.escape(demo.get("probe_scope", "Selected CpG probes; these counts do not describe a whole genome.")) + "</p><ul>"
        for row in measurements:
            reading = f"{row['fraction']:.2f} from {row['valid_reads']} valid reads" if row['fraction'] is not None else row['status']
            if row['fraction'] is None and row['valid_reads'] is not None:
                reading += f" ({row['valid_reads']} valid reads; fraction withheld)"
            introduction += "<li>" + html.escape(f"{row['probe']}: {reading}") + "</li>"
        introduction += "</ul>"
    reference = result.scan_stats["nanopore"].get("reference_dataset", {})
    if reference:
        annotations = result.scan_stats["nanopore"].get("annotations", {})
        introduction = (
            "<aside class='reference-demo-notice' style='padding:16px;color:var(--ink,#1b1c18);background:var(--accent-soft,#e6efe9);border:1px solid var(--accent,#2b6a5b);border-radius:6px'>"
            "<strong>HG002 reference genome + methylome</strong>"
            "<p>Real published Nanopore measurements from the HG002 / GM24385 cultured lymphoblastoid cell line. "
            "This report describes a reference sample; it does not assess the donor's or reader's health, lifestyle or biological age.</p>"
            f"<p>Publisher-reported mean coverage: {reference['reported_mean_depth']:.2f}×. "
            f"{annotations['matched_association_rows']:,} research-association rows matched measured CpGs across "
            f"{annotations['traits_with_associations']:,} traits. The browser shows one leading CpG for each of "
            f"{annotations['displayed_trait_representatives']:,} selected traits, plus the retained ClinVar findings. "
            "All EPICv2-mapped CpG measurements and matched EWAS rows are available below. Other genome-wide CpGs remain in the source bedMethyl files.</p>"
            "<p>Download: <a href='/demo/hg002?format=json'>report JSON</a> · "
            "<a href='/demo/hg002?format=markdown'>report Markdown</a> · "
            "<a href='/demo/hg002/files/HG002.cpg-measurements.tsv.gz'>all EPICv2-mapped CpG measurements</a> · "
            "<a href='/demo/hg002/files/HG002.associations.tsv.gz'>all matched associations</a> · "
            "<a href='/demo/hg002/files/HG002.variant-findings.json.gz'>retained ClinVar findings</a> · "
            "<a href='/demo/hg002/files/provenance.json'>provenance</a> · "
            "<a href='/demo/hg002/files/artifact-index.json'>checksums</a></p>"
            "<p>Source: <a href='https://epi2me.nanoporetech.com/giab-2025.01/'>Oxford Nanopore GIAB 2025.01</a>, "
            "CC BY-NC 4.0. Published-output import and reporting are demonstrated here; basecalling and independent caller benchmarking have not run.</p></aside>")
    return ("<section class='sequencing-summary' style='max-width:900px;margin:28px 0;padding:22px;border:1px solid var(--line,#ddd);border-radius:8px;overflow-wrap:anywhere'>"
            + introduction +
            "<h2>Native sequencing measurements</h2>"
            "<p>The sample label links these outputs; a label alone does not verify biological identity.</p>"
            f"<dl style='display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px'>{items}</dl><p>{CAUTIONS}</p>"
            "<details><summary>Coverage, thresholds and processing provenance</summary>"
            f"<pre style='white-space:pre-wrap;overflow-wrap:anywhere'>{provenance}</pre></details></section>")


def summary_markdown(result):
    rows = summary_rows(result)
    if not rows:
        return []
    def safe(value):
        return html.escape(str(value)).replace("|", "\\|").replace("\n", " ")
    lines = ["## Native sequencing measurements", "", "| Measurement | Value |", "| --- | --- |"]
    demo = result.scan_stats["nanopore"].get("demo", {})
    if demo.get("synthetic"):
        lines = ["## Synthetic Nanopore demo", "", demo["description"], "", demo.get("finding_scope", ""), "", demo["probe_scope"], ""] + lines
    reference = result.scan_stats["nanopore"].get("reference_dataset", {})
    if reference:
        lines = ["## HG002 public reference dataset", "", "Real published measurements from a cultured lymphoblastoid cell line; no personal health or age prediction.", "", "Source: https://epi2me.nanoporetech.com/giab-2025.01/ — CC BY-NC 4.0.", "", "Complete tables and provenance: https://dna.goodancestor.com/demo/hg002", ""] + lines
    lines += [f"| {safe(key)} | {safe(value)} |" for key, value in rows]
    lines += ["", "The sample label links these outputs; a label alone does not verify biological identity.", "", CAUTIONS, "",
              "Coverage, thresholds, hashes and tool versions are retained in the JSON export.", ""]
    return lines
