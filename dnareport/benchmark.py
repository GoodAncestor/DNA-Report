# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local, explicit-input validation tools. No downloads or clinical conclusions.

Publisher filtering is preparation, not accuracy. Accuracy requires an installed
hap.py and pinned independent truth, confidence regions and matching reference.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import json
import math
import contextlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from .nanopore import sha256
from .reference_dataset import AUTOSOMES, REFERENCE


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def filter_destinations(source, output, receipt=None):
    """Reject every collision before touching either VCF or receipt output."""
    source = Path(source).resolve()
    destinations = [Path(output)]
    if str(output).endswith(".gz"):
        destinations.append(Path(str(output) + ".tbi"))
    if receipt is not None:
        destinations.append(Path(receipt))
    resolved = [p.resolve() for p in destinations]
    if (source in resolved or len(set(resolved)) != len(resolved)
            or any(p.exists() or p.is_symlink() for p in destinations)):
        raise ValueError("Every output/index/receipt must be a distinct new file, separate from the source")


def filter_publisher(source, output, *, min_dp=10, min_gq=20):
    """Retain complete report-eligible genotypes, preserving original VCF fields.

    The report can annotate a supported allele of a mixed-size genotype. A
    genotype benchmark cannot silently replace its unsupported allele: exclude
    that entire record and count this narrower comparison scope explicitly.
    """
    import pysam
    if min_dp < 1 or min_gq < 0:
        raise ValueError("Invalid prespecified filter thresholds")
    filter_destinations(source, output)
    source, output = Path(source).resolve(), Path(output).resolve()
    counts = Counter()
    with pysam.VariantFile(str(source)) as vf:
        if list(vf.header.samples) != ["SAMPLE"] or f"##reference={REFERENCE}\n" not in str(vf.header):
            raise ValueError("Expected the publisher SAMPLE and exact GRCh38 reference header")
        with pysam.VariantFile(str(output), "wz" if output.suffix == ".gz" else "w", header=vf.header) as out:
            for rec in vf:
                counts["input_records"] += 1
                reason = None
                if rec.chrom.removeprefix("chr") not in AUTOSOMES:
                    reason = "non_autosomal"
                elif set(rec.filter.keys()) != {"PASS"}:
                    reason = "filter"
                else:
                    call = rec.samples["SAMPLE"]
                    gt = call.get("GT")
                    if not gt or len(gt) != 2 or any(i is None or i < 0 or i >= len(rec.alleles) for i in gt):
                        reason = "genotype"
                    elif call.get("DP") is None or call.get("GQ") is None or call["DP"] < min_dp or call["GQ"] < min_gq:
                        reason = "quality"
                    else:
                        carried = set(gt) - {0}
                        supported = {i for i in carried if max(len(rec.ref), len(rec.alleles[i])) <= 50
                                     and all(b in "ACGT" for b in rec.ref + rec.alleles[i])}
                        counts["report_supported_carried_alleles"] += len(supported)
                        counts["unsupported_carried_alleles"] += len(carried - supported)
                        if supported:
                            counts["report_retained_records"] += 1
                        if not carried:
                            reason = "reference_genotype"
                        elif not supported:
                            reason = "unsupported_alleles"
                        elif supported != carried:
                            reason = "mixed_scope_genotype"
                if reason:
                    counts["excluded_" + reason] += 1
                else:
                    out.write(rec)
                    counts["benchmark_retained_records"] += 1
    if output.suffix == ".gz":
        pysam.tabix_index(str(output), preset="vcf", force=False)
    return {"status": "filtering_only_accuracy_pending", "counts": dict(counts),
            "input_sha256": sha256(source), "output_sha256": sha256(output),
            "thresholds": {"min_dp": min_dp, "min_gq": min_gq, "max_allele_length": 50},
            "scope": "Autosomal PASS complete diploid carried small genotypes; mixed-scope records excluded in full",
            "accuracy_metrics": None}


