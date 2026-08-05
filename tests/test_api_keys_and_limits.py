"""Multi-tenant API keys, abuse limits, and the metrics surface.

These cover the three things that stand between a partner having API access and
the front door being trivially knocked over: who a key belongs to, how much any
one caller may consume, and whether an operator can see it happening.
"""
import os
import pytest
pytest.importorskip("fastapi")
# Match what the other web tests put in the env, because whichever test module
# imports dnareport.web first fixes the import-time key set for the whole run.
# Setting something different here would silently break their key assertions
# depending on collection order. This module swaps in its own labelled keys per
# test instead, and puts them back afterwards.
os.environ.setdefault("DNAREPORT_API_KEY", "test-key")
from fastapi.testclient import TestClient
import dnareport.web as web

client = TestClient(web.app)

TINY = ("# rsid\tchromosome\tposition\tgenotype\n"
        "rs4988235\t2\t136608646\tAA\n")

TENANTS = {"partner-secret": "partner", "internal-secret": "internal"}


@pytest.fixture(autouse=True)
def _reset():
    """Buckets and counters are process-global; a leaked bucket from one test
    would show up as a spurious 429 in the next."""
    saved = dict(web.API_KEYS)
    web.API_KEYS.clear()
    web.API_KEYS.update(TENANTS)
    web._rate_state.clear()
    web._counters.clear()
    web._inflight.reset()
    yield
    web.API_KEYS.clear()
    web.API_KEYS.update(saved)
    web._rate_state.clear()
    web._counters.clear()
    web._inflight.reset()


# ---- key parsing ----------------------------------------------------------

def test_labelled_keys_each_authenticate_and_name_their_tenant():
    assert web._resolve_key("partner-secret") == "partner"
    assert web._resolve_key("internal-secret") == "internal"


def test_unknown_secret_resolves_to_nothing():
    assert web._resolve_key("not-a-key") is None
    assert web._resolve_key("") is None


def test_a_bare_secret_still_works_so_existing_deployments_do_not_break():
    """The env var was a single opaque secret before labels existed. A deployment
    that still holds one must keep working, or the upgrade is a breaking change
    dressed up as a feature."""
    assert web._parse_api_keys("just-one-secret") == {"just-one-secret": "default"}


def test_a_bare_secret_containing_a_colon_is_not_mistaken_for_a_label():
    """Generated secrets contain punctuation. Splitting naively on ':' would turn
    a valid secret into a label plus a shorter secret, silently changing what
    authenticates."""
    parsed = web._parse_api_keys("aB3$xy:zzz/quux+long")
    assert parsed == {"aB3$xy:zzz/quux+long": "default"}


def test_labels_are_stripped_of_whitespace_around_the_comma():
    parsed = web._parse_api_keys("a:one, b:two")
    assert parsed == {"one": "a", "two": "b"}


def test_json_requires_a_known_key():
    r = client.get("/demo/blood?format=json&api_key=wrong")
    assert r.status_code == 401


def test_json_accepts_a_labelled_key_by_header():
    r = client.get("/demo/blood", headers={"Accept": "application/json",
                                           "X-API-Key": "partner-secret"})
    assert r.status_code == 200


# ---- rate limiting --------------------------------------------------------

def test_analyze_is_rate_limited_for_anonymous_callers():
    """/analyze is the most expensive route in the service and was the only one
    with no limit at all — an unauthenticated caller could run the box flat."""
    web._RATE_MAX = 3
    try:
        codes = []
        for _ in range(5):
            r = client.post("/analyze", files={"file": ("g.txt", TINY, "text/plain")},
                            headers={"CF-Connecting-IP": "203.0.113.9"})
            codes.append(r.status_code)
        assert 429 in codes, f"never rate limited: {codes}"
    finally:
        web._RATE_MAX = 60


def test_one_tenant_exhausting_its_budget_does_not_lock_out_another():
    """Buckets are per key, so a partner running hot must not spend the internal
    allowance — otherwise one noisy consumer is a denial of service on everyone."""
    web._RATE_MAX = 2
    try:
        for _ in range(4):
            client.get("/demo/blood?format=json&api_key=partner-secret")
        r = client.get("/demo/blood?format=json&api_key=internal-secret")
        assert r.status_code != 429
    finally:
        web._RATE_MAX = 60


