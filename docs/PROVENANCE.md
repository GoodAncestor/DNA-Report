# Provenance — DNA-Report

New code, written as the product layer over the existing engines. It introduces
no analysis of its own:

| Module | What it does | Depends on |
|---|---|---|
| `detect.py` | classify upload by extension + content sniff; routing map | — |
| `orchestrate.py` | run routed engine(s), merge Findings, render | methylask, geneask, biocore |
| `cli.py` | `detect` / `analyze` commands | — |

The routing contract (which file type feeds which engine) follows the
architecture in MethylAsk `docs/GENOMIC_SCOPE.md`: mechanism in bio-core,
knowledge in the engines, product on top. The modBAM "split into methylation +
variant streams" step is reserved in the orchestrator and wires to bio-core's
modBAM reader when that lands.
