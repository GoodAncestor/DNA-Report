# DNA-Report baselines and outcomes: Lane S report

Date: 2026-09-02  
Branch: `outcomes/S`

## Result

Lane S carries age and sex through every analysis path and labels estimates as guesses.

Methylation clocks now expose age acceleration and signed site contributions.

GWAS findings now produce offline population positions when at least three matching gnomAD frequencies exist.

The report result now groups findings by outcome and builds actions from the four approved evidence sources.

JSON, Markdown, and MCP now expose person baselines, trait scores, outcomes, and actions.

No mirror was rebuilt. No feature was enabled or deployed.

## Commits

### dna-report

- `ebaad6e` Carry age and sex through report requests
- `a39dd5b` Estimate age and sex with explicit guess labels
- `54df96b` Group findings by outcome and build cited actions
- `081e95c` Export person baselines outcomes and actions

### dna-report-deploy

- `78c482a` Pass age and sex from queued jobs into analysis

### MethylAsk

- `8cbe2ca` Calculate clock acceleration and signed site contributions

### GeneAsk

- `54b67c6` Calculate offline polygenic positions from GWAS and gnomAD
- `b97e6ee` Keep ClinGen interventions and outcomes in schema two

## Delivered behavior

### Person inputs and guesses

Multipart, inline JSON, queued jobs, and workers carry optional age and sex fields.

Invalid values are ignored and create the specified input note.

User entries carry source `user`. Estimates carry source `guess`.

Genome estimates use called X and Y records. Methylome estimates use the planned XIST and TSIX probe set.

Age estimates use the median age from valid clocks.

### Clock baselines

Valid clocks calculate acceleration as predicted age minus supplied age.

Each available clock site keeps its probe, coefficient, beta, predictor value, and signed contribution in years.

Horvath-type contributions use the local slope of the anti-transformation.

`top_contributions()` orders sites by absolute contribution while preserving each sign.

### Polygenic positions

GWAS odds ratios use log-odds weights. Beta findings use their reported beta.

Dosage follows carried-risk-allele and zygosity fields.

Population moments use offline gnomAD frequencies. The calculation does not call a live API.

Percentiles round to tens and stay within 10 through 90.

Every `TraitScore` carries the required ancestry caveat verbatim.

### Outcomes and actions

Conditions, medicines, traits, and epigenetic age become ordered `Outcome` objects.

The alias table merges matching EWAS and GWAS traits, including BMI and smoking.

Actions come only from ClinGen actionability, CPIC, ACMG, or the approved smoking marker.

The EWAS-only test returns no action.

ClinGen actionability schema two stores the intervention and outcome fields associated with the retained highest score.

Schema-one mirrors remain unavailable until rebuilt.

### Exports

JSON schema 2.1 adds `person`, `trait_scores`, `outcomes`, and `actions` at the top level.

Clock JSON adds acceleration and signed contributions.

Markdown format 2.1 adds the person line, cited actions, and the By outcome section.

Markdown does not label an age or sex as a user entry when its source is unknown.

Lower polygenic positions compare against the complementary population share.

MCP adds structured `get_outcomes` and `get_actions` tools.

## Test-first checks

Each task began with a failing focused test.

The final repository suites pass:

- dna-report: 310 passed, 2 skipped, 1 warning.
- dna-report-deploy: 16 passed, 1 skipped.
- MethylAsk: 75 passed.
- GeneAsk: 131 passed.

The task checkpoints were:

| Checkpoint | dna-report | deploy | MethylAsk | GeneAsk |
|---|---:|---:|---:|---:|
| Baseline | 285 passed, 1 skipped | 15 passed, 1 skipped | 70 passed | 126 passed |
| S1 | 289 passed, 1 skipped | 16 passed, 1 skipped | 70 passed | 126 passed |
| S2 | 295 passed, 2 skipped | 16 passed, 1 skipped | 70 passed | 126 passed |
| S3 | 295 passed, 2 skipped | 16 passed, 1 skipped | 75 passed | 126 passed |
| S4 | 295 passed, 2 skipped | 16 passed, 1 skipped | 75 passed | 130 passed |
| S5 | 305 passed, 2 skipped | 16 passed, 1 skipped | 75 passed | 131 passed |
| S6 | 310 passed, 2 skipped | 16 passed, 1 skipped | 75 passed | 131 passed |

One DNA-Report skip requires object-store configuration.

The second DNA-Report skip records the non-editable wheel-build limitation below.

The deploy skip requires a built worker image through `IN_WORKER_IMAGE`.

The warning reports Starlette's pending multipart import deprecation.

## Vendor verification

The official ClinGen actionability download host was unreachable from this sandbox.

The official actionability interface showed `Outcome` and `Intervention` columns.

The raw TSV's planned first-row `# docId` shape remains unverified against a downloaded file.

The parser detects a header containing the required fields instead of assuming a preamble length.

The test fixture follows the planned `# docId` header and includes `outcome` and `intervention`.

All ten planned sex-inference probes were absent from the bundled HM450, EPIC, and EPICv2 manifests.

The data file marks those probe identifiers as unverified and keeps `reviewed_by` empty.

## Package data check

The existing `dnareport` package-data rule includes `data/*.json`.

The non-editable wheel test invokes `pip wheel` against the git tree and inspects the resulting archive.

The current setuptools rejects the repository's existing `project.license` field before creating a wheel.

The test records that environment limitation as a skip.

## Plan adjustments

GWAS findings did not carry chromosome and position into `Finding.detail` on current main.

The GWAS adapter now preserves both fields so position-based gnomAD lookup can follow the planned interface.

MethylAsk's current trait-copy table contains 22 keys rather than the plan's stated 23.

The outcome alias file seeds all 22 current keys and the specified ontology aliases.

The worker still supports analyzers without the new keyword arguments during the cross-repository transition.

Automated review found no banned voice terms in the changed reader-facing strings.

## Lane F handoff

`ReportResult` now provides the interfaces in the plan without changes to Lane F files.

Lane F needs to pass `result.outcomes`, `result.actions`, and the person dictionary from `report.py` into the renderer.

Lane F can read acceleration and contributions directly from each clock result.

Outcome findings remain full `Finding` objects. Serialized outcomes expose their marker identifiers.

Each outcome owns matching actions through `Action.outcome_key`.

No changes were made to `landing.py`, `highlights.py`, `report.py`, or bio-core `render.py`.

## Open operations

- Rebuild the ClinGen mirror with `dna-report-refresh clingen` before the Phase B+C release gate.
- Verify the raw actionability TSV header during that refresh.
- Review the ten sex-inference probes against an authoritative manifest or published method.
- Re-run the non-editable wheel check when the packaging metadata builds with current setuptools.
- Complete the Lane F renderer and report composition wiring.
- Keep the feature disabled until the combined lanes pass their release gate.

The plan estimates 16 seconds for the ClinGen refresh. This lane did not measure it.

## Push status

At Task S6 close, `outcomes/S` was pushed for dna-report, dna-report-deploy, MethylAsk, and GeneAsk.
