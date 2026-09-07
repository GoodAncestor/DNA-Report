# Synthetic Nanopore report fixture

No person was sequenced. `ONT-DEMO-SYNTHETIC` is a fictional label shared by the
illustrative VCF and bedMethyl. Genotypes, DP/GQ, read counts and fractions are
invented. There is no source BAM, POD5, reference FASTA, or model execution.

The live demo exercises real VCF parsing, MethylAsk's native CpG reader, shared
native report interpretation, and HTML/JSON/Markdown exports. It uses frozen
reference evidence so opening it needs no network, model, or local database.
It is not a test of extraction, sequencing, alignment or calling accuracy.

## Measurements

The bedMethyl has the modkit 18-column layout, separate modification `m` (5mC),
strand-combined `.` rows and GRCh38 coordinates (0-based forward CpG start).
Fractions divide modified calls by all valid calls, including other modifications.
The four-probe map is a subset of the bundled EPICv2 hg38 manifest, not a whole
genome. The EWAS record's historical chrpos is not used for input mapping.

- cg05575921: 13 modified / 20 valid = 0.65; two valid calls are other modifications.
- cg03636183: 0 modified / 10 valid = 0.0; this measured zero is retained.
- cg16867657: 1 modified / 2 valid; withheld below the minimum of 5 valid calls.
- cg19693031: no row; missing, never imputed to zero.

The VCF contains one invented heterozygous genotype at a real BRCA2 variant
(ClinVar Variation ID 421014). Its public classification is copied from the
bundled GeneAsk panel; it is not evidence anyone carries the variant. The EWAS
association is an actual Catalog row from PMID 23691101. Full records, retrieval
date, source locations and the source-panel hash are in `reference.json`.

Array clocks, reference-group classifications and pharmacogenomic diplotypes
are withheld. Published methylation associations are context only; the demo does
not infer smoking status from the synthetic native fraction.

## Reproduce

Open `/demo/nanopore`, with `?format=json` or `?format=markdown` for public synthetic
exports. The four downloads are available under `/demo/nanopore/files/`.

In an installed source checkout:

```python
from dnareport.nanopore_demo import build_demo
from dnareport.report import render_report
render_report(build_demo(), 'nanopore-demo.html')
```

A bedMethyl-only upload with GRCh38 and combined strands selected exercises the
regular worker path. That path uses its full manifest and installed reference
mirrors, so its missing-probe counts and annotation coverage differ from this
four-probe fixture. Combining an uploaded VCF and bedMethyl requires a source
BAM for validation; the explicitly synthetic demo does not bypass that rule.
