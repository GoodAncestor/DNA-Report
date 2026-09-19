"""Validation harness regressions. All biological inputs here are fictional."""
import json
from dataclasses import replace
from pathlib import Path
import sys

import pysam
import pytest

from dnareport import benchmark as b
from dnareport import nanopore as n
from tests.test_nanopore import fixture, stub_tools


def test_native_validation_retains_raw_bam_and_failure(fixture, stub_tools, tmp_path):
    bam, config = fixture
    config = replace(config, validation_dir=str(tmp_path / "evidence"))
    with n.prepare_nanopore(str(bam), config=config) as prepared:
        root = Path(prepared.validation_path)
        raw = Path(prepared.raw_vcf_path)
        assert raw.is_file()
        assert Path(prepared.aligned_bam_path).is_file()
    record = json.loads((root / "validation-status.json").read_text())
    assert record["state"] == "completed"
    assert {"clair3/merge_output.vcf.gz", "aligned.sorted.bam", "cpg.bed", "variants.pass.vcf", "commands.jsonl", "provenance.json"} <= record["files"].keys()
    assert record["files"]["clair3/merge_output.vcf.gz"]["sha256"] == n.sha256(raw)
    assert root.stat().st_mode & 0o777 == 0o700
    with pytest.raises(RuntimeError):
        with n.prepare_nanopore(str(bam), config=config) as second:
            failed = Path(second.validation_path)
            raise RuntimeError("synthetic downstream failure")
    assert json.loads((failed / "validation-status.json").read_text())["state"] == "failed"
    assert (failed / "clair3/merge_output.vcf.gz").is_file()


def test_native_tool_failure_preserves_partial_evidence(fixture, stub_tools, tmp_path, monkeypatch):
    bam, config = fixture
    original = n._run
    def fail(args, **kwargs):
        if kwargs["stage"] == "modkit CpG pileup":
            raise n.NanoporeError("fictional modkit error")
        return original(args, **kwargs)
    monkeypatch.setattr(n, "_run", fail)
    root = tmp_path / "evidence"
    with pytest.raises(n.NanoporeError):
        with n.prepare_nanopore(str(bam), config=replace(config, validation_dir=str(root))):
            pytest.fail("must not yield")
    run = next(root.iterdir())
    status = json.loads((run / "validation-status.json").read_text())
    assert status["state"] == "failed"
    assert (run / "clair3/merge_output.vcf.gz").exists()
    commands = [json.loads(line) for line in (run / "commands.jsonl").read_text().splitlines()]
    assert commands[-1]["state"] == "failed"


def publisher_vcf(path):
    header = pysam.VariantHeader()
    header.add_meta("reference", b.REFERENCE)
    header.contigs.add("chr1", length=1000)
    header.contigs.add("chrX", length=1000)
    for key, typ in (("GT", "String"), ("DP", "Integer"), ("GQ", "Integer")):
        header.formats.add(key, 1, typ, key)
    header.add_sample("SAMPLE")
    header.filters.add("LowQual", None, None, "Low quality")
    with pysam.VariantFile(str(path), "w", header=header) as out:
        cases = [(("A", "C"), (0,1), 20, 30, "PASS", "chr1"),
                 (("A", "T"), (0,1), 2, 30, "PASS", "chr1"),
                 (("A", "T"), (0,None), 20, 30, "PASS", "chr1"),
                 (("A", "T"), (0,0), 20, 30, "PASS", "chr1"),
                 (("A", "T", "A"*60), (1,2), 20, 30, "PASS", "chr1"),
                 (("A", "C"), (0,1), 20, 30, "LowQual", "chr1"),
                 (("A", "C"), (0,1), 20, 30, "PASS", "chrX")]
        for i,(alleles,gt,dp,gq,filt,chrom) in enumerate(cases):
            rec = out.new_record(contig=chrom, start=i*100, alleles=alleles)
            rec.filter.add(filt)
            for key,value in (("GT",gt),("DP",dp),("GQ",gq)):
                rec.samples["SAMPLE"][key] = value
            out.write(rec)


