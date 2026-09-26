# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded research predictions, with coverage separate from clinical evidence."""
from __future__ import annotations
import heapq
import itertools
import os
import re
import sqlite3
from biocore.providers.base import Finding, Tier, Category, ProviderStatus, Health


def normalize_variant(value: str) -> str:
    value = str(value).strip().replace(':', '-').replace('>', '-')
    m = re.fullmatch(r'(?:chr)?([1-9]|1[0-9]|2[0-2]|X|Y)-(\d+)-([ACGTacgt]{1,50})-([ACGTacgt]{1,50})', value)
    if not m or int(m[2]) < 1 or int(m[2]) > 250_000_000 or m[3].upper() == m[4].upper():
        raise ValueError('Enter a GRCh38 variant as chromosome-position-reference-alternate, for example 22-36201698-A-C. Use 1–50 DNA bases per allele.')
    return f'{m[1]}-{int(m[2])}-{m[3].upper()}-{m[4].upper()}'


def candidate_limit():
    try:
        return max(1, min(200, int(os.environ.get('DNAREPORT_AI_MAX_CANDIDATES', '50'))))
    except ValueError:
        return 50


def declared_grch38(path):
    import pysam
    with pysam.VariantFile(path) as vf:
        refs = [str(r) for r in vf.header.records if r.key in ('reference', 'assembly')]
    text = ' '.join(refs).lower()
    return ('grch38' in text or 'hg38' in text or 'gca_000001405.15' in text) and not ('grch37' in text or 'hg19' in text)


def select_novel_candidates(carried, existing, path, limit=None):
    """Quality-first bounded screen; absence from a partial panel is not novelty.

    Only an available local ClinVar mirror can establish an uncatalogued candidate.
    Selection is a transparent operational ranking, not a pathogenicity score.
    """
    from geneask.annotators.clinvar_mirror import lookup_from_mirror
    limit = candidate_limit() if limit is None else limit
    coverage = {'status': 'ready', 'quality_eligible': 0, 'screened': 0,
                'selected': 0, 'limit': limit, 'selection': 'PASS autosomal GRCh38, DP ≥10, GQ ≥20; highest GQ then DP, then variant ID',
                'scope': 'New candidates absent from the available local ClinVar snapshot; not proof of biological novelty'}
    if not declared_grch38(path):
        coverage['status'] = 'reference_not_declared_grch38'
        return [], coverage
    seen = {f.marker for f in existing}
    def eligible():
        for v in carried:
            vid = v['variant_id']
            try:
                normalized = normalize_variant(vid)
            except ValueError:
                continue
            if (normalized.split('-')[0] not in {str(i) for i in range(1,23)} or
                v.get('filter') != 'PASS' or v.get('zygosity') not in ('het','hom') or
                (v.get('dp') or 0) < 10 or (v.get('gq') or 0) < 20 or vid in seen):
                continue
            seen.add(vid)
            coverage['quality_eligible'] += 1
            yield v
    from geneask.annotators import atlas_avi
    atlas_state = atlas_avi.local_status()
    coverage['atlas_ranking'] = {**atlas_state, 'scored': 0, 'queried': 0}
    def ranked():
        stream = eligible()
        while chunk := list(itertools.islice(stream, 10000)):
            scores = {}
            if atlas_state.get('available'):
                batch_status = {}
                scores = atlas_avi.lookup_many([v['variant_id'] for v in chunk], status=batch_status)
                coverage['atlas_ranking']['queried'] += len(chunk)
                coverage['atlas_ranking']['scored'] += len(scores)
                if not batch_status.get('available'):
                    atlas_state['available'] = False
                    coverage['atlas_ranking'].update(status='partial', available=False)
            for v in chunk:
                yield {**v, '_atlas_avi': scores.get(v['variant_id'])}
    def priority(v):
        avi = (v.get('_atlas_avi') or {}).get('avi_score')
        return (avi is None, -avi if avi is not None else 0, -v['gq'], -v['dp'], v['variant_id'])
    selected = heapq.nsmallest(limit, ranked(), key=priority)
    if coverage['atlas_ranking']['scored']:
        coverage['selection'] = 'PASS autosomal GRCh38, DP ≥10, GQ ≥20; local AVI descending, unscored after scored, then GQ, DP, variant ID'
    records = lookup_from_mirror([v['variant_id'] for v in selected])
    if records is None:
        coverage['status'] = 'clinvar_mirror_unavailable'
        return [], coverage
    coverage['screened'] = len(selected)
    out = []
    for v in selected:
        v = dict(v)
        atlas_result = v.pop('_atlas_avi', None)
        rec = records.get(v['variant_id'])
        sig = str((rec or {}).get('clinical_significance') or '').lower()
        uncertain = 'uncertain' in sig or 'conflicting' in sig
        if rec and not uncertain:
            continue
        description = ('ClinVar classification is uncertain or conflicting; selected for research prediction.' if rec else
                       'No exact match in the available local ClinVar snapshot; selected for research prediction.')
        out.append(Finding(marker=v['variant_id'], source='variant_lookup',
            description=description, tier=Tier.SPECULATIVE, categories=[Category.CLINICAL],
            detail={**v, **(rec or {}), 'novel_candidate': rec is None, 'modality': 'genome', 'topic': 'other',
                    'reference_build': 'GRCh38', 'clinvar_lookup': 'matched' if rec else 'no_match',
                    **({'alphagenome_atlas': atlas_result} if atlas_result else {}),
                    'selection_reason': 'Quality-qualified uncertain or uncatalogued candidate'}))
    out.sort(key=lambda f: (bool(f.detail.get('novel_candidate')), f.marker))
    coverage['uncertain_selected'] = sum(not f.detail['novel_candidate'] for f in out)
    coverage['uncatalogued_selected'] = sum(f.detail['novel_candidate'] for f in out)
    coverage['selected'] = len(out)
    coverage['not_screened'] = coverage['quality_eligible'] - coverage['screened']
    return out, coverage


