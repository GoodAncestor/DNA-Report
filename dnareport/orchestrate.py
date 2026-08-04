# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Orchestrator — one upload in, one merged evidence-tiered report out.

DNA-Report is the thin product layer above the engines. It owns no analysis and
no databases: it detects the file type, routes to MethylAsk (methylation) and/or
GeneAsk (variants), collects the bio-core Finding objects each returns, and hands
the merged set to bio-core's renderer. An ONT modBAM routes to BOTH — bio-core
splits it into a methylation stream and a variant stream first.

Dependency direction (acyclic): DNA-Report -> {MethylAsk, GeneAsk} -> bio-core.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .detect import detect, InputKind, ROUTING


@dataclass
class ReportResult:
    kind: InputKind
    engines: tuple
    findings: list = field(default_factory=list)
    provider_status: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    clocks: list = field(default_factory=list)   # list[ClockResult] from the aging engine
    tissue: str | None = None                     # sample tissue used for clock validity
    scan_stats: dict = field(default_factory=dict)  # metrics about what was scanned


def _attach_sample_readings(findings: list, betas: dict):
    """Put the reader's own value on each finding about one of their probes.

    A finding that says a site is associated with lower methylation, without ever
    showing what this person's methylation was, is a fact about a cohort rather
    than about them. The reference highlight cards are skipped — they already plot
    the reading on their own scale, so annotating them would state it twice.

    Only `detail` is set, never the description: bio-core groups findings into one
    card per marker, so the renderer shows this once in the card header. Writing
    it into each description restated the same number up to 30 times per card.
    """
    for f in findings:
        if f.source == "marker_reference":
            continue
        v = betas.get(f.marker)
        if v is None:
            continue
        f.detail = {**(f.detail or {}), "your reading": round(float(v), 3)}
    return findings


def _reference_findings(betas: dict, tissue: str | None = None):
    """Position a beta profile against MethylAsk's curated reference values.

    These are absolute population levels quoted from named publications, so the
    report can say "your value is X; never-smokers median Y [PMID]" rather than
    leaving a bare number. Local arithmetic, so it runs on the FULL profile —
    unlike the provider annotation above, which is capped because it is one
    network lookup per marker.

    A reference measured in a tissue this sample is not is reported as
    unavailable (Tier.UNKNOWN) with the reason, never rendered as a comparison:
    at cg05575921 the pediatric buccal demo would otherwise land nearest the
    whole-blood current-smoker median.
    """
    from biocore.providers.base import Finding, Tier, Category
    from methylask.reference import (positions_for_sample, load_reference_table,
                                     marker_meaning, reference_for)

    table = load_reference_table()
    findings = []
    for mp in positions_for_sample(betas, tissue=tissue, table=table):
        meaning = marker_meaning(table, mp.probe)
        base = {"beta": mp.sample_beta, **meaning}

        if mp.suppressed_reason:
            ref_tissues = sorted({r.get("tissue", "") for r in reference_for(table, mp.probe)
                                  if r.get("tissue")})
            findings.append(Finding(
                marker=mp.probe, source="marker_reference",
                description=(f"No comparison shown for a {tissue} sample — published "
                             f"values are {', '.join(ref_tissues)} only."
                             if tissue and ref_tissues else
                             f"No comparison shown: {mp.suppressed_reason}."),
                tier=Tier.UNKNOWN, categories=[Category.AGING],
                detail={**base, "reference_withheld": True,
                        "reference_tissue": ", ".join(ref_tissues) or None,
                        "suppressed_reason": mp.suppressed_reason}))
            continue

        groups = [{"group": p.reference_group, "beta": p.reference_beta,
                   "stat": p.stat, "n": p.n, "tissue": p.tissue, "pmid": p.pmid}
                  for p in mp.positions]
        nearest = min(mp.positions, key=lambda p: abs(p.delta))
        findings.append(Finding(
            marker=mp.probe, source="marker_reference",
            description=(f"Closest published group: {nearest.reference_group}."),
            tier=Tier.MODERATE, categories=[Category.AGING],
            detail={**base, "groups": groups,
                    "reference_tissue": nearest.tissue,
                    "reference_n": nearest.n,
                    "nearest_group": nearest.reference_group},
            pmids=sorted({p.pmid for p in mp.positions if p.pmid})))
    return findings


