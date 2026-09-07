"""The public demo is reproducible without caller tools, models or annotation I/O."""
import json
import socket
import subprocess
import urllib.request

import pytest
from fastapi.testclient import TestClient

from dnareport import web
from dnareport.nanopore_demo import build_demo
from dnareport.report import compose_result_views, report_html


@pytest.fixture
def no_external_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('A demo must not access network, models, callers or live mirrors')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    from methylask import clocks
    from methylask.providers.ewas_catalog import EwasCatalogProvider
    from geneask.annotators import pharmcat, clinvar_mirror
    monkeypatch.setattr(clocks, 'run_all', forbidden)
    monkeypatch.setattr(pharmcat, 'call_diplotypes', forbidden)
    monkeypatch.setattr(EwasCatalogProvider, 'get_many', forbidden)
    monkeypatch.setattr(clinvar_mirror, 'lookup_from_mirror', forbidden)
    web._DEMO_HTML_CACHE.pop('nanopore', None)


def test_demo_parses_both_streams_and_distinguishes_zero_missing_and_low_depth(no_external_work):
    result = build_demo()
    assert {f.detail['modality'] for f in result.findings} == {'genome', 'methylome'}
    assert result.clocks == []
    assert all(f.source != 'marker_reference' for f in result.findings)
    meta = result.scan_stats['nanopore']
    assert meta['sample_id'] == 'ONT-DEMO-SYNTHETIC'
    assert meta['demo']['synthetic'] is True
    assert meta['methylation']['mapped_probes'] == 2
    assert meta['methylation']['missing_probes'] == 1
    assert meta['methylation']['low_coverage_probes'] == 1
    rows = {r['probe']: r for r in meta['demo_measurements']}
    assert rows['cg03636183']['fraction'] == 0.0
    assert rows['cg03636183']['valid_reads'] == 10
    assert rows['cg16867657']['fraction'] is None and rows['cg16867657']['valid_reads'] == 2
    assert rows['cg19693031']['fraction'] is None and rows['cg19693031']['valid_reads'] is None
    methyl = next(f for f in result.findings if f.detail['modality'] == 'methylome')
    assert methyl.detail['your reading'] == .65  # 13/20, not 13/18
    assert methyl.pmids == ['23691101']
    variant = next(f for f in result.findings if f.detail['modality'] == 'genome')
    assert variant.detail['dp'] == 32 and variant.detail['gq'] == 60
    assert variant.link.endswith('/421014/')
    assert not result.scan_stats['live_apis_called']
    assert 'diplotypes are withheld' in ' '.join(result.notes)
    for f in result.findings:
        assert f.detail['measurement_origin'] == 'synthetic_demo'
        assert f.interpretation.found.startswith('Synthetic example.')
        assert 'called from native ONT reads' not in f.interpretation.how_sure
        assert 'Your reading comes from native ONT sequencing' not in f.interpretation.how_sure


def test_demo_exports_are_deterministic_and_labelled(no_external_work):
    first = compose_result_views(build_demo())
    second = compose_result_views(build_demo())
    assert first == second
    for serialized in (json.dumps(first['json']), first['markdown'], report_html(build_demo())):
        assert 'Synthetic Nanopore demo' in serialized
        assert 'no person was sequenced' in serialized
        assert 'ONT-DEMO-SYNTHETIC' in serialized
        assert 'withheld' in serialized
    assert '0.00 from 10 valid reads' in report_html(build_demo())


def test_public_routes_exports_fixtures_and_landing(no_external_work, monkeypatch):
    monkeypatch.setattr(web, 'API_KEYS', {})
    client = TestClient(web.app)
    assert client.get('/demo/nanopore').status_code == 200
    response = client.get('/demo/nanopore?format=json')
    assert response.status_code == 200
    assert response.json()['scan_stats']['nanopore']['demo']['synthetic']
    assert client.get('/demo/nanopore?format=markdown').headers['content-type'].startswith('text/markdown')
    for name in ('calls.vcf', 'calls.bedmethyl', 'reference.json', 'README.md'):
        response = client.get('/demo/nanopore/files/' + name)
        assert response.status_code == 200 and response.content
        assert 'attachment' in response.headers['content-disposition']
    assert client.get('/demo/nanopore/files/unknown').status_code == 404
    assert client.get('/demo/nanopore?format=unknown').status_code == 400
    landing = client.get('/').text
    assert 'href="/demo/nanopore"' in landing and 'SPECIMEN 05 · SYNTHETIC' in landing
    assert 'All four are real' not in landing
    assert 'nanopore' in client.get('/health').json()['demos']
