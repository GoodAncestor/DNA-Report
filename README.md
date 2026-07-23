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
| ONT modBAM (combined genome + methylome) | **both** — [bio-core](https://github.com/GoodAncestor/bio-core) splits the file into a methylation stream and a variant stream, each engine takes its part |

## Where it sits

DNA-Report owns **no analysis and no databases** — it is orchestration only. All
the work is in the engines below it, and the report rendering is bio-core's. The
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
GeneAsk), declared as git-source dependencies in `pyproject.toml`. A plain
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