def test_publisher_complete_genotype_scope_and_missingness(tmp_path):
    source, output = tmp_path / "raw.vcf", tmp_path / "filtered.vcf.gz"
    publisher_vcf(source)
    receipt = b.filter_publisher(source, output)
    counts = receipt["counts"]
    assert counts["input_records"] == 7
    assert counts["benchmark_retained_records"] == 1
    assert counts["report_retained_records"] == 2
    assert counts["report_supported_carried_alleles"] == 2
    assert counts["excluded_mixed_scope_genotype"] == 1
    assert sum(v for k,v in counts.items() if k.startswith("excluded_")) + counts["benchmark_retained_records"] == 7
    assert receipt["accuracy_metrics"] is None
    with pysam.VariantFile(str(output)) as vf:
        assert len(list(vf.fetch("chr1"))) == 1
    with pytest.raises(ValueError, match="new file"):
        b.filter_publisher(source, output)


def test_bed_union_denominator_not_variant_positions(tmp_path):
    a = tmp_path / "a.bed"; c = tmp_path / "c.bed"
    a.write_text("chr1\t0\t10\nchr1\t5\t20\nchr1\t30\t40\n")
    c.write_text("chr1\t15\t35\n")
    assert b.intersection_bases(b.bed_intervals(a), b.bed_intervals(c)) == 10
    c.write_text("chr1\t20\t20\n")
    with pytest.raises(ValueError): b.bed_intervals(c)
    c.write_text("chrX\t0\t20\n")
    with pytest.raises(ValueError): b.bed_intervals(c)


def test_methylation_zero_absent_and_low_coverage_distinct(tmp_path):
    left = tmp_path / "a.tsv"; right = tmp_path / "b.tsv"
    header = "chrom\tstart\tvalid_calls\tmodified_calls\n"
    left.write_text(header + "chr1\t0\t10\t0\nchr1\t2\t2\t1\nchr1\t4\t20\t10\nchr1\t6\t10\t9\n")
    right.write_text(header + "chr1\t0\t10\t1\nchr1\t2\t20\t10\nchr1\t4\t40\t20\nchr1\t8\t10\t3\n")
    result = b.methylation_concordance(left, right)
    assert result["left_only"] == result["right_only"] == 1
    assert result["shared_below_coverage"] == 1
    assert result["overall"]["n"] == 2
    assert result["overall"]["mean_bias"] == pytest.approx(-.05)
    assert result["overall"]["mean_absolute_error"] == pytest.approx(.05)
    left.write_text(header + "chr1\t0\t10\t11\n")
    with pytest.raises(ValueError): b.methylation_concordance(left, right)


def make_plan(tmp_path):
    source = tmp_path / "raw.vcf"
    publisher_vcf(source)
    reference = tmp_path / "reference.fa"
    reference.write_text(">chr1\n" + "A"*1000 + "\n>chrX\n" + "A"*1000 + "\n")
    pysam.faidx(str(reference))
    mask = tmp_path / "mask.bed"; mask.write_text("chr1\t0\t800\n")
    regions = tmp_path / "regions.bed"; regions.write_text("chr1\t0\t900\n")
    files = dict(truth_vcf=source, raw_query=source, filtered_query=source,
                 reference_fasta=reference, reference_fai=Path(str(reference)+".fai"),
                 confident_bed=mask, regions_bed=regions)
    plan = dict(schema=1, sample="HG002", build="GRCh38", evidence_kind="synthetic_fixture", truth_release="SYNTHETIC-NOT-TRUTH",
                truth_sample="SAMPLE", query_sample="SAMPLE", happy_version="0.0.fixture",
                reference_compatibility_evidence="Fictional all-A control only",
                acceptance={"purpose":"Fictional harness validation", "minimum_precision":None},
                files={k:{"path":str(p),"sha256":b.sha256(p)} for k,p in files.items()},
                stratifications={"fictional_difficult":{"path":str(mask),"sha256":b.sha256(mask)}})
    path=tmp_path/"plan.json"; b.dump(path,plan)
    return path


