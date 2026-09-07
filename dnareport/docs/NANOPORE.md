# Native Nanopore → DNA-Report

The integration prepares a single sample's POD5 or modBAM, then sends the same
aligned BAM through Clair3 for small germline variants and modkit for native CpG
5mC fractions. GeneAsk and MethylAsk produce one HTML report and matching JSON and
Markdown exports. This is a research workflow awaiting validation on real runs.

## Prepare the local worker

Use the coordinated dependency commits in `pyproject.toml`. The engines are private
repositories; installation requires existing Git credentials. See
[dna-report-deploy native worker setup](https://github.com/GoodAncestor/dna-report-deploy/blob/feature/nanopore-integration/workers/NANOPORE.md)
for installation, resource bounds and the separate ONT queue.

Provide an indexed GRCh38 FASTA, its independently verified SHA256, and compatible
local Clair3/Dorado models. Chemistry, pore, sampling rate, basecaller and model
must agree. Configured model paths and executable names come from the operator's
environment, never from a browser upload. No model is downloaded by this workflow.

```sh
export DNAREPORT_ONT_REFERENCE=/private/references/GRCh38.fa
export DNAREPORT_ONT_REFERENCE_SHA256='<verified 64-character SHA256>'
export DNAREPORT_ONT_CLAIR3_MODEL=/private/models/clair3-compatible-with-your-basecaller
export DNAREPORT_ONT_CLAIR3_MODEL_ID='<recorded model release and chemistry>'
export DNAREPORT_ONT_SCRATCH=/private/scratch
# Required for POD5 or when the BAM must be realigned:
export DNAREPORT_ONT_DORADO_MODEL=/private/models/dorado-canonical
# POD5 also requires its compatible native modification model:
export DNAREPORT_ONT_MOD_MODEL=/private/models/dorado-modified-bases
```

The executable defaults are `samtools`, `modkit`, `run_clair3.sh` and `dorado`.
Override with `DNAREPORT_ONT_SAMTOOLS`, `_MODKIT`, `_CLAIR3`, `_DORADO` as needed.
Resource controls are `_THREADS` (4), `_TIMEOUT` (86400 seconds), `_SCRATCH_GB`
(500), `_SORT_MEMORY_MB` (512 per thread), and `_DEVICE` (cpu). These are bounds,
not guarantees that a particular machine can complete a human genome run.

## Analyze a run

```sh
dna-report analyze /private/run/sample.bam \
  --sample-id SELF-001 --tissue blood --reference-build GRCh38 \
  --min-coverage 5 --out /private/reports/SELF-001.html \
  --artifacts-dir /private/analysis-artifacts
```

Use a private label matching the BAM read-group sample name. If the BAM lacks
sample metadata, the explicitly supplied label is recorded as an assertion.
Sample names cannot establish biological identity. Input/output hashes and
reference verification are retained in the report's processing provenance.

For a BAM whose reference sequence checksums are absent or disagree, the workflow
realigns to the pinned FASTA. A BAM with no native modification tags is rejected;
retained POD5 can be basecalled with a compatible modification model.

POD5 input is one file. A MinKNOW run often has many chunks: merge the intended
single-sample run using the official POD5 tool before analysis, as documented in
the OpenLab handoff. A partial chunk does not represent a complete genome run.
Never merge different people or unseparated barcodes.

`--nanopore-vcf /private/run/existing.vcf.gz` reuses calls with their source BAM.
Exactly one matching sample label and compatible reference alleles are required.
The VCF remains externally supplied: these checks do not prove it was called from
that BAM. The report records that distinction. Filtering applies to reused calls too.

## Methylation-only input

```sh
dna-report analyze /private/run/cpg.bed \
  --reference-build GRCh38 --combined-strands --sample-id SELF-001 \
  --tissue blood --min-coverage 5 --out /private/reports/methylation.html
```

Use `--combined-strands` only for modkit output generated with `--combine-strands`.
Separate-strand rows otherwise map minus-strand calls to the forward CpG start.
Direct bedMethyl requires an explicit GRCh38/hg38 declaration; this is the supplied
coordinate build, not an automatic liftover or independent build verification.
It supplies methylation only. Extended modkit counts (18 columns) are required.

The bridge maps CpG coordinates to the bundled hg38 EPICv2 manifest. It reports
5mC separately from 5hmC, divides modified calls by all valid calls, excludes
ambiguous mappings, and records low coverage separately from absent measurements.
Native values locate local research associations. Array-trained clocks and
reference-group classifications are withheld pending cross-platform validation.
The native path uses local annotation data and does not query live per-variant APIs.

## Outputs and limits

The CLI writes `SELF-001.html`, `.json` and `.md` from one analysis. The JSON
includes tool versions, model/reference hashes, filtering counts, observed read
coverage, sample provenance and CpG mapping/coverage statistics. Per-finding
methylation fractions include their valid-read depth. A zero-match report still
shows measurement coverage and source availability.

`--artifacts-dir` (or `DNAREPORT_ONT_ARTIFACT_DIR`) retains filtered VCF, complete
bedMethyl and provenance JSON in a new private sample directory. This preserves
measurements without known annotations too. BAM/POD5 inputs remain yours to retain;
intermediate alignment scratch is removed after analysis. Cloud workers disable
this local archive option and follow the existing upload/result deletion policy.

Variant interpretation includes PASS records with complete diploid genotypes,
DP ≥ 8 and GQ ≥ 20 by default. Missing DP/GQ and partial/no-call genotypes are
excluded. Override thresholds through `_MIN_VARIANT_COVERAGE` and
`_MIN_GENOTYPE_QUALITY`; review such changes against a validation dataset.
Methylation probability threshold defaults to 0.8 (`_MOD_PROBABILITY`).

The variant scope is autosomal small germline variants. X/Y, mitochondrial DNA,
structural variants, copy number and repeat expansions are excluded. Read depth
and breadth are descriptive, not a validated callable-genome mask. Missing
variants cannot be treated as homozygous reference or a negative disease screen.
Personalized pharmacogenomic diplotypes are withheld for this variant-only
callset; gene-level published context can still appear.

## Validation performed

Synthetic same-sample BAM/VCF/bedMethyl fixtures exercise real parsing, reference
and sample checks, filtering, both interpretation engines, report composition and
all exports. Tool invocation tests substitute sequencer/caller executables; they
do not validate real Dorado/Clair3 models or clinical sensitivity/specificity.
Before interpreting a personal run, compare a suitable truth/control dataset and
review read length, coverage, contamination/sample identity and call concordance.
Record equipment adaptations and measurements in OpenLab's run record.