def _run_methylask(path: str, kind: InputKind, *, tissue: str | None = None,
                   max_markers: int | None = 40):
    """Call the MethylAsk engine -> (findings, provider_status, clocks). Import
    lazily so DNA-Report can run with only one engine installed.

    Runs the epigenetic clocks on the full beta profile (passing `tissue` so
    blood-trained clocks on a non-blood sample are flagged, not trusted), and
    annotates a capped set of markers via the providers (annotation is one live
    lookup per marker, so it is capped to keep the request path light — heavy
    full-array runs go through the queue tier)."""
    from biocore.providers.registry import Registry
    from methylask.providers.ewas_catalog import EwasCatalogProvider
    from methylask.providers.clinvar import ClinVarProvider
    from methylask.providers.gdc import GdcProvider
    from methylask.ingest.beta_matrix import read_beta_matrix
    from methylask import clocks as _clocks

    reg = Registry()
    for p in (EwasCatalogProvider(), ClinVarProvider(), GdcProvider()):
        reg.register(p)

    from methylask.normalize import base_probe

    clock_results = []
    markers = []
    base_betas: dict = {}
    if kind == InputKind.BETA_MATRIX:
        sample = read_beta_matrix(path)
        markers = sample.markers                      # list[str] of probe ids
        # Clocks are keyed by BASE probe id. EPICv2 exports carry replicate
        # suffixes (cg#####_TC21); without stripping them every clock resolves
        # zero CpGs and the report silently shows no age.
        base_betas = {base_probe(k): v for k, v in sample.betas.items()}
        # clocks run on the WHOLE profile (cheap arithmetic, no network)
        clock_results = _clocks.run_all(base_betas, tissue=tissue)

    capped = markers[:max_markers] if (max_markers and markers) else markers
    rep = reg.annotate(capped) if capped else reg.annotate([])
    findings = _attach_sample_readings(rep.all_findings(), base_betas)

    # Curated reference context: local, so it sees the whole profile rather than
    # the capped subset. Keyed by base probe id (EPICv2 carries replicate suffixes).
    if kind == InputKind.BETA_MATRIX and base_betas:
        findings += _reference_findings(base_betas, tissue=tissue)

    return findings, reg.status(), clock_results


