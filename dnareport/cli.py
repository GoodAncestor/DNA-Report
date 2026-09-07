# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""DNA-Report CLI: `dna-report analyze <file>`."""
from __future__ import annotations
import argparse, json, os, math
from pathlib import Path
from .detect import route, InputKind
from .orchestrate import analyze, compare
from .report import render_report, compose_result_views


def main():
    ap = argparse.ArgumentParser(prog="dna-report")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="detect file type + routing")
    d.add_argument("file")

    a = sub.add_parser("analyze", help="analyze an upload -> merged report")
    a.add_argument("file")
    a.add_argument("--traits", help="optional trait table for GeneAsk")
    a.add_argument("--reference", help="local GRCh38 FASTA; its SHA256 must be configured in DNAREPORT_ONT_REFERENCE_SHA256")
    a.add_argument("--reference-build", choices=("GRCh38", "hg38"), help="required assembly declaration for bedMethyl")
    a.add_argument("--sample-id", help="private sample label; must agree with BAM/VCF sample metadata")
    a.add_argument("--tissue", help="sample tissue, e.g. blood or saliva")
    a.add_argument("--age", type=float)
    a.add_argument("--sex", choices=("male", "female", "other"))
    a.add_argument("--min-coverage", type=int, default=5, help="minimum valid methylation calls per CpG (default: 5)")
    a.add_argument("--combined-strands", action="store_true", help="declare strand-combined modkit bedMethyl input")
    a.add_argument("--nanopore-vcf", help="reuse a single-sample VCF with its source BAM; validate sample label and reference")
    a.add_argument("--artifacts-dir", help="retain private VCF, bedMethyl and provenance in a new directory below this path")
    a.add_argument("--out", default="report.html")

    c = sub.add_parser("compare", help="reconcile multiple tests of one person "
                                       "(merged multi-sample VCF) -> concordance report")
    c.add_argument("vcf", help="merged multi-sample VCF (one sample column per test)")
    c.add_argument("--out", default="compare_report.html")

    args = ap.parse_args()
    if args.cmd == "detect":
        kind, engines = route(args.file)
        print(json.dumps({"file": args.file, "kind": kind.value, "engines": list(engines)}))
    elif args.cmd == "analyze":
        try:
            if args.age is not None and (not math.isfinite(args.age) or not 0 <= args.age <= 120):
                ap.error("--age must be a number between 0 and 120")
            target = Path(args.out)
            if target.suffix.lower() not in (".html", ".htm"):
                ap.error("--out must name an .html or .htm report")
            if Path(args.file).resolve() in {target.resolve(), target.with_suffix(".json").resolve(), target.with_suffix(".md").resolve()}:
                ap.error("Report outputs must not overwrite the input file")
            input_kind, _ = route(args.file)
            if (args.artifacts_dir or args.nanopore_vcf) and input_kind not in (InputKind.MODBAM, InputKind.POD5):
                ap.error("--artifacts-dir and --nanopore-vcf require a source BAM or POD5 input")
            config = None
            if args.artifacts_dir:
                from dataclasses import replace
                from .nanopore import NanoporeConfig
                config = replace(NanoporeConfig.from_env(), artifact_dir=args.artifacts_dir)
            res = analyze(args.file, trait_table=args.traits, reference_fasta=args.reference,
                          nanopore_config=config,
                          reference_build=args.reference_build, sample_id=args.sample_id,
                          tissue=args.tissue, age=args.age, sex=args.sex,
                          min_coverage=args.min_coverage, combined_strands=args.combined_strands,
                          nanopore_vcf=args.nanopore_vcf)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if input_kind in (InputKind.MODBAM, InputKind.POD5, InputKind.BEDMETHYL):
                target.touch(mode=0o600, exist_ok=True)
                target.chmod(0o600)
            out = render_report(res, str(target), filename=Path(args.file).name)
            exports = {}
            if res.kind in (InputKind.MODBAM, InputKind.POD5, InputKind.BEDMETHYL):
                target.chmod(0o600)
                views = compose_result_views(res, filename=Path(args.file).name)
                for ext, content in (("json", json.dumps(views["json"], indent=2)), ("md", views["markdown"])):
                    sidecar = target.with_suffix("." + ext)
                    with open(sidecar, "w", opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
                        handle.write(content)
                    sidecar.chmod(0o600)
                    exports[ext] = str(sidecar)
        except (ValueError, OSError) as exc:
            ap.error(str(exc))
        print(json.dumps({
            "kind": res.kind.value, "engines": list(res.engines),
            "n_findings": len(res.findings), "report": out, "exports": exports, "notes": res.notes,
        }))
    elif args.cmd == "compare":
        res = compare(args.vcf)
        out = render_report(res, args.out, filename=args.vcf)
        print(json.dumps({
            "mode": "compare", "n_findings": len(res.findings),
            "report": out, "notes": res.notes,
        }))


if __name__ == "__main__":
    main()