def enrich(findings, *, offline=False, atlas_live=None):
    """Return per-model statuses, without hiding missing providers as negative scores."""
    from geneask.annotators import alphamissense as am, alphagenome_vep as ag
    status = {}
    am_status = {}
    try:
        am.annotate_findings(findings, status=am_status)
        status['alphamissense'] = am_status
    except Exception:
        status['alphamissense'] = {'status': 'unavailable', 'scored': 0}
    # Atlas is precomputed evidence: local AVI and cache reads follow the output-mode licensing policy.
    # Interactive callers independently choose Atlas lookup and fresh inference.
    try:
        from geneask.annotators import alphagenome_atlas as atlas
        atlas_status = {}
        # Coordinate-only Atlas lookups require a declared assembly. No build
        # is guessed from a chromosome-position marker.
        atlas_findings = [f for f in findings if f.detail.get('reference_build') == 'GRCh38']
        atlas.annotate_findings(atlas_findings, offline=offline if atlas_live is None else not atlas_live,
                                status=atlas_status)
        atlas_status['reference_skipped'] = len(findings) - len(atlas_findings)
        if findings and not atlas_findings:
            atlas_status['status'] = 'reference_not_declared_grch38'
            atlas_status['note'] = 'Atlas requires declared GRCh38 coordinates; these findings were not submitted.'
        status['alphagenome_atlas'] = atlas_status
    except Exception:
        status['alphagenome_atlas'] = {'status': 'unavailable', 'scored': 0}
    if offline:
        status['alphagenome'] = {'status': 'offline', 'scored': 0, 'note': 'Live predictions were not requested for this reference import.'}
    else:
        try:
            pacing = ag.default_pacing()
            ag.annotate_findings(findings, pacing=pacing)
            status['alphagenome'] = pacing.as_dict()
        except Exception:
            status['alphagenome'] = {'status': 'unavailable', 'scored': 0}
    return status


