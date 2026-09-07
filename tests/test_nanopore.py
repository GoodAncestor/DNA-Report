"""Synthetic BAM/VCF validation and mocked caller wiring; no human sequence.

The command stub uses real pysam sort/index on a 100bp synthetic contig. It does
NOT validate Dorado, Clair3 models, modkit probabilities or clinical accuracy.
"""
import array
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import time

import pysam
import pytest

from dnareport import nanopore as n


@pytest.fixture
def fixture(tmp_path):
    reference = tmp_path / "ref.fa"
    sequence = "CG" * 50
    reference.write_text(">chr1\n" + sequence + "\n")
    pysam.faidx(str(reference))
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights.pt").write_bytes(b"synthetic stub, not model weights")
    config = n.NanoporeConfig(reference_fasta=str(reference), reference_sha256=n.sha256(reference),
                              clair3_model_path=str(model), clair3_model_id="synthetic_test_only",
                              scratch_dir=str(tmp_path), timeout_seconds=30)
    bam = tmp_path / "sample.bam"
    header = {"HD": {"VN": "1.6", "SO": "coordinate"},
              "SQ": [{"SN": "chr1", "LN": 100, "M5": hashlib.md5(sequence.encode()).hexdigest()}],
              "RG": [{"ID": "r1", "SM": "test_sample", "PL": "ONT"}]}
    with pysam.AlignmentFile(str(bam), "wb", header=header) as out:
        read = pysam.AlignedSegment(out.header)
        read.query_name = "synthetic-read"
        read.query_sequence = "CGCGCGCGCG"
        read.flag = 0
        read.reference_id = 0
        read.reference_start = 0
        read.mapping_quality = 60
        read.cigarstring = "10M"
        read.query_qualities = pysam.qualitystring_to_array("I" * 10)
        read.set_tag("RG", "r1")
        read.set_tag("MM", "C+m?,0,0,0,0,0;")
        read.set_tag("ML", array.array("B", [255] * 5))
        read.set_tag("MN", 10)
        out.write(read)
    return bam, config


def write_vcf(path, *, sample="test_sample", ref="C", chrom="chr1"):
    header = pysam.VariantHeader()
    header.add_meta("fileformat", "VCFv4.2")
    header.add_meta("clair3_version", "synthetic-stub")
    header.contigs.add(chrom, length=100)
    header.formats.add("GT", 1, "String", "Genotype")
    header.formats.add("DP", 1, "Integer", "Depth")
    header.formats.add("GQ", 1, "Integer", "Genotype quality")
    header.add_sample(sample)
    path.parent.mkdir(exist_ok=True)
    with pysam.VariantFile(str(path), "wz" if str(path).endswith(".gz") else "w", header=header) as out:
        record = out.new_record(contig=chrom, start=0, alleles=(ref, "T"))
        record.filter.add("PASS")
        record.samples[sample]["GT"] = (0, 1)
        record.samples[sample]["DP"] = 20
        record.samples[sample]["GQ"] = 30
        out.write(record)


@pytest.fixture
def stub_tools(monkeypatch):
    commands = []
    monkeypatch.setattr(n, "_tool", lambda name, label: name)

    def run(args, *, stage, config, work, deadline, stdout=None):
        commands.append((stage, args))
        if stage == "BAM sorting":
            pysam.sort("-o", args[args.index("-o") + 1], args[-1])
        elif stage == "BAM indexing":
            pysam.index(args[-1])
        elif stage == "alignment coverage":
            Path(args[args.index("-o") + 1]).write_text(pysam.coverage(args[-1]))
        elif stage == "Clair3 variant calling":
            directory = next(x.split("=", 1)[1] for x in args if x.startswith("--output="))
            sample = next(x.split("=", 1)[1] for x in args if x.startswith("--sample_name="))
            write_vcf(Path(directory) / "merge_output.vcf.gz", sample=sample)
        elif stage == "modkit CpG pileup":
            Path(args[3]).write_text("chr1\t0\t1\tm\t10\t.\t0\t1\t0,0,0\t10\t100\t10\t0\t0\t0\t0\t0\t0\n")
        elif "version capture" in stage:
            stdout.write_text("modkit 0.6.4\n" if args[0] == "modkit" else "synthetic stub 1.3.2\n")
        elif stage == "alignment":
            import shutil
            shutil.copyfile(args[3], stdout)
        else:
            raise AssertionError(stage)
    monkeypatch.setattr(n, "_run", run)
    return commands


