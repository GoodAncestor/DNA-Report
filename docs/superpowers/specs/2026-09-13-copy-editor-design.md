# Copy editor for the meaning-layer tables — design

Date: 2026-09-13. Requirement from Colby, 2026-09-02 (`knowledge/master-followup-list.md:523`):
an admin editor for the meaning-layer copy tables as a standing feature on the main site, so
**Morgan and Fabiola** can edit reviewed wording and set `reviewed_by` / `reviewed_on` without a
git workflow. Spec only — nothing here is built, and implementation waits on approval.

Read alongside `docs/superpowers/specs/2026-09-02-dna-report-meaning-layer-design.md` (which
created the tables) and `HANDOFF-2026-09-02-dna-meaning-layer.md` in the hub.

## 1. Problem

The wording a lay reader sees comes from five places, and every one of them is a code change today:

| What | Where | Shape |
|---|---|---|
| How sure / next step, per classification class | `GeneAsk/geneask/data/copy/clinical_next_step.json` | 8 template rows keyed `class` + `platform` + `zygosity` |
| One sentence of gene function | `GeneAsk/geneask/data/copy/gene_function.json` | 157 rows keyed by gene symbol |
| Condition name, inheritance, link | `GeneAsk/geneask/data/copy/condition_phrasing.json` | 81 rows keyed by MedGen id or gene |
| ACMG SF notes | `GeneAsk/geneask/data/reference/acmg_sf_v3_2.json` | 81 published rows; only `note` is ours to write |
| Report vocabulary (16 terms) | `bio-core/biocore/report/terms.py` | a Python dict — **not data at all** |

Three consequences, all confirmed by reading the code:

1. **The reviewers cannot reach the text.** Editing it means a checkout, a JSON edit, a commit, a
   push, a GitHub Actions image build, `docker pull` + `systemctl restart dna-report-worker` on
   alien02 and alien03, and a Coolify deploy of the app (~2 min edge downtime). `terms.py` is worse:
   it is source.
2. **`reviewed_by` is never set, so every report carries the unreviewed notice.** All three tables
   ship `"reviewed_by": []`, and `render.py:1646` prints *"The plain-language wording on this report
   has not yet been reviewed by a person"* when **any** finding's interpretation has an empty
   `reviewed_by`.
3. **The checks that define the house voice live only in tests.** `GeneAsk/tests/test_copy_tables.py`
   holds a banned-construction regex plus the ≤25-word rule; `bio-core/tests/test_terms.py` holds a
   *different* regex and a ≤40-word rule. Nothing a save path could call.

## 2. What the reviewers do, concretely

One flow, two people, no engineer:

1. Open `https://dna.goodancestor.com/admin/copy`. Cloudflare Access asks for their Google
   Workspace identity; the page opens already knowing who they are.
2. Pick a table from the rail. Read the rows that matter — filters for *changed*, *unreviewed*,
   *has problems*.
3. Edit a sentence in place. The word count and the voice verdict update as they type. **Save draft
   is always allowed**, even with problems outstanding: a reviewer mid-sentence must never lose work.
4. Press **I reviewed this table**. That stamps `reviewed_by` with their Access identity and
   `reviewed_on` with today's date. There is no name field to type into.
5. Press **Publish**. They see a before/after diff of every changed field, who changed it, and the
   validator verdict. Publish is refused while any error stands (warnings never block).
6. The site shows the new wording within a minute; the next job on each worker picks it up. No
   deploy, no rebuild, no merge.

Non-goals: no workflow states (draft → approved → published as separate roles), no per-row
sign-off, no email notifications, no rich text, no new wording *authored* by the editor (the tables
exist; this edits them).

## 3. Decisions

