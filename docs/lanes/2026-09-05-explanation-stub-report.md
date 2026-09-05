# DNA deeper-dive stub proof

Date: 2026-09-05
Branch: `lane-0905-s-dna`

## Result

The OpenAI-compatible deeper-dive path is proven from the environment-based
backend selector through request construction, draft extraction, safety checks,
finding attachment, metadata, and the SQLite cache. The proof uses an in-process
stub at `urllib`'s network boundary. It sends no traffic and uses only the literal
test credential `stub-secret`.

No product-code change was needed. The existing wiring behaved as designed.

`tests/test_explain_run.py` now proves all of these together, without replacing
`select_backend()`:

- `DNAREPORT_EXPLAIN_ENABLED=1` selects the configured OpenAI-compatible backend.
- `DNAREPORT_EXPLAIN_MODEL=stub-model` reaches the JSON request and accepted
  finding metadata.
- `DNAREPORT_EXPLAIN_BASE_URL=http://stub.invalid/v1` becomes exactly
  `http://stub.invalid/v1/chat/completions`.
- Omitting `DNAREPORT_EXPLAIN_KEY_FILE` sends no `Authorization` header.
- Pointing it at a `0600` file containing `stub-secret` sends exactly
  `Authorization: Bearer stub-secret`.
- The returned marked draft is extracted, post-checked, and attached to the
  promoted finding.
- A second run with identical public facts reads the accepted draft from the
  cache and does not call the stub again.
- `DNAREPORT_EXPLAIN_ENABLED=0` makes `select_backend()` return `None`, leaves the
  report untouched, and makes no stub call.

The request assertion also confirms that the public facts reach the model while
the genotype does not.

## The exact model-day change

If both workers are preconfigured with the enable flag and one stable
OpenAI-compatible gateway URL, the one line to edit on each worker when a model
is chosen is:

```dotenv
DNAREPORT_EXPLAIN_MODEL=<exact model id exposed by the configured gateway>
```

The preconfiguration required for that statement to be true is:

```dotenv
DNAREPORT_EXPLAIN_ENABLED=1
DNAREPORT_EXPLAIN_BASE_URL=<stable OpenAI-compatible gateway root ending in /v1>
```

`DNAREPORT_EXPLAIN_BACKEND=openai_compat` is optional because that is the code
default. `DNAREPORT_EXPLAIN_KEY_FILE=<path>` is also optional. When the gateway
requires authentication, that path must name a file with mode exactly `0600`;
the code reads it at call time.

There is an important corrected premise: if the enable flag and stable base URL
are not already present on the two workers, activation is not a one-line change.
It requires those two values as well as the model line. This repository contains
neither the worker environment files nor the deployment service name, so this
lane did not invent or apply their edits and cannot truthfully name their exact
restart command. The deployment handoff must pre-stage those two values and use
its existing documented restart command if one-line model-day activation is a
hard requirement.

Leaving the model value empty is safe even with the two prerequisites present:
`select_backend()` returns `None` until both model and base URL are non-empty.

## Commands and output

Focused baseline before the new proof:

```text
$ pytest -q tests/test_explain_prompt.py tests/test_explain_check.py tests/test_explain_run.py tests/test_explain_gate.py
..................                                                       [100%]
18 passed, 1 warning in 0.10s
```

The first implementation attempted a literal loopback HTTP server. This managed
sandbox forbids socket binding, including `127.0.0.1`:

```text
$ pytest -q tests/test_explain_run.py
.......FFF                                                               [100%]
PermissionError: [Errno 1] Operation not permitted
3 failed, 7 passed, 1 warning in 0.34s
```

Per the lane's no-network rule, the proof then moved the stub to the final
transport boundary. No external network or local socket is needed.

Final focused suite:

```text
$ pytest -q tests/test_explain_prompt.py tests/test_explain_check.py tests/test_explain_run.py tests/test_explain_gate.py
.....................                                                    [100%]
21 passed, 1 warning in 0.09s
```

Full fast suite:

```text
$ pytest -q
........................................................................ [ 22%]
........................................................................ [ 44%]
........s............................................................... [ 66%]
..........................................................s............. [ 89%]
...................................                                      [100%]
321 passed, 2 skipped, 1 warning in 16.94s
```

The warning in all three runs is the existing Starlette
`PendingDeprecationWarning` for `import multipart`. The two full-suite skips are
existing environment-dependent tests.

## Deliberately not done

- No worker, live host, environment file, service, or model endpoint was touched.
- No real key was read, created, or searched for.
- No external request was attempted.
- No deployment-repository file was read or changed because the lane permits
  work only in this worktree.
- A real TCP exchange remains untested here because the sandbox prohibits socket
  binding. The already-existing unit test plus this end-to-end proof cover the
  request and response schema on either side of that transport boundary; the
  endpoint address remains the deployment-specific variable.
