import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from biocore.providers.base import Finding, Tier, Category
from dnareport import predictions as p, web
from dnareport.explorer import build_ai_demo
from dnareport.report import compose_result_views, report_html


def test_public_demo_has_verified_outputs_and_no_network(monkeypatch):
    from geneask.annotators import alphagenome_vep as ag, alphamissense as am
    def forbidden(*a, **kw):
        raise AssertionError('Demo must not call a model or live mirror')
    monkeypatch.setattr(ag, 'annotate_findings', forbidden)
    monkeypatch.setattr(am, 'annotate_findings', forbidden)
    result = build_ai_demo()
    assert len(result.findings) == 3
    views = compose_result_views(result)
    assert 'AlphaMissense research prediction' in views['markdown']
    assert '0.9927' in views['markdown']
    assert views['json']['scan_stats']['ai_predictions']['alphagenome']['scored'] == 1
    client = TestClient(web.app)
    for fmt in ('', '?format=json', '?format=markdown'):
        response = client.get('/demo/ai'+fmt)
        assert response.status_code == 200
    page=client.get('/demo/ai').text
    assert 'view=site&predictions=only' in page
    assert 'not a person' in page
    assert '/explore' in client.get('/').text


@pytest.mark.parametrize('value', ['1-0-A-T', '1-12-A-A', '1-12-N-T', '1-12-A-<DEL>', '1-999999999-A-T','<script>','1-2-A-T;evil'])
def test_variant_rejects_invalid_input(value):
    with pytest.raises(ValueError): p.normalize_variant(value)


def test_variant_normalization():
    assert p.normalize_variant('chr22:36201698:a:c') == '22-36201698-A-C'