| Decision | Ruling | Why |
|---|---|---|
| Delivery mechanism | **Versioned artefact in R2** (see §6), with a PR raised off-plane for provenance | Seconds, not a release; no repo-write credential on the internet-facing box |
| Source of truth at runtime | The published bundle, when one exists; package data otherwise | One precedence rule, and the site works with no bundle at all |
| Editable surface | Declared per field in a manifest checked into code | `acmg_sf_v3_2.json` gene/condition/inheritance are *published facts*; only `note` is ours |
| `terms.py` | Becomes `biocore/report/data/terms.json`, loaded by the same code path | A reviewer cannot edit Python, and should not have to |
| Validation | One validator module, three callers: the tests, the save path, the publish gate | The two regexes in tests today are already drifting |
| Gating | Path-scoped Cloudflare Access app on `/admin/copy*`, reusable **`Allow GA staff`** policy | `knowledge/infrastructure/audience-gating.md`: a policy naming an existing group, never a list |
| Reviewer identity | The verified `Cf-Access-Jwt-Assertion` email claim | An identity you can type is not an audit trail |
| Blast radius | The pointer object is one atomic flip, and `copy publish --bundle <old id>` re-flips it | A bad sentence is site-wide the moment it lands |

## 4. Data model

### 4.1 Every table normalises to one shape

```json
{"version": "…", "reviewed_by": ["morgan@goodancestor.com"], "reviewed_on": "2026-09-13",
 "note": "…", "rows": {"<key>": {"<field>": "<value>"}}}
```

The three copy tables already look like this. `acmg_sf_v3_2.json` already carries the header keys.
`terms.py` becomes `terms.json` with rows `{key: {label, sentence}}`, and `biocore.report.terms`
keeps exporting `TERMS` from it so `terms_html()` / `term_link()` and their tests are untouched.

### 4.2 The manifest (code, not editable)

`biocore/copy/registry.py` declares, per table: the package and path it ships in, what a row key
means, which fields a reviewer may edit, each field's kind, the checks that apply, whether rows may
be added or removed, and any coverage requirement:

```python
TABLE = Table(
    name="gene_function",
    package="geneask", path="data/copy/gene_function.json",
    key="gene symbol, uppercase",
    fields={"sentence": Field(kind="sentence", max_words=25, required=True),
            "source":   Field(kind="label", editable=False),
            "url":      Field(kind="url", hosts=("www.ncbi.nlm.nih.gov",))},
    rows_addable=True, rows_removable=False,
    coverage="clinvar_panel_157 ∪ acmg_sf",   # a missing gene is an error, not a warning
)
```

The editor's UI is **generated from the manifest**. Adding the sixth table later is a manifest
entry, not new screens. Field kinds: `sentence` (prose, word-capped), `template` (prose with
`{placeholders}`), `url`, `label`, `enum`.

Word caps differ deliberately and are recorded per field: **25** for the copy tables (the rule in
`test_copy_tables.py`), **40** for terms (the rule in `test_terms.py`). Tightening terms to 25 would
fail on text already shipped, so the manifest keeps them apart rather than silently converging them.

### 4.3 The bundle — the unit of delivery

```json
{"bundle_version": 1,
 "id": "2026-09-13T18-02-11Z-7f3a1c",
 "created_by": "fabiola@…", "created_at": "2026-09-13T18:02:11Z",
 "base": {"geneask": "<git sha>", "biocore": "<git sha>"},
 "tables": {"clinical_next_step": {…whole table…}, "gene_function": {…}, "condition_phrasing": {…},
            "acmg_sf_v3_2": {…}, "terms": {…}},
 "audit": [ …events since the previous bundle… ],
 "checks": {"validator_version": "1", "errors": 0, "warnings": 3, "ran_at": "…"}}
```

Whole tables, never patches: a bundle is self-sufficient, so a worker that missed ten bundles is
still correct after one fetch. `base` records the package shas the edits were derived from, which is
how drift is detected (§6).

Storage — R2, the one substrate the edge, the app and every worker already touch
(`dna-report-deploy/workers/worker.py:427`):

- bucket **`dna-report-copy`**, new, **no lifecycle expiry rule** (unlike `dna-report-results`)
- `bundles/<id>.json` — immutable, write-once
- `current.json` — `{"id": …, "sha256": …, "key": "bundles/<id>.json"}`, the only mutable object,
  and the only thing a publish or a rollback changes

### 4.4 The editor store

SQLite at `/var/lib/dna-report/copy/editor.db` on a named Coolify volume. Three tables:

- `draft(table_name, key, field, value, base_rev, updated_by, updated_at)` — the working copy. One
  row per edited field; unedited fields are absent.
