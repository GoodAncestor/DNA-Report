# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline import of the published HG002 reference run, separate from uploads.

The known release manifest binds generic SAMPLE-labelled outputs to one flowcell.
Only manifest-addressed CpGs are retained in memory; every input row is visited.
"""
from __future__ import annotations
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

RELEASE = 'giab_2025.01'
FLOWCELL = 'PAW70337'
PREFIX = f'{RELEASE}/analysis/wf-human-variation/sup/HG002/{FLOWCELL}/output/'
PARTITIONS = ('1', '2', 'ungrouped')
AUTOSOMES = {str(i) for i in range(1,23)}
REFERENCE = 'GCA_000001405.15_GRCh38_no_alt_analysis_set.fasta'
REQUIRED = ('SAMPLE.wf_snp.vcf.gz',) + tuple(f'SAMPLE.wf_mods.{p}.bedmethyl.gz' for p in PARTITIONS)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_inputs(directory, manifest_path):
    """Verify an operator-reviewed local acquisition manifest before import.

    This is not an upload API. Hashes attest to the downloaded objects recorded
    by the acquisition step; they are not an independent publisher signature.
    """
    directory = Path(directory)
    meta = json.loads(Path(manifest_path).read_text())
    if (meta.get('sample_id'), meta.get('dataset'), meta.get('flowcell'), meta.get('basecalling')) != ('HG002', RELEASE, FLOWCELL, 'sup'):
        raise ValueError('Expected the selected HG002 release, flowcell and SUP analysis')
    if meta.get('reference_from_vcf_header') != REFERENCE:
        raise ValueError('Reference does not match the selected GRCh38 release')
    files = {}
    for row in meta['files']:
        name = row.get('local_name')
        if name not in REQUIRED:
            continue
        if name in files or row.get('Key') != PREFIX + name:
            raise ValueError('Duplicate or mismatched release object')
        if row.get('url') != 'https://ont-open-data.s3.amazonaws.com/' + PREFIX + name:
            raise ValueError('Unexpected source object URL')
        path = directory / name
        if path.stat().st_size != row['bytes'] or sha256(path) != row.get('sha256'):
            raise ValueError(f'Input integrity check failed: {name}')
        files[name] = path
    if set(files) != set(REQUIRED):
        raise ValueError('The matched VCF and all three methylation partitions are required')
    return meta, files


def pool_partitions(paths, *, min_coverage=5, probe_map=None, measurements_path=None):
    """Sum disjoint HP integer counts before filtering; keep observed zero.

    Memory is bounded by the EPICv2 target manifest, not genome-wide row count.
    Non-target sites contribute input tallies but not an array-marker finding.
    """
    from methylask.ingest.nanopore import _prepare, _convert
    from biocore.methylation.model import MethylSite, Context
    if isinstance(min_coverage, bool) or not isinstance(min_coverage, int) or min_coverage < 1:
        raise ValueError('Minimum coverage must be a positive integer')
    if set(paths) != set(PARTITIONS):
        raise ValueError('Require exactly HP1, HP2 and ungrouped partitions')
    reverse, manifest, ambiguous = _prepare('GRCh38', 'EPICv2', min_coverage, 'm', probe_map)
    pooled = {}
    tallies = {}
    for group in PARTITIONS:
        counts = Counter()
        seen = set()
        path = Path(paths[group])
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt') as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip() or line.startswith(('#', 'track ', 'browser ')):
                    continue
                counts['input_rows'] += 1
                f = line.split()
                if len(f) != 18:
                    raise ValueError(f'{group} row {lineno}: expected 18 columns')
                code = f[3].split(',')[0]
                if code != 'm':
                    counts['other_modification_rows'] += 1
                    continue
                try:
                    pos, end = int(f[1]), int(f[2])
                    cov, nmod, ncan, other = (int(f[i]) for i in (9, 11, 12, 13))
                    if pos < 0 or end != pos + 1 or f[5] != '.' or min(cov, nmod, ncan, other) < 0 or cov != nmod + ncan + other:
                        raise ValueError('invalid strand, coordinates or valid-call counts')
                except ValueError as exc:
                    raise ValueError(f'{group} row {lineno}: {exc}') from exc
                counts['5mc_rows'] += 1
                locus = (f[0], pos)
                if locus not in reverse:
                    continue
                if locus in seen:
                    raise ValueError(f'Duplicate 5mC target within partition {group}: {locus}')
                seen.add(locus)
                counts['mapped_loci'] += 1
                # Each group's four counts remain available in the full table.
                cells = pooled.setdefault(locus, {})
                cells[group] = (cov, nmod, ncan, other)
        tallies[group] = dict(counts)
        print(f'Pooled partition {group}: {counts["5mc_rows"]:,} 5mC rows, {len(seen):,} target loci', flush=True)
    def sites():
        for (chrom, pos), groups in pooled.items():
            cov, nmod, ncan, other = (sum(v[i] for v in groups.values()) for i in range(4))
            yield MethylSite(chrom, pos, Context.CG, nmod, ncan, '.', cov, 'm', other)
    sample = _convert(sites(), reverse=reverse, source='HG002 published HP1 + HP2 + ungrouped',
        reference_build='GRCh38', array='EPICv2', min_coverage=min_coverage, mod_code='m',
        combined_strands=True, format_name='published partitioned bedMethyl',
        stats={'partition_input_counts': tallies},
        provenance={'manifest': manifest, 'sample_identity': 'release/flowcell acquisition manifest',
                    'partition_policy': 'Sum disjoint HP1, HP2 and ungrouped integer counts before coverage filtering'},
        ambiguous=ambiguous)
    if measurements_path:
        with gzip.open(measurements_path, 'wt') as out:
            writer = csv.writer(out, delimiter='\t')
            writer.writerow(['probe', 'chrom', 'start', 'status', 'valid_calls', 'modified_calls', '5mc_fraction'] +
                            [f'HP{p}_{field}' for p in PARTITIONS for field in ('valid', 'modified', 'canonical', 'other_mod')])
            for locus, probes in sorted(reverse.items()):
                groups = pooled.get(locus, {})
                cov = sum(v[0] for v in groups.values())
                nmod = sum(v[1] for v in groups.values())
                for probe in probes:
                    status = 'retained' if probe in sample.betas else 'low_coverage' if probe in sample.coverage else 'missing'
                    writer.writerow([probe, *locus, status, cov if groups else '', nmod if groups else '',
                                     sample.betas.get(probe, '')] + [v for p in PARTITIONS for v in groups.get(p, ('',)*4)])
    return sample


def screen_variants(path, *, batch_size=10000, min_dp=10, min_gq=20):
    """Screen every QC-passing autosomal carried small allele against local ClinVar.

    Retain explicit exclusion counts. This checks supplied calls, not caller
    accuracy or callable reference genotypes. No personal trait/PGx prediction.
    """
    import pysam
    from geneask.interpret.clinvar_screen import screen_findings
    from geneask.annotators.clinvar_mirror import lookup_from_mirror
    if lookup_from_mirror(["1-1-A-C"]) is None:
        raise ValueError('A local full ClinVar mirror is required for the reference report')
    def screen_batch(batch):
        # Pass a complete batch-specific panel so the shared screener can never
        # silently substitute its small bundled panel if a later lookup fails.
        index = lookup_from_mirror(v['variant_id'] for v in batch)
        if index is None:
            raise ValueError('Full ClinVar mirror lookup failed; refusing partial annotation')
        panel = {}
        for variant_id, record in index.items():
            panel.setdefault(record.get('gene') or '?', {'variants': []})['variants'].append(
                dict(record, variant_id=variant_id))
        rows = screen_findings(batch, panel=panel)
        for finding in rows:
            finding.source = 'clinvar_mirror'
        return rows
    counts = Counter()
    findings, batch = [], []
    with pysam.VariantFile(str(path)) as vf:
        if list(vf.header.samples) != ['SAMPLE'] or f'##reference={REFERENCE}\n' not in str(vf.header):
            raise ValueError('VCF sample/reference header does not match the selected release')
        for rec in vf:
            counts['input_records'] += 1
            chrom = rec.chrom.removeprefix('chr')
            if chrom not in AUTOSOMES:
                counts['excluded_non_autosomal'] += 1; continue
            if set(rec.filter.keys()) != {'PASS'}:
                counts['excluded_filter'] += 1; continue
            call = rec.samples['SAMPLE']
            gt = call.get('GT')
            if not gt or len(gt) != 2 or any(i is None or i < 0 or i >= len(rec.alleles) for i in gt):
                counts['excluded_genotype'] += 1; continue
            dp, gq = call.get('DP'), call.get('GQ')
            if dp is None or gq is None or dp < min_dp or gq < min_gq:
                counts['excluded_quality'] += 1; continue
            carried = set(gt) - {0}
            if not carried:
                counts['reference_genotype'] += 1; continue
            retained = False
            for i in sorted(carried):
                alt = rec.alleles[i]
                if max(len(rec.ref), len(alt)) > 50 or any(b not in 'ACGT' for b in rec.ref + alt):
                    counts['excluded_non_small_alleles'] += 1; continue
                batch.append({'variant_id':f'{chrom}-{rec.pos}-{rec.ref}-{alt}',
                    'genotype':'/'.join(rec.alleles[a] for a in gt), 'platform':'ONT reference cell line',
                    'zygosity':'hom' if gt[0] == gt[1] else 'het', 'filter':'PASS',
                    'qual':rec.qual,'dp':dp,'gq':gq})
                counts['retained_carried_alleles'] += 1; retained = True
            if retained: counts['retained_records'] += 1
            if len(batch) >= batch_size:
                findings.extend(screen_batch(batch));batch=[]
        if batch: findings.extend(screen_batch(batch))
    counts['clinvar_findings'] = len(findings)
    return findings, {**counts, 'min_dp':min_dp, 'min_gq':min_gq, 'max_allele_length':50,
                      'scope':'Autosomal PASS diploid small carried alleles; full local ClinVar clinical-significance screen'}


def annotate_methylation(sample, mirror_path, archive_path, *, max_traits=1000):
    """Archive every matched EWAS row, select one leading CpG per displayed trait.

    Ranking is evidence tier, p-value, sample size, then stable marker/PMID.
    Display selection never changes the complete measured/association tables.
    """
    import sqlite3, math
    from functools import lru_cache
    from methylask.providers.ewas_catalog import EwasCatalogProvider, _tier_from
    from methylask.traits import trait_class
    from biocore.providers.base import Tier
    rank = {Tier.ROBUST:0, Tier.MODERATE:1, Tier.SPECULATIVE:2, Tier.UNKNOWN:3}
    @lru_cache(maxsize=None)
    def covariate(trait): return trait_class(trait) == 'covariate'
    def number(v, fallback):
        try:
            n=float(v)
            return n if math.isfinite(n) else fallback
        except (TypeError,ValueError): return fallback
    def score(row):
        return (rank[_tier_from(row)], number(row.get('p'),1), -number(row.get('n'),0), row['cpg'], str(row.get('pmid') or ''))
    path=Path(mirror_path)
    if not path.is_file(): raise ValueError('Local EWAS mirror is required')
    con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    best={}; matched=set(); nrows=0
    provider=EwasCatalogProvider(offline_only=True)
    try:
        con.execute('CREATE TEMP TABLE observed(cpg TEXT PRIMARY KEY)')
        con.executemany('INSERT INTO observed VALUES (?)',((p,) for p in sample.betas))
        total_mirror=con.execute('SELECT count(*) FROM findings').fetchone()[0]
        with gzip.open(archive_path,'wt',compresslevel=1) as out:
            fields=['cpg','trait','gene','beta','se','p','n','tissue','methylation_array','chrpos','pmid','efo']
            writer=csv.writer(out,delimiter='\t')
            writer.writerow(fields+['HG002_5mc_fraction','HG002_valid_calls'])
            query='SELECT f.* FROM findings f JOIN observed o ON f.cpg=o.cpg'
            for raw in con.execute(query):
                row=dict(raw);nrows+=1;matched.add(row['cpg'])
                writer.writerow([row.get(k,'') for k in fields]+[sample.betas[row['cpg']],sample.coverage[row['cpg']]])
                trait=str(row.get('trait') or 'unknown trait')
                if not covariate(trait) and (trait not in best or score(row)<score(best[trait])):
                    best[trait]=row
                if nrows%1000000==0:print(f'Archived {nrows:,} matched EWAS associations',flush=True)
        selected=sorted(best.values(),key=score)[:max_traits]
        findings=[]
        for row in selected:
            # Keep all study rows for the chosen marker/trait, not just its best p.
            for raw in con.execute('SELECT * FROM findings WHERE cpg=? AND trait=?',(row['cpg'],row['trait'])):
                findings.append(provider._finding(row['cpg'],dict(raw)))
    finally:
        con.close()
    stats={'matched_association_rows':nrows,'measured_probes_with_associations':len(matched),
           'traits_with_associations':len(best),'displayed_trait_representatives':len(selected),
           'display_policy':'One leading CpG per raw EWAS trait, ranked by evidence tier, p-value, study size and stable IDs; all matched rows downloadable',
           'max_displayed_traits':max_traits,'mirror_rows':total_mirror}
    return findings,stats


def reference_interpretations(result):
    """Give real cell-line observations reference-sample interpretation context."""
    from biocore.providers.base import Interpretation
    from .outcomes import build_outcomes
    for f in result.findings:
        d=f.detail
        if d.get('modality')=='methylome':
            f.interpretation=Interpretation(
                found=f"HG002 cell line: {f.marker} has native 5mC fraction {d['your reading']:.3f} from {d['methylation_coverage']} valid calls.",
                can_mean=f"Published studies associate this site with {d.get('trait','a trait')}. This cell-line fraction does not estimate that trait in the donor or a reader.",
                how_sure='The evidence tier describes the published association. The measurement is from the ONT reference run; cell culture, study tissue and sequencing/array differences limit biological comparisons.',
                next_step='Review the contributing study tissues and assay methods; use an orthogonal methylation reference to evaluate measurement agreement.')
        else:
            f.interpretation=Interpretation(
                found=f"HG002 reference cell line: {d.get('gene','variant')} genotype {d.get('genotype','')} at {f.marker}.",
                can_mean=f"The local ClinVar record labels this variant {d.get('clinical_significance','unclassified')}. This is a reference-dataset observation, not a diagnosis of the donor or reader.",
                how_sure=f"ClinVar review stars: {d.get('gold_stars','unknown')}/4. Published ONT call passed this report's stated genotype/quality filters; independent caller benchmarking has not run here.",
                next_step='Compare the call against the compatible HG002 benchmark inside its confident regions.',
                condition=(d.get('conditions') or [None])[0])
        d['reference_sample']='HG002 cultured lymphoblastoid cell line'
        f.promoted=False;f.promoted_reason=''
    result.actions=[];result.read_first=[];result.trait_scores=[];result.clocks=[]
    result.outcomes=build_outcomes(result)
    return result


def build_reference_report(sample, vcf_path, acquisition, output_dir, *, ewas_mirror, clinvar_mirror, max_traits=1000):
    """Build cached report artifacts from an already verified/prepared reference run."""
    import os
    from biocore.providers.base import ProviderStatus, Health
    from .detect import InputKind
    from .orchestrate import ReportResult
    from .nanopore_report import _annotate_native, _finish_native
    from .report import render_report, compose_result_views
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    if Path(os.environ.get('CLINVAR_MIRROR_DB', '')).resolve() != Path(clinvar_mirror).resolve():
        raise ValueError('Set CLINVAR_MIRROR_DB to the reviewed local mirror for this offline process')
    findings,vstats=screen_variants(vcf_path)
    print(f'Screened {vstats["input_records"]:,} VCF records: {len(findings):,} ClinVar findings',flush=True)
    with gzip.open(output/'HG002.variant-findings.json.gz','wt') as out:
        json.dump([f.to_dict() for f in findings],out)
    annotations,astats=annotate_methylation(sample,ewas_mirror,output/'HG002.associations.tsv.gz',max_traits=max_traits)
    result=ReportResult(kind=InputKind.VCF,engines=('geneask','methylask'),tissue='cell_line')
    result.notes.append('HG002 / GM24385 is a cultured lymphoblastoid reference cell line. These are real published measurements; no personal health, lifestyle, age or pharmacogenomic prediction is made.')
    ref_sources={Path(p).name:{'sha256':sha256(p),'bytes':Path(p).stat().st_size} for p in (ewas_mirror,clinvar_mirror)}
    status=ProviderStatus('ewas_catalog',Health.OK,version=ref_sources[Path(ewas_mirror).name]['sha256'],record_count=astats['mirror_rows'],note='Complete local mirror screened at qualifying measured CpGs; all matching rows archived.')
    methyl=_annotate_native(str(vcf_path),result,sample,annotations=(annotations,[status]))
    for f in findings:f.detail['sequencing_platform']='ONT'
    result.findings.extend(findings)
    result.provider_status.append(ProviderStatus('clinvar_mirror',Health.OK,version=ref_sources[Path(clinvar_mirror).name]['sha256'],note='All retained autosomal small carried alleles screened against the full local mirror.'))
    result.scan_stats['limits']={'methylation_display':{'shown':astats['displayed_trait_representatives'],'found':astats['traits_with_associations'],'policy':astats['display_policy']}}
    result.notes.append(f"The web report shows {astats['displayed_trait_representatives']} leading CpG/trait examples from {astats['matched_association_rows']:,} matched EWAS rows. All EPICv2-mapped CpG measurements and all matched EWAS rows are downloadable; other genome-wide CpGs remain in the source bedMethyl files.")
    metadata={'sample_id':'HG002 / GM24385','reference_build':'GRCh38','input':'Published Clair3 VCF + three modkit HP partitions',
        'reference_dataset':{'id':'hg002-2025.01-PAW70337','title':'HG002 reference genome + methylome',
            'material':'Cultured lymphoblastoid cell line','synthetic':False,'source':'https://epi2me.nanoporetech.com/giab-2025.01/',
            'license':'CC BY-NC 4.0; Oxford Nanopore Technologies Benchmark Datasets',
            'reported_mean_depth':acquisition['reported_statistics']['Total depth (mean)'],
            'validation_scope':'Published-output ingestion, count pooling, annotation and reporting; no basecalling or independent caller benchmark executed'},
        'variants':{'status':'interpreted','records_scanned':vstats['retained_carried_alleles'],'scope':vstats['scope']},
        'methylation':methyl,'annotations':astats,
        'preparation':{'variant_source':'supplied VCF','variant_qc':{'reported_records':vstats['retained_records'],**vstats},
            'acquisition':acquisition,'reference_mirrors':ref_sources,'basecaller':'Publisher Dorado v0.8.2; not run here','caller':'Publisher Clair3 v1.0.8; not run here'}}
    _finish_native(str(vcf_path),result,metadata,{'markers_scanned':vstats['retained_carried_alleles']})
    reference_interpretations(result)
    result.scan_stats['input_bytes']=sum(f['bytes'] for f in acquisition['files'] if f.get('local_name') in REQUIRED)
    views=compose_result_views(result,filename='HG002 public reference dataset')
    (output/'report.json').write_text(json.dumps(views['json']))
    (output/'report.md').write_text(reference_labels(views['markdown']))
    render_report(result,str(output/'report.html'),filename='HG002 public reference dataset')
    (output/'report.html').write_text(reference_labels((output/'report.html').read_text()))
    (output/'provenance.json').write_text(json.dumps(metadata,indent=2))
    print(f'Rendered {len(result.findings):,} reference findings',flush=True)
    return result


def reference_labels(text):
    """Reference-only presentation substitutions; schema reading keys stay stable."""
    for before, after in (
        ('your reading', 'HG002 reading'),
        ('your position', 'the reference sample position'),
        ('not your tissue', 'study tissue differs'),
    ):
        text = text.replace(before, after)
    return text


def write_artifact_index(output_dir):
    """Publish readiness last, after hashing every reviewed public artifact."""
    from .reference_demo import DATASET_ID, FILES
    output = Path(output_dir)
    index = {'dataset_id': DATASET_ID, 'sample_id': 'HG002', 'complete': True,
             'files': {name: {'bytes': (output/name).stat().st_size, 'sha256': sha256(output/name)}
                       for name in FILES if name != 'artifact-index.json'}}
    pending = output / 'artifact-index.json.part'
    pending.write_text(json.dumps(index, indent=2) + '\n')
    pending.replace(output / 'artifact-index.json')
    return index


def main(argv=None):
    """Rebuild the public reference artifact set in an empty staging directory."""
    import argparse, os, urllib.request
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--clinvar-mirror', type=Path, required=True)
    parser.add_argument('--ewas-mirror', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true', help='Download 1.75 GB of pinned published outputs if missing')
    args = parser.parse_args(argv)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use an empty staging output directory; install only after verification')
    if not all(p.is_file() for p in (args.clinvar_mirror, args.ewas_mirror)):
        parser.error('Both local reference mirrors are required')
    manifest_path = Path(__file__).parent / 'data' / 'hg002-source-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    args.inputs.mkdir(parents=True, exist_ok=True)
    if args.fetch:
        for row in manifest['files']:
            name = row.get('local_name')
            if name not in REQUIRED or (args.inputs/name).exists():
                continue
            pending = args.inputs / (name + '.part')
            print('Downloading ' + name, flush=True)
            with urllib.request.urlopen(row['url'], timeout=120) as response, pending.open('wb') as out:
                n = 0
                while chunk := response.read(8 * 1024 * 1024):
                    n += len(chunk)
                    if n > row['bytes']:
                        raise ValueError('Download exceeds pinned object size')
                    out.write(chunk)
            if n != row['bytes'] or sha256(pending) != row['sha256']:
                raise ValueError('Downloaded object failed integrity check: ' + name)
            pending.replace(args.inputs/name)
    manifest, paths = verify_inputs(args.inputs, manifest_path)
    args.output.mkdir(parents=True, exist_ok=True)
    sample = pool_partitions({p: paths[f'SAMPLE.wf_mods.{p}.bedmethyl.gz'] for p in PARTITIONS},
                             measurements_path=args.output/'HG002.cpg-measurements.tsv.gz')
    os.environ['CLINVAR_MIRROR_DB'] = str(args.clinvar_mirror.resolve())
    build_reference_report(sample, paths['SAMPLE.wf_snp.vcf.gz'], manifest, args.output,
                           ewas_mirror=args.ewas_mirror, clinvar_mirror=args.clinvar_mirror)
    write_artifact_index(args.output)
    print('Complete public artifact set: ' + str(args.output), flush=True)


if __name__ == '__main__':
    main()