def provider_statuses(status):
    out = []
    for model in ('alphagenome', 'alphamissense', 'alphagenome_atlas'):
        row = status.get(model, {})
        state = row.get('status', 'not_run')
        good = state in ('ready', 'complete', 'ok', 'available') or (state == 'partial' and row.get('scored', 0) > 0)
        out.append(ProviderStatus(name=model, health=Health.OK if good else Health.UNAVAILABLE,
            note=f"{state.replace('_', ' ')}; {row.get('scored', 0)} variants scored. " + str(row.get('note', ''))))
    return out


def explore_variant(variant, *, predict=False, atlas=False):
    from .orchestrate import ReportResult
    from .detect import InputKind
    from geneask.interpret.clinvar_screen import load_panel, index_by_variant_id, _record_link
    from geneask.annotators.clinvar_mirror import lookup_from_mirror
    vid = normalize_variant(variant)
    try:
        lookup = lookup_from_mirror([vid])
        catalogue = 'local_mirror' if lookup is not None else 'bundled_panel'
        records = lookup if lookup is not None else index_by_variant_id(load_panel(), wanted=[vid])
        rec = records.get(vid, {})
        cv_state = 'matched' if rec else ('no_match' if lookup is not None else 'not_found_in_partial_panel')
    except (OSError, ValueError, sqlite3.DatabaseError):
        rec, cv_state, catalogue = {}, 'unavailable', 'unavailable'
    detail = {**rec, 'modality': 'genome', 'reference_build': 'GRCh38',
              'research_candidate': True, 'clinvar_lookup': cv_state, 'clinvar_scope': catalogue,
              'variant_explorer_url': '/explore?variant=' + vid}
    fs = [Finding(marker=vid, source='variant_lookup',
        description='Variant submitted for research exploration; no sample genotype supplied.',
        tier=Tier.SPECULATIVE, categories=[Category.CLINICAL], detail=detail,
        link=_record_link(rec.get('clinvar_variation_id'), rec.get('gene') or ''))]
    # Mirror only: this action must never silently create a second external request.
    try:
        from geneask.annotators.gnomad_mirror import lookup_many, mirror_status
        gs = mirror_status()
        if gs.health == Health.UNAVAILABLE:
            detail['gnomad_status'] = 'unavailable'
        else:
            freq = lookup_many([vid]).get(vid)
            detail['gnomad_status'] = 'matched' if freq else 'no_match'
            if freq:
                detail['gnomad'] = freq
                detail['gnomad_af'] = freq.get('af')
    except (ImportError, OSError, ValueError, sqlite3.DatabaseError):
        detail['gnomad_status'] = 'unavailable'
    if detail.get('gnomad_status') == 'unavailable':
        try:
            from geneask.annotators.gnomad_freq import allele_frequency, LiveBudget
            af = allele_frequency(vid, budget=LiveBudget(limit=0))
            if af is not None:
                detail['gnomad_status'] = 'cached_match'
                detail['gnomad_af'] = af
                detail['gnomad'] = {'af': af, 'provenance': 'local API cache; no live request'}
        except (OSError, ValueError, sqlite3.DatabaseError):
            pass
    status = enrich(fs, offline=not predict, atlas_live=atlas)
    if not predict:
        status['alphagenome'] = {'status': 'not_requested', 'eligible': 1, 'scored': 0, 'note': 'Use Request AlphaGenome prediction to ask the model about this variant.'}
    result = ReportResult(kind=InputKind.VCF, engines=('geneask',), findings=fs,
        provider_status=provider_statuses(status),
        scan_stats={'context':'variant_lookup', 'ai_predictions': status, 'markers_scanned': 1, 'findings_total':len(fs)},
        notes=['Single-variant research lookup. GRCh38 coordinates are declared by the user; no sample genotype or health assessment is inferred.',
               'AlphaGenome scores are research predictions, not disease probabilities. Unscored is not benign.'])
    return result
