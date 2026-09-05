import json

import pytest

from dnareport import explain
from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult
from tests.test_explain_prompt import _f


GOOD = (
    "The change sits in BRCA2 [BRCA2]. ClinVar classifies it as likely pathogenic "
    "[HBOC]. Confirm it with a clinical test."
)


class OpenAIStub:
    """An OpenAI-compatible responder at urllib's network boundary."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "body": json.loads(request.data),
                "timeout": timeout,
            }
        )
        document = {
            "choices": [{"message": {"content": f"<dive>{GOOD}</dive>"}}]
        }

        class Response:
            def read(self):
                return json.dumps(document).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()


class Fake:
    def __init__(self, text=GOOD, fail=False):
        self.text = text
        self.fail = fail
        self.calls = 0

    def draft(self, system, user, *, timeout):
        self.calls += 1
        if self.fail:
            raise RuntimeError("backend down")
        assert "C/CAG" not in user
        return self.text


def _result(n=1):
    result = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    result.findings = [_f() for _ in range(n)]
    for index, finding in enumerate(result.findings):
        finding.marker = f"13-{index}-A-G"
    result.read_first = list(result.findings)
    result.notes = []
    result.scan_stats = {}
    return result


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DNAREPORT_EXPLAIN_ENABLED", "1")
    monkeypatch.setenv("DNAREPORT_EXPLAIN_CACHE_DB", str(tmp_path / "explain.db"))
    monkeypatch.setenv("DNAREPORT_EXPLAIN_MODEL", "fake-model")
    return tmp_path


def test_drafts_promoted_only_and_caches(env, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(
        explain,
        "select_backend",
        lambda job_backend=None: ("openai_compat", "fake-model", fake),
    )
    result = _result(2)
    extra = _f()
    extra.marker = "rs-not-promoted"
    extra.promoted = False
    result.findings.append(extra)

    out = explain.explain_promoted(result)

    assert out == {"drafted": 2, "cached": 0, "rejected": 0, "skipped": 0}
    assert all(finding.deeper_dive == GOOD for finding in result.read_first)
    assert extra.deeper_dive is None
    assert result.read_first[0].deeper_dive_meta["model"] == "fake-model"
    out2 = explain.explain_promoted(_result(2))
    assert out2["cached"] == 2 and fake.calls == 2


def test_rejected_draft_is_not_cached_and_says_why(env, monkeypatch):
    fake = Fake(text="You will develop cancer. [BRCA2]")
    monkeypatch.setattr(
        explain,
        "select_backend",
        lambda job_backend=None: ("openai_compat", "fake-model", fake),
    )
    result = _result(1)

    out = explain.explain_promoted(result)

    assert out["rejected"] == 1 and result.read_first[0].deeper_dive is None
    assert result.read_first[0].deeper_dive_meta["rejected_reason"].startswith(
        "forbidden phrase"
    )
    assert any("deeper dive" in note.lower() for note in result.notes)
    assert explain.explain_promoted(_result(1))["cached"] == 0


def test_backend_failure_and_time_budget(env, monkeypatch):
    fake = Fake(fail=True)
    monkeypatch.setattr(
        explain,
        "select_backend",
        lambda job_backend=None: ("openai_compat", "fake-model", fake),
    )
    out = explain.explain_promoted(_result(1))
    assert out["rejected"] == 1

    monkeypatch.setenv("DNAREPORT_EXPLAIN_TIME_BUDGET_S", "0")
    good = Fake()
    monkeypatch.setattr(
        explain,
        "select_backend",
        lambda job_backend=None: ("openai_compat", "fake-model", good),
    )
    out = explain.explain_promoted(_result(2))
    assert out["skipped"] == 2 and good.calls == 0


def test_cache_only_never_calls_a_backend(env, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(
        explain,
        "select_backend",
        lambda job_backend=None: ("openai_compat", "fake-model", fake),
    )
    out = explain.explain_promoted(_result(1), cache_only=True)
    assert out == {"drafted": 0, "cached": 0, "rejected": 0, "skipped": 1}
    assert fake.calls == 0


def test_admin_backends_are_gated(env, monkeypatch, tmp_path):
    monkeypatch.setenv("DNAREPORT_EXPLAIN_BACKEND", "openai_compat")
    monkeypatch.setenv("DNAREPORT_EXPLAIN_BASE_URL", "http://127.0.0.1:9/v1")
    key_file = tmp_path / "key"
    key_file.write_text("secret")
    key_file.chmod(0o600)
    monkeypatch.setenv("DNAREPORT_EXPLAIN_KEY_FILE", str(key_file))
    assert explain.select_backend("codex_cli") is not None
    assert explain.select_backend("codex_cli")[0] == "openai_compat"
    monkeypatch.setenv("DNAREPORT_EXPLAIN_ADMIN_BACKENDS", "1")
    assert explain.select_backend("codex_cli")[0] == "codex_cli"
    assert explain.select_backend("claude_cli")[0] == "claude_cli"
    monkeypatch.setenv("DNAREPORT_EXPLAIN_ENABLED", "0")
    assert explain.select_backend(None) is None


def test_openai_compat_posts_chat_completion(monkeypatch, tmp_path):
    seen = {}

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return Response(
            json.dumps(
                {"choices": [{"message": {"content": "ok [BRCA2]"}}]}
            ).encode()
        )

    monkeypatch.setattr(explain.urllib.request, "urlopen", fake_urlopen)
    key_file = tmp_path / "key"
    key_file.write_text("old-secret\n")
    key_file.chmod(0o600)
    backend = explain.OpenAICompat(
        "http://h:8000/v1", "GLM-5.3-MLX-4bit", str(key_file)
    )
    key_file.write_text("secret\n")

    assert backend.draft("sys", "usr", timeout=5) == "ok [BRCA2]"
    assert seen["url"] == "http://h:8000/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["model"] == "GLM-5.3-MLX-4bit"
    assert seen["body"]["messages"][1]["content"] == "usr"
    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["max_tokens"] >= 300


def test_openai_compat_refuses_a_group_readable_key_file(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("secret")
    key_file.chmod(0o640)
    backend = explain.OpenAICompat("http://h:8000/v1", "model", str(key_file))

    with pytest.raises(PermissionError, match="0600"):
        backend.draft("sys", "usr", timeout=5)


@pytest.mark.parametrize("key", [None, "stub-secret"])
def test_openai_compat_end_to_end_against_in_process_stub(
    env, monkeypatch, tmp_path, key
):
    monkeypatch.setenv("DNAREPORT_EXPLAIN_BACKEND", "openai_compat")
    monkeypatch.setenv("DNAREPORT_EXPLAIN_MODEL", "stub-model")
    monkeypatch.delenv("DNAREPORT_EXPLAIN_KEY_FILE", raising=False)
    if key:
        key_file = tmp_path / "stub-key"
        key_file.write_text(f"{key}\n")
        key_file.chmod(0o600)
        monkeypatch.setenv("DNAREPORT_EXPLAIN_KEY_FILE", str(key_file))

    stub = OpenAIStub()
    monkeypatch.setattr(explain.urllib.request, "urlopen", stub)
    monkeypatch.setenv("DNAREPORT_EXPLAIN_BASE_URL", "http://stub.invalid/v1")

    result = _result()
    outcome = explain.explain_promoted(result)

    assert outcome == {
        "drafted": 1,
        "cached": 0,
        "rejected": 0,
        "skipped": 0,
    }
    assert result.read_first[0].deeper_dive == GOOD
    assert result.read_first[0].deeper_dive_meta == {
        "backend": "openai_compat",
        "model": "stub-model",
        "prompt_version": explain.PROMPT_VERSION,
    }
    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request["url"] == "http://stub.invalid/v1/chat/completions"
    assert request["authorization"] == (f"Bearer {key}" if key else None)
    assert request["body"]["model"] == "stub-model"
    assert request["body"]["messages"][0]["role"] == "system"
    public_facts = json.loads(request["body"]["messages"][1]["content"])
    assert public_facts["marker"] == "13-0-A-G"
    assert "genotype" not in public_facts

    cached = explain.explain_promoted(_result())
    assert cached == {
        "drafted": 0,
        "cached": 1,
        "rejected": 0,
        "skipped": 0,
    }
    assert len(stub.requests) == 1


def test_disabled_explanation_gate_never_calls_in_process_stub(
    env, monkeypatch
):
    monkeypatch.setenv("DNAREPORT_EXPLAIN_ENABLED", "0")
    monkeypatch.setenv("DNAREPORT_EXPLAIN_MODEL", "stub-model")

    stub = OpenAIStub()
    monkeypatch.setattr(explain.urllib.request, "urlopen", stub)
    monkeypatch.setenv("DNAREPORT_EXPLAIN_BASE_URL", "http://stub.invalid/v1")

    assert explain.select_backend() is None
    assert explain.explain_promoted(_result()) == {
        "drafted": 0,
        "cached": 0,
        "rejected": 0,
        "skipped": 0,
    }
    assert stub.requests == []