def bed_intervals(path):
    """Read/merge autosomal half-open intervals; never infer callability from VCF."""
    grouped = {}
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 3:
                raise ValueError("BED needs at least three columns")
            chrom, start, end = fields[0], int(fields[1]), int(fields[2])
            if start < 0 or end <= start:
                raise ValueError("BED has invalid half-open coordinates")
            if chrom.removeprefix("chr") not in AUTOSOMES:
                raise ValueError("This benchmark requires prespecified autosomal intervals only")
            grouped.setdefault(chrom, []).append((start, end))
    merged = {}
    for chrom, rows in grouped.items():
        result = []
        for start, end in sorted(rows):
            if result and start <= result[-1][1]:
                result[-1] = (result[-1][0], max(end, result[-1][1]))
            else:
                result.append((start, end))
        merged[chrom] = result
    if not merged:
        raise ValueError("BED cannot be empty")
    return merged


def intersection_bases(a, b):
    total = 0
    for chrom, left in a.items():
        right = b.get(chrom, [])
        i = j = 0
        while i < len(left) and j < len(right):
            total += max(0, min(left[i][1], right[j][1]) - max(left[i][0], right[j][0]))
            if left[i][1] <= right[j][1]:
                i += 1
            else:
                j += 1
    return total


def validate_plan(plan_path):
    """Resolve only explicitly pinned files; missing truth is a hard preflight gap."""
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text())
    if plan.get("schema") != 1 or plan.get("sample") != "HG002" or plan.get("build") != "GRCh38":
        raise ValueError("Require schema 1, HG002 and GRCh38")
    if not plan.get("truth_release") or not plan.get("reference_compatibility_evidence"):
        raise ValueError("Record truth release and reviewed reference compatibility evidence")
    if not isinstance(plan.get("acceptance"), dict) or not plan["acceptance"].get("purpose"):
        raise ValueError("Prespecify acceptance purpose and limits (null means no pass claim)")
    files = {}
    for key in ("truth_vcf", "confident_bed", "regions_bed", "reference_fasta", "reference_fai", "raw_query", "filtered_query"):
        item = plan["files"][key]
        path = (plan_path.parent / item["path"]).resolve()
        if not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError(f"Missing pinned SHA256: {key}")
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"Missing or changed pinned input: {key}")
        files[key] = path
    strata = plan.get("stratifications", {})
    if not isinstance(strata, dict) or not strata:
        raise ValueError("Prespecify at least one named difficult-region stratification")
    for name, item in strata.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid stratification name")
        path = (plan_path.parent / item["path"]).resolve()
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError(f"Missing or changed stratification: {name}")
        files["stratum_" + name] = path
    if str(files["reference_fasta"]) + ".fai" != str(files["reference_fai"]):
        raise ValueError("FAI must index the selected FASTA at its adjacent .fai path")
    lengths = {}
    for line in files["reference_fai"].read_text().splitlines():
        f = line.split("\t")
        lengths[f[0]] = int(f[1])
    mask, regions = bed_intervals(files["confident_bed"]), bed_intervals(files["regions_bed"])
    for collection in (mask, regions, *(bed_intervals(files["stratum_" + name]) for name in strata)):
        for chrom, rows in collection.items():
            if chrom not in lengths or any(end > lengths[chrom] for _, end in rows):
                raise ValueError("BED/reference contig mismatch or out-of-reference interval")
    assessed = intersection_bases(mask, regions)
    if not assessed:
        raise ValueError("Evaluation regions have no confident reference bases")
    evidence_kind = plan.get("evidence_kind", "independent_truth")
    if evidence_kind not in {"independent_truth", "synthetic_fixture"}:
        raise ValueError("Unknown benchmark evidence kind")
    if evidence_kind == "independent_truth":
        for key in ("raw_query", "filtered_query"):
            if (files["truth_vcf"].samefile(files[key])
                    or plan["files"]["truth_vcf"]["sha256"] == plan["files"][key]["sha256"]):
                raise ValueError("Independent truth cannot be the query file or identical bytes")
    import pysam
    for name, sample in (("truth_vcf", plan.get("truth_sample")),
                         ("raw_query", plan.get("query_sample")),
                         ("filtered_query", plan.get("query_sample"))):
        with pysam.VariantFile(str(files[name])) as vf:
            if not sample or list(vf.header.samples) != [sample]:
                raise ValueError(f"Wrong or multiple samples: {name}")
    return plan, files, {"evaluation_bases": sum(e-s for rows in regions.values() for s,e in rows),
                         "confident_evaluation_bases": assessed,
                         "coverage_exclusions": "none; low/missing query coverage remains in recall denominator"}


