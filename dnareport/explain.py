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


PROMPT_VERSION = "1"

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
Use at most 180 words.
Cover the change first.
Then cover the condition, classification meaning, source certainty, and a sensible next step.
Prompt version {version}."""


def build_prompt(facts: dict) -> tuple[str, str]:
    """Return the system instruction and a user turn containing facts only."""
    return _SYSTEM.format(version=PROMPT_VERSION), json.dumps(
        facts, indent=1, sort_keys=True
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


class Backend(Protocol):
    """The common interface for deeper-dive drafting backends."""

    def draft(self, system: str, user: str, *, timeout: float) -> str: ...


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

    def draft(self, system: str, user: str, *, timeout: float) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0.2,
                "max_tokens": 400,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        key = self._key()
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            document = json.loads(response.read())
        return document["choices"][0]["message"]["content"]


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
        return process.stdout.strip()


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
