"""The agent-facing surface.

A finished report can hold hundreds of thousands of findings, so an agent must be
able to get an account of it and then query, rather than swallow a document. The
properties tested here are less about MCP than about accuracy: a truncated report
has to announce itself on every response that returns findings, and "still
running", "failed" and "no such report" have to be distinguishable — an agent that
cannot tell them apart will poll a dead job for ever or abandon a live one.
"""
import pytest

from dnareport.mcp_server import (MCP_ALLOWED_HOSTS, _bad_id, query_findings,
                                  summarise)

REPORT = {
    "schema_version": "2.0",
    "summary": {"n_findings": 3, "bounded": True,
                "limits": {"gwas_catalog": {"shown": 1000, "found": 442719}}},
    "notes": ["GWAS Catalog: showing the 1000 most significant of 442719 found"],
    "findings": [
        {"marker": "rs2", "gene": "MTHFR", "tier": "moderate",
         "topic": "metabolic", "magnitude": 4.0, "description": "b"},
        {"marker": "rs1", "gene": "BRCA2", "tier": "robust",
         "topic": "cancer", "magnitude": 8.5, "description": "a"},
        {"marker": "rs3", "gene": "MTHFR", "tier": "speculative",
         "topic": "metabolic", "magnitude": 1.0, "description": "c"},
    ],
}

ID = "a" * 32


def _ready(_rid):
    return {"status": "ready", "report": REPORT}


# ------------------------------------------------------------------ addressing
def test_a_report_id_is_validated_before_it_reaches_storage():
    """A tool argument must not be able to name an arbitrary stored object."""
    for bad in ("../../etc/passwd", "", "z" * 32, "a" * 31, "A" * 32):
        assert _bad_id(bad) is not None, bad
    assert _bad_id(ID) is None


def test_a_bad_id_is_refused_without_touching_storage():
    def explode(_rid):
        raise AssertionError("storage must not be consulted for a bad id")
    out = summarise(explode, "../secrets")
    assert out["status"] == "invalid_report_id"


# -------------------------------------------------------------------- summary
def test_the_summary_carries_the_counts_and_the_caveats():
    out = summarise(_ready, ID)
    assert out["status"] == "ready"
    assert out["summary"]["n_findings"] == 3
    assert out["summary"]["report_id"] == ID
    assert out["summary"]["schema_version"] == "2.0"
    assert any("442719" in c for c in out["summary"]["caveats"])


# ------------------------------------------------------------------- querying
def test_findings_come_back_strongest_first():
    out = query_findings(_ready, ID)
    assert [f["marker"] for f in out["findings"]] == ["rs1", "rs2", "rs3"]


def test_findings_can_be_filtered():
    assert query_findings(_ready, ID, tier="robust")["total_matching"] == 1
    assert query_findings(_ready, ID, gene="mthfr")["total_matching"] == 2
    assert query_findings(_ready, ID, topic="cancer")["total_matching"] == 1
    assert query_findings(_ready, ID, gene="nosuchgene")["total_matching"] == 0


def test_paging_reports_its_own_position():
    first = query_findings(_ready, ID, limit=2)
    assert first["returned"] == 2 and first["more"] is True
    assert first["total_matching"] == 3
    second = query_findings(_ready, ID, limit=2, offset=2)
    assert second["returned"] == 1 and second["more"] is False


def test_a_page_size_cannot_be_used_to_pull_the_whole_report():
    out = query_findings(_ready, ID, limit=100000)
    assert out["returned"] <= 50


def test_truncation_travels_with_every_findings_response():
    """An agent may only ever see one page, and may never call the summary. If
    the warning lived only there, a bounded report would be presented as a
    complete one — the failure this whole surface exists to prevent."""
    for kwargs in ({}, {"tier": "robust"}, {"offset": 2}, {"limit": 1}):
        out = query_findings(_ready, ID, **kwargs)
        assert out["bounded"] is True, kwargs
        assert out["limits"]["gwas_catalog"]["found"] == 442719, kwargs


def test_a_complete_report_is_not_labelled_bounded():
    plain = {"status": "ready",
             "report": {"summary": {"bounded": False}, "findings": []}}
    out = query_findings(lambda _r: plain, ID)
    assert out["bounded"] is False
    assert out["limits"] == {}