def score(plan_path, output_dir, *, executable="hap.py", timeout=3600, threads=2):
    """Run raw and filtered queries against the SAME fixed truth/mask/regions.

    Retain hap.py's own summary/extended tables and annotated output. Do not
    replace haplotype-aware matching with naive position/allele equality.
    """
    if not 1 <= threads <= 16 or not 1 <= timeout <= 86400:
        raise ValueError("Invalid local benchmark resource bounds")
    plan, files, denominators = validate_plan(plan_path)
    tool = shutil.which(executable)
    if not tool:
        raise ValueError("hap.py is not installed; accuracy remains pending")
    expected = plan.get("happy_version")
    if not expected or not re.fullmatch(r"[A-Za-z0-9_.+-]+", expected):
        raise ValueError("Pin the installed hap.py version before execution")
    version = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True, timeout=30)
    actual_version = (version.stdout + version.stderr).strip()
    if not re.search(r"(?<![A-Za-z0-9_.+-])" + re.escape(expected) + r"(?![A-Za-z0-9_.+-])", actual_version):
        raise ValueError("hap.py version differs from the prespecified plan")
    destination = Path(output_dir).resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copyfile(plan_path, destination / "plan.json")
    record = {"state": "running", "started_unix": time.time(), "denominators": denominators,
              "plan_sha256": sha256(plan_path), "tool_version": actual_version,
              "tool_executable_sha256": sha256(tool), "commands": [], "clinical_validity": False,
              "benchmark_pass": None, "acceptance": plan["acceptance"], "summary_metrics": {},
              "evidence_kind": plan.get("evidence_kind", "independent_truth")}
    dump(destination / "run.json", record)
    stratification = destination / "stratification.tsv"
    stratification.write_text("".join(name + "\t" + str(files["stratum_" + name]) + "\n"
                                     for name in sorted(plan["stratifications"])))
    deadline = time.monotonic() + timeout
    try:
        for label in ("raw", "filtered"):
            argv = [tool, str(files["truth_vcf"]), str(files[label + "_query"]),
                    "-f", str(files["confident_bed"]), "-T", str(files["regions_bed"]),
                    "-r", str(files["reference_fasta"]), "-o", str(destination / label),
                    "--threads", str(threads), "--engine", "xcmp",
                    "--stratification", str(stratification)]
            record["commands"].append(argv)
            dump(destination / "run.json", record)
            with (destination / (label + ".log")).open("wb") as log:
                proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = proc.wait(timeout=max(.01, deadline-time.monotonic()))
                    if code:
                        raise subprocess.CalledProcessError(code, argv)
                except BaseException:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    raise
            if not (destination / (label + ".summary.csv")).is_file() or not (destination / (label + ".extended.csv")).is_file():
                raise ValueError("hap.py did not produce required summary and stratified metrics")
            record["summary_metrics"][label] = read_summary(destination / (label + ".summary.csv"))
        record["state"] = "completed_requires_review"
    except BaseException as exc:
        record.update(state="failed", exception_type=type(exc).__name__)
        raise
    finally:
        record["finished_unix"] = time.time()
        record["artifacts"] = {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                               for p in sorted(destination.iterdir()) if p.is_file() and p.name != "run.json"}
        dump(destination / "run.json", record)
    return record


