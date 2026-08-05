# DNA-Report JSON API

Base URL: `https://dna.goodancestor.com`

The HTML surface (the upload page and the rendered reports) needs no
credentials. The JSON surface does, and it is off entirely on any instance where
no key has been configured — a request to it then returns `503`, not `401`, so
"disabled" and "wrong key" never look the same.

## Authentication

Send the key as a header:

```
X-API-Key: <your key>
```

or as a query parameter, `?api_key=<your key>`, where a header is inconvenient
(the Swagger UI uses this form).

Ask for JSON with either `Accept: application/json` or `?format=json`. Both
select the same output; `format` is a **query parameter**, not a form field, which
is the usual first thing to get wrong on the multipart route.

| Response | Meaning |
|---|---|
| `503` | No key is configured on this instance. The JSON API is switched off. |
| `401` | The key was missing or is not one we issued. |
| `429` | Your key's rate budget is spent. Back off and retry. |

Keys are per-consumer. Rate budget and usage counts are tracked per key, so one
consumer running hot cannot spend another's allowance.

## Analysing a file

```bash
curl -sS https://dna.goodancestor.com/analyze \
  -H "X-API-Key: $DNA_REPORT_KEY" \
  -H "Accept: application/json" \
  -F "file=@genome.txt"
```

Multipart fields: `file` (required), `tissue` (optional; only meaningful for
methylation input, and inferred from the file when omitted).

Accepted: 23andMe / AncestryDNA / MyHeritage / FTDNA genotype exports, VCF,
bedMethyl, methylation beta-value tables. Up to 180 MB.

**Compressed uploads do not work on this route.** A `.zip` or `.gz` in a
multipart body is rejected at the CDN edge with a `403` block page before it ever
reaches us — the edge refuses high-entropy bodies it cannot parse, and a
compressed genome is exactly that. Decompress before sending, or use the
presigned-upload route below.

### Compressed and large files

Three steps, no key required on any of them:

1. `POST /upload/sign` with `{"filename": "...", "size": <bytes>}` → returns
   `{"key", "url", "expires_in"}`. The `url` is a presigned PUT valid 15 minutes.
2. `PUT` the raw file bytes to that URL. This goes straight to object storage and
   does not cross the CDN, which is what makes a compressed upload possible.
3. `POST /analyze/r2` with `{"key": "<the key from step 1>"}` → the report.

Over 64 MB, step 1 answers `413` and you need the multipart flow
(`/upload/multipart/create`, `/sign`, `/complete`), which returns a `job_id`
instead of a report. Parts are PUT to presigned URLs on
`*.r2.cloudflarestorage.com`, so compressed content is fine at any size — it
never crosses the CDN edge that refuses it. Verified with an 82.9 MB `.vcf.gz`.

Poll `GET /result/{job_id}`: `202` while it is still running, `200` with the
report when it is done. The job id is the only credential on that URL — anyone
holding it can read the report.

Two other answers are possible and both are final, so stop polling on either:
`500` when the job failed outright (the body names the recorded error) and `504`
when it has gone so long without producing a report that it is treated as never
coming. Neither is a transient state to retry through.

### Getting the report as data

The same claim link serves three formats, all written by the worker in one pass
from one analysis — so the JSON and the page can never disagree about a genome:

| Request | You get |
|---|---|
| `GET /result/{job_id}` | the HTML report |
| `GET /result/{job_id}?format=json` | the structured report |
| `GET /result/{job_id}?format=md` | Markdown |

`Accept: application/json` works too, though an explicit `?format=` wins over it.
**No API key is needed** — the job id is the capability, exactly as it is for the
page, so a report's owner can read it with their own tools.

This is the endpoint to build an agent against. The synchronous JSON on
`/analyze?format=json` still exists and is unchanged, but it runs the analysis
while your connection waits and is therefore bounded by the edge's ~100s ceiling;
a whole genome will not finish inside it.

**Honour the `Retry-After` on the 202 and do not poll faster than it says.** The
edge's managed security rules treat rapid repeat requests from one address as
abuse and answer with a challenge, which a browser can solve and your client
cannot; it will surface as a `403` that clears on its own a few minutes later.
Polling every 10 seconds was enough to trigger it in testing.

## For agents: MCP

There is an MCP server at **`https://dna.goodancestor.com/mcp/`** — note the
trailing slash; `/mcp` answers 307 to it, which a following client handles and a
non-following one does not. Protocol revision `2026-07-28` (stateless), and no
authentication: the report id is the capability, exactly as it is for the page.

| Tool | What it does |
|---|---|
| `get_report_summary(report_id)` | Counts by tier and topic, databases consulted, the strongest findings, and whether the report is complete. Call this first. |
| `get_findings(report_id, tier, gene, topic, limit, offset)` | Query the findings. Ordered strongest-evidence-first, paged at 50. |

`report_id` is the 32-hex string from a claim link (`/result/<report_id>`).

**Every findings response carries `bounded` and `limits`.** When `bounded` is
true the report itself is a truncated view — a consumer array commonly yields
hundreds of thousands of GWAS associations and the report keeps the strongest
1,000 — and no amount of paging reaches what was left out. Say so rather than
presenting the page you have as the whole set.

A report that is not ready answers with a status rather than an empty result:
`working` (with `retry_after_seconds`), `failed`, `overdue` or `not_found`. Only
`working` is worth retrying.

## Two client gotchas

Python's **stdlib `urllib` is blocked at the edge** — its default User-Agent
matches a browser-integrity signature and every request returns `403` with
Cloudflare error `1010`, before it reaches us. `requests`, `httpx` and `curl` are
all fine. If you must use `urllib`, set an explicit `User-Agent`.

Sustained request rates are capped both at the edge and in the app. The app
answers `429` with your key named in its own counters; the edge answers `403`.

## Response

`200` with a versioned object. `schema_version` is currently `1.2`; additions are
made without a major bump, so read fields by name rather than assuming a shape.
Findings carry `marker`, `description`, `tier`, `magnitude` (0-10),
`direction` where the source asserts one, and resolved `links`.

**Read `scan_stats` and `notes` before you trust a count.** A report can be
bounded — a consumer array commonly yields hundreds of thousands of GWAS
associations and the report carries the strongest 1,000 — and those two fields
are where that is stated. A consumer that ignores them will report a truncated
set as though it were complete.

Errors carry a structured body:

```json
{"error": {"code": "unrecognised_format", "title": "...", "message": "...", "hint": "..."},
 "detail": "..."}
```

`code` is the field to branch on. `415` means we could not use the file, `413`
means it needs one of the upload routes above, `503` with a `Retry-After` header
means the analyser is saturated and the request was shed rather than queued —
retry after the stated interval.

## Timing

Reports run synchronously on the instant routes. Large consumer arrays can take
a long time, and the CDN cuts any request off at roughly 100 seconds with a
`524`. If you are submitting whole arrays in bulk, prefer the multipart flow and
the `job_id`, which has no such ceiling.

## Schema

`GET /api/openapi.json` and `GET /api/docs` (Swagger UI) — both key-gated, both
`503` when the API is switched off.

## Operations

`GET /health` is open and reports build commit, queue availability and whether
the JSON API is enabled. `GET /metrics` is key-gated and returns request,
refusal, shed and per-consumer counters. See `docs/OPERATIONS.md`.
