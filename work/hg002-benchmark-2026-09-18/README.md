# HG002 staged benchmark: local evidence and reproducible harness

18 September 2026. Stage A preparation completed against the existing public ONT derived VCF. **Independent accuracy remains pending.** No native caller, raw-read transfer, GPU, model download, personal genome, annotation service or deployment was used.

## Measured preparation result

The complete publisher file was scanned: **5,661,980 records**. Its SHA256 matches the existing acquisition manifest. The harness reproduces the report's **4,454,522 retained records / 4,487,526 supported carried alleles**. A genotype comparison requires a narrower export: **4,454,479 complete supported genotypes**, with **43 mixed-scope genotypes** excluded explicitly. The report can annotate a supported allele in those mixed records; the benchmark must not silently replace its other oversized allele with reference or missing data.

Other mutually exclusive record exclusions: 168,028 non-autosomal, 1,039,236 insufficient/missing DP or GQ, and 194 wholly unsupported-allele records. Counts plus retained records reconcile to every input row. These are processing counts, not TP/FP/FN or accuracy.

The pinned [receipt](filter-receipt.json) records thresholds and hashes. Derived output stays outside Git at `~/.cache/goodancestor/hg002-benchmark-20260918/publisher.filtered.vcf.gz`, with its tabix index. The source remains in the existing public-data cache.

## Reproduce preparation

Use the project's test environment with pysam (tested 0.24.0). The output must not exist; choose a fresh destination rather than overwriting evidence.

```sh
python -m dnareport.benchmark filter \
  /path/to/SAMPLE.wf_snp.vcf.gz \
  /new/output/publisher.filtered.vcf.gz \
  --receipt /new/output/filter-receipt.json
python -m pytest -q tests/test_benchmark.py tests/test_nanopore.py
```

This export is the public-report filter policy at DP10/GQ20 and maximum allele length50, with the stated complete-genotype restriction. Native preparation defaults remain DP8/GQ20; harmonize or prespecify both as sensitivity runs, never compare them as identical filters.

## Independent variant comparison

`python -m dnareport.benchmark preflight plan.json` verifies file hashes, sample columns, autosomal region/reference bounds, merged confident-region denominators, named difficult-region strata and a declared acceptance purpose. `score plan.json /new/evidence-directory --happy /installed/bin/hap.py` then runs the raw and filtered VCFs against the same fixed truth/mask/regions, with an explicit xcmp haplotype-comparison engine. It retains commands, logs, annotated tool outputs, hashes, the immutable plan copy and failure status. Default total comparison timeout is one hour and two threads.

A naive position/allele join is deliberately not offered as an accuracy substitute. The summary keeps truth TP and query TP distinct; precision uses the query representation, recall the truth representation. Full hap.py extended tables retain subtype, genotype and requested regional strata. Missing/low query coverage is not subtracted from the confident evaluation denominator. Undefined rates remain null. A successful executable run still requires scientific review; it does not automatically establish an acceptance pass.

See `plan.template.json`. It intentionally fails preflight until all placeholder paths, hashes, version, reference-compatibility evidence and acceptance limits have been selected before examining benchmark results. The truth VCF/mask and reference must be compatible with the actual publisher reference, not merely share a GRCh38 label. Sample labels can differ between the publisher and truth material; declare both explicitly. Never substitute the publisher calls for independent truth.

The local environment has pysam but no installed hap.py, independent truth VCF/mask or matching full FASTA identified by this bounded check. No accuracy values are fabricated. NIST's v5.0q release directory was inspected on September18 and lists a GRCh38 small-variant VCF of approximately40MB plus its index/mask. That listing alone does not settle reference compatibility. Read the exact release guidance, acquire and pin compatible inputs/strata/reference, then provision a reviewed comparison runtime before executing this stage. Native read/model/GPU work remains a separate resource stage.

Primary method references inspected September18:
- [hap.py user manual](https://github.com/Illumina/hap.py/blob/master/doc/happy.md): haplotype comparison, confident regions, TP representations and stratification.
- [NIST HG002 v5.0q release directory](https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/giab/release/AshkenazimTrio/HG002_NA24385_son/v5.0q/): source selection lead, not a completed compatibility check.
- [GIAB stratifications](https://github.com/usnistgov/giab-stratifications): source for reference-specific difficult-region masks.

## Native evidence retention and bounded pilot

An explicit local `NanoporeConfig(validation_dir='/new/private/evidence')` now retains the complete preparation workspace, including the aligned BAM/index, raw caller VCF, filtered VCF, complete bedMethyl, tool logs, exact commands, provenance and file hashes. Failed tool runs retain whatever was produced, with failure status. The private run directory is unique and mode0700. This setting is not read from the environment or exposed through web input; normal workflows still remove scratch. Retention adds disk use and the operator must manage it. A supplied external VCF is copied into the retained workspace.

Before a native pilot: verify the exact CRAM decode reference, mod-tag availability, model/chemistry compatibility and local resources. Prespecify an interior scoring interval and read-selection padding; choose limits before results. Keep failed runs. No full-genome inference follows from a bounded region's successful result. Do not acquire the approximately99GB read file or provision caller models as an incidental test run.

## Methylation concordance

`methylation_concordance()` accepts matched, pre-pooled integer-count TSVs and reports missing sites, low coverage, depth-stratified bias and mean absolute error. It preserves measured zero separately from absence; valid-call counts include other modification states. Minimum coverage is at least5. Inputs must already share reference, forward-CpG/combined-strand policy and modification identity; this function does not establish those biological prerequisites or perform partition pooling. Tests use fictional counts. Publisher comparison would be processing concordance, not orthogonal accuracy, and no methylation accuracy run is claimed here.

## Acceptance boundaries

Software acceptance: complete input/exclusion accounting, immutable input checks, failure retention, real subprocess wiring under fictional fixtures, correct denominator arithmetic and unchanged ordinary cleanup. Scientific acceptance: pending independent truth/reference/runtime and reviewer-selected limits. Native and orthogonal methylation accuracy remain pending. No clinical or donor-health interpretation is supported by this cell-line software exercise.
