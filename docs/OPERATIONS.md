# Operating the front door

## Issuing and revoking API keys

`DNAREPORT_API_KEY` holds the whole key set. It accepts either a single opaque
secret (the original form) or a comma-separated list of `label:secret` pairs:

```
DNAREPORT_API_KEY=partner-lab:0Nd3...,internal:7fQa...
```

Labels are `[A-Za-z0-9_.-]`, up to 32 characters. Anything that does not match
that pattern before a colon is read as a bare secret, so a generated secret is
never accidentally split into a label and a shorter secret.

Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

To issue a key, append a pair. To revoke one, remove its pair. Both are one edit
to the Coolify environment plus a redeploy — there is no key database and nothing
to migrate. This is sized for a handful of partners; past that it wants a real
key store with per-key expiry and self-service rotation.

The label is what appears in rate limiting and in `/metrics`, so it should name
the consumer.

## Abuse controls

| Variable | Default | What it bounds |
|---|---|---|
| `DNAREPORT_RATE_MAX` | 60 | Requests per window, per key or per client IP |
| `DNAREPORT_RATE_WINDOW` | 60 | Window in seconds |
| `DNAREPORT_MAX_CONCURRENT` | 4 | Analyses running at once; excess is shed with `503` + `Retry-After` |

Anonymous callers are bucketed by `CF-Connecting-IP`, which is trustworthy only
while the app is reachable solely through the tunnel. Keyed callers are bucketed
by label, so consumers cannot spend each other's budget.

The concurrency cap is the one that protects the box. A rate limit counts
requests per minute and cannot stop several whole-genome analyses arriving in the
same second; the box is mild-CPU by design and each analysis holds a threadpool
slot for its whole runtime.

**None of this bounds how long a single report takes.** There is no report
deadline anywhere on the inline path, and a large consumer array can exceed the
CDN's ~100 second ceiling and return `524`.

## Metrics

`GET /metrics`, key-gated, returns JSON:

```
scope           "shared" (Redis-backed) or "process" (this worker only)
uptime_s        seconds since this worker started
inflight        analyses running now
counters        route:*, status:*, refusal:*, key:*, shed:busy
```

**Check `scope` before building an alert on it.** The container runs more than one
uvicorn worker. With a queue backend configured the counters live in Redis and
are shared and durable; without one they are per-process, and consecutive scrapes
hit different workers and return different numbers. A threshold trigger on
`process` numbers will flap.

Signals worth alerting on: `shed:busy` rising at all (the box is saturated),
`status:429` rising (someone is hammering, or a legitimate consumer needs a
bigger budget), `status:401` rising (key guessing), `refusal:*` dominated by one
code (a format we should support, or a broken client).
