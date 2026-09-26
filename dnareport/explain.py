# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Build a short, checked explanation of one promoted finding.

The prompt contains public facts about one finding and its zygosity class. It
contains no genotype, upload identifier, file name, job identifier, or other
finding. Cache keys use the same bounded facts, so drafts can be reused safely.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Protocol


PROMPT_VERSION = "2"
RESPONSE_CONTRACT_VERSION = "2"

_ZYGOSITY_CLASS = {
    "het": "one altered copy",
    "hom": "two altered copies",
    "hemi": "one copy on the X or Y chromosome",
}


def facts_for(finding) -> dict:
    """Return JSON-safe public facts without personal or upload data."""
    detail = finding.detail or {}
    interpretation = finding.interpretation
    gnomad = detail.get("gnomad") or {}
    frequency = None
    if gnomad.get("ac") is not None and gnomad.get("an"):
        frequency = {
            "ac": gnomad.get("ac"),
            "an": gnomad.get("an"),
            "version": gnomad.get("version"),
        }

    return {
        "marker": finding.marker,
        "gene": detail.get("gene"),
        "condition": interpretation.condition if interpretation else None,
        "condition_ids": list(interpretation.condition_ids) if interpretation else [],
        "classification": detail.get("clinical_significance"),
        "review_status": detail.get("review_status"),
        "gold_stars": detail.get("gold_stars"),
        "zygosity_class": _ZYGOSITY_CLASS.get(
            detail.get("zygosity") or "", "copies not determined"
        ),
        "platform_class": (
            "array"
            if str(detail.get("platform") or "").upper() == "ARRAY"
            else "sequencing"
        ),
        "consequence": detail.get("molecular_consequence"),
        "frequency": frequency,
        "found": interpretation.found if interpretation else "",
        "can_mean": interpretation.can_mean if interpretation else "",
        "how_sure": interpretation.how_sure if interpretation else "",
        "next_step": interpretation.next_step if interpretation else "",
        "chain": [
            {"kind": link.kind, "label": link.label, "id": link.id, "url": link.url}
            for link in (finding.evidence_chain or [])
        ],
        "promoted_reason": finding.promoted_reason,
        "trait": detail.get("trait"),
        "effect_type": detail.get("effect_type"),
        "effect": detail.get("effect"),
        "drug": detail.get("drug"),
    }


