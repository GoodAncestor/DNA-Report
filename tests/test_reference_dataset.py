import csv,gzip,json
from pathlib import Path
import pytest
from dnareport.reference_dataset import pool_partitions,verify_inputs,sha256,PARTITIONS,REQUIRED,PREFIX,REFERENCE


def row(pos,cov,mod,other=0,code='m'):
    return f'chr1\t{pos}\t{pos+1}\t{code}\t{cov}\t.\t{pos}\t{pos+1}\t255,0,0\t{cov}\t0\t{mod}\t{cov-mod-other}\t{other}\t0\t0\t0\t0\n'


def inputs(tmp_path,contents):
    paths={}
    for group,text in zip(PARTITIONS,contents):
        p=tmp_path/f'{group}.bed';p.write_text(text);paths[group]=p
    return paths


def test_pool_counts_before_threshold_keeps_zero_and_missing(tmp_path):
    paths=inputs(tmp_path,[row(10,2,2)+row(20,2,0),row(10,8,0,2)+row(20,3,0),row(30,2,1)])
    out=tmp_path/'measurements.tsv.gz'
    sample=pool_partitions(paths,probe_map={'cg1':('chr1',10),'cg2':('chr1',20),'cg3':('chr1',30),'cg4':('chr1',40)},measurements_path=out)
    assert sample.betas=={'cg1':.2,'cg2':0.0}
    assert sample.coverage=={'cg1':10,'cg2':5,'cg3':2}
    assert sample.stats['low_coverage_probes']==1 and sample.stats['missing_probes']==1
    with gzip.open(out,'rt') as f:rows=list(csv.DictReader(f,delimiter='\t'))
    assert rows[0]['HP1_modified']=='2' and rows[0]['HP2_other_mod']=='2'
    assert rows[-1]['status']=='missing' and rows[-1]['valid_calls']==''


def test_duplicate_partition_target_and_invalid_counts_fail(tmp_path):
    paths=inputs(tmp_path,[row(10,5,1)*2,'',''])
    with pytest.raises(ValueError,match='Duplicate'):pool_partitions(paths,probe_map={'cg1':('chr1',10)})
    paths['1'].write_text(row(10,2,3))
    with pytest.raises(ValueError,match='valid-call'):pool_partitions(paths,probe_map={'cg1':('chr1',10)})
    with pytest.raises(ValueError,match='exactly'):pool_partitions({'1':paths['1']})


def test_other_modifications_not_added_to_5mc(tmp_path):
    paths=inputs(tmp_path,[row(10,5,1,3)+row(10,5,3,1,code='h'),'',''])
    sample=pool_partitions(paths,probe_map={'cg1':('chr1',10)})
    assert sample.betas['cg1']==.2 and sample.coverage['cg1']==5
    assert sample.stats['partition_input_counts']['1']['other_modification_rows']==1


def test_manifest_requires_matched_complete_unchanged_objects(tmp_path):
    records=[]
    for name in REQUIRED:
        p=tmp_path/name;p.write_bytes(b'fixture')
        records.append({'local_name':name,'Key':PREFIX+name,'url':'https://ont-open-data.s3.amazonaws.com/'+PREFIX+name,'bytes':p.stat().st_size,'sha256':sha256(p)})
    m={'sample_id':'HG002','dataset':'giab_2025.01','flowcell':'PAW70337','basecalling':'sup','reference_from_vcf_header':REFERENCE,'files':records}
    mp=tmp_path/'manifest.json';mp.write_text(json.dumps(m))
    assert len(verify_inputs(tmp_path,mp)[1])==4
    (tmp_path/REQUIRED[0]).write_bytes(b'changed')
    with pytest.raises(ValueError,match='integrity'):verify_inputs(tmp_path,mp)
    m['flowcell']='other';mp.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='selected'):verify_inputs(tmp_path,mp)