def read_summary(path):
    """Keep truth/query TP denominators distinct, as specified by hap.py."""
    rows = []
    count_fields = ("TRUTH.TOTAL", "TRUTH.TP", "TRUTH.FN", "QUERY.TOTAL", "QUERY.TP", "QUERY.FP", "QUERY.UNK", "FP.gt")
    with Path(path).open() as handle:
        for source in csv.DictReader(handle):
            if source["Filter"] != "ALL" or source["Type"] not in {"SNP", "INDEL"}:
                continue
            row = {"Type": source["Type"], "Filter": "ALL"}
            for name in count_fields:
                value = float(source[name])
                if not math.isfinite(value) or value < 0 or not value.is_integer():
                    raise ValueError("Invalid hap.py count")
                row[name] = int(value)
            td = row["TRUTH.TP"] + row["TRUTH.FN"]
            qd = row["QUERY.TP"] + row["QUERY.FP"]
            recall = row["TRUTH.TP"]/td if td else None
            precision = row["QUERY.TP"]/qd if qd else None
            row.update(recall=recall, precision=precision,
                       f1=(2*recall*precision/(recall+precision) if recall+precision else 0)
                       if recall is not None and precision is not None else None)
            rows.append(row)
    if len(rows) != 2 or {r["Type"] for r in rows} != {"SNP", "INDEL"}:
        raise ValueError("Require one ALL summary row each for SNP and INDEL")
    return rows


def methylation_concordance(left, right, *, min_coverage=5):
    """Compare pre-pooled integer-count TSVs, never equating absent and zero.

    Columns: chrom,start,valid_calls,modified_calls. Inputs must already represent
    matched build, forward CpG, combined strands and the same modification.
    Their biological/assay compatibility is an explicit external prerequisite.
    """
    if min_coverage < 5:
        raise ValueError("Minimum coverage must be at least five for these depth strata")
    def read(path):
        rows = {}
        with Path(path).open() as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                key = row["chrom"], int(row["start"])
                valid, modified = int(row["valid_calls"]), int(row["modified_calls"])
                if key in rows or key[1] < 0 or valid < 0 or not 0 <= modified <= valid:
                    raise ValueError("Duplicate/invalid CpG counts")
                rows[key] = valid, modified
        return rows
    a, b = read(left), read(right)
    groups = {"5-9": [], "10-19": [], "20+": []}
    low = 0
    for key in a.keys() & b.keys():
        av, am = a[key]; bv, bm = b[key]
        if min(av, bv) < min_coverage:
            low += 1
            continue
        difference = am/av - bm/bv
        label = "5-9" if min(av, bv) < 10 else "10-19" if min(av, bv) < 20 else "20+"
        groups[label].append(difference)
    def summary(values):
        return {"n": len(values), "mean_bias": sum(values)/len(values) if values else None,
                "mean_absolute_error": sum(abs(v) for v in values)/len(values) if values else None}
    return {"kind": "processing_concordance_not_independent_accuracy", "left_only": len(a.keys()-b.keys()),
            "right_only": len(b.keys()-a.keys()), "shared_below_coverage": low,
            "shared_sites": len(a.keys() & b.keys()), "min_coverage": min_coverage,
            "depth_strata": {key: summary(value) for key,value in groups.items()},
            "overall": summary([v for values in groups.values() for v in values]),
            "left_sha256": sha256(left), "right_sha256": sha256(right)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    filt = sub.add_parser("filter")
    filt.add_argument("source"); filt.add_argument("output"); filt.add_argument("--receipt", required=True)
    check = sub.add_parser("preflight")
    check.add_argument("plan")
    run = sub.add_parser("score")
    run.add_argument("plan"); run.add_argument("output"); run.add_argument("--happy", default="hap.py")
    args = parser.parse_args()
    if args.command == "filter":
        filter_destinations(args.source, args.output, args.receipt)
        dump(args.receipt, filter_publisher(args.source, args.output))
    elif args.command == "preflight":
        print(json.dumps(validate_plan(args.plan)[2], indent=2))
    else:
        print(json.dumps(score(args.plan, args.output, executable=args.happy), indent=2))


if __name__ == "__main__":
    main()
