"""Apply the server's dataset output policy to every presentation surface."""
from __future__ import annotations
from copy import copy, deepcopy
import html
import json

DATASETS = (
    ('alphagenome_atlas_local_avi', 'Atlas AVI · static download'),
    ('alphagenome_atlas_api', 'Atlas AVI · API and API cache'),
    ('alphagenome_atlas_splicing', 'Atlas merged splicing'),
    ('alphagenome_atlas_features', 'Atlas feature contributions'),
    ('alphagenome', 'AlphaGenome sequence predictions'),
    ('alphamissense', 'AlphaMissense'),
)
_RESTRICTED = ('alphagenome', 'alphagenome_atlas')


def output_policy():
    from biocore.licensing import output_policy as shared_policy, prediction_license
    configuration = shared_policy()
    mode = configuration["mode"]
    datasets = []
    for kind, label in DATASETS:
        license = prediction_license(kind)
        allowed = bool(license.get('commercial_allowed'))
        datasets.append({**license, 'kind': kind, 'label': label,
                         'included_in_mode': mode != 'commercial' or allowed})
    return {**configuration, 'datasets': datasets}


def policy_html():
    policy = output_policy()
    rows = ''.join('<li><strong>' + html.escape(row['label']) + '</strong>: '
                   + ('Commercial use permitted' if row['commercial_allowed'] else 'Noncommercial use only')
                   + (' · excluded in this output mode' if not row['included_in_mode'] else '')
                   + ' · <a href="' + html.escape(row['terms_url'], quote=True) + '">'
                   + ('Google Output Terms' if row['id'] == 'AlphaGenome-Output-Terms' else 'Dataset terms') + '</a></li>'
                   for row in policy['datasets'])
    return ('<section class="output-policy" aria-label="Dataset usage eligibility"><h2>Model dataset usage</h2>'
            '<p>Output mode: <strong>' + policy['mode'] + '</strong>. '
            'These labels cover the listed model datasets and access routes, not a general commercial-compliance guarantee. Cached API results retain the API terms.</p>'
            '<p>' + html.escape(policy.get('configuration_note', '')) + '</p><ul>' + rows + '</ul></section>')


def result_for_output(result):
    """Filter once per result/mode, without changing the caller's research data."""
    from biocore.licensing import output_mode, filter_findings_for_output
    mode = output_mode()
    if getattr(result, '_output_policy_mode', None) == mode:
        return result
    filtered = copy(result)
    original = list(getattr(result, 'findings', None) or [])
    filtered.findings = filter_findings_for_output(original)
    filtered._output_policy_mode = mode
    filtered.output_policy = output_policy()
    if mode != 'commercial':
        return filtered
    filtered.scan_stats = deepcopy(getattr(result, 'scan_stats', None) or {})
    # Provenance/counts from a previous research-mode run must not survive as
    # scores, ranked candidates, or summaries in a commercial output.
    filtered.scan_stats.pop('ai_demo', None)
    old_coverage = filtered.scan_stats.pop('ai_predictions', {})
    coverage = {}
    for model in _RESTRICTED:
        n = len({f.marker for f in filtered.findings if (f.detail or {}).get(model)})
        coverage[model] = {'status': 'complete' if n else 'excluded_by_output_mode', 'scored': n,
                           'note': 'Only commercial-eligible dataset outputs are included.'}
    if old_coverage.get('alphamissense'):
        coverage['alphamissense'] = deepcopy(old_coverage['alphamissense'])
    if old_coverage:
        filtered.scan_stats['ai_predictions'] = coverage
    filtered.scan_stats['findings_total'] = len(filtered.findings)
    filtered.notes = [n for n in (getattr(result, 'notes', None) or [])
                      if not any(word in str(n).lower() for word in ('alphagenome', 'atlas', 'avi '))]
    filtered.notes.append('Commercial output mode excludes noncommercial model outputs, including API-derived caches and frozen examples.')
    from biocore.providers.base import ProviderStatus, Health
    filtered.provider_status = [s for s in (getattr(result, 'provider_status', None) or []) if s.name not in _RESTRICTED]
    filtered.provider_status += [ProviderStatus(name=k, health=Health.OK if v['scored'] else Health.UNAVAILABLE,
                                               note=v['note']) for k, v in coverage.items() if k in _RESTRICTED]
    filtered.read_first = filter_findings_for_output(list(getattr(result, 'read_first', None) or []))
    # Composite objects are rebuilt using the existing curated composers,
    # preventing nested contributions/actions from carrying old model outputs.
    from dataclasses import is_dataclass, asdict
    def mentions_model(value):
        if is_dataclass(value):
            return mentions_model(asdict(value))
        if isinstance(value, str):
            return any(k in value.lower() for k in ('alphagenome', 'atlas', 'avi score'))
        if isinstance(value, dict):
            return any(mentions_model(k) or mentions_model(v) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return any(mentions_model(v) for v in value)
        return False
    filtered.trait_scores = [score for score in (getattr(result, 'trait_scores', None) or []) if not mentions_model(score)]
    filtered.clocks = list(getattr(result, 'clocks', None) or [])
    filtered.actions = []
    filtered.outcomes = []
    if getattr(result, 'actions', None):
        from .action_plan import build_actions
        filtered.actions = build_actions(filtered)
    if getattr(result, 'outcomes', None):
        from .outcomes import build_outcomes
        filtered.outcomes = build_outcomes(filtered)
    return filtered


def artifact_allowed(body, fmt):
    """Historical unlabelled/research artifacts cannot bypass a stricter server."""
    from biocore.licensing import commercial_mode
    if not commercial_mode():
        return True
    if fmt == 'json':
        try:
            return json.loads(body).get('output_policy', {}).get('mode') == 'commercial'
        except (ValueError, AttributeError):
            return False
    if fmt == 'md':
        return body.startswith('---\n') and len(body.split('---', 2)) == 3 and '\noutput_mode: commercial\n' in body.split('---', 2)[1]
    return '<meta name="dna-output-mode" content="commercial">' in body.split('</head>', 1)[0]