def _run_geneask(path: str, kind: InputKind, trait_table: str | None = None):
    """Interpret a single-sample VCF/23andMe callset -> (findings, status).

    Two screens: the ClinVar clinical panel (pathogenic/likely-pathogenic hits,
    incl. cancer-predisposition genes) always runs; the trait table runs when
    supplied. Every finding is tagged modality='genome' so the report's source
    bubble/filter mark it as genome-derived (vs methylome).
    """
    findings = []
    notes: list[str] = []
    is_array = kind in (InputKind.ARRAY_GENOTYPE, InputKind.TWENTYTHREE_AND_ME)

    # array formats have no REF/ALT and vary in genome build, so parse once and
    # reuse the parsed callset for both the ClinVar screen and the trait table.
    parsed = None
    if is_array:
        try:
            from geneask.parsers import parse_file
            parsed = parse_file(path)
        except ImportError:
            raise
        except Exception:
            parsed = None

    # ClinVar clinical screen. Two input paths:
    #   VCF        -> bio-core carried_variants (pysam, REF/ALT already in the file)
    #   array data -> parser registry -> panel-anchored carried variants, with
    #                 GRCh37->38 liftover so build-37 uploads still match the
    #                 GRCh38 panel (otherwise they silently match nothing)
    try:
        from geneask.interpret.clinvar_screen import screen_findings, load_panel
        panel = load_panel()
        if is_array:
            if parsed is not None:
                from geneask.parsers.to_carried import carried_from_parse
                carried = carried_from_parse(parsed, panel)   # appends build note to parsed.notes
                findings += screen_findings(carried, panel)
        else:
            from biocore.variants.carried import carried_variants
            carried = carried_variants(path)
            findings += screen_findings(carried, panel)
    except ImportError:
        raise
    except Exception:
        # a malformed file or missing pysam shouldn't kill the whole report
        pass

    # trait table: fall back to GeneAsk's bundled consumer-genetics table when a
    # caller doesn't supply one, so a genome upload reports traits by default.
    try:
        from geneask.interpret.traits import (trait_findings,
            trait_findings_from_parse, DEFAULT_TRAIT_TABLE)
        table = trait_table or DEFAULT_TRAIT_TABLE
        if is_array:
            tf = trait_findings_from_parse(parsed, table) if parsed is not None else []
        else:
            tf = trait_findings(path, table)
        for f in tf:
            if f.detail is None:
                f.detail = {}
            # traits are behavioural/physiological, not clinical -> topic 'trait'
            f.detail.setdefault("topic", "other")
        findings += tf
    except Exception:
        pass

    # GWAS Catalog SNP-trait annotations, mirror-first: only fires when the
    # GWAS mirror volume has been built (refresh:gwas on a worker). For array
    # uploads we have the carried rsIDs + alleles directly; a bare mirror-miss
    # returns nothing, so this is a no-op until the mirror exists.
    try:
        if is_array and parsed is not None:
            from geneask.annotators.gwas_catalog import findings_for
            seen = 0
            for r in parsed.records:
                if r.is_nocall or not r.rsid.startswith("rs"):
                    continue
                carried = {a for a in (r.allele1, r.allele2) if a}
                gfs = findings_for(r.rsid, carried_alleles=carried)
                findings += gfs
                seen += 1 if gfs else 0
            if seen:
                notes.append(f"GWAS Catalog: annotated {seen} variants from the local mirror")
    except Exception:
        pass

    # CPIC pharmacogenomics: for pharmacogenes the person carries variants in,
    # surface CPIC drug-response guidance. Mirror-first (refresh:pgx). This keys on
    # the GENE (from ClinVar-screen findings), not a called diplotype — an honest
    # "you carry variants in CYP2C19; here is CPIC's drug guidance for this gene"
    # rather than a phenotype claim (diplotype calling is a documented follow-on).
    try:
        from geneask.annotators.cpic_pgx import recommendations_for_gene
        genes = {str((f.detail or {}).get("gene")) for f in findings
                 if (f.detail or {}).get("gene")}
        pgx_seen = set()
        for g in sorted(genes):
            recs = recommendations_for_gene(g)
            # one guidance line per drug (dedup the phenotype-expanded rows)
            by_drug = {}
            for r in recs:
                by_drug.setdefault(r.detail["drug"], r)
            for r in by_drug.values():
                findings.append(r)
                pgx_seen.add(g)
        if pgx_seen:
            notes.append(f"CPIC: pharmacogenomic drug guidance for {len(pgx_seen)} gene(s) "
                         f"you carry variants in")
    except Exception:
        pass

    # AlphaMissense pathogenicity, layered onto variant findings (chrom-pos-ref-alt):
    # a computational benign/pathogenic call for missense variants ClinVar hasn't
    # curated. Mirror-first — no-op until refresh:alphamissense builds the mirror.
    try:
        from geneask.annotators.alphamissense import annotate_findings as _am
        amn = _am(findings)
        if amn:
            notes.append(f"AlphaMissense: added pathogenicity to {amn} missense variants")
    except Exception:
        pass

    # AlphaGenome regulatory VEP, layered onto UNCERTAIN variant findings (the ones
    # ClinVar can't resolve): predicts a regulatory effect from sequence for
    # non-coding / uncertain-significance variants the catalogues miss. Key-gated,
    # opt-in, per-report capped, disk-cached — no-op unless ALPHA_GENOME_KEY +
    # ALPHAGENOME_ENABLED are set (the license constraint travels with the key).
    try:
        from geneask.annotators.alphagenome_vep import (annotate_findings as _ag,
                                                        default_pacing as _ag_pacing)
        # AlphaGenome publishes no quota — their stated advice is to increase load
        # until RESOURCE_EXHAUSTED — so there is no daily number to budget against.
        # Pace, bound the wait, and report honestly when a run was cut short.
        agp = _ag_pacing()
        agn = _ag(findings, pacing=agp)
        if agn:
            notes.append(f"AlphaGenome: predicted regulatory effect for {agn} uncertain variants")
        if agp.client_missing:
            notes.append("AlphaGenome: enabled but its client library is not installed on this "
                         "server, so nothing was scored — a deployment fault, not a result")
        elif agp.halted:
            notes.append("AlphaGenome: quota exhausted upstream; remaining variants were not scored")
        elif agp.deadline is not None and agp.deadline.expired():
            notes.append("AlphaGenome: stopped at this report's time budget; "
                         "remaining uncertain variants were not scored")
    except Exception:
        pass

    # gnomAD population frequency, layered onto variant findings (chrom-pos-ref-alt
    # markers): reframes a scary ClinVar hit with how common the variant actually is.
    # Per-variant + disk-cached, so it's a bounded number of lookups, no mirror.
    try:
        from geneask.annotators.gnomad_freq import (annotate_findings as _gnomad,
                                                    default_budget as _gnomad_budget)
        # Paced, not capped: gnomAD publishes 30 requests/minute per IP and no
        # daily quota, so a report may make as many lookups as it needs at that
        # rate. What is bounded is how long ONE report waits for it — a latency
        # decision. Either way, say so in the notes when lookups were skipped: a
        # silently truncated enrichment reads as "this variant isn't in gnomAD",
        # which is a different and stronger claim than "we didn't look".
        budget = _gnomad_budget()
        got = _gnomad(findings, budget=budget)
        if got:
            notes.append(f"gnomAD: added population frequency to {got} variant findings")
        if budget.halted:
            notes.append("gnomAD: rate-limited by the API; some frequencies were not looked up")
        elif budget.deadline is not None and budget.deadline.expired():
            notes.append(f"gnomAD: made {budget.spent} lookups within this report's time "
                         "budget; uncached variants were left un-annotated")
    except Exception:
        pass

    # tag provenance so the renderer's modality bubble/filter light up
    for f in findings:
        if f.detail is None:
            f.detail = {}
        f.detail.setdefault("modality", "genome")
    # surface build/liftover handling to the report (parsed.notes set by the converter)
    if parsed is not None:
        notes += parsed.notes
    return findings, notes