def cache_key(facts: dict, backend: str, model: str) -> str:
    """Return a stable key for public facts, backend, model, and prompt version."""
    stable = {
        "marker": facts.get("marker"),
        "gene": facts.get("gene"),
        "condition_ids": sorted(facts.get("condition_ids") or []),
        "classification": facts.get("classification"),
        "gold_stars": facts.get("gold_stars"),
        "zygosity_class": facts.get("zygosity_class"),
        "platform_class": facts.get("platform_class"),
        "consequence": facts.get("consequence"),
        "trait": facts.get("trait"),
        "effect_type": facts.get("effect_type"),
        "effect": facts.get("effect"),
        "drug": facts.get("drug"),
    }
    raw = json.dumps(
        {
            "v": PROMPT_VERSION,
            "response_contract": RESPONSE_CONTRACT_VERSION,
            "backend": backend,
            "model": model,
            "facts": stable,
            "mirror": os.environ.get("DNAREPORT_MIRROR_VERSION", ""),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


_SYSTEM = """You write a short explanation of one genetic finding for a person with no medical training.
Use only the facts given.
Do not add facts, numbers, gene functions, or conditions that are absent from the facts.
Use plain English.
Write one idea per sentence.
Use 25 words or fewer per sentence.
Use active voice and present tense.
Never say the person has, will get, or will develop a condition.
Make no diagnosis.
Cite each factual claim with its chain label in square brackets.
For example, write [BRCA2] or [ClinVar record].
Use at most 160 words.
Cover the change first.
Then cover the condition, classification meaning, source certainty, and a sensible next step.
Plan silently. Do not show your planning or your notes.
Write only the final text, and place it between the markers <dive> and </dive>.
Prompt version {version}."""


def build_prompt(facts: dict) -> tuple[str, str]:
    """Return the system instruction and a user turn containing facts only."""
    labels = [c.get("label") for c in (facts.get("chain") or []) if c.get("label")]
    user_facts = dict(facts)
    user_facts["allowed_citation_labels"] = labels
    return _SYSTEM.format(version=PROMPT_VERSION), json.dumps(
        user_facts, indent=1, sort_keys=True
    )


_FORBIDDEN = (
    "you have ",
    "you will ",
    "you'll ",
    "diagnos",
    "you are at risk",
    "you suffer",
    "guarantee",
    "certainly",
    "definitely",
)
_BANNED = re.compile(
    r"honest|it's not |not a [A-Z]|the point is|worth (noting|being explicit)"
    r"|in other words|crucially|importantly,|notably,|by design|buckle up|dive in",
    re.I,
)
_CITATION = re.compile(r"\[([^\]]{1,80})\]")
MAX_WORDS = 180


_DIVE = re.compile(r"<dive>(.*?)</dive>", re.S)


def extract_dive(text: str) -> str:
    """The final text between the last <dive> and </dive> markers. A model that
    shows its planning in the same stream puts the answer last; without markers
    the whole text is taken as the draft and the post-check judges it."""
    found = _DIVE.findall(text or "")
    return (found[-1] if found else (text or "")).strip()


def check_draft(text: str, facts: dict) -> str | None:
    """Return a short rejection reason, or ``None`` for an accepted draft."""
    draft = (text or "").strip()
    if not draft:
        return "empty"
    if len(draft.split()) > MAX_WORDS:
        return "too long"

    lowered = draft.lower()
    for phrase in _FORBIDDEN:
        if phrase in lowered:
            return f"forbidden phrase: {phrase.strip()}"

    banned = _BANNED.search(draft)
    if banned:
        return f"banned construction: {banned.group(0)}"

    labels = {link.get("label") for link in (facts.get("chain") or [])}
    citations = _CITATION.findall(draft)
    if not citations:
        return "no citation"
    for citation in citations:
        if citation not in labels:
            return f"unknown citation: {citation}"
    return None


class EmptyDraftError(RuntimeError):
    """A backend answered with no visible text.

    On a thinking model the deliberation is billed against ``max_tokens``, so a
    budget that fits the answer alone comes back with ``content: ""`` and the
    whole reply in ``reasoning_content``. That is a failed call, not a short
    explanation: it is raised here so the finding records a backend error
    instead of silently carrying a blank deeper dive.
    """


class Backend(Protocol):
    """The common interface for deeper-dive drafting backends."""

    def draft(self, system: str, user: str, *, timeout: float) -> str: ...


DEFAULT_MAX_TOKENS = 4000


def reasoning_off_body() -> dict:
    """The documented switches that turn a thinking model's deliberation off.

    vLLM honours ``chat_template_kwargs.enable_thinking=false`` and LiteLLM
    forwards the key unchanged; measured 2026-09-13 on the alien trio
    (``qwen3.8-27b``: 1500 tokens of thought and an empty answer becomes
    132-423 tokens and a passing draft) and on the gx10 quad
    (``glm-5.3-int4mix``: 143 s becomes 14 s). GLM-5.3 also reads
    ``reasoning_effort`` (low/high/max; Z.ai's guidance is to use ``low``
    where disabling used to be possible), so it is sent when
    ``DNAREPORT_EXPLAIN_REASONING_EFFORT`` names one. ai-silo's ds4 and the
    oMLX studios honour neither switch, which is why ``max_tokens`` has to be
    large enough on its own.
    """
    body: dict = {"chat_template_kwargs": {"enable_thinking": False}}
    effort = os.environ.get("DNAREPORT_EXPLAIN_REASONING_EFFORT", "").strip()
    if effort:
        body["reasoning_effort"] = effort
    return body


def _max_tokens() -> int:
    """The completion budget, sized to hold deliberation plus a 180-word answer."""
    try:
        wanted = int(os.environ.get("DNAREPORT_EXPLAIN_MAX_TOKENS", ""))
    except ValueError:
        wanted = DEFAULT_MAX_TOKENS
    return max(wanted, 600)


class DraftResponseError(RuntimeError):
    """The backend answered, but did not return a complete usable draft."""


_TRUNCATED = frozenset({"length", "max_tokens", "max_output_tokens"})


class OpenAICompat:
    def __init__(self, base_url: str, model: str, key_file: str | None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.key_file = key_file

    def _key(self) -> str:
        if not self.key_file:
            return ""
        path = Path(self.key_file)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise PermissionError("The deeper-dive key file must use mode 0600.")
        return path.read_text().strip()

    @staticmethod
    def _apply_request_options(body: dict) -> dict:
        """Overlay operator-configured OpenAI-compatible request fields.

        Compatible servers expose different generation controls.  The JSON
        object in ``DNAREPORT_EXPLAIN_REQUEST_OPTIONS`` is applied last, so an
        operator can set a field their server accepts, or remove a default by
        giving it ``null``, without coupling those controls to model selection.
        """
        configured = os.environ.get("DNAREPORT_EXPLAIN_REQUEST_OPTIONS", "")
        if not configured:
            return body
        try:
            overlay = json.loads(configured)
        except json.JSONDecodeError as error:
            raise ValueError(
                "DNAREPORT_EXPLAIN_REQUEST_OPTIONS must be a JSON object"
            ) from error
        if not isinstance(overlay, dict):
            raise ValueError(
                "DNAREPORT_EXPLAIN_REQUEST_OPTIONS must be a JSON object"
            )
        protected = {"model", "messages", "stream"}.intersection(overlay)
        if protected:
            fields = ", ".join(sorted(protected))
            raise ValueError(
                f"DNAREPORT_EXPLAIN_REQUEST_OPTIONS cannot set {fields}"
            )
        for field, value in overlay.items():
            if value is None:
                body.pop(field, None)
            else:
                body[field] = value
        return body

    def _post(self, body: dict, timeout: float) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        key = self._key()
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())

    @staticmethod
    def _read(document: object) -> tuple[str, str | None, int]:
        """Return the visible text, the finish reason, and the thought length.

        A malformed response, or one the backend says it stopped for any reason
        other than completion or its token budget, raises DraftResponseError.
        An empty or budget-truncated answer is returned for ``draft`` to retry.
        """
        if not isinstance(document, dict):
            raise DraftResponseError("backend returned a malformed response")
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DraftResponseError("backend returned no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise DraftResponseError("backend returned a malformed choice")

        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise DraftResponseError("backend returned a malformed finish reason")
        if finish_reason not in {None, "stop"} | _TRUNCATED:
            raise DraftResponseError("backend did not complete the draft")

        message = choice.get("message")
        if not isinstance(message, dict):
            raise DraftResponseError("backend returned no message")
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "".join(
                part.get("text", "")
                for part in content
                if (
                    isinstance(part, dict)
                    and part.get("type") in {"text", "output_text"}
                    and isinstance(part.get("text"), str)
                )
            ).strip()
        else:
            text = ""
        thought = message.get("reasoning_content") or message.get("reasoning") or ""
        return text, finish_reason, len(thought) if isinstance(thought, str) else 0

    def draft(self, system: str, user: str, *, timeout: float) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        budget = _max_tokens()
        body = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": budget,
            "messages": messages,
        }
        if os.environ.get("DNAREPORT_EXPLAIN_THINKING", "0") != "1":
            body.update(reasoning_off_body())
        self._apply_request_options(body)
        budget = body.get("max_tokens", budget)

        content, finish, thought = self._read(self._post(body, timeout))
        if content and finish not in _TRUNCATED:
            return content

        # One retry: deliberation off for the engines that read the switch, and
        # a doubled budget for the engines that ignore it (ai-silo needed 4000
        # to finish a thought and still answer, measured 2026-09-13). A draft
        # cut off by its budget gets the same retry; a second cut-off is
        # refused rather than shown as a partial explanation.
        retry = dict(body, max_tokens=max(budget * 2, DEFAULT_MAX_TOKENS))
        retry.update(reasoning_off_body())
        content, finish, thought = self._read(self._post(retry, timeout))
        if content and finish in _TRUNCATED:
            raise DraftResponseError(
                f"backend truncated the draft (finish_reason={finish})"
            )
        if content:
            return content
        raise EmptyDraftError(
            f"{self.model} returned empty content twice "
            f"(finish_reason={finish or 'unset'}, {thought} characters of "
            f"reasoning, max_tokens={retry['max_tokens']}); "
            "the model's deliberation cannot be switched off at this endpoint."
        )


class _Cli:
    """Run an administrator-gated subscription CLI without tools."""

    argv: list[str] = []

    def __init__(self, model: str = ""):
        self.model = model

    def draft(self, system: str, user: str, *, timeout: float) -> str:
        process = subprocess.run(
            self.argv + ([self.model] if self.model else []),
            input=f"{system}\n\n{user}",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"{self.argv[0]} exit {process.returncode}: {process.stderr[-200:]}"
            )
        text = process.stdout.strip()
        if not text:
            raise EmptyDraftError(f"{self.argv[0]} wrote nothing to stdout.")
        return text


class CodexCli(_Cli):
    argv = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--disable",
        "shell_tool",
        "--ephemeral",
        "--skip-git-repo-check",
        "-m",
    ]

    def __init__(self, model: str = "gpt-5.6-sol"):
        super().__init__(model)


class ClaudeCli(_Cli):
    argv = ["claude", "-p", "--tools", "", "--output-format", "text", "--model"]

    def __init__(self, model: str = "opus"):
        super().__init__(model)


_CLI_BACKENDS = {"codex_cli", "claude_cli"}


def select_backend(job_backend: str | None = None) -> tuple[str, str, Backend] | None:
    """Select an enabled backend while enforcing the worker-side CLI gate."""
    if os.environ.get("DNAREPORT_EXPLAIN_ENABLED", "0") != "1":
        return None

    configured = os.environ.get("DNAREPORT_EXPLAIN_BACKEND", "openai_compat")
    admin_enabled = os.environ.get("DNAREPORT_EXPLAIN_ADMIN_BACKENDS", "0") == "1"
    if job_backend in _CLI_BACKENDS and admin_enabled:
        wanted = job_backend
    else:
        wanted = configured
    if wanted in _CLI_BACKENDS and not (
        admin_enabled and job_backend == wanted
    ):
        wanted = "openai_compat"

    if wanted == "codex_cli":
        backend = CodexCli()
        return "codex_cli", backend.model, backend
    if wanted == "claude_cli":
        backend = ClaudeCli()
        return "claude_cli", backend.model, backend

    model = os.environ.get("DNAREPORT_EXPLAIN_MODEL", "")
    base_url = os.environ.get("DNAREPORT_EXPLAIN_BASE_URL", "")
    if not (model and base_url):
        return None
    backend = OpenAICompat(
        base_url, model, os.environ.get("DNAREPORT_EXPLAIN_KEY_FILE")
    )
    return "openai_compat", model, backend


class Cache:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS dive("
                "key TEXT PRIMARY KEY, text TEXT, meta TEXT, created REAL)"
            )

    def get(self, key: str) -> dict | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT text, meta FROM dive WHERE key=?", (key,)
            ).fetchone()
        return {"text": row[0], "meta": json.loads(row[1])} if row else None

    def put(self, key: str, text: str, meta: dict) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO dive VALUES (?,?,?,?)",
                (key, text, json.dumps(meta), time.time()),
            )


