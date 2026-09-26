# AI prediction exploration

The report shows model-specific coverage and side-by-side clinical, population,
protein and regulatory evidence. Prediction scores never promote a clinical tier.
A missing score is not a benign result. Model disagreement is preserved.

- `/demo/ai` serves three frozen public examples. Opening it needs no key, mirror,
  or network inference. JSON/Markdown exports include recorded provenance.
- `/explore` accepts one GRCh38 variant (`chrom-position-ref-alt`, 1-based).
  GET only prefills. POST `/api/variant` accepts `variant`, `reference_build`
  (`GRCh38` required), Boolean `predict`, and Boolean `atlas` (both default false).
- Evidence lookup uses local ClinVar/AlphaMissense/gnomAD references and local AVI /
  cached Atlas results. `atlas=true` requests precomputed AVI and feature contributions
  from Atlas; it does not request fresh sequence inference. An explicit
  prediction request sends variant/sequence context to AlphaGenome when configured.
  No sample genotype is inferred. Returned reports are not persisted by this route;
  existing model-result caching stores variant predictions, not uploaded genomes.
- Live interactive inference runs in a child process with a 45-second wall-clock
  deadline, including termination of nested model processes. On timeout/error, local
  evidence is returned with explicit model status.
  The existing API pacing/cache is shared. The SDK's connection timeout is not an
  RPC deadline, which is why the interactive path needs a process boundary.

## Candidate selection on uploads

Existing finding enrichments remain. An additional bounded screen covers carried
VCF alleles missing from the original finding list, including ordinary ClinVar VUS.
It requires an explicitly declared GRCh38/hg38 reference, autosomal PASS fully
called diploid genotypes, DP >=10, GQ >=20 and explicit A/C/G/T alleles <=50 bases.
Reference alleles are not independently validated against a FASTA by this screen.
Consumer arrays and offline reference imports do not use the expanded screen.

When a readable local AVI mirror is installed, rank quality-qualified SNVs by AVI
impact descending (unscored candidates follow scored), then GQ, DP and variant ID.
Without the mirror, use GQ, DP and variant ID. The selection coverage records the
local mirror status and counts, including partial failures. This step reads only
local data, never genome-wide API requests. Inspect at most
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
and BRAF V600E results verified against the production GRCh38 mirror. APOE also
includes a verified Atlas AVI score. The
substitution is not the CAG>C deletion used by the older bundled genome demo.
These separate examples are not a person's genome. ClinVar snapshot records and
an existing gnomAD API-cache frequency are preserved with retrieval provenance.

## AlphaGenome Atlas

Reports automatically enrich declared-GRCh38 SNVs with Atlas when configured.
Consumer-array panel matches use their panel-anchored GRCh38 coordinates. Atlas
scores and coverage are separate from fresh AlphaGenome sequence inference.
The explorer offers separate evidence, Atlas and fresh-inference actions.
All three result formats preserve model-specific provenance. No clinical tier
is changed by AVI ranking or feature contributions.

GeneAsk defaults to `AVI_SCORE` and `AVI_SCORE_FEATURE_IMPORTANCE`. It uses a
persistent, separately versioned SQLite cache, a 20-second child-process request
timeout, a 25-second report request budget and at most 10 remote variants. Local
scores/cache hits do not consume the remote allowance. Local AVI evidence is
retained if explanatory API results fail, with an explicit partial status.
Set `ALPHAGENOME_ATLAS_ENABLED=1` and server-side `ALPHA_GENOME_KEY` for live access;
set `ALPHAGENOME_ATLAS_CACHE_DB` to a persistent writable path. See GeneAsk's
`docs/ai-predictions.md` for all controls and cache version semantics.

### Local AVI installation

The official download catalog lists the AVI Tabix ZIP as 88.5 GB. It separately
lists splicing scores (20.6 GB) and AVI feature contributions (283.9 GB). The paper
makes static AVI permissive for commercial/non-commercial use; the explanatory
API and the other downloads have non-commercial terms. Raw Atlas features are
API-only. Keep downloads and extracted indexes on the destination server.

`dna-report-deploy` contains the staged AVI installer. Inspect the real ZIP member
names and columns before publishing it; do not infer the format from the filename.
Set `ALPHAGENOME_ATLAS_AVI_FILE` to the published BGZF or chromosome-shard directory
with its `.tbi`/`.csi` indexes. Unknown column layouts fail explicitly; a verified
layout can be supplied as `ALPHAGENOME_ATLAS_AVI_COLUMNS`.

On 2026-09-26, bounded HEAD/range-GET probes of the catalog's official AVI ZIP URL
returned HTTP 500 from studio8t, alien02 and Helsinki. Therefore the release must
not claim the full local AVI mirror is populated until a successful installation
and exact-allele lookup are recorded. The API/cache path is independent of this
bulk-download failure.

Sources:
- https://alphagenome.google/downloads
- https://www.alphagenomedocs.com/api/atlas.html
- https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphagenome-atlas-a-predictive-map-of-every-possible-dna-letter-change-in-the-human-genome/alphagenome-atlas.pdf


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