def _run_modbam_methylation(path: str, *, reference_fasta: str | None = None):
    """Extract the methylation stream from an ONT modBAM via bio-core and
    summarize per-context weighted methylation as Findings (AGING/CLINICAL are
    for interpreted markers; a whole-genome context summary is a TRAIT-level
    descriptive finding). Returns a list of bio-core Findings."""
    from biocore.io.modbam import pileup_methyl
    from biocore.methylation.model import weighted_methylation, Context
    from biocore.providers.base import Finding, Tier, Category

    sites = list(pileup_methyl(path, min_prob=0.5, min_coverage=5,
                               reference_fasta=reference_fasta))
    by_ctx = {}
    for s in sites:
        by_ctx.setdefault(s.context, []).append(s)
    findings = []
    for ctx in (Context.CG, Context.CHG, Context.CHH):
        cs = by_ctx.get(ctx, [])
        if not cs:
            continue
        wm = weighted_methylation(cs, min_coverage=5) * 100
        findings.append(Finding(
            marker=f"modBAM:{ctx.value}", source="biocore.modbam",
            description=f"Genome-wide {ctx.value} weighted methylation: {wm:.1f}% "
                        f"over {len(cs)} covered cytosines (ONT MM/ML pileup, cov>=5)",
            tier=Tier.MODERATE, categories=[Category.TRAIT]))
    return findings


def compare(vcf: str) -> ReportResult:
    """Reconcile multiple tests of one person from a merged multi-sample VCF.

    Each sample column is one test (e.g. several 23andMe chips + a WGS). Drives
    GeneAsk's compare (which rests on bio-core.compare): pairwise concordance,
    discordance typing, and a KING self-identity check (are all samples really
    the same person?). Returns a ReportResult whose findings are the identity
    check + cross-test concordance, renderable through the same bio-core renderer
    as analyze().
    """
    result = ReportResult(kind=InputKind.VCF, engines=("geneask.compare",))
    try:
        from geneask.compare import compare_findings
        result.findings += compare_findings(vcf)
        if not result.findings:
            result.notes.append("Comparison produced no findings — check the VCF "
                                 "has >=2 sample columns.")
    except ImportError as e:
        result.notes.append(f"GeneAsk not installed: {e}")
    return result


def analyze(path: str, *, trait_table: str | None = None,
            reference_fasta: str | None = None,
            tissue: str | None = None) -> ReportResult:
    """Detect, route, run engine(s), collect merged findings.

    reference_fasta: optional; enables CG/CHG/CHH context resolution for a modBAM
    (required to report non-CpG contexts).
    tissue: sample tissue (e.g. 'blood','saliva','buccal'); passed to the clock
    engine so a clock trained on a different tissue is flagged, not trusted.
    """
    kind = detect(path)
    engines = ROUTING[kind]
    result = ReportResult(kind=kind, engines=engines, tissue=tissue)

    if kind == InputKind.UNKNOWN:
        result.notes.append(f"Could not determine file type for {path}; no engine routed.")
        return result

    # modBAM: bio-core splits into methylation + variant streams.
    if kind == InputKind.MODBAM:
        try:
            f = _run_modbam_methylation(path, reference_fasta=reference_fasta)
            result.findings += f
            result.notes.append("ONT modBAM: methylation stream extracted via bio-core "
                                "(MM/ML pileup). Variant stream requires a variant caller "
                                "on the same BAM (bcftools/DeepVariant) before GeneAsk can "
                                "interpret it — not run here.")
        except ImportError as e:
            result.notes.append(f"bio-core modBAM reader unavailable ({e}); needs pysam.")
        return result

    if "methylask" in engines:
        try:
            f, st, clk = _run_methylask(path, kind, tissue=tissue)
            result.findings += f
            result.provider_status += st
            result.clocks += clk
        except ImportError as e:
            result.notes.append(f"MethylAsk not installed: {e}")

    if "geneask" in engines:
        try:
            f, gnotes = _run_geneask(path, kind, trait_table=trait_table)
            result.findings += f
            result.notes += gnotes
        except ImportError as e:
            result.notes.append(f"GeneAsk not installed: {e}")

    result.scan_stats = _scan_stats(path, result)
    return result


