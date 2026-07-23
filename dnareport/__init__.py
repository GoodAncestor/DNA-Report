"""DNA-Report: one upload, all relevant analysis.

The product front door above the analysis engines. Detects an uploaded file's
type, routes it to MethylAsk (methylation) and/or GeneAsk (variants), and merges
their findings into one evidence-tiered report via bio-core's renderer.

Owns no analysis and no databases — orchestration only. Dependency direction is
acyclic: DNA-Report -> {MethylAsk, GeneAsk} -> bio-core.
"""
__version__ = "0.1.0"
