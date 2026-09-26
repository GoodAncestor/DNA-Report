# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bound a single interactive model request even when the vendor RPC has no deadline."""
import json
import subprocess
import sys


def payload(variant, predict=False, failure=None):
    from .predictions import explore_variant, provider_statuses
    from .report import compose_result_views, report_html
    result = explore_variant(variant, predict=predict)
    if failure:
        result.scan_stats['ai_predictions']['alphagenome'] = {
            'status': failure, 'eligible': 1, 'scored': 0, 'skipped': 1,
            'note': 'The request did not complete. Local evidence is retained; no negative model result is inferred.'}
        result.provider_status = provider_statuses(result.scan_stats['ai_predictions'])
    views = compose_result_views(result, filename='Single-variant research lookup')
    html = report_html(result, filename='Single-variant research lookup')
    # Evidence lookup can have no prediction at all; show all findings rather
    # than a predictions-only filter that hides the very evidence requested.
    html = html.replace('<body>', '<body><script>location.hash="view=site";</script>', 1)
    html = html.replace('<option value="robust" selected>', '<option value="robust">', 1)
    html = html.replace('<option value="robust moderate speculative unknown">',
                        '<option value="robust moderate speculative unknown" selected>', 1)
    return {'report':views['json'], 'markdown':views['markdown'], 'html':html}


def bounded_payload(variant, predict=False):
    if not predict:
        return payload(variant)
    try:
        child = subprocess.run([sys.executable, '-m', 'dnareport.prediction_job'],
            input=json.dumps({'variant':variant}), text=True, capture_output=True,
            timeout=45, check=True)
        return json.loads(child.stdout)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the child before raising; no orphan RPC.
        return payload(variant, failure='timeout')
    except (subprocess.CalledProcessError, ValueError, OSError):
        return payload(variant, failure='failed')


if __name__ == '__main__':
    request = json.load(sys.stdin)
    json.dump(payload(request['variant'], predict=True), sys.stdout, allow_nan=False)
