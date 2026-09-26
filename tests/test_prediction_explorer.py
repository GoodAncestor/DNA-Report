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
    def fake(variant, *, predict, atlas=False):
        seen.append((variant,predict,atlas))
        return build_ai_demo()
    from dnareport import prediction_job
    monkeypatch.setattr(p,'explore_variant',fake)
    monkeypatch.setattr(prediction_job,'bounded_payload',prediction_job.payload)
    client=TestClient(web.app)
    for payload in ({'variant':'1-1-A-T'}, {'variant':'1-1-A-T','reference_build':'GRCh37'}, {'variant':'1-1-A-T','reference_build':'GRCh38','predict':'yes'}):
        assert client.post('/api/variant',json=payload).status_code==400
    response=client.post('/api/variant',json={'variant':'chr22-36201698-A-C','reference_build':'GRCh38','predict':True})
    assert response.status_code==200
    assert seen==[('22-36201698-A-C',True,False)]
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
    killed=[]
    class Child:
        pid=987654
        calls=0
        def communicate(self, value=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                assert timeout == 45
                raise subprocess.TimeoutExpired('prediction',45)
            return '', ''
        def kill(self): killed.append(self.pid)
    child=Child()
    monkeypatch.setattr(job.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(job.os,'killpg',lambda pid,sig:killed.append(pid))
    monkeypatch.setattr(job,'payload',lambda variant,**kw:{'variant':variant,**kw})
    result=job.bounded_payload('1-10-A-T',atlas=True)
    assert result['failure']=='timeout'
    assert result['failed_models']==['alphagenome_atlas']
    assert killed==[child.pid] and child.calls==2



def test_explorer_keeps_full_clinvar_record(monkeypatch):
    from geneask.annotators import clinvar_mirror as cv
    rec={'clinical_significance':'Uncertain significance','gene':'TEST','gold_stars':2,'conditions':['Condition'],'clinvar_variation_id':'123'}
    monkeypatch.setattr(cv,'lookup_from_mirror',lambda ids:{'1-10-A-T':rec})
    monkeypatch.setattr(p,'enrich',lambda *a,**k:{'alphagenome':{'status':'offline'},'alphamissense':{'status':'unavailable'}})
    f=p.explore_variant('1-10-A-T').findings[0]
    assert f.detail['conditions']==['Condition'] and f.detail['gold_stars']==2
    assert f.link.endswith('/123/')


def test_atlas_request_is_separate_from_fresh_inference(monkeypatch):
    from dnareport import prediction_job
    calls=[]
    monkeypatch.setattr(prediction_job, 'bounded_payload', lambda variant, **kw: calls.append(kw) or {})
    client=TestClient(web.app)
    assert client.post('/api/variant', json={'variant':'1-10-A-T', 'reference_build':'GRCh38', 'atlas':True}).status_code==200
    assert calls==[{'predict':False, 'atlas':True}]
    assert client.post('/api/variant', json={'variant':'1-10-A-T', 'reference_build':'GRCh38', 'atlas':'yes'}).status_code==400


def test_local_avi_prioritizes_impact_after_call_quality_gate(monkeypatch):
    from geneask.annotators import atlas_avi, clinvar_mirror as cv
    monkeypatch.setattr(p, 'declared_grch38', lambda path:True)
    monkeypatch.setattr(cv, 'lookup_from_mirror', lambda ids:{})
    monkeypatch.setattr(atlas_avi, 'local_status', lambda:{'status':'ready','available':True})
    def local(ids, *, status):
        status['available']=True
        return {vid:{'avi_score':score, 'tracks':[], 'status':'complete'}
                for vid,score in [('1-11-A-T',.95),('1-12-A-T',.5),('1-13-A-T',.99)] if vid in ids}
    monkeypatch.setattr(atlas_avi,'lookup_many',local)
    fs,coverage=p.select_novel_candidates([call('1-10-A-T',gq=99),call('1-11-A-T',gq=30),
                                          call('1-12-A-T',gq=80),call('1-13-A-T',dp=2)],[],'unused',limit=2)
    assert [f.marker for f in fs]==['1-11-A-T','1-12-A-T']
    assert fs[0].detail['alphagenome_atlas']['avi_score']==.95
    assert coverage['atlas_ranking']['scored']==2 and coverage['quality_eligible']==3
    assert 'AVI descending' in coverage['selection']


def test_atlas_local_partial_is_usable_and_explicit():
    from biocore.providers.base import Health
    row=p.provider_statuses({'alphagenome_atlas':{'status':'partial','scored':1}})[-1]
    assert row.health==Health.OK and 'partial' in row.note


def test_atlas_requires_grch38_and_offline_never_enables_remote(monkeypatch):
    from geneask.annotators import alphagenome_atlas as atlas, alphamissense as am
    monkeypatch.setattr(am, 'annotate_findings', lambda *a, **kw:None)
    calls=[]
    def annotate(fs, *, offline, status):
        calls.append(([f.marker for f in fs], offline))
        status.update(status='complete', scored=0)
    monkeypatch.setattr(atlas, 'annotate_findings', annotate)
    unknown=Finding(marker='1-10-A-T',source='variant_lookup',description='test',tier=Tier.SPECULATIVE, categories=[Category.CLINICAL],detail={})
    known=Finding(marker='1-11-A-T',source='variant_lookup',description='test',tier=Tier.SPECULATIVE,categories=[Category.CLINICAL],detail={'reference_build':'GRCh38'})
    status=p.enrich([unknown,known],offline=True)
    assert calls==[(['1-11-A-T'],True)]
    assert status['alphagenome_atlas']['reference_skipped']==1
    assert p.enrich([unknown],offline=True)['alphagenome_atlas']['status']=='reference_not_declared_grch38'
