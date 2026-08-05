"""Test-wide safety rails.

This suite gets run inside the DEPLOYED container as well as on a laptop — it is
the only place the engines and the real mirrors both exist, so it is the only
place some of these tests can run at all. That makes the environment real: a
configured queue, live R2 credentials, a production redis.

It has already bitten once. With every upload now routed to a worker, tests that
POST an upload reached the real `_enqueue_job` and pushed four jobs onto the
production queue with fixture keys like `inline/abc/junk.txt`. They failed
correctly — the input did not exist, so they retried and dead-lettered rather
than spinning — but a test suite must not be able to put anything on a real
queue at all.

So queueing is OFF for every test unless that test switches it on itself. The
tests that exercise the queued path monkeypatch `_queue_is_usable` to True and
also stub `_enqueue_job`, so they assert the routing without a backend.
"""
import pytest

try:
    import dnareport.web as web
except Exception:          # fastapi absent: the web tests skip themselves anyway
    web = None


@pytest.fixture(autouse=True)
def _no_real_queue_in_tests(monkeypatch):
    if web is not None:
        monkeypatch.setattr(web, "_queue_is_usable", lambda: False, raising=False)
    yield
