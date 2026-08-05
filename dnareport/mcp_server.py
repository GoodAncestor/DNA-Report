# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""MCP server — the report, for someone else's agent.

A finished report is routinely far larger than anything a model can read: a
consumer array yields hundreds of thousands of associations. Handing over the
whole document therefore does not work, and handing over the first N kilobytes of
it is worse, because the cut lands wherever the context ran out. These tools let
an agent ask for the summary first and then query for the findings it actually
wants.

Protocol: MCP 2026-07-28, stateless. There is no session — every request carries
its own protocol version and capabilities, so this mounts inside the existing app
and needs no shared state between calls. Correlation is done the way the spec
recommends for exactly this case: an explicit handle passed as a tool argument.
Ours is the report id from the claim link — a 32-hex UUID4 with a bounded
lifetime, which is what the tools specification asks for of an unauthenticated
server ("generated with sufficient entropy... and given a bounded lifetime").

Authorization is OPTIONAL in this revision and there is nothing to declare when
you have none. The report id IS the capability, exactly as it is for the web
page: someone holding the link can read the report, with their browser or with
their tools, and nothing else can.

The tools read the JSON artefact the worker already published. They never re-run
an analysis — a second run against live sources could disagree with the report
the person is looking at.
"""
from __future__ import annotations
import os
import re
from typing import Any

#: The tool return annotation. `dict` alone is rejected for structured output —
#: the SDK needs a type it can turn into an outputSchema.
ToolResult = dict[str, Any]

#: Reports are addressed by the same id as the claim link, and validated the same
#: way. Without this a tool argument would be a path into the results bucket.
_REPORT_ID = re.compile(r"[0-9a-f]{32}")

#: Host header values this endpoint answers to. The SDK's DNS-rebinding
#: protection is ON by default and an EMPTY allow-list rejects everything with
#: HTTP 421 — behind Cloudflare the Host is the public name, not localhost, so
#: leaving this unset would mean every request from every agent failed with a
#: status nothing else in this app produces.
MCP_ALLOWED_HOSTS = [h.strip() for h in os.environ.get(
    "DNAREPORT_MCP_ALLOWED_HOSTS",
    "dna.goodancestor.com,localhost,localhost:*,127.0.0.1,127.0.0.1:*").split(",")
    if h.strip()]

MAX_FINDINGS_PER_CALL = int(os.environ.get("DNAREPORT_MCP_PAGE_SIZE", "50"))


def _bad_id(report_id: str) -> dict | None:
    if not _REPORT_ID.fullmatch(report_id or ""):
        return {"status": "invalid_report_id",
                "detail": "A report id is the 32-character hex string at the end "
                          "of a claim link, e.g. /result/<report_id>."}
    return None


def _limits_note(doc: dict) -> dict:
    """The truncation state, repeated on EVERY response that returns findings.

    An agent may only ever see one page. If the warning lived solely in the
    summary, a caller that went straight to get_findings — or paginated past the
    first response — would present a bounded set as a complete one, which is the
    failure this whole surface exists to prevent.
    """
    summary = doc.get("summary") or {}
    return {"bounded": bool(summary.get("bounded")),
            "limits": summary.get("limits") or {}}


def summarise(read_report, report_id: str) -> dict:
    """Implementation of the get_report_summary tool (module-level so it can be
    tested without standing up a protocol server)."""
    bad = _bad_id(report_id)
    if bad:
        return bad
    state = read_report(report_id)
    if state.get("status") != "ready":
        return state
    doc = state["report"]
    summary = dict(doc.get("summary") or {})
    summary["report_id"] = report_id
    summary["caveats"] = list(doc.get("notes") or [])
    summary["schema_version"] = doc.get("schema_version")
    return {"status": "ready", "summary": summary}


def query_findings(read_report, report_id: str, tier: str = "", gene: str = "",
                   topic: str = "", limit: int = MAX_FINDINGS_PER_CALL,
                   offset: int = 0) -> dict:
    """Implementation of the get_findings tool."""
    bad = _bad_id(report_id)
    if bad:
        return bad
    state = read_report(report_id)
    if state.get("status") != "ready":
        return state
    doc = state["report"]

    rows = list(doc.get("findings") or [])
    if tier:
        rows = [f for f in rows if str(f.get("tier", "")).lower() == tier.lower()]
    if gene:
        rows = [f for f in rows if gene.lower() in str(f.get("gene") or "").lower()]
    if topic:
        rows = [f for f in rows if str(f.get("topic", "")).lower() == topic.lower()]
    rows.sort(key=lambda f: f.get("magnitude") or 0.0, reverse=True)

    total = len(rows)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), MAX_FINDINGS_PER_CALL))
    page = rows[offset:offset + limit]
    return {"status": "ready", "report_id": report_id,
            "total_matching": total, "offset": offset, "returned": len(page),
            "more": offset + len(page) < total,
            **_limits_note(doc),
            "findings": page}


def build_mcp(read_report):
    """Construct the MCP server. `read_report(report_id)` returns either
    {"status": "ready", "report": <the published JSON>} or a status dict
    explaining why there is nothing to read yet.

    Returns None when the mcp package is not installed, so the app runs without
    it rather than failing to import.
    """
    try:
        from mcp.server import MCPServer
    except ImportError:
        return None

    mcp = MCPServer("DNA-Report")

    # structured_output: the result travels as `structuredContent` against a
    # declared outputSchema, not only as JSON serialised into a text block. A
    # consumer that has to json.loads a string out of prose is back to scraping,
    # which is the thing this surface exists to replace. The SDK still emits the
    # text form alongside it for older clients.
    @mcp.tool(structured_output=True)
    def get_report_summary(report_id: str) -> ToolResult:
        """Summarise a finished DNA-Report: how many findings, of what strength,
        which reference databases were consulted, the strongest findings, and
        whether the report is a complete account or a truncated one.

        Call this before get_findings. A report can contain hundreds of thousands
        of findings, and this is the only response guaranteed to fit in context.
        """
        return summarise(read_report, report_id)

    @mcp.tool(structured_output=True)
    def get_findings(report_id: str, tier: str = "", gene: str = "",
                     topic: str = "", limit: int = MAX_FINDINGS_PER_CALL,
                     offset: int = 0) -> ToolResult:
        """Query the findings in a finished DNA-Report.

        tier filters by strength of evidence ('robust', 'moderate',
        'speculative', 'unknown'); gene and topic filter by those fields. Results
        are ordered strongest-evidence-first by the same magnitude score the
        report itself uses — that ordering is about evidence, not about clinical
        importance, which nothing here is in a position to rank.

        Always returns `bounded` and `limits`: if `bounded` is true the underlying
        report is already a truncated view, and no amount of paging will reach the
        findings it left out.
        """
        return query_findings(read_report, report_id, tier=tier, gene=gene,
                              topic=topic, limit=limit, offset=offset)

    return mcp


def mcp_app(mcp):
    """The ASGI app to mount, configured for this deployment.

    stateless_http because the protocol is stateless in this revision and there is
    no reason for an instance to hold anything between calls. json_response
    because nothing here streams — every tool returns a whole answer.
    """
    from mcp.server.transport_security import TransportSecuritySettings
    return mcp.streamable_http_app(
        # The sub-app serves "/mcp" of its OWN path space by default, so mounting
        # it under /mcp puts the endpoint at /mcp/mcp and everything else 404s.
        # Serving its root means the mount point IS the endpoint.
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=MCP_ALLOWED_HOSTS,
            allowed_origins=MCP_ALLOWED_HOSTS),
    )