# reference-corpus sizes (approx, measured) each source represents — so the report
# can say "N million variants of science scanned", investing the user in the scale.
_CORPUS_SCALE = {
    "clinvar": 4_200_000, "gwas_catalog": 1_000_000, "alphamissense": 71_000_000,
    "gnomad": 800_000_000, "cpic": 5_000, "ewas_catalog": 8_000_000,
    "gdc": 480_000, "alphagenome": 0,
}


def _scan_stats(path: str, result: "ReportResult") -> dict:
    """Honest metrics about what this scan touched — derived from what actually
    happened, not invented. Feeds the report's 'scan summary' panel."""
    import os
    from biocore.report.sources import resolve as _rsrc
    findings = result.findings
    # classified vs uncertain (a finding is 'uncertain' if its tier is speculative/
    # unknown or its ClinVar significance is uncertain/conflicting)
    def _uncertain(f):
        sig = str((f.detail or {}).get("clinical_significance", "")).lower()
        t = getattr(f.tier, "value", str(f.tier))
        return ("uncertain" in sig or "conflicting" in sig or t in ("speculative", "unknown"))
    classified = sum(1 for f in findings if not _uncertain(f))
    uncertain = len(findings) - classified
    # distinct external sources, split local-mirror vs live-API
    srcs = {}
    for f in findings:
        s = _rsrc(getattr(f, "source", "") or "")
        if s:
            srcs[s.key] = s
    _LIVE_API = {"gnomad", "alphagenome"}   # queried live per-variant, not mirrored
    dbs_queried = sorted(k for k in srcs if k not in _LIVE_API)
    apis_called = sorted(k for k in srcs if k in _LIVE_API)
    variants_of_science = sum(_CORPUS_SCALE.get(k, 0) for k in srcs)
    try:
        input_bytes = os.path.getsize(path)
    except OSError:
        input_bytes = 0
    return {
        "input_bytes": input_bytes,
        "markers_scanned": len({f.marker for f in findings}),
        "findings_total": len(findings),
        "classified": classified,
        "uncertain": uncertain,
        "local_dbs_queried": [srcs[k].name for k in dbs_queried],
        "live_apis_called": [srcs[k].name for k in apis_called],
        "reference_variants_scanned": variants_of_science,
    }


def _disclaimer_path() -> str:
    """DNA-Report is the product, so it owns the user-facing disclaimer text
    (bio-core's own DISCLAIMER.md says the product owns it). Bundled as package
    data so it is present in the deployed container."""
    from pathlib import Path
    return str(Path(__file__).parent / "docs" / "DISCLAIMER.md")


def _marker_url(marker: str) -> str | None:
    """Map a marker id to a public record (product-level domain knowledge; kept
    out of organism-agnostic bio-core). CpG probe -> EWAS Catalog; rsID -> dbSNP;
    chrom:pos -> UCSC browser."""
    m = marker.strip()
    if m[:2] in ("cg", "ch"):
        return f"https://www.ewascatalog.org/?query={m}"
    if m.startswith("rs") and m[2:].isdigit():
        return f"https://www.ncbi.nlm.nih.gov/snp/{m}"
    if m.startswith("chr") and ":" in m:
        return f"https://genome.ucsc.edu/cgi-bin/hgTracks?db=hg38&position={m}"
    return None


def render(result: ReportResult, out_html: str = "report.html") -> str:
    """Render the merged findings through bio-core's renderer (one report,
    grouped by category then marker, robust first, single disclaimer)."""
    from biocore.report.render import render_html
    html = render_html(result.findings, result.provider_status,
                       disclaimer_path=_disclaimer_path(),
                       title="DNA-Report", marker_url=_marker_url,
                       scan_stats=result.scan_stats)
    with open(out_html, "w") as fh:
        fh.write(html)
    return out_html
