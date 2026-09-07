# DNA-Report

**One upload, all relevant analysis.** The product front door for the
GoodAncestor genomics family: you hand it a file, it figures out what the file
is, routes it to the right analysis engine(s), and returns one merged report
with every finding tagged by evidence tier.

## What it does

    upload  ->  detect type  ->  route to engine(s)  ->  merge  ->  one report

| You upload | Routes to |
|---|---|
| 23andMe raw data, VCF | [GeneAsk](https://github.com/GoodAncestor/GeneAsk) (variants) |
| methylation bedMethyl, Illumina beta-matrix / IDAT | [MethylAsk](https://github.com/GoodAncestor/MethylAsk) (methylation) |
| ONT modBAM or POD5 | Local Dorado/alignment as needed, then Clair3 variants and modkit methylation from the same BAM → GeneAsk + MethylAsk |

## Native Oxford Nanopore sequencing

[Setup, commands, outputs and validation scope](dnareport/docs/NANOPORE.md) describe
how a native run becomes one genome/methylation report. This path requires locally
installed tools, compatible models, a pinned GRCh38 reference, and private scratch
storage. It never downloads sequencing models during analysis.

Open `/demo/nanopore` for a deterministic synthetic example of linked variants and
native CpG measurements, with coverage, missingness and provenance. Its public
JSON/Markdown exports and tiny inputs need no API key. The [fixture guide](dnareport/demo_data/nanopore_README.md)
distinguishes invented measurements from frozen public evidence. No sequencer,
calling model, remote lookup or personal data is involved in opening the demo.

Raw BAM/POD5 web uploads default to disabled. Set `DNAREPORT_ONT_UPLOADS_ENABLED=1`
only after a configured sequencing worker consumes `dnareport:jobs:ont`; the
`/health` response reports `native_uploads_enabled`. The flag controls the hosted
upload routes, not CLI analysis or the demo. Prepared bedMethyl and VCF remain on
the ordinary queue. This prevents raw data uploads from waiting in an unattended queue.

The [OpenLab preparation protocol](https://github.com/GoodAncestor/open-dna-lab/pull/1)
includes sample records and a review register for practical equipment adaptations.

## Where it sits

DNA-Report orchestrates file preparation and the knowledge engines; it owns no
reference databases. Variant calling and methylation extraction use external tools,
interpretation lives in the engines below, and report rendering is shared with bio-core. The
dependency direction is acyclic:

    bio-core                          (mechanism)
       ^
    MethylAsk · GeneAsk               (knowledge engines)
       ^
    DNA-Report                        (product — this repo)

That is why this is a separate repo, not a bio-core feature: bio-core must not
depend on the engines that depend on it. bio-core stays pure mechanism;
DNA-Report is the product that stitches the engines together.

## Install

DNA-Report depends on three private `GoodAncestor` repos (bio-core, MethylAsk,
GeneAsk), pinned to coordinated commits in `pyproject.toml`. Update those pins
to adopt reviewed engine changes. A plain
`pip install .` resolves all three from GitHub, so the machine needs git access
to the private repos (SSH key or a token in the git credential helper).

    # A) let pip pull all engines from GitHub (needs private-repo git access)
    pip install .

    # B) develop against local checkouts instead
    for r in bio-core MethylAsk GeneAsk; do
      git clone https://github.com/GoodAncestor/$r.git ../$r && pip install -e ../$r
    done
    pip install -e . --no-deps

## Quick start

    dna-report detect sample.vcf
    dna-report analyze sample_beta.csv --out report.html
    dna-report analyze genome.vcf.gz --traits traits.csv --out report.html
    dna-report compare six_tests.merged.vcf --out compare.html  # reconcile several tests of one person

## Modules

- `dnareport.detect` — file-type detection + routing map (extension + content sniff)
- `dnareport.orchestrate` — run the routed engine(s), collect bio-core Findings, render one merged report
- `dnareport.cli` — `dna-report detect|analyze`

## Disclaimer

DNA-Report presents research associations with evidence tiers, not medical
diagnoses. The disclaimer text is owned and shown by each engine's report; this
front door does not add health claims of its own.
