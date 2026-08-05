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
instead of a report. Poll `GET /result/{job_id}`: `202` while it is still
running, `200` with the report when it is done. Note that the job id is the only
credential on that URL — anyone holding it can read the report.

## Response

`200` with a versioned object. `schema_version` is currently `1.1`; additions are
made without a major bump, so read fields by name rather than assuming a shape.
Findings carry `marker`, `description`, `tier`, `magnitude` (0-10),
`direction` where the source asserts one, and resolved `links`.

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