def test_anonymous_callers_are_bucketed_by_real_client_ip_not_by_the_edge():
    web._RATE_MAX = 2
    try:
        for _ in range(4):
            client.post("/analyze", files={"file": ("g.txt", TINY, "text/plain")},
                        headers={"CF-Connecting-IP": "203.0.113.1"})
        r = client.post("/analyze", files={"file": ("g.txt", TINY, "text/plain")},
                        headers={"CF-Connecting-IP": "203.0.113.2"})
        assert r.status_code != 429
    finally:
        web._RATE_MAX = 60


# ---- concurrency guard ----------------------------------------------------

def test_analysis_sheds_load_once_the_box_is_saturated():
    """Rate limiting counts requests per minute; it cannot stop N simultaneous
    whole-genome analyses from arriving inside the same second. The Hetzner box
    is mild-CPU by design, so the guard is a concurrency cap, and shedding must
    be an explicit 503 with Retry-After rather than a timeout nobody sees."""
    web._inflight.limit = 1
    web._inflight.count = 1          # pretend one analysis is already running
    try:
        r = client.post("/analyze", files={"file": ("g.txt", TINY, "text/plain")})
        assert r.status_code == 503
        assert r.headers.get("retry-after")
        assert "busy" in r.text.lower()
    finally:
        web._inflight.reset()


def test_the_guard_releases_its_slot_even_when_analysis_fails():
    """A leaked slot is worse than no guard: the service would refuse everything
    forever after the first bad upload, and look identical to being overloaded."""
    web._inflight.limit = 2
    client.post("/analyze", files={"file": ("junk.txt", "not a genome", "text/plain")})
    assert web._inflight.count == 0


def test_analysis_does_not_run_on_the_event_loop_thread(monkeypatch):
    """/analyze is `async def` but the whole analysis under it is synchronous.
    Called directly it blocks the event loop, so ONE slow report stops the worker
    answering anything at all — including /health, which is what turns a slow
    report into an apparent outage and what makes the concurrency cap above
    unreachable (nothing else gets scheduled to reach it)."""
    import asyncio
    # This guards the inline path specifically; with a queue configured the
    # analysis happens on a worker and never reaches this box at all.
    monkeypatch.setattr(web, "_queue_is_usable", lambda: False)
    seen = {}
    real = web._run_and_respond

    def _spy(*a, **kw):
        # A thread running the event loop can see it; a threadpool worker cannot.
        # This is the property that matters, and it does not depend on which
        # thread the test harness happens to drive the loop from.
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return real(*a, **kw)

    web._run_and_respond = _spy
    try:
        client.post("/analyze", files={"file": ("g.txt", TINY, "text/plain")})
    finally:
        web._run_and_respond = real
    assert "on_loop" in seen, "analysis never ran"
    assert seen["on_loop"] is False


# ---- metrics --------------------------------------------------------------

def test_metrics_requires_a_key():
    assert client.get("/metrics").status_code == 401


def test_metrics_counts_requests_refusals_and_names_each_tenant():
    client.get("/demo/blood?format=json&api_key=partner-secret")
    client.post("/analyze", files={"file": ("junk.txt", "not a genome", "text/plain")})
    m = client.get("/metrics", headers={"X-API-Key": "internal-secret"}).json()
    assert m["counters"]["key:partner"] >= 1
    assert m["counters"]["route:/analyze"] >= 1
    assert m["counters"]["status:415"] >= 1
    assert "inflight" in m and "uptime_s" in m


def test_metrics_reports_where_its_numbers_come_from():
    """Counters are per-process unless Redis is backing them, and the app runs
    more than one worker. A scraper that does not know which it is looking at
    will alert on numbers that bounce between workers."""
    m = client.get("/metrics", headers={"X-API-Key": "internal-secret"}).json()
    assert m["scope"] in ("process", "shared")