- `event(id, at, who, kind, table_name, key, field, before, after, bundle_id, detail)` —
  append-only. Kinds: `edit`, `revert`, `review_set`, `publish`, `rollback`, `publish_blocked`,
  `fetch_failed`. `before` / `after` verbatim, never summarised.
- `bundle(id, at, who, sha256, r2_key, status)`.

The box is not the archive: every publish embeds the events since the previous bundle inside the
bundle itself, so the whole trail is reconstructible from R2 alone, and the mirror PR body lists
them again.

## 5. Loading, precedence and hot reload

`biocore/copy/tables.py` is the single reader:

```python
table(name) -> dict          # header + rows, bundle first, package data otherwise
use(path) -> None            # point at a fetched bundle; invalidates every cache
active_version() -> str      # bundle id, or "pkg:<version>" when no bundle is loaded
```

Precedence: a loaded bundle wins; otherwise the file inside the installed package. **No bundle, or
an unreachable R2, must never fail a report** — it degrades to package data and logs once.

`GeneAsk/geneask/interpret/copy.py` and `lists.py` stop calling `json.loads` on a path and read
through `table()`. Both currently memoise with `functools.lru_cache` (`copy.py:_load`,
`lists.py:_acmg`), so a bundle cannot take effect without explicit invalidation — `use()` clears
them, and `dnareport/web.py`'s `_DEMO_HTML_CACHE` (`web.py:783`) is dropped in the same call, or the
demo pages keep serving pre-edit HTML.

Dependency direction stays acyclic: bio-core owns the loader, manifest and validator and knows
nothing about R2; GeneAsk registers its tables; dna-report does the fetching, the UI and the gate.

Who refreshes, and when:

- **app** — on a 60 s TTL, checked lazily when a page or report renders; a conditional GET on
  `current.json` (ETag) so the steady state is one cheap request a minute.
- **worker** — once at start, then before each leased job. A job already costs seconds of compute;
  one conditional GET is free, and it means a publish reaches the queue within one job.
- Both record the active bundle id in report provenance, so a rendered report says which wording it
  used. `Interpretation.copy_version` already carries this into the card.

## 6. How a saved change reaches the workers — artefact, with a PR behind it

**Recommendation: the versioned artefact is the delivery path; the PR is an archival mirror raised
from the jumpbox, not something anyone waits on.**

Why not the PR as the delivery path:

1. **It does not deliver.** A merged PR changes nothing live until an Actions image build, a
   `docker pull` + `systemctl restart dna-report-worker` on both aliens, and a Coolify deploy with
   ~2 min of edge downtime. A copy typo would ride a release train.
2. **It needs a merge**, which needs an engineer — the exact person this feature exists to remove
   from the loop.
3. **It needs a repo-write credential on the internet-facing box.** That is a new mint against the
   credential store, held by the most exposed process we run, to change wording. The artefact path
   needs only R2 creds, scoped to one new bucket.
4. **It couples wording to code review.** Two reviewers correcting a sentence should not open a
   branch on a public repo.

Why the artefact works here: R2 is already the shared substrate (app, edge, every worker), the
delivery is one atomic pointer flip, rollback is the same flip backwards, and the bundle is
self-describing and verified by sha256 before it is used.

What keeps git honest — the **mirror job**, on the jumpbox (already the deploy plane, already holding
SSH keys per the gh-SSH-auth policy; a systemd timer unit ships with the work, disarmed):

1. Fetch the newest bundle; skip if its id is already mirrored.
2. Write each table back to its file in `GeneAsk` / `bio-core` checkouts.
3. Run both suites. Green only.
4. Push a branch and open a PR titled with the bundle id, body = the audit events.

Merging keeps package data equal to the published bundle, so a rebuilt image is never a regression.
If nobody merges for a month, the site is still correct — that is the point of the split.

**Drift rule.** A bundle records `base` shas. If a table's package data has changed since (a merged
mirror PR, or an engineer's edit), the editor shows the row-level differences and requires a reviewer
to re-accept them before the next publish. Because a publish always writes whole tables, the
accepted result is unambiguous.

## 7. Validation on save

`biocore/copy/check.py`:

```python
check_table(name, table) -> list[Problem]     # Problem(severity, row, field, message)
check_field(name, key, field, value) -> list[Problem]      # the live, per-keystroke path
```

Checks, all derived from rules that already exist somewhere in the tree:

