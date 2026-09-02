from biocore.providers.base import Health, ProviderStatus
from dnareport.pages import empty_report_page


def test_empty_page_names_unavailable_providers():
    html = empty_report_page(
        kind_label="VCF genome", notes=[], statuses=[
            ProviderStatus(
                name="clinvar_mirror", health=Health.UNAVAILABLE,
                note="ClinVar mirror schema v2 requires a rebuild.",
            ),
            ProviderStatus(name="gwas_catalog", health=Health.OK),
        ],
    )
    assert "We could not use 1 of 2 reference databases" in html
    assert "clinvar_mirror" in html
    assert "ClinVar mirror schema v2 requires a rebuild" in html
    assert "parsed cleanly and was screened" not in html


def test_empty_page_with_healthy_providers_says_screened():
    html = empty_report_page(
        kind_label="VCF genome", notes=[], statuses=[
            ProviderStatus(name="gwas_catalog", health=Health.OK),
        ],
    )
    assert "We screen your file against 1 reference database" in html


def test_scan_counts_use_input_markers_and_provider_records(tmp_path):
    from dnareport.detect import InputKind
    from dnareport.orchestrate import ReportResult, _scan_stats

    path = tmp_path / "input.txt"
    path.write_text("data")
    result = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    result.scan_stats = {"markers_scanned": 75}
    result.provider_status = [
        ProviderStatus(
            name="clinvar_panel_157", health=Health.OK, record_count=393806,
        ),
        ProviderStatus(name="gwas_catalog", health=Health.UNAVAILABLE),
    ]
    stats = _scan_stats(str(path), result)
    assert stats["markers_scanned"] == 75
    assert stats["local_dbs_queried"] == ["clinvar_panel_157", "gwas_catalog"]
    assert stats["reference_records_scanned"] == 393806