def test_same_bam_feeds_both_streams_and_cleanup(fixture, stub_tools):
    bam, config = fixture
    with n.prepare_nanopore(str(bam), config=config) as result:
        output = Path(result.aligned_bam_path)
        assert output.exists()
        assert result.sample_id == "test_sample"
        assert result.provenance["reference_verification"] == "BAM SQ M5 checked"
        assert result.provenance["variant_qc"]["called_genotypes"] == 1
        assert result.provenance["alignment_qc"]["reads_with_mod_tags"] == 1
        assert result.provenance["methylation"]["combined_strands"]
        assert not result.provenance["methylation"]["combine_modifications"]
        calling = next(args for stage, args in stub_tools if stage == "Clair3 variant calling")
        pileup = next(args for stage, args in stub_tools if stage == "modkit CpG pileup")
        assert f"--bam_fn={output}" in calling
        assert str(output) in pileup
        assert "--platform=ont" in calling
        assert "--ctg_name=chr1" in calling
        assert "--combine-mods" not in pileup
        assert str(bam.parent) not in repr(result.provenance)
    assert not output.parent.exists()
    assert bam.exists()


def test_cleanup_when_consumer_fails(fixture, stub_tools):
    bam, config = fixture
    with pytest.raises(RuntimeError):
        with n.prepare_nanopore(str(bam), config=config) as result:
            output = Path(result.aligned_bam_path)
            raise RuntimeError("renderer failed")
    assert not output.parent.exists()


def test_reference_checksum_failure_before_tools(fixture, monkeypatch):
    bam, config = fixture
    monkeypatch.setattr(n, "_tool", lambda *args: pytest.fail("must not invoke tools"))
    with pytest.raises(n.NanoporeConfigurationError, match="does not match"):
        with n.prepare_nanopore(str(bam), config=replace(config, reference_sha256="0" * 64)):
            pass


def test_missing_caller_is_an_error_not_bam_variant_interpretation(fixture, monkeypatch):
    bam, config = fixture
    monkeypatch.setattr(n.shutil, "which", lambda name: None if name == config.clair3 else name)
    with pytest.raises(n.NanoporeConfigurationError, match="Clair3"):
        with n.prepare_nanopore(str(bam), config=config):
            pass


def test_sample_mismatch(fixture, stub_tools):
    bam, config = fixture
    with pytest.raises(n.NanoporeError, match="sample ID does not match"):
        with n.prepare_nanopore(str(bam), config=config, sample_id="different"):
            pass
    assert all("version capture" in stage for stage, _ in stub_tools)


def test_precomputed_vcf_reuses_calls_and_checks_reference(fixture, stub_tools):
    bam, config = fixture
    supplied = bam.parent / "precomputed.vcf"
    write_vcf(supplied)
    with n.prepare_nanopore(str(bam), config=replace(config, clair3_model_path=""), vcf_path=str(supplied)) as result:
        assert result.provenance["variant_source"] == "supplied VCF"
        assert any("biological sample identity" in note for note in result.notes)
    assert not any(stage == "Clair3 variant calling" for stage, _ in stub_tools)
    assert supplied.exists()
    write_vcf(supplied, ref="A")
    with pytest.raises(n.NanoporeError, match="REF allele"):
        with n.prepare_nanopore(str(bam), config=config, vcf_path=str(supplied)):
            pass


def test_supplied_vcf_sample_mismatch(fixture, stub_tools):
    bam, config = fixture
    supplied = bam.parent / "precomputed.vcf"
    write_vcf(supplied, sample="someone_else")
    with pytest.raises(n.NanoporeError, match="exactly the same sample"):
        with n.prepare_nanopore(str(bam), config=config, vcf_path=str(supplied)):
            pass


