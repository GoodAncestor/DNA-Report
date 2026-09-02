from types import SimpleNamespace

from dnareport.detect import InputKind
from dnareport.orchestrate import _run_methylask


def test_cap_is_written_as_note_and_limit(tmp_path, monkeypatch):
    path = tmp_path / "betas.csv"
    rows = ["probe,sample"] + [f"cg{index:08d},0.5" for index in range(75)]
    path.write_text("\n".join(rows) + "\n")
    seen = {}

    def fake_annotate(_registry, markers):
        seen["n"] = len(markers)
        return SimpleNamespace(all_findings=lambda: [], provider_status=[])

    monkeypatch.setattr("biocore.providers.registry.Registry.annotate", fake_annotate)
    monkeypatch.setattr("methylask.clocks.run_all", lambda *args, **kwargs: [])
    monkeypatch.setattr("dnareport.orchestrate._reference_findings", lambda *a, **k: [])
    notes, stats = [], {}
    _run_methylask(
        str(path), InputKind.BETA_MATRIX, notes=notes, scan_stats=stats,
        max_markers=40,
    )
    assert seen["n"] == 40
    assert any("first 40 of 75" in note for note in notes)
    assert stats["limits"]["methylation_markers"] == {"shown": 40, "found": 75}
