# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serve only the reviewed, precomputed public HG002 artifact set."""
from __future__ import annotations
import json
import os
from pathlib import Path

DATASET_ID='hg002-2025.01-PAW70337'
FILES={
    'report.html':'text/html', 'report.json':'application/json', 'report.md':'text/markdown',
    'HG002.cpg-measurements.tsv.gz':'application/gzip',
    'HG002.associations.tsv.gz':'application/gzip',
    'HG002.variant-findings.json.gz':'application/gzip',
    'provenance.json':'application/json', 'artifact-index.json':'application/json',
}
CARD='''<a class="specimen demo-link" href="/demo/hg002">
<div class="no">SPECIMEN 06 · PUBLIC REFERENCE</div>
<div class="t">HG002 genome + methylome</div>
<div class="d">Real, genome-wide Nanopore measurements from a reference cell line.
Explore coverage, variant findings and all EPICv2-mapped CpG measurements.</div></a>'''


def directory(result_dir):
    return Path(os.environ.get('DNAREPORT_HG002_DEMO_DIR') or Path(result_dir)/'public-demo'/'hg002')


def ready(root):
    root=Path(root).resolve()
    try:
        index=json.loads((root/'artifact-index.json').read_text())
        if index.get('dataset_id')!=DATASET_ID or index.get('sample_id')!='HG002' or index.get('complete') is not True:
            return False
        if set(index['files']) != set(FILES)-{'artifact-index.json'}:
            return False
        for name,meta in index['files'].items():
            p=root/name
            if p.resolve().parent!=root or not p.is_file() or p.stat().st_size!=meta['bytes']:
                return False
        return True
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return False