def _cache_path() -> str:
    configured = os.environ.get("DNAREPORT_EXPLAIN_CACHE_DB")
    if configured:
        return configured
    gnomad_cache = os.environ.get("GNOMAD_AF_CACHE", "/tmp/gnomad_af.db")
    return os.path.join(os.path.dirname(gnomad_cache), "explain_cache.db")


def explain_promoted(
    result,
    *,
    job_backend: str | None = None,
    cache_only: bool = False,
    now=time.monotonic,
) -> dict:
    """Attach accepted drafts to promoted findings and return outcome counts."""
    outcome = {"drafted": 0, "cached": 0, "rejected": 0, "skipped": 0}
    selected = select_backend(job_backend)
    if selected is None:
        return outcome

    name, model, backend = selected
    cache = Cache(_cache_path())
    budget = float(os.environ.get("DNAREPORT_EXPLAIN_TIME_BUDGET_S", "120"))
    call_timeout = float(
        os.environ.get("DNAREPORT_EXPLAIN_CALL_TIMEOUT_S", "60")
    )
    maximum = int(os.environ.get("DNAREPORT_EXPLAIN_MAX_PER_REPORT", "8"))
    started = now()
    attempted = 0
    targets = [
        finding
        for finding in (getattr(result, "read_first", None) or [])
        if finding.promoted and finding.interpretation
    ]

    for finding in targets:
        facts = facts_for(finding)
        key = cache_key(facts, name, model)
        hit = cache.get(key)
        if hit:
            finding.deeper_dive = hit["text"]
            finding.deeper_dive_meta = {**hit["meta"], "cached": True}
            outcome["cached"] += 1
            continue

        if cache_only:
            reason = "cache only"
        elif attempted >= maximum:
            reason = "per-report cap"
        elif now() - started >= budget:
            reason = "time budget"
        else:
            reason = ""
        if reason:
            outcome["skipped"] += 1
            finding.deeper_dive_meta = {"rejected_reason": reason}
            continue

        system, user = build_prompt(facts)
        attempted += 1
        try:
            text = backend.draft(system, user, timeout=call_timeout)
            text = extract_dive(text)
            if not text.strip():
                raise EmptyDraftError(
                    f"{model} wrote nothing between the <dive> markers."
                )
        except DraftResponseError as error:
            outcome["rejected"] += 1
            finding.deeper_dive_meta = {
                "rejected_reason": str(error),
                "backend": name,
                "model": model,
            }
            continue
        except Exception as error:
            outcome["rejected"] += 1
            finding.deeper_dive_meta = {
                "rejected_reason": f"backend error: {type(error).__name__}",
                "backend": name,
                "model": model,
            }
            continue

        rejection = check_draft(text, facts)
        if rejection:
            outcome["rejected"] += 1
            finding.deeper_dive_meta = {
                "rejected_reason": rejection,
                "backend": name,
                "model": model,
            }
            continue

        meta = {
            "backend": name,
            "model": model,
            "prompt_version": PROMPT_VERSION,
        }
        accepted = text.strip()
        cache.put(key, accepted, meta)
        finding.deeper_dive = accepted
        finding.deeper_dive_meta = meta
        outcome["drafted"] += 1

    if outcome["rejected"] or outcome["skipped"]:
        written = outcome["drafted"] + outcome["cached"]
        result.notes.append(
            f"Deeper dive: {written} written, {outcome['rejected']} refused, "
            f"{outcome['skipped']} not attempted. "
            "A missing deeper dive says nothing about the finding."
        )
    return outcome
