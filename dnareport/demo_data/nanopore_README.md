# Synthetic Nanopore report fixture

No person was sequenced. `ONT-DEMO-SYNTHETIC` is a fictional label shared by the
illustrative VCF and bedMethyl. Genotypes, DP/GQ, read counts and fractions are
invented. There is no source BAM, POD5, reference FASTA, or model execution.

The demo deliberately contains **eight findings: six CpG associations and two
variant classifications**. It covers smoking, body mass index, triglycerides,
HDL cholesterol and age associations, plus likely pathogenic and conflicting
ClinVar examples. These are selected examples of reporting behavior, not a
representative prevalence of findings in a person or a whole-genome screen.

Opening the demo exercises real VCF parsing, MethylAsk's native CpG reader,
shared report interpretation, and HTML/JSON/Markdown exports. Frozen reference
evidence makes this deterministic without network, models, or local databases.
It does not test extraction, sequencing, alignment or calling accuracy.

## Measurements and evidence

The bedMethyl uses the modkit 18-column layout, separate modification `m` (5mC),
strand-combined `.` rows and GRCh38 coordinates (0-based forward CpG start).
Fractions divide modified calls by all valid calls, including other modifications.
The eight-probe map is a subset of the bundled EPICv2 hg38 manifest. The EWAS
records' historical chrpos annotations are not used for input mapping.

| CpG | Synthetic valid reads | Synthetic 5mC fraction | Frozen association | PMID |
| --- | ---: | ---: | --- | --- |
| cg05575921 / AHRR | 20 | 13/20 = 0.65 | Smoking | 23691101 |
| cg03636183 / F2RL3 | 10 | 0/10 = 0.00 | Smoking | 23691101 |
| cg06500161 / ABCG1 | 24 | 18/24 = 0.75 | Body mass index | 28002404 |
| cg00574958 / CPT1A | 30 | 9/30 = 0.30 | Triglycerides | 28194238 |
| cg17901584 / DHCR24 | 25 | 15/25 = 0.60 | HDL cholesterol | 39706196 |
| cg22454769 / FHL2 | 20 | 8/20 = 0.40 | Age | 23177740 |
| cg16867657 | 2 | Withheld below minimum 5 | No finding | — |
| cg19693031 | Missing | Missing, never zero | No finding | — |

Only one Catalog association per retained CpG is included. The FHL2 row is the
Catalog's re-analysis of GSE40279, stated in its original analysis field. The
AHRR valid-read denominator includes two calls of other modifications; F2RL3
shows a retained measured zero. Low coverage and absent measurements stay distinct.

The VCF contains two invented heterozygous genotypes at real autosomal variants:

- BRCA2 `13-32316419-CAG-C`: likely pathogenic in the bundled panel, two review
  stars; [ClinVar Variation ID 421014](https://www.ncbi.nlm.nih.gov/clinvar/variation/421014/).
- PALB2 `16-23603449-T-A`: conflicting classifications of pathogenicity in the
  bundled panel; [ClinVar Variation ID 921019](https://www.ncbi.nlm.nih.gov/clinvar/variation/921019/).

Their classification records come from GeneAsk's bundled ClinVar panel (release
2026-06-06), not from invented clinical evidence. Full selected EWAS records,
retrieval dates, API response hashes for the added records, source-panel hash,
and manifest hash are in `reference.json`. API records were obtained directly
from the EWAS Catalog on 2026-09-06. The demo freezes these records; it does not
assert that classifications or Catalog data will remain unchanged.

Array clocks, reference-group classifications and pharmacogenomic diplotypes
are withheld. Published methylation associations are context only: the synthetic
fractions do not establish smoking status, BMI, blood lipid levels or age.

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
eight-probe fixture. Combining an uploaded VCF and bedMethyl requires a source
BAM for validation; the explicitly synthetic demo does not bypass that rule.
