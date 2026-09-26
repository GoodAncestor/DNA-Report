# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bound a single interactive model request even when the vendor RPC has no deadline."""
import json
import os
import signal
import subprocess
import sys


def payload(variant, predict=False, atlas=False, failure=None, failed_models=None):
    from .predictions import explore_variant, provider_statuses
    from .report import compose_result_views, report_html
    result = explore_variant(variant, predict=predict, atlas=atlas)
    if failure:
        for model in failed_models or ('alphagenome',):
            row = result.scan_stats['ai_predictions'].get(model, {})
            result.scan_stats['ai_predictions'][model] = {
                **row, 'status': 'partial' if row.get('scored') else failure,
                'remote_status': failure,
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


def bounded_payload(variant, predict=False, atlas=False):
    if not predict and not atlas:
        return payload(variant)
    models = [name for name, requested in (('alphagenome', predict), ('alphagenome_atlas', atlas)) if requested]
    # A new process group lets timeout cleanup also reap Atlas's nested RPC
    # subprocess. Killing only the direct child could leave network work alive.
    child = None
    try:
        child = subprocess.Popen([sys.executable, '-m', 'dnareport.prediction_job'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=(os.name == 'posix'))
        stdout, _ = child.communicate(json.dumps({'variant': variant, 'predict': predict, 'atlas': atlas}), timeout=45)
        if child.returncode:
            raise ValueError('Prediction process failed')
        return json.loads(stdout)
    except subprocess.TimeoutExpired:
        if child is not None:
            if os.name == 'posix':
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                child.kill()
            child.communicate()
        return payload(variant, failure='timeout', failed_models=models)
    except (ValueError, OSError):
        return payload(variant, failure='failed', failed_models=models)


if __name__ == '__main__':
    request = json.load(sys.stdin)
    json.dump(payload(request['variant'], predict=request.get('predict', False),
                      atlas=request.get('atlas', False)), sys.stdout, allow_nan=False)