def test_explorer_get_only_prefills_and_escapes(monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('GET must not analyze')
    monkeypatch.setattr(p, 'explore_variant', forbidden)
    r=TestClient(web.app).get('/explore', params={'variant':'"><script>alert(1)</script>'})
    assert r.status_code == 200
    assert 'value=""><script>' not in r.text
    assert 'no-store' in r.headers['cache-control']


def test_lookup_post_validates_and_exports(monkeypatch):
    seen=[]
    def fake(variant, *, predict):
        seen.append((variant,predict))
        return build_ai_demo()
    from dnareport import prediction_job
    monkeypatch.setattr(p,'explore_variant',fake)
    monkeypatch.setattr(prediction_job,'bounded_payload',prediction_job.payload)
    client=TestClient(web.app)
    for payload in ({'variant':'1-1-A-T'}, {'variant':'1-1-A-T','reference_build':'GRCh37'}, {'variant':'1-1-A-T','reference_build':'GRCh38','predict':'yes'}):
        assert client.post('/api/variant',json=payload).status_code==400
    response=client.post('/api/variant',json={'variant':'chr22-36201698-A-C','reference_build':'GRCh38','predict':True})
    assert response.status_code==200
    assert seen==[('22-36201698-A-C',True)]
    assert set(response.json())=={'html','report','markdown'}
    assert response.headers['cache-control']=='no-store'


def call(vid,**overrides):
    return {'variant_id':vid,'filter':'PASS','zygosity':'het','dp':20,'gq':40,**overrides}


def test_novel_selection_quality_bound_and_clinvar_completeness(monkeypatch):
    from geneask.annotators import clinvar_mirror as cv
    monkeypatch.setattr(p,'declared_grch38',lambda path:True)
    monkeypatch.setattr(cv,'lookup_from_mirror',lambda ids:{'1-10-A-T':{'clinical_significance':'Benign'}})
    calls=[call('1-10-A-T',gq=99),call('1-11-A-T',gq=90),call('1-12-A-T',gq=80),call('1-13-A-T',dp=2),call('X-14-A-T'),call('1-15-A-T',zygosity='unknown')]
    fs,status=p.select_novel_candidates(calls,[],'unused',limit=2)
    assert [f.marker for f in fs]==['1-11-A-T']
    assert status['quality_eligible']==3 and status['not_screened']==1
    assert fs[0].tier==Tier.SPECULATIVE and not fs[0].promoted
    monkeypatch.setattr(cv,'lookup_from_mirror',lambda ids:None)
    assert p.select_novel_candidates(calls,[],'unused')[1]['status']=='clinvar_mirror_unavailable'
    monkeypatch.setattr(p,'declared_grch38',lambda path:False)
    assert p.select_novel_candidates(calls,[],'unused')[1]['status']=='reference_not_declared_grch38'


def test_enrichment_offline_never_calls_alphagenome(monkeypatch):
    from geneask.annotators import alphamissense as am, alphagenome_vep as ag
    def local(fs, status): status.update(status='ready',scored=0)
    def forbidden(*a,**k): raise AssertionError('offline API call')
    monkeypatch.setattr(am,'annotate_findings',local)
    monkeypatch.setattr(ag,'annotate_findings',forbidden)
    assert p.enrich([],offline=True)['alphagenome']['status']=='offline'


def test_explorer_does_not_claim_genotype(monkeypatch):
    from geneask.interpret import clinvar_screen as cs
    from geneask.annotators import clinvar_mirror as cv, gnomad_mirror as gm
    monkeypatch.setattr(cs,'screen_findings',lambda *a:[])
    monkeypatch.setattr(cv,'lookup_from_mirror',lambda *a:{})
    monkeypatch.setattr(gm,'lookup_many',lambda *a:{})
    monkeypatch.setattr(p,'enrich',lambda *a,**k:{'alphagenome':{'status':'offline'},'alphamissense':{'status':'unavailable'}})
    result=p.explore_variant('1-12-A-T')
    assert result.findings[0].detail.get('genotype') is None
    assert result.findings[0].detail['research_candidate'] is True
    assert not result.findings[0].detail.get('novel_candidate')
    assert 'no sample genotype' in ' '.join(result.notes)
    assert not result.actions and not result.read_first


def test_vus_is_selected_even_without_pathogenic_classification(monkeypatch):
    from geneask.annotators import clinvar_mirror as cv
    monkeypatch.setattr(p,'declared_grch38',lambda path:True)
    monkeypatch.setattr(cv,'lookup_from_mirror',lambda ids:{'1-10-A-T':{'clinical_significance':'Uncertain significance','gene':'TEST','gold_stars':1}})
    fs,status=p.select_novel_candidates([call('1-10-A-T'),call('1-11-A-T')],[],'unused')
    assert len(fs)==2 and status['uncertain_selected']==1 and status['uncatalogued_selected']==1
    assert fs[0].detail['clinical_significance']=='Uncertain significance'
    assert fs[0].detail['novel_candidate'] is False


def test_hung_prediction_is_killed_and_local_evidence_retained(monkeypatch):
    import subprocess
    from dnareport import prediction_job as job
    def timeout(*a,**kw):
        assert kw['timeout']==45
        raise subprocess.TimeoutExpired(a[0],45)
    monkeypatch.setattr(job.subprocess,'run',timeout)
    monkeypatch.setattr(job,'payload',lambda variant,**kw:{'variant':variant,**kw})
    assert job.bounded_payload('1-10-A-T',True)['failure']=='timeout'


def test_explorer_keeps_full_clinvar_record(monkeypatch):
    from geneask.annotators import clinvar_mirror as cv
    rec={'clinical_significance':'Uncertain significance','gene':'TEST','gold_stars':2,'conditions':['Condition'],'clinvar_variation_id':'123'}
    monkeypatch.setattr(cv,'lookup_from_mirror',lambda ids:{'1-10-A-T':rec})
    monkeypatch.setattr(p,'enrich',lambda *a,**k:{'alphagenome':{'status':'offline'},'alphamissense':{'status':'unavailable'}})
    f=p.explore_variant('1-10-A-T').findings[0]
    assert f.detail['conditions']==['Condition'] and f.detail['gold_stars']==2
    assert f.link.endswith('/123/')
