# Explanation request and response reliability

Date: 2026-09-18
Branch: `credit-20260918-dna`

## Result

The OpenAI-compatible explanation backend now treats completion state as part of
the response contract. A reply with `finish_reason: length` is rejected even if
its partial `content` would pass the citation and wording checks. Empty content,
missing choices, malformed choices, and other non-completing finish reasons also
produce explicit refusal reasons and are never cached as explanations.
The cache key now includes response-contract version 2, so entries accepted
before these completion checks cannot bypass them after upgrade.

Request settings are configurable through
`DNAREPORT_EXPLAIN_REQUEST_OPTIONS`, a JSON object overlaid on the existing
defaults (`temperature: 0.2`, `max_tokens: 1500`). This supports compatible
server-specific fields such as `reasoning_effort` and
`chat_template_kwargs`, changing the token limit, or removing a default by
setting it to `null`. The overlay cannot replace `model`, `messages`, or enable
streaming; those remain owned by the backend code and existing selection gate.

For example, this is a valid configuration shape, but it was tested only at the
captured request boundary in this change:

```dotenv
DNAREPORT_EXPLAIN_REQUEST_OPTIONS={"max_tokens":3200,"temperature":null,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}
```

Model choice, backend choice, `DNAREPORT_EXPLAIN_ENABLED`, administrator CLI
gating, and cache acceptance checks are unchanged.

## Regression proof

`tests/test_explain_run.py` covers:

- the default request body;
- a backend-specific request overlay, including removal of `temperature`;
- the historical OpenAI-compatible truncation shape with
  `finish_reason: length` and otherwise acceptable cited content;
- an empty `content` reply and a reply with no choices;
- propagation of a stable truncation reason onto the finding; and
- a second attempt proving the truncated response was not cached.

Focused suite:

```text
$ pytest -q tests/test_explain_prompt.py tests/test_explain_check.py tests/test_explain_run.py tests/test_explain_gate.py
..........................                                               [100%]
26 passed in 0.07s
```

Full suite:

```text
$ pytest -q
........................................................................ [ 17%]
................................................................s....... [ 35%]
........................................................................ [ 53%]
........................................................................ [ 71%]
................................................................s....... [ 89%]
.........................................                                [100%]
399 passed, 2 skipped in 166.11s (0:02:46)
```

`python -m compileall -q dnareport tests` and `git diff --check` also passed.

## Limits

The September 13 observation that eight of nine benchmark candidates returned
empty output was not rerun. No live model endpoint, credential, worker,
deployment, or production setting was touched. These tests prove local request
construction and captured response handling; they do not claim that a particular
live backend honors any optional generation field.
