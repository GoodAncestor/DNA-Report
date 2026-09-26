# AI prediction exploration

The report shows model-specific coverage and side-by-side clinical, population,
protein and regulatory evidence. Prediction scores never promote a clinical tier.
A missing score is not a benign result. Model disagreement is preserved.

- `/demo/ai` serves three frozen public examples. Opening it needs no key, mirror,
  or network inference. JSON/Markdown exports include recorded provenance.
- `/explore` accepts one GRCh38 variant (`chrom-position-ref-alt`, 1-based).
  GET only prefills. POST `/api/variant` accepts `variant`, `reference_build`
  (`GRCh38` required), and Boolean `predict` (default false).
- Evidence lookup uses local ClinVar/AlphaMissense/gnomAD references. An explicit
  prediction request sends variant/sequence context to AlphaGenome when configured.
  No sample genotype is inferred. Returned reports are not persisted by this route;
  existing model-result caching stores variant predictions, not uploaded genomes.
- Live interactive inference runs in a child process with a 45-second wall-clock
  deadline. On timeout/error, local evidence is returned with explicit model status.
  The existing API pacing/cache is shared. The SDK's connection timeout is not an
  RPC deadline, which is why the interactive path needs a process boundary.

## Candidate selection on uploads

Existing finding enrichments remain. An additional bounded screen covers carried
VCF alleles missing from the original finding list, including ordinary ClinVar VUS.
It requires an explicitly declared GRCh38/hg38 reference, autosomal PASS fully
called diploid genotypes, DP >=10, GQ >=20 and explicit A/C/G/T alleles <=50 bases.
Reference alleles are not independently validated against a FASTA by this screen.
Consumer arrays and offline reference imports do not use the expanded screen.

Select the highest GQ, then DP, then variant ID; inspect at most
`DNAREPORT_AI_MAX_CANDIDATES` (default 50, clamped 1–200) against the available
local ClinVar snapshot. Preserve uncertain/conflicting records and exact-key
misses. Score uncertain matches first within that selection, then uncatalogued
candidates. Resolved classifications are not overwritten. Only new candidates
with actual model results become findings; coverage retains unscored counts.

`scan_stats.ai_predictions` exports per-model status, eligible/scored/skipped/error
counts and selection denominators. “Uncatalogued” means no exact match in this
local snapshot: it does not establish a biologically novel variant, a complete
ClinVar search, or normalized indel equivalence.

## Verified examples

`dnareport/demo_data/ai_predictions.json` contains a fresh AlphaGenome regulatory
result for public test substitution 13-32316419-C-G, plus AlphaMissense APOE C130R
and BRAF V600E results verified against the production GRCh38 mirror. The
substitution is not the CAG>C deletion used by the older bundled genome demo.
These separate examples are not a person's genome. ClinVar snapshot records and
an existing gnomAD API-cache frequency are preserved with retrieval provenance.

## AlphaGenome Atlas evaluation

The official API now exposes precomputed Atlas results. GeneAsk includes an
explicit opt-in `alphagenome_atlas.query_variant` adapter; it is not automatically
invoked by reports or this explorer. The September 26 release installed
AlphaGenome 0.9.0 with `alphagenome.atlas` in the app and workers. A bounded
production access check retrieved 22 scorer names and successfully queried the
public APOE example `19-44908684-T-C` using `AVI_SCORE` (raw score 0.49989903,
quantile 0.98698962). These are scorer outputs, not personal disease probabilities.
Atlas was enabled only inside that isolated verification process; service
configuration remains off. Explicit scorer configuration and an execution
deadline are still required before enabling a live path.
No silent fallback to fresh inference occurs. The explorer links to the official
Atlas portal for immediate access.

Sources checked September 25, 2026:
- https://github.com/google-deepmind/alphagenome
- https://deepmind.google/blog/alphagenome-atlas-a-predictive-map-of-every-possible-dna-letter-change-in-the-human-genome/
- GeneAsk `docs/ai-predictions.md` pins adapter semantics and limitations.

## Verify

```sh
DNAREPORT_API_KEY=test-key python -m pytest -q
curl -fsS https://dna.goodancestor.com/health
curl -fsS 'https://dna.goodancestor.com/demo/ai?format=json'
```

After a release, verify both app and worker dependency revisions. Precomputed
HG002 artifacts retain their original presentation until deliberately regenerated;
this release does not silently replace reference provenance or run new predictions
on the complete HG002 genome.