- **Shape** — header keys present, `rows` a dict, no unknown fields, no non-string values where
  prose is expected.
- **≤25 words per sentence** — split on `(?<=[.!?])\s+`, count per sentence, cap from the manifest
  (40 for terms). This is the rule in `test_copy_tables.py` today.
- **Banned constructions** — the union of the two regexes in `test_copy_tables.py` and
  `test_terms.py` (`honest`, `it's not `, `in other words`, `crucially`, `importantly,`, `notably,`,
  `by design`, `you have`, `you will`, …), so there is one list instead of two.
- **Forbidden clinical phrasing** — reuses `dnareport/explain.py`'s `_FORBIDDEN` (diagnosis claims,
  second-person prediction) applied to human-written copy as well as model drafts.
- **Templates resolve** — placeholders ⊆ the set the calling code fills (`{stars}`,
  `{platform_words}` for `clinical_next_step`), and a smoke `.format()` must not raise. A typo like
  `{star}` currently becomes a `KeyError` at report time.
- **No markup** — no `<`, `>` or HTML entities. Copy is escaped at render (`render.py:690`), so this
  is about text that would read as literal tags, not about injection.
- **URLs** — https only, host on the manifest's allowlist. `url` values are not validated anywhere
  today and land in an `href`.
- **Enums** — e.g. `inheritance ∈ {dominant, recessive, x-linked, null}`.
- **Coverage** — `gene_function` must keep a row for every gene in `clinvar_panel_157 ∪ acmg_sf`;
  deleting one is an error (it breaks a live report and CI).
- **Header** — `reviewed_by` a list of identities, `reviewed_on` an ISO date.

Severity contract: **errors block publish and never block save.** Warnings never block anything.
The same module runs in CI — `test_copy_tables.py` and `test_terms.py` import it instead of holding
their own regexes — so a new rule fails the suite with the text that violates it, and lands
together with the fix rather than ambushing a reviewer at the save button.

## 8. Access gating

`dna.goodancestor.com` is public by design (demos, uploads, `/analyze`). Access apps are
path-scoped, so:

- One Access application on `dna.goodancestor.com/admin/copy` and `/admin/copy/*`.
- One policy: the **reusable `Allow GA staff`** policy — Google Workspace identity, 24 h session,
  the same object `grafana.goodancestor.com` and the rest of the estate use. **No new group, no new
  sync, and no email list in the policy.** If a reviewer is not on Workspace, the fix is a Workspace
  identity (or a Workspace group the staff policy already derives from) — not a list.
- Everything else on the host is untouched and stays public.
- Add the row to `knowledge/infrastructure/web-properties.md` naming the app and the policy.
- **Verify post-auth.** An Access-gated path 302s to login for any unauthenticated request, even
  against a dead origin, so an edge `curl` proves only that Access is on.