def test_archive_preserves_all_matches_when_display_is_limited(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from dnareport.reference_dataset import annotate_methylation
    db=tmp_path/'ewas.db'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE findings(cpg TEXT, trait TEXT, gene TEXT, beta REAL, se REAL, p REAL, n INTEGER, tissue TEXT, methylation_array TEXT, chrpos TEXT, pmid TEXT, efo TEXT)')
        con.executemany('INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',[
            ('cg1','Trait A','GENE',.1,.01,1e-10,2000,'Blood','EPIC','chr1:10','1',None),
            ('cg1','Trait A','GENE',.2,.02,1e-8,1200,'Blood','EPIC','chr1:10','2',None),
            ('cg2','Trait B','GENE',-.1,.01,1e-5,300,'Blood','EPIC','chr1:20','3',None),
            ('cg3','Unmeasured','GENE',.3,.01,1e-20,5000,'Blood','EPIC','chr1:30','4',None)])
    out=tmp_path/'associations.gz'
    findings,stats=annotate_methylation(SimpleNamespace(betas={'cg1':0.0,'cg2':.4},coverage={'cg1':10,'cg2':20}),db,out,max_traits=1)
    assert stats['matched_association_rows']==3 and stats['mirror_rows']==4
    assert stats['traits_with_associations']==2 and stats['displayed_trait_representatives']==1
    assert len(findings)==2 and {f.marker for f in findings}=={'cg1'}
    with gzip.open(out,'rt') as f:rows=list(csv.DictReader(f,delimiter='\t'))
    assert len(rows)==3 and rows[0]['HG002_5mc_fraction']=='0.0'


def test_variant_filters_and_fail_closed_mirror(tmp_path,monkeypatch):
    import geneask.annotators.clinvar_mirror as mirror
    from dnareport.reference_dataset import screen_variants
    vcf=tmp_path/'calls.vcf'
    header='##fileformat=VCFv4.2\n##reference='+REFERENCE+'\n'
    header+='##contig=<ID=chr1,length=10000>\n##contig=<ID=chrX,length=10000>\n'
    header+='##FILTER=<ID=LowQual,Description="Low quality">\n'
    for name,kind in [('GT','String'),('DP','Integer'),('GQ','Integer')]:
        header+=f'##FORMAT=<ID={name},Number=1,Type={kind},Description="{name}">\n'
    header+='#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n'
    rows=[('chr1',10,'G','PASS','0/1:20:40'),('chr1',20,'G','PASS','0/1:9:40'),
          ('chrX',30,'G','PASS','0/1:20:40'),('chr1',40,'G','LowQual','0/1:20:40'),
          ('chr1',50,'G','PASS','./1:20:40'),('chr1',60,'G','PASS','0/0:20:40'),
          ('chr1',70,'G'*51,'PASS','0/1:20:40')]
    vcf.write_text(header+''.join(f'{c}\t{p}\t.\tA\t{a}\t60\t{fil}\t.\tGT:DP:GQ\t{gt}\n' for c,p,a,fil,gt in rows))
    record={'gene':'TEST','clinical_significance':'Pathogenic','gold_stars':2,'conditions':['Test condition']}
    monkeypatch.setattr(mirror,'lookup_from_mirror',lambda ids: {'1-10-A-G':record} if '1-10-A-G' in set(ids) else {})
    findings,stats=screen_variants(vcf,batch_size=1)
    assert len(findings)==1 and findings[0].source=='clinvar_mirror'
    assert stats['input_records']==7 and stats['retained_records']==1
    assert all(stats[k]==1 for k in ['excluded_quality','excluded_non_autosomal','excluded_filter','excluded_genotype','reference_genotype','excluded_non_small_alleles'])
    # A mirror failure after preflight must not switch to the bundled panel.
    calls=iter([{},None])
    monkeypatch.setattr(mirror,'lookup_from_mirror',lambda ids: next(calls))
    with pytest.raises(ValueError,match='refusing partial'):screen_variants(vcf,batch_size=1)
    vcf.write_text(vcf.read_text().replace(REFERENCE,'wrong-reference'))
    monkeypatch.setattr(mirror,'lookup_from_mirror',lambda ids: {})
    with pytest.raises(ValueError,match='header'):screen_variants(vcf)


def test_reference_interpretations_and_labels():
    from dnareport.nanopore_demo import build_demo
    from dnareport.reference_dataset import reference_interpretations,reference_labels
    result=reference_interpretations(build_demo())
    assert not result.actions and not result.clocks and not result.trait_scores
    assert all(not f.promoted and 'HG002' in f.interpretation.found for f in result.findings)
    assert 'your reading' not in reference_labels('your reading; your position; not your tissue')


def test_public_artifact_readiness_and_routes(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from dnareport import web
    from dnareport.reference_demo import FILES,ready
    from dnareport.reference_dataset import write_artifact_index
    monkeypatch.setenv('DNAREPORT_HG002_DEMO_DIR',str(tmp_path))
    monkeypatch.setattr(web,'API_KEYS',{})
    client=TestClient(web.app)
    assert client.get('/demo/hg002').status_code==503
    assert 'hg002' not in client.get('/health').json()['demos']
    for name in FILES:
        if name!='artifact-index.json':(tmp_path/name).write_text('{}' if name.endswith('.json') else 'HG002 fixture')
    index=write_artifact_index(tmp_path)
    assert ready(tmp_path) and len(index['files'])==7
    assert client.get('/demo/hg002').status_code==200
    assert client.get('/demo/hg002?format=json').json()=={}
    assert client.get('/demo/hg002?format=markdown').headers['content-type'].startswith('text/markdown')
    assert client.get('/demo/hg002?format=bad').status_code==400
    assert 'href="/demo/hg002"' in client.get('/').text
    assert 'hg002' in client.get('/health').json()['demos']
    assert client.get('/demo/hg002/files/provenance.json').headers['content-disposition'].startswith('attachment')
    assert client.get('/demo/hg002/files/private.json').status_code==404
    (tmp_path/'report.html').write_text('truncated')
    assert not ready(tmp_path) and client.get('/demo/hg002').status_code==503
    (tmp_path/'artifact-index.json').write_text('[]')
    assert not ready(tmp_path)
