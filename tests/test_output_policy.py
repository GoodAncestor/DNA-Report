"""Output mode applies equally to fresh, cached, frozen and exported reports."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from biocore.providers.base import Finding, Tier, Category, Interpretation
from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult
from dnareport.explorer import build_ai_demo, explorer_html, guided_demo_html
from dnareport.report import report_html, compose_result_views
from dnareport.serialize import result_to_json
from dnareport.exports import report_markdown
from dnareport.output_policy import result_for_output
from dnareport import web


def result(local=False):
    atlas = {'provenance': 'alphagenome_atlas_local_avi' if local else 'alphagenome_atlas_api',
             'avi_score': 0.123456789, 'tracks': [{'scorer': 'AVI_SCORE', 'raw_score': 0.123456789}]}
    f = Finding('1-10-A-C', 'clinvar', 'Curated finding — AlphaGenome: SECRET_RESTRICTED',
                Tier.SPECULATIVE, [Category.CLINICAL],
                detail={'clinical_significance': 'Uncertain significance', 'gene': 'GENE',
                        'alphagenome': {'quantile_score': 0.987654321}, 'alphagenome_atlas': atlas},
                interpretation=Interpretation('SECRET_RESTRICTED', 'meaning', 'uncertain'),
                deeper_dive='SECRET_RESTRICTED')
    return ReportResult(kind=InputKind.VCF, engines=('geneask',), findings=[f], read_first=[f],
        notes=['AlphaGenome SECRET_RESTRICTED', 'Input reference is GRCh38.'],
        scan_stats={'ai_demo': {'raw_score': 'SECRET_RESTRICTED'}, 'ai_predictions': {'alphamissense': {'scored': 1}}})


def test_commercial_sanitizes_all_formats_without_mutating_research_result(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    original = result()
    views = compose_result_views(original)
    for output in (json.dumps(views), report_markdown(original), report_html(original), json.dumps(result_to_json(original))):
        assert 'SECRET_RESTRICTED' not in output
        assert '0.987654321' not in output
        assert '0.123456789' not in output
        assert 'Curated finding' in output
    assert original.findings[0].detail['alphagenome']['quantile_score'] == 0.987654321
    assert original.findings[0].deeper_dive == 'SECRET_RESTRICTED'
    assert views['json']['output_policy']['mode'] == 'commercial'
    assert 'Input reference is GRCh38.' in views['json']['notes']


def test_permissive_local_avi_survives_but_api_avi_does_not(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    permitted = result_to_json(result(local=True))
    assert permitted['findings'][0]['detail']['alphagenome_atlas']['avi_score'] == 0.123456789
    assert 'alphagenome' not in permitted['findings'][0]['detail']
    assert 'alphagenome_atlas' not in result_to_json(result())['findings'][0]['detail']


def test_demo_and_explorer_reflect_commercial_mode(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    demo = build_ai_demo()
    html = guided_demo_html(demo)
    assert '0.4998990297317505' not in html
    assert '0.9927' in json.dumps(result_to_json(demo))
    assert all(not f.detail.get('alphagenome_atlas') for f in demo.findings)
    page = explorer_html()
    assert 'id="atlas"' not in page and 'id="predict"' not in page
    assert 'Commercial use permitted' in page and 'Noncommercial use only' in page
    assert 'Google Output Terms' in page
    assert 'excluded in this output mode' in page


def test_noncommercial_labels_and_outputs_remain_available(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'noncommercial')
    doc = result_to_json(result())
    assert doc['findings'][0]['detail']['alphagenome']['quantile_score'] == 0.987654321
    assert doc['output_policy']['mode'] == 'noncommercial'
    datasets = {d['kind']: d for d in doc['output_policy']['datasets']}
    assert datasets['alphagenome_atlas_local_avi']['commercial_allowed']
    assert not datasets['alphagenome_atlas_api']['commercial_allowed']
    assert 'output_mode: noncommercial' in report_markdown(result())


@pytest.mark.parametrize('fmt,body', [('html','<html>old research score</html>'), ('json','{"findings":[]}'), ('md','---\nformat: old\n---\nsecret')])
def test_old_saved_artifacts_cannot_bypass_commercial_mode(monkeypatch, fmt, body):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    monkeypatch.setattr(web, '_r2_result_html', lambda *args: body)
    response = TestClient(web.app).get('/result/'+'a'*32, params={'format': fmt})
    assert response.status_code == 409
    assert 'old research score' not in response.text


def test_new_commercial_artifacts_are_claimable(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    artifacts = {'html': report_html(result()), 'json': json.dumps(result_to_json(result())), 'md': report_markdown(result())}
    monkeypatch.setattr(web, '_r2_result_html', lambda job, fmt: artifacts[fmt])
    for fmt in artifacts:
        assert TestClient(web.app).get('/result/'+'b'*32, params={'format': fmt}).status_code == 200


def test_demo_html_cache_is_partitioned_by_output_mode(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'noncommercial')
    assert web._cached_demo_html('license-test', lambda: 'restricted') == 'restricted'
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    assert web._cached_demo_html('license-test', lambda: 'permitted') == 'permitted'


def test_mcp_saved_report_is_blocked_without_compatible_policy(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    monkeypatch.setattr(web, '_r2_result_html', lambda *args: '{"findings":["SECRET_RESTRICTED"]}')
    response = web._read_report_for_mcp('c'*32)
    assert response['status'] == 'blocked'
    assert 'SECRET_RESTRICTED' not in json.dumps(response)


@pytest.mark.parametrize('route', ['/demo/hg002', '/demo/hg002?format=json', '/demo/hg002?format=md',
                                  '/demo/hg002/files/report.html', '/demo/hg002/files/report.json',
                                  '/demo/hg002/files/report.md', '/demo/hg002/files/HG002.variant-findings.json.gz'])
def test_frozen_hg002_report_downloads_respect_commercial_mode(monkeypatch, tmp_path, route):
    import gzip
    from dnareport import reference_demo
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    monkeypatch.setattr(reference_demo, 'directory', lambda *args: tmp_path)
    monkeypatch.setattr(reference_demo, 'ready', lambda *args: True)
    for fmt in ('html', 'json', 'md'):
        (tmp_path/f'report.{fmt}').write_text('SECRET_RESTRICTED')
    (tmp_path/'HG002.variant-findings.json.gz').write_bytes(gzip.compress(b'{"findings":["SECRET_RESTRICTED"]}'))
    response = TestClient(web.app).get(route)
    assert response.status_code == 409
    assert 'SECRET_RESTRICTED' not in response.text


@pytest.mark.parametrize('flag', ['atlas', 'predict'])
def test_commercial_api_cannot_be_overridden_by_request(monkeypatch, flag):
    from dnareport import prediction_job
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    def forbidden(*args, **kwargs):
        raise AssertionError('Restricted remote request must be rejected before execution')
    monkeypatch.setattr(prediction_job, 'bounded_payload', forbidden)
    response = TestClient(web.app).post('/api/variant', json={
        'variant': '19-44908684-T-C', 'reference_build': 'GRCh38', flag: True,
        'output_mode': 'noncommercial'})
    assert response.status_code == 403


def test_nested_outcome_contributions_are_rebuilt_from_permitted_findings(monkeypatch):
    from dnareport.outcomes import Outcome
    from dnareport.action_plan import Action
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    original = result()
    original.findings[0].detail['conditions'] = ['Curated condition']
    original.outcomes = [Outcome('condition:x', 'Curated condition', 'condition', findings=original.findings,
                        contributions=[{'value': 'SECRET_RESTRICTED'}],
                        actions=[Action('SECRET_RESTRICTED', 'why', 'AlphaGenome', None, None)])]
    original.actions = original.outcomes[0].actions
    doc = result_to_json(original)
    assert 'SECRET_RESTRICTED' not in json.dumps(doc)
    assert 'Curated condition' in json.dumps(doc)


def test_invalid_output_mode_is_explained_and_fails_closed(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commerical')
    doc = result_to_json(result())
    assert doc['output_policy']['mode'] == 'commercial'
    assert doc['output_policy']['configuration_valid'] is False
    assert doc['output_policy']['configuration_note']
    assert '0.987654321' not in json.dumps(doc)


def test_noncommercial_hg002_download_does_not_preload_large_artifact(monkeypatch, tmp_path):
    from dnareport import reference_demo
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'noncommercial')
    monkeypatch.setattr(reference_demo, 'directory', lambda *args: tmp_path)
    monkeypatch.setattr(reference_demo, 'ready', lambda *args: True)
    def forbidden(*args, **kwargs):
        raise AssertionError('FileResponse must stream without a pre-read in research mode')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    response = web.hg002_demo_file('HG002.variant-findings.json.gz')
    assert str(response.path).endswith('HG002.variant-findings.json.gz')


def test_commercial_preserves_alphamissense_scores_and_attribution(monkeypatch):
    monkeypatch.setenv('DNAREPORT_OUTPUT_MODE', 'commercial')
    original = result(local=True)
    original.findings[0].detail['alphamissense'] = {'pathogenicity': 0.314159, 'class': 'ambiguous'}
    doc = result_to_json(original)
    assert doc['findings'][0]['detail']['alphamissense']['pathogenicity'] == 0.314159
    am = next(x for x in doc['output_policy']['datasets'] if x['kind'] == 'alphamissense')
    assert am['commercial_allowed'] and am['id'] == 'CC-BY-4.0'