Origin-side, `/admin/copy*` verifies `Cf-Access-Jwt-Assertion` against
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` with `aud` = the application's AUD tag,
and refuses every request without a valid assertion — even though the origin is reachable only
through the cloudflared tunnel. It fails closed, and it stays correct if the tunnel ingress is ever
widened. The email claim from that JWT is the only identity the editor records.

The existing `DNAREPORT_API_KEY` mechanism grants nothing here: the `admin`-labelled key that
unlocks the CLI explain backends (`web.py:591`) is not an editor credential, and no query-parameter
path into `/admin/copy*` exists.

## 9. Audit trail

Every edit, revert, review stamp, publish, blocked publish and rollback becomes an `event` row with
the verified identity, the timestamp, and the exact before/after text (§4.4). The trail surfaces in
three places: the **table history** panel in the editor, the **audit block inside every published
bundle**, and the **mirror PR body**. Reports carry the bundle id they rendered with, so a printed
report can be traced to the wording, the reviewer and the date that produced it.

## 10. Page shape

Server-rendered HTML in the app's existing style, plus a little JavaScript for word counts and the
live verdict. No SPA framework, no build step. Rail of tables with dirty/problem counts; one row per
card showing current text, an editable field, word count, verdict, and the source link; a
reader-preview for template rows (filled with `{stars}` = 2 and each platform); filters for
*changed*, *unreviewed*, *has problems*; a footer bar with **Save draft**, **I reviewed this table**,
and **Publish** (disabled with a reason while errors stand). Publish opens a full before/after diff
first. Usable on a phone; this spec does not attempt the visual design.

## 11. Observations from the code read

Raw observations, not conclusions:

- `render.py:1646` prints the report-wide unreviewed notice when **any** interpretation has an empty
  `reviewed_by`. GWAS, CPIC, EWAS and TCGA interpretations are composed inline with
  `reviewed_by=[]` hard-coded (`GeneAsk/geneask/interpret/meaning.py:207,259`;
  `MethylAsk/methylask/meaning.py:67,81`). **Setting `reviewed_by` in all five tables will not clear
  the notice on a typical report.** M3 makes the notice per-source.
- `copy.py:_load` and `lists.py:_acmg` are `lru_cache`d at import, so no bundle can take effect
  without an explicit invalidation hook.
- Copy is HTML-escaped where the four parts are rendered (`render.py:690`); `url` fields are not
  checked at all today.
- `test_copy_tables.py::test_gene_table_covers_the_panel_and_the_acmg_list` asserts a `gene_function`
  row for every panel and ACMG SF gene — so row deletion is a CI failure as well as a product bug.
- Any new data file under a package needs a `[tool.setuptools.package-data]` entry, or every
  non-editable install (CI, both images) ships without it. This cost 15 minutes of broken worker on
  2026-09-02; `terms.json` walks straight into it.

## 12. Failure modes and what absorbs them

| Failure | Behaviour |
|---|---|
| R2 unreachable / no bundle | Package data serves; one log line; reports never 500 |
| Bundle sha mismatch | Refused, previous tables stay loaded, `fetch_failed` event |
| Bad wording published | `dnareport copy publish --bundle <previous id>` re-flips the pointer; live in ≤60 s |
| Two reviewers on one field | `base_rev` mismatch → 409 with the other value shown; no silent overwrite |
| Package data moved under a draft | Drift view, re-accept required before publish |
| Access misconfigured | JWT verification fails closed; the editor is unreachable, the public site unaffected |
| Coolify volume lost | Drafts lost; every published bundle and its audit survive in R2 |

## 13. Build plan — three milestones

Executors: use `superpowers:subagent-driven-development` or `superpowers:executing-plans`, TDD, a
commit per task. Global constraints: Python 3.12; dependency direction bio-core ← GeneAsk ←
dna-report; every new package data file gets a `[tool.setuptools.package-data]` entry; no Anthropic
or Google API key anywhere; no credential in chat; commit by explicit path.

### M1 — the tables become data, behind one validator (no web surface)

1. **`terms.py` → `biocore/report/data/terms.json`.** Test first: `TERMS` equals the pre-move dict
   verbatim (assert on a fixture copy of all 16 entries), `terms_html(["het","stars","nope","het"])`
   is unchanged, and the file is present in a non-editable install (`pip install .` into a temp venv,
   then import). Add the package-data entry in the same commit.
2. **`biocore/copy/registry.py`** — the manifest for all five tables. Tests: every declared path
   exists in its installed package; every editable field has a kind and a cap; `acmg_sf_v3_2`
   exposes only `note`.
3. **`biocore/copy/tables.py`** — `table()`, `use()`, `active_version()`. Tests: package data by
   default; `use()` on a temp bundle switches values and clears the caches; `active_version()`
   reports `pkg:<version>` and then the bundle id; a malformed bundle raises before anything is
   swapped in.
4. **Rewire readers.** `GeneAsk/geneask/interpret/copy.py` and `lists.py` read through `table()`.
   Tests: the whole existing GeneAsk suite stays green unchanged; `next_step`, `gene_function`,
   `condition_phrase` and `acmg_sf` all follow a `use()` switch.
5. **`biocore/copy/check.py`** — `check_table`, `check_field`. Tests, one per rule: a 26-word
   sentence errors and a 25-word one does not; `"Importantly, it's not X"` errors; `{star}` errors
   and `{stars}` does not; `http://` and an off-allowlist host error; deleting `BRCA2` from
   `gene_function` errors on coverage; `<b>` errors; `inheritance: "maybe"` errors; a clean table
   returns `[]`.