# --------------------------------------------------------- unfinished reports
@pytest.mark.parametrize("state", [
    {"status": "working", "waited_seconds": 20, "retry_after_seconds": 15},
    {"status": "failed", "detail": "RuntimeError: mirror is read-only"},
    {"status": "not_found", "detail": "no such report"},
    {"status": "overdue", "waited_seconds": 9000},
])
def test_an_unfinished_report_is_explained_rather_than_faked(state):
    """Each of these is a different instruction to the caller. Returning an empty
    findings list for any of them is how an agent ends up polling something that
    will never finish, or giving up on something that would have completed."""
    for call in (lambda: summarise(lambda _r: state, ID),
                 lambda: query_findings(lambda _r: state, ID)):
        out = call()
        assert out["status"] == state["status"]
        assert "findings" not in out          # never an empty list standing in


# ------------------------------------------------------------------ transport
def test_the_host_allow_list_is_never_empty():
    """The SDK's DNS-rebinding protection is on by default, and an EMPTY
    allow-list rejects every request with 421 — a status nothing else in this app
    produces, and so a genuinely baffling failure from behind a proxy."""
    assert MCP_ALLOWED_HOSTS
    assert "dna.goodancestor.com" in MCP_ALLOWED_HOSTS


def test_the_server_builds_and_registers_both_tools():
    mcp_mod = pytest.importorskip("mcp.server")
    from dnareport.mcp_server import build_mcp
    assert build_mcp(_ready) is not None and mcp_mod is not None


# ------------------------------------------------- the real protocol, mounted
_HDRS = {"MCP-Protocol-Version": "2026-07-28",
         "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream",
         "Host": "dna.goodancestor.com"}
_META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
         "io.modelcontextprotocol/clientCapabilities": {}}


@pytest.fixture(scope="module")
def mcp_client():
    """A client with the app LIFESPAN running.

    The lifespan is what enters the MCP session manager. A mounted sub-app's own
    lifespan never runs, so without the host app entering it every request fails
    with "Task group is not initialized" — a failure no unit test would catch.

    Module-scoped because the session manager may be entered only once for the
    life of the server object; a per-test client re-enters it and errors.
    """
    pytest.importorskip("mcp.server")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import dnareport.web as web
    if web._MCP is None:
        pytest.skip("MCP server did not build in this environment")
    with TestClient(web.app) as c:
        yield c


def test_the_endpoint_is_where_the_mount_point_says(mcp_client):
    """The sub-app serves its own '/mcp' by default, so mounting it at /mcp once
    put the real endpoint at /mcp/mcp and everything 404'd."""
    r = mcp_client.post("/mcp", headers={**_HDRS, "Mcp-Method": "tools/list"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                              "params": {"_meta": _META}})
    assert r.status_code == 200, r.text


def test_both_tools_are_advertised_with_an_output_schema(mcp_client):
    r = mcp_client.post("/mcp", headers={**_HDRS, "Mcp-Method": "tools/list"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                              "params": {"_meta": _META}})
    result = r.json()["result"]
    assert result["resultType"] == "complete"
    names = {t["name"] for t in result["tools"]}
    assert names == {"get_report_summary", "get_findings"}
    # an agent should receive data, not JSON embedded in prose it has to re-parse
    assert all("outputSchema" in t for t in result["tools"])


def test_a_tool_call_answers_with_structured_content(mcp_client):
    r = mcp_client.post("/mcp",
                        headers={**_HDRS, "Mcp-Method": "tools/call",
                                 "Mcp-Name": "get_report_summary"},
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "get_report_summary",
                                         "arguments": {"report_id": "b" * 32},
                                         "_meta": _META}})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["isError"] is False
    # no such report -> a stated status, never an empty success
    assert result["structuredContent"]["status"] in (
        "not_found", "working", "failed", "overdue")


def test_the_slashless_path_redirects_rather_than_failing(mcp_client):
    """The published endpoint is `/mcp/`. A request to `/mcp` is redirected by the
    parent router before the mounted app is reached, so it cannot be served
    directly — but 307 preserves method and body, so a following client still
    works. Pinned because a 404 or a 405 here would look identical to an outage
    to anyone who left the slash off."""
    r = mcp_client.post("/mcp", headers={**_HDRS, "Mcp-Method": "tools/list"},
                        json={"jsonrpc": "2.0", "id": 9, "method": "tools/list",
                              "params": {"_meta": _META}},
                        follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].endswith("/mcp/")
