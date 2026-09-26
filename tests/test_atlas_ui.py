"""Atlas UI uses real frozen evidence and an explicit remote-lookup control."""
from dnareport.explorer import build_ai_demo, explorer_html, guided_demo_html


def test_demo_atlas_result_is_verified_and_visible():
    result = build_ai_demo()
    example = next(f for f in result.findings if f.marker == '19-44908684-T-C')
    atlas = example.detail['alphagenome_atlas']
    assert atlas['tracks'][0]['raw_score'] == 0.4998990297317505
    assert atlas['tracks'][0]['quantile_score'] == 0.9869896173477173
    assert 'feature contributions were not requested' in atlas['provenance']['note']
    page = guided_demo_html(result)
    assert 'AlphaGenome Atlas / AVI' in page
    assert 'Scorer: AVI_SCORE; Raw score: 0.4998990297317505' in page
    assert 'frozen replay makes no API request' in page


def test_explorer_has_distinct_atlas_action_and_data_disclosure():
    page = explorer_html('19-44908684-T-C')
    assert 'Look up Atlas / AVI' in page
    assert 'variant coordinates to Google’s Atlas service' in page
    assert 'run(false,true)' in page
    assert "reference_build:'GRCh38',predict,atlas" in page
    assert 'impact ranking, not a disease probability' in page