def rewrite_bam(path, transform_header=lambda h: h, transform_read=lambda r: r):
    new = path.with_suffix(".new.bam")
    with pysam.AlignmentFile(str(path), "rb") as src:
        with pysam.AlignmentFile(str(new), "wb", header=transform_header(src.header.to_dict())) as out:
            for read in src:
                out.write(transform_read(read))
    new.replace(path)


def test_missing_modification_tags_fail(fixture, stub_tools):
    bam, config = fixture
    def strip(read):
        read.set_tag("MM", None)
        read.set_tag("ML", None)
        return read
    rewrite_bam(bam, transform_read=strip)
    with pytest.raises(n.NanoporeError, match="No usable cytosine 5mC"):
        with n.prepare_nanopore(str(bam), config=config):
            pass


def test_unverified_reference_requires_realignment(fixture, stub_tools):
    bam, config = fixture
    def strip(header):
        del header["SQ"][0]["M5"]
        return header
    rewrite_bam(bam, transform_header=strip)
    with n.prepare_nanopore(str(bam), config=config) as result:
        assert result.provenance["reference_verification"] == "realigned to pinned FASTA"
    assert any(stage == "alignment" for stage, _ in stub_tools)


def test_mixed_samples_refused(fixture):
    bam, _ = fixture
    def add(header):
        header["RG"].append({"ID": "r2", "SM": "other", "PL": "ONT"})
        return header
    rewrite_bam(bam, transform_header=add)
    with pytest.raises(n.NanoporeError, match="multiple sample"):
        n._bam_info(str(bam))


def test_process_timeout_kills_and_reports_stage(tmp_path):
    config = n.NanoporeConfig(reference_fasta="unused", reference_sha256="0" * 64)
    with pytest.raises(n.NanoporeError, match="time limit"):
        n._run([sys.executable, "-c", "import time; time.sleep(60)"], stage="test stage",
               config=config, work=tmp_path, deadline=time.monotonic() + .1)


def test_process_error_does_not_leak_logs(tmp_path):
    config = n.NanoporeConfig(reference_fasta="unused", reference_sha256="0" * 64)
    with pytest.raises(n.NanoporeError, match="exit 2") as err:
        n._run([sys.executable, "-c", "import sys; print('private-path-secret'); sys.exit(2)"], stage="test stage",
               config=config, work=tmp_path, deadline=time.monotonic() + 5)
    assert "private-path-secret" not in str(err.value)


def test_invalid_env_numbers(monkeypatch):
    monkeypatch.setenv("DNAREPORT_ONT_THREADS", "all")
    with pytest.raises(n.NanoporeConfigurationError, match="numeric"):
        n.NanoporeConfig.from_env()


def test_opt_in_measurement_archive_survives_scratch_cleanup(fixture, stub_tools):
    import json
    bam, config = fixture
    destination = bam.parent / "private-results"
    config = replace(config, artifact_dir=str(destination))
    with n.prepare_nanopore(str(bam), config=config) as result:
        archive = Path(result.artifacts_path)
        assert archive.is_dir()
        assert archive.stat().st_mode & 0o777 == 0o700
        assert n.sha256(archive / "cpg.bed") == result.provenance["bedmethyl_sha256"]
        assert n.sha256(archive / "variants.pass.vcf") == result.provenance["vcf_sha256"]
        metadata = json.loads((archive / "provenance.json").read_text())
        assert metadata["provenance"] == result.provenance
        assert str(destination) not in repr(result.provenance)
        assert not list(archive.glob("*.bam"))
        temporary = Path(result.aligned_bam_path).parent
    assert archive.is_dir()
    assert not temporary.exists()
    with n.prepare_nanopore(str(bam), config=config) as result:
        assert result.artifacts_path != str(archive)


@pytest.mark.parametrize("field,value,reason", [("DP", 2, "depth"), ("GQ", 0, "genotype_quality"),
    ("DP", None, "depth"), ("GQ", None, "genotype_quality"), ("GT", (0, None), "genotype")])