def test_plan_hash_drift_fails_and_denominators_preserved(tmp_path):
    plan = make_plan(tmp_path)
    _,_,denom = b.validate_plan(plan)
    assert denom["evaluation_bases"] == 900
    assert denom["confident_evaluation_bases"] == 800
    (tmp_path/"mask.bed").write_text("chr1\t0\t200\n")
    with pytest.raises(ValueError,match="changed pinned"):
        b.validate_plan(plan)


def test_real_subprocess_harness_keeps_commands_and_failure(tmp_path):
    plan = make_plan(tmp_path)
    tool = tmp_path/"fake-happy"
    tool.write_text(f"#!{sys.executable}\n" + '''import sys,pathlib
if '--version' in sys.argv:
 print('hap.py 0.0.fixture');sys.exit(0)
p=pathlib.Path(sys.argv[sys.argv.index('-o')+1])
p.with_suffix('.summary.csv').write_text('Type,Filter,TRUTH.TOTAL,TRUTH.TP,TRUTH.FN,QUERY.TOTAL,QUERY.TP,QUERY.FP,QUERY.UNK,FP.gt\\nSNP,ALL,10,8,2,12,9,1,2,1\\nINDEL,ALL,0,0,0,0,0,0,0,0\\n')
p.with_suffix('.extended.csv').write_text('FICTIONAL SUBPROCESS CONTRACT ONLY\\n')
''')
    tool.chmod(0o700)
    record=b.score(plan,tmp_path/"success",executable=str(tool))
    assert record["state"] == "completed_requires_review"
    assert record["summary_metrics"]["raw"][0]["recall"] == .8
    assert record["summary_metrics"]["raw"][0]["precision"] == .9
    assert record["summary_metrics"]["raw"][1]["recall"] is None
    assert record["benchmark_pass"] is None
    assert len(record["commands"]) == 2
    assert all('--stratification' in argv for argv in record["commands"])
    tool.write_text(f"#!{sys.executable}\nimport sys\nif '--version' in sys.argv: print('hap.py 0.0.fixture')\nelse: print('synthetic failure');sys.exit(7)\n")
    import subprocess
    with pytest.raises(subprocess.CalledProcessError):
        b.score(plan,tmp_path/"failure",executable=str(tool))
    failed=json.loads((tmp_path/"failure/run.json").read_text())
    assert failed["state"] == "failed"
    assert 'synthetic failure' in (tmp_path/"failure/raw.log").read_text()


@pytest.mark.parametrize('receipt_name', ['raw.vcf','output.vcf.gz','output.vcf.gz.tbi','existing.json'])
def test_cli_receipt_collision_never_mutates_source(tmp_path, monkeypatch, receipt_name):
    source=tmp_path/'raw.vcf';publisher_vcf(source)
    original=source.read_bytes()
    existing=tmp_path/'existing.json';existing.write_text('existing evidence')
    output=tmp_path/'output.vcf.gz'
    monkeypatch.setattr(sys,'argv',['benchmark','filter',str(source),str(output),'--receipt',str(tmp_path/receipt_name)])
    with pytest.raises(ValueError,match='distinct new file'):
        b.main()
    assert source.read_bytes()==original
    assert not output.exists()
    assert existing.read_text()=='existing evidence'


def test_independent_plan_rejects_self_comparison(tmp_path):
    path=make_plan(tmp_path)
    plan=json.loads(path.read_text());plan['evidence_kind']='independent_truth';b.dump(path,plan)
    with pytest.raises(ValueError,match='Independent truth'):
        b.validate_plan(path)
