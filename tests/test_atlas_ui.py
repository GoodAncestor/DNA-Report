"""Atlas UI uses real frozen evidence and an explicit remote-lookup control."""
from dnareport.explorer import build_ai_demo, explorer_html, guided_demo_html
from dnareport.report import compose_result_views


def test_demo_atlas_result_is_verified_and_visible():
    result = build_ai_demo()
    example = next(f for f in result.findings if f.marker == '19-44908684-T-C')
    atlas = example.detail['alphagenome_atlas']
    assert atlas['tracks'][0]['raw_score'] == 0.4998990297317505
    assert atlas['tracks'][0]['quantile_score'] == 0.9869896173477173
    features = [t for t in atlas['tracks'] if t['scorer'] == 'AVI_SCORE_FEATURE_IMPORTANCE']
    assert [t['name'] for t in features] == ['CACTUS_241_WAY', 'PHASTCONS_470_WAY', 'ALPHAMISSENSE', 'MAX_ABS_POLYADENYLATION', 'MERGED_SPLICING']
    assert features[0]['raw_score'] == 0.2944031357765198
    assert features[1]['raw_score'] == 0.14471329748630524
    assert features[2]['raw_score'] == 0.02913823164999485
    assert atlas['n_tracks'] == 19
    assert atlas['queried_at'] == '2026-09-26T07:20:47.931514+00:00'
    assert 'not independent confirmations' in example.description
    page = guided_demo_html(result)
    assert 'AlphaGenome Atlas / AVI' in page
    assert 'Scorer: AVI_SCORE; Name: AVI_SCORE; Raw score: 0.4998990297317505' in page
    assert 'Name: CACTUS_241_WAY; Raw score: 0.2944031357765198' in page
    assert 'frozen replay makes no API request' in page
    exports = compose_result_views(result)
    assert 'AlphaGenome Atlas / AVI research prediction' in exports['markdown']
    assert 'name: CACTUS_241_WAY; raw score: 0.2944031357765198' in exports['markdown']
    assert 'avi score: 0.4998990297317505' in exports['markdown']
    assert 'queried at: 2026-09-26T07:20:47.931514+00:00' in exports['markdown']
    for text in (page, exports['markdown']):
        assert "higher than about 98.7% of the model's reference distribution" in text
        assert 'conservation across 241 mammals (about 59% of the score)' in text
        assert 'not approved for, any clinical use' in text


def test_live_shaped_atlas_export_retains_cache_and_partial_evidence():
    result = build_ai_demo()
    result.findings = [next(f for f in result.findings if f.marker == '19-44908684-T-C')]
    atlas = result.findings[0].detail['alphagenome_atlas']
    atlas.update(cache_hit=True, remote_status='timeout', missing_scorers=['RNA_SEQ'])
    exports = compose_result_views(result)
    assert 'cache hit: True' in exports['markdown']
    assert 'remote status: timeout' in exports['markdown']
    assert "missing scorers: ['RNA_SEQ']" in exports['markdown']
    assert exports['json']['findings'][0]['detail']['alphagenome_atlas']['cache_hit'] is True


def test_explorer_has_distinct_atlas_action_and_data_disclosure():
    page = explorer_html('19-44908684-T-C')
    assert 'Look up Atlas / AVI' in page
    assert 'variant coordinates to Google’s Atlas service' in page
    assert 'run(false,true)' in page
    assert "reference_build:'GRCh38',predict,atlas" in page
    assert 'impact ranking, not a disease probability' in page