def test_interpretation_gate_excludes_low_or_unknown_quality(fixture, stub_tools, field, value, reason):
    bam, config = fixture
    supplied = bam.parent / "input.vcf"
    write_vcf(supplied)
    filtered = bam.parent / "modified.vcf"
    with pysam.VariantFile(str(supplied)) as source:
        with pysam.VariantFile(str(filtered), "w", header=source.header) as output:
            for record in source:
                record.samples["test_sample"][field] = value
                output.write(record)
    with n.prepare_nanopore(str(bam), config=config, vcf_path=str(filtered)) as result:
        assert result.provenance["variant_qc"]["excluded"][reason] == 1
        assert result.provenance["variant_qc"]["reported_records"] == 0
        with pysam.VariantFile(result.vcf_path) as passing:
            assert list(passing) == []


def test_explicit_pass_required(fixture, stub_tools):
    bam, config = fixture
    supplied = bam.parent / "input.vcf"
    write_vcf(supplied)
    supplied.write_text(supplied.read_text().replace("\tPASS\t", "\t.\t"))
    with n.prepare_nanopore(str(bam), config=config, vcf_path=str(supplied)) as result:
        assert result.provenance["variant_qc"]["excluded"]["filter"] == 1


def test_modification_length_integrity(fixture):
    bam, _ = fixture
    def wrong_length(read):
        read.set_tag("MN", 12)
        return read
    rewrite_bam(bam, transform_read=wrong_length)
    with pytest.raises(n.NanoporeError, match="MN tag"):
        n._bam_info(str(bam))


def test_partial_read_group_sample_ids_are_not_verified(fixture):
    bam, _ = fixture
    def add(header):
        header["RG"].append({"ID": "unlabelled", "PL": "ONT"})
        return header
    rewrite_bam(bam, transform_header=add)
    with pytest.raises(n.NanoporeError, match="Some BAM read groups lack"):
        n._bam_info(str(bam))


@pytest.mark.parametrize("alt", ["<DEL>", "C[chr1:12[", "C" + "A" * 51])
def test_structural_and_long_variants_do_not_enter_small_variant_report(fixture, stub_tools, alt):
    bam, config = fixture
    supplied = bam.parent / "input.vcf"
    write_vcf(supplied)
    supplied.write_text(supplied.read_text().replace("\tC\tT\t", "\tC\t" + alt + "\t"))
    with n.prepare_nanopore(str(bam), config=config, vcf_path=str(supplied)) as result:
        assert result.provenance["variant_qc"]["excluded"]["outside_scope"] == 1
        assert result.provenance["variant_qc"]["reported_records"] == 0


def test_coverage_denominator_includes_autosomes_missing_from_bam(tmp_path):
    coverage = tmp_path / "coverage.tsv"
    coverage.write_text("#rname startpos endpos numreads covbases coverage meandepth meanbaseq meanmapq\nchr1\t1\t8\t1\t8\t100\t1\t40\t60\n")
    summary = n._coverage(coverage, {"chr1": 8, "chr2": 8})
    assert summary["autosomal_reference_bases"] == 16
    assert summary["autosomal_bases_summarized"] == 8
    assert summary["autosomal_breadth_fraction"] == .5
    assert summary["autosomal_mean_depth"] == .5
    assert not summary["callability_assessed"]


def test_old_modkit_fails_before_expensive_calling(fixture, stub_tools, monkeypatch):
    bam, config = fixture
    run = n._run
    def old_modkit(args, **kwargs):
        if args == ["modkit", "--version"]:
            kwargs["stdout"].write_text("modkit 0.6.0\n")
        else:
            run(args, **kwargs)
    monkeypatch.setattr(n, "_run", old_modkit)
    with pytest.raises(n.NanoporeConfigurationError, match="modkit >= 0.6.2"):
        with n.prepare_nanopore(str(bam), config=config):
            pass
    assert not any(stage == "Clair3 variant calling" for stage, _ in stub_tools)