6. **CI adopts the validator.** `test_copy_tables.py` and `test_terms.py` import `check_table` and
   drop their local regexes. Test: the live tables pass with zero errors (this is the regression
   guard for every future rule).

**Exit:** `pytest` green in bio-core, GeneAsk and dna-report; the rendered demos are byte-identical
to before; no site behaviour changed.

### M2 — bundle, distribution and rollback (still no UI)

1. **`dnareport/copy_bundle.py`** — `build(tables, drafts, who) -> dict`, `publish(bundle) -> id`
   (put `bundles/<id>.json`, then flip `current.json`), `refresh(force=False) -> str|None`
   (conditional GET, sha256 verify, atomic write under `/var/lib/dna-report/copy/`, then `use()`).
   Tests with a faked S3 client: publish writes the immutable object before the pointer; refresh
   returns the id on change and `None` on a 304; a corrupted body is refused and the previously
   loaded tables stay active; a missing pointer falls back to package data and logs once.
2. **App wiring.** 60 s TTL refresh on render; `_DEMO_HTML_CACHE` dropped when the id changes; the
   active bundle id in `/health`. Tests: a pointer flip changes a demo's HTML after the TTL and not
   before; `/health` names the id.
3. **Worker wiring.** `refresh()` at start and before each lease; the id recorded in the report's
   provenance. Tests against the worker's existing harness: a flip between two leases changes the
   second report's wording; provenance carries the id.
4. **CLI** `dnareport copy show|verify|publish [--bundle <id>]` — the publish and the rollback path,
   exercisable with no UI. Tests: `publish --bundle <old id>` re-flips the pointer and writes a
   `rollback` event; `verify` fails on a tampered bundle.
5. **Bucket** `dna-report-copy` created with **no lifecycle rule**, creds scoped to it, documented in
   `dna-report-deploy/cloudflare/`.

**Exit:** a sentence changed via the CLI appears on the live site inside a minute and on the workers
within one job, with no image build and no deploy; the rollback is demonstrated on the live pointer.

### M3 — the editor, the gate, the audit and the mirror

1. **Store + audit.** `dnareport/copy_store.py` over the SQLite schema in §4.4 on the mounted volume.
   Tests: an edit writes exact before/after; a second write with a stale `base_rev` raises a conflict;
   `events_since(bundle_id)` returns them in order.
2. **JWT gate.** `dnareport/access.py` verifying `Cf-Access-Jwt-Assertion` (cached JWKS, `aud`
   checked, clock skew bounded). Tests: no header → 403; wrong `aud` → 403; expired → 403; a valid
   assertion yields the email claim; an `X-API-Key` on `/admin/copy` → 403.
3. **Routes + page.** `GET /admin/copy`, `GET /admin/copy/{table}`, `POST …/field`,
   `POST …/review`, `GET …/diff`, `POST …/publish`. Manifest-driven rendering. Tests: publish is
   refused (409, listing problems) while an error stands and a `publish_blocked` event is written;
   a clean publish writes the bundle, flips the pointer and records `publish`; the review stamp uses
   the JWT identity and ignores any submitted name; a concurrent edit surfaces the other value.
4. **Per-source review notice.** `render.py` counts only table-sourced interpretations toward the
   unreviewed notice, and inline-sourced ones get their own line. Tests: with all five tables
   reviewed the report-wide notice is gone while an inline-sourced row still discloses; no finding
   silently claims review it does not have.
5. **Access application** on `/admin/copy*` with the reusable `Allow GA staff` policy; post-auth
   verification recorded; `web-properties.md` row added.
6. **Mirror job** in `dna-report-deploy/jumpbox/` — script plus a systemd timer unit, shipped
   disarmed. Tests: on a bundle whose tables match the checkout it opens nothing; on a changed one it
   writes the files, runs the suites and produces the branch and PR body (dry-run asserted).

**Exit:** Morgan or Fabiola signs in, edits a sentence, stamps the review, publishes, and sees the new
wording plus their name on `/demo/genome` — with a PR open for the archive and the whole sequence in
the audit trail.

## 14. Out of scope

The inline sentences in `meaning.py` / `methylask/meaning.py` become a sixth table later (M3 only
makes their unreviewed state honest). No approval workflow, no email notification, no scheduled
publish, no translation, no editing of mirror data or thresholds, and no second Access group.
