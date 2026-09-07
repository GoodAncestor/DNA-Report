# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic synthetic inputs, frozen public evidence, real report composition.

No read calling, remote lookups or deployment-dependent mirror access occurs.
This demonstrates the reporting contract, not sequencing or clinical accuracy.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from .detect import InputKind
from .orchestrate import ReportResult
from .nanopore_report import _annotate_native, _finish_native

DATA = Path(__file__).parent / 'demo_data'
DEMO_NOTE = (
    'Synthetic Nanopore demo — no person was sequenced. Genotypes, read counts '
    'and methylation fractions were invented for one fictional sample. Public '
    'ClinVar and EWAS annotations are frozen reference records, not invented '
    'clinical evidence. This tests reporting; it does not validate basecalling, '
    'variant calling, a laboratory protocol or clinical accuracy.'
)
DOWNLOADS = {
    'calls.vcf': ('nanopore_synthetic.vcf', 'text/plain'),
    'calls.bedmethyl': ('nanopore_synthetic.bedmethyl', 'text/plain'),
    'reference.json': ('nanopore_synthetic.json', 'application/json'),
    'README.md': ('nanopore_README.md', 'text/markdown'),
}


def build_demo():
    from biocore.providers.base import Finding, Tier, Category, ProviderStatus, Health
    from biocore.variants.carried import carried_variants
    from methylask.ingest.nanopore import read_bedmethyl
    from methylask.providers.ewas_catalog import EwasCatalogProvider

    bed = DATA / 'nanopore_synthetic.bedmethyl'
    vcf = DATA / 'nanopore_synthetic.vcf'
    snapshot = json.loads((DATA / 'nanopore_synthetic.json').read_text())
    sample = read_bedmethyl(str(bed), reference_build='GRCh38', min_coverage=5,
                           combined_strands=True, probe_map=snapshot['probe_map'])
    sample.provenance['source'] = bed.name
    observed_depth = {(fields[0], int(fields[1])): int(fields[9])
                      for fields in (line.split('\t') for line in bed.read_text().splitlines())}
    result = ReportResult(kind=InputKind.MODBAM, engines=('methylask', 'geneask'),
                          tissue='blood', notes=[DEMO_NOTE])
    provider = EwasCatalogProvider(offline_only=True)
    annotations = [provider._finding(row['cpg'], row) for row in snapshot['ewas_rows']
                   if row['cpg'] in sample.betas]
    methylation = _annotate_native(str(bed), result, sample, annotations=(annotations, [
        ProviderStatus('ewas_catalog', Health.OK, version='demo-snapshot-2026-09-06',
                       record_count=len(annotations), note='Bundled public EWAS evidence; synthetic measurements; no live lookup.')]))
    carried = {row['variant_id']: row for row in carried_variants(str(vcf))}
    for raw in snapshot['variant_findings']:
        if raw['marker'] not in carried:
            continue
        row = carried[raw['marker']]
        detail = {**raw['detail'], **{k: row.get(k) for k in ('genotype', 'zygosity', 'filter', 'qual', 'gq', 'dp')},
                  'sequencing_platform': 'ONT', 'measurement_origin': 'synthetic_demo'}
        result.findings.append(Finding(**{**raw, 'tier': Tier(raw['tier']),
            'categories': [Category(c) for c in raw['categories']], 'detail': detail}))
    result.provider_status.append(ProviderStatus('clinvar_panel_157', Health.OK,
        version='demo-snapshot-2026-09-06', record_count=len(snapshot['variant_findings']),
        note='Frozen bundled ClinVar evidence; synthetic genotype; no live lookup.'))
    result.notes.append('Pharmacogenomic diplotypes are withheld: these illustrative variant records do not establish callable reference sites or complete gene coverage.')
    files = [bed, vcf, DATA / 'nanopore_synthetic.json']
    meta = {
        'sample_id': snapshot['sample_id'], 'reference_build': 'GRCh38',
        'input': 'synthetic VCF + bedMethyl; no source BAM or POD5',
        'demo': {'synthetic': True, 'description': DEMO_NOTE,
                 'validation_scope': 'Parsers, CpG mapping, evidence attachment, report composition and exports only.',
                 'probe_scope': f"{len(snapshot['probe_map'])} selected GRCh38 probes; counts do not describe a whole genome.",
                 'finding_scope': 'Eight curated examples: six CpG associations and two variant classifications. The demo contains a fixed set of illustrative inputs.'},
        'variants': {'status': 'illustrated', 'records_scanned': len(carried),
                     'scope': f'{len(carried)} illustrative autosomal small variants; no genome-wide screen.'},
        'methylation': methylation,
        'preparation': {'variant_source': 'synthetic VCF', 'caller': 'not run',
                        'basecaller': 'not run', 'variant_qc': {'reported_records': len(carried)},
                        'files': {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files},
                        'reference_sources': snapshot['sources']},
        'demo_measurements': [
            {'probe': probe, 'fraction': sample.betas.get(probe), 'valid_reads': observed_depth.get(tuple(position)),
             'status': 'retained' if probe in sample.betas else ('below coverage threshold' if tuple(position) in observed_depth else 'not measured')}
            for probe, position in snapshot['probe_map'].items()],
    }
    result = _finish_native(str(bed), result, meta, {'markers_scanned': len(carried)})
    # Interpretations retain actual evidence classifications, while every
    # measurement stays visibly fictional even if a card is copied separately.
    for finding in result.findings:
        finding.detail['measurement_origin'] = 'synthetic_demo'
        if finding.interpretation:
            finding.interpretation.found = 'Synthetic example. ' + finding.interpretation.found
            if finding.detail.get('measurement_platform') == 'ONT':
                finding.interpretation.how_sure = finding.interpretation.how_sure.replace(
                    'Your reading comes from native ONT sequencing',
                    'This reading is a synthetic fraction in a native-format fixture')
            if finding.detail.get('sequencing_platform') == 'ONT':
                finding.interpretation.how_sure = (
                    'The genotype and quality numbers are synthetic; no reads were called. '
                    f"The frozen ClinVar record classifies this variant as {finding.detail['clinical_significance']} "
                    f"with {finding.detail['gold_stars']} review stars. This is a report example, not a person's result."
                )
    result.scan_stats['input_bytes'] = bed.stat().st_size + vcf.stat().st_size
    return result
