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


def _run_methylask(path: str, kind: InputKind):
    """Call the MethylAsk engine -> (findings, provider_status). Import lazily so
    DNA-Report can run with only one engine installed."""
    from biocore.providers.registry import Registry
    from methylask.providers.ewas_catalog import EwasCatalogProvider
    from methylask.providers.clinvar import ClinVarProvider
    from methylask.providers.gdc import GdcProvider
    from methylask.ingest.beta_matrix import read_beta_matrix

    reg = Registry()
    for p in (EwasCatalogProvider(), ClinVarProvider(), GdcProvider()):
        reg.register(p)
    markers = read_beta_matrix(path).markers if kind == InputKind.BETA_MATRIX else []
    rep = reg.annotate(markers) if markers else reg.annotate([])
    return rep.all_findings(), reg.status()


def _run_geneask(path: str, kind: InputKind, trait_table: str | None = None):
    """Call the GeneAsk engine -> (findings, provider_status)."""
    from geneask.interpret.traits import trait_findings
    findings = []
    if trait_table:
        findings = trait_findings(path, trait_table)
    return findings, []


def analyze(path: str, *, trait_table: str | None = None) -> ReportResult:
    """Detect, route, run engine(s), collect merged findings."""
    kind = detect(path)
    engines = ROUTING[kind]
    result = ReportResult(kind=kind, engines=engines)

    if kind == InputKind.UNKNOWN:
        result.notes.append(f"Could not determine file type for {path}; no engine routed.")
        return result

    # modBAM: bio-core splits into methylation + variant streams first
    if kind == InputKind.MODBAM:
        result.notes.append("ONT modBAM: bio-core splits into methylation + variant streams; "
                            "both engines run on the respective stream.")
        # (stream extraction wired once bio-core's modBAM reader lands; interface reserved here)

    if "methylask" in engines:
        try:
            f, st = _run_methylask(path, kind)
            result.findings += f
            result.provider_status += st
        except ImportError as e:
            result.notes.append(f"MethylAsk not installed: {e}")

    if "geneask" in engines:
        try:
            f, st = _run_geneask(path, kind, trait_table=trait_table)
            result.findings += f
            result.provider_status += st
        except ImportError as e:
            result.notes.append(f"GeneAsk not installed: {e}")

    return result


def render(result: ReportResult, out_html: str = "report.html") -> str:
    """Render the merged findings through bio-core's renderer (one report,
    grouped by category, sorted by evidence tier, single disclaimer)."""
    from biocore.report.render import render_html
    html = render_html(result.findings, result.provider_status)
    with open(out_html, "w") as fh:
        fh.write(html)
    return out_html
