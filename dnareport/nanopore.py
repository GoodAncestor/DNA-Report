# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local, bounded ONT preparation; no model or reference downloads.

Both analysis streams derive from the same sorted modBAM. Clair3 calls autosomal
small germline variants; modkit makes a strand-combined CpG bedMethyl. This is a
research pipeline, not a validation of a caller/model or a clinical assay.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field


class NanoporeError(ValueError):
    """Input or preparation failure; safe to display without subprocess logs."""


class NanoporeConfigurationError(NanoporeError):
    """Operator must configure a local tool, model, reference, or resource bound."""


@dataclass(frozen=True)
class NanoporeConfig:
    reference_fasta: str
    reference_sha256: str
    clair3_model_path: str = ""
    clair3_model_id: str = ""
    reference_build: str = "GRCh38"
    samtools: str = "samtools"
    modkit: str = "modkit"
    clair3: str = "run_clair3.sh"
    dorado: str = "dorado"
    dorado_model_path: str = ""
    dorado_mod_model_path: str = ""
    device: str = "cpu"
    threads: int = 4
    timeout_seconds: int = 86400
    scratch_dir: str | None = None
    artifact_dir: str | None = None
    scratch_limit_gb: int = 500
    sort_memory_mb: int = 512
    min_variant_coverage: int = 8
    min_genotype_quality: int = 20
    mod_probability: float = 0.8

    @classmethod
    def from_env(cls):
        """Only server/local environment supplies paths and executable names."""
        e = os.environ
        try:
            return cls(
                reference_fasta=e.get("DNAREPORT_ONT_REFERENCE", ""),
                reference_sha256=e.get("DNAREPORT_ONT_REFERENCE_SHA256", ""),
                reference_build=e.get("DNAREPORT_ONT_BUILD", "GRCh38"),
                clair3_model_path=e.get("DNAREPORT_ONT_CLAIR3_MODEL", ""),
                clair3_model_id=e.get("DNAREPORT_ONT_CLAIR3_MODEL_ID", ""),
                samtools=e.get("DNAREPORT_ONT_SAMTOOLS", "samtools"),
                modkit=e.get("DNAREPORT_ONT_MODKIT", "modkit"),
                clair3=e.get("DNAREPORT_ONT_CLAIR3", "run_clair3.sh"),
                dorado=e.get("DNAREPORT_ONT_DORADO", "dorado"),
                dorado_model_path=e.get("DNAREPORT_ONT_DORADO_MODEL", ""),
                dorado_mod_model_path=e.get("DNAREPORT_ONT_MOD_MODEL", ""),
                device=e.get("DNAREPORT_ONT_DEVICE", "cpu"),
                threads=int(e.get("DNAREPORT_ONT_THREADS", "4")),
                timeout_seconds=int(e.get("DNAREPORT_ONT_TIMEOUT", "86400")),
                scratch_dir=e.get("DNAREPORT_ONT_SCRATCH") or None,
                artifact_dir=e.get("DNAREPORT_ONT_ARTIFACT_DIR") or None,
                scratch_limit_gb=int(e.get("DNAREPORT_ONT_SCRATCH_GB", "500")),
                sort_memory_mb=int(e.get("DNAREPORT_ONT_SORT_MEMORY_MB", "512")),
                min_variant_coverage=int(e.get("DNAREPORT_ONT_MIN_VARIANT_COVERAGE", "8")),
                min_genotype_quality=int(e.get("DNAREPORT_ONT_MIN_GENOTYPE_QUALITY", "20")),
                mod_probability=float(e.get("DNAREPORT_ONT_MOD_PROBABILITY", "0.8")),
            )
        except ValueError as exc:
            raise NanoporeConfigurationError("Invalid numeric Nanopore configuration.") from exc


@dataclass
class PreparedNanopore:
    aligned_bam_path: str
    vcf_path: str
    bedmethyl_path: str
    sample_id: str
    provenance: dict
    notes: list[str] = field(default_factory=list)
    artifacts_path: str | None = None


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _model_digest(path: str) -> str:
    root = Path(path)
    if not path or not root.is_dir():
        raise NanoporeConfigurationError("A local model directory is required; downloads are disabled.")
    digest = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise NanoporeConfigurationError("Configured model directory is empty.")
    for item in files:
        digest.update(str(item.relative_to(root)).encode() + b"\0")
        digest.update(sha256(item).encode() + b"\n")
    return digest.hexdigest()


def _tool(name: str, label: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise NanoporeConfigurationError(f"Nanopore preparation requires installed {label}.")
    return executable


def _validate_config(config: NanoporeConfig, *, call_variants: bool):
    if config.reference_build != "GRCh38":
        raise NanoporeConfigurationError("The Nanopore interpretation workflow currently requires GRCh38.")
    if not (1 <= config.threads <= 64 and 1 <= config.timeout_seconds <= 604800
            and 1 <= config.scratch_limit_gb <= 4000 and 64 <= config.sort_memory_mb <= 8192
            and 1 <= config.min_variant_coverage <= 100 and 0 <= config.min_genotype_quality <= 100
            and 0 < config.mod_probability < 1):
        raise NanoporeConfigurationError("Nanopore resource bounds or thresholds are invalid.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", config.reference_sha256):
        raise NanoporeConfigurationError("Set DNAREPORT_ONT_REFERENCE_SHA256 to the verified reference SHA256.")
    reference = Path(config.reference_fasta)
    if not reference.is_file() or not Path(str(reference) + ".fai").is_file():
        raise NanoporeConfigurationError("Provide a local reference FASTA with its samtools .fai index.")
    if sha256(reference) != config.reference_sha256.lower():
        raise NanoporeConfigurationError("Reference FASTA does not match the configured SHA256.")
    tools = {"samtools": _tool(config.samtools, "samtools"), "modkit": _tool(config.modkit, "modkit")}
    models = {}
    if call_variants:
        tools["clair3"] = _tool(config.clair3, "Clair3")
        if not config.clair3_model_id or not re.fullmatch(r"[A-Za-z0-9_.+-]+", config.clair3_model_id):
            raise NanoporeConfigurationError("Provide an explicit chemistry/basecaller-compatible Clair3 model identifier.")
        if "with_mv" in config.clair3_model_id:
            raise NanoporeConfigurationError("Move-table Clair3 models are not yet supported; configure a non-with_mv model.")
        models["clair3"] = {"id": config.clair3_model_id, "sha256": _model_digest(config.clair3_model_path)}
    return tools, models


def _run(args: list[str], *, stage: str, config: NanoporeConfig, work: Path,
         deadline: float, stdout: Path | None = None):
    """No shell; private logs; kill descendants on timeout, cancellation or disk cap."""
    env = dict(os.environ, OMP_NUM_THREADS=str(config.threads),
               OPENBLAS_NUM_THREADS=str(config.threads), MKL_NUM_THREADS=str(config.threads),
               TMPDIR=str(work))
    with open(work / "tool.log", "ab") as log, contextlib.ExitStack() as stack:
        out = stack.enter_context(open(stdout, "wb")) if stdout else log
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out if stage.endswith("version capture") else log,
                                env=env, start_new_session=True)
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise NanoporeError(f"Nanopore {stage} exceeded the configured time limit.")
                used = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())
                if used > config.scratch_limit_gb * 1024**3:
                    raise NanoporeError(f"Nanopore {stage} exceeded the scratch storage limit.")
                try:
                    code = proc.wait(timeout=min(1, max(.01, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if code:
                raise NanoporeError(f"Nanopore {stage} failed (exit {code}); no combined report was produced.")
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


def _bam_info(path: str, requested_sample: str | None = None) -> dict:
    import pysam
    if requested_sample and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", requested_sample):
        raise NanoporeError("Sample ID must be 1–80 letters, numbers, dots, underscores or hyphens.")
    try:
        with pysam.AlignmentFile(path, "rb", check_sq=False) as bam:
            header = bam.header.to_dict()
            groups = {g["ID"]: g for g in header.get("RG", [])}
            # Validate declared platforms even when a read omits RG and the
            # sole header group is inferred. Such reads must not bypass PL.
            if any(g.get("PL", "ONT").upper() not in {"ONT", "OXFORD_NANOPORE"} for g in groups.values()):
                raise NanoporeError("BAM read groups identify a non-Nanopore sequencing platform.")
            samples = {g["SM"] for g in groups.values() if g.get("SM")}
            if samples and any(not group.get("SM") for group in groups.values()):
                raise NanoporeError("Some BAM read groups lack sample IDs; supply a consistently labelled single-sample BAM.")
            if len(samples) > 1:
                raise NanoporeError("BAM contains multiple sample IDs; supply one demultiplexed sample.")
            if requested_sample and samples and samples != {requested_sample}:
                raise NanoporeError("Requested sample ID does not match the BAM read groups.")
            sample = next(iter(samples), requested_sample)
            if not sample:
                raise NanoporeError("BAM has no sample ID; supply an explicit sample_id for this single-sample file.")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", sample):
                raise NanoporeError("BAM sample ID contains unsupported characters.")
            counts = {"primary_reads": 0, "mapped_primary_reads": 0, "reads_with_mod_tags": 0,
                      "mapped_query_bases": 0}
            for read in bam.fetch(until_eof=True):
                if read.is_secondary or read.is_supplementary:
                    continue
                counts["primary_reads"] += 1
                if read.has_tag("RG"):
                    group = groups.get(read.get_tag("RG"))
                    if group is None:
                        raise NanoporeError("A read refers to an unknown read group.")
                elif len(groups) > 1:
                    raise NanoporeError("Reads without read groups cannot be assigned across multiple groups.")
                has_mm = read.has_tag("MM") or read.has_tag("Mm")
                has_ml = read.has_tag("ML") or read.has_tag("Ml")
                if has_mm != has_ml:
                    raise NanoporeError("BAM has incomplete MM/ML modified-base tags.")
                if has_mm and has_ml:
                    if read.has_tag("MN") and read.get_tag("MN") != read.query_length:
                        raise NanoporeError("Modified-base MN tag does not match read length; use unclipped original basecalls.")
                    # pysam/htslib validates tag offsets and probability counts.
                    mods = read.modified_bases
                    if mods is None:
                        raise NanoporeError("BAM contains invalid modified-base tags.")
                    if any(key[0] == "C" and key[2] == "m" for key in mods):
                        counts["reads_with_mod_tags"] += 1
                if not read.is_unmapped:
                    counts["mapped_primary_reads"] += 1
                    counts["mapped_query_bases"] += read.query_alignment_length or 0
            if not counts["primary_reads"]:
                raise NanoporeError("BAM contains no primary reads.")
            if not counts["reads_with_mod_tags"]:
                raise NanoporeError("No usable cytosine 5mC MM/ML tags found; re-basecall retained POD5 with a methylation model.")
            return {**counts, "sample_id": sample, "header": header,
                    "sample_identity": "BAM read-group SM" if samples else "operator-declared single sample"}
    except NanoporeError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise NanoporeError("Cannot read the Nanopore BAM or its read groups.") from exc


def _reference_matches(header, fasta) -> bool:
    """Lengths alone do not prove reference identity; require SQ MD5 for reuse."""
    sq = header.get("SQ", [])
    if not sq:
        return False
    for contig in sq:
        name = contig["SN"]
        if (name not in fasta.references or fasta.get_reference_length(name) != contig["LN"]
                or not contig.get("M5")):
            return False
        expected = hashlib.md5(fasta.fetch(name).upper().encode()).hexdigest()
        if contig["M5"].lower() != expected:
            return False
    return True


def _validate_vcf(path: str, sample_id: str, fasta, contigs: list[str],
                  output: Path, config: NanoporeConfig) -> dict:
    """Validate reference/sample; expose only fully called PASS genotypes with QC."""
    import pysam
    counts = {"records": 0, "pass_records": 0, "called_genotypes": 0,
              "reported_records": 0, "excluded": {"filter": 0, "genotype": 0,
                                                    "depth": 0, "genotype_quality": 0, "outside_scope": 0}}
    try:
        with pysam.VariantFile(path) as variants:
            if list(variants.header.samples) != [sample_id]:
                raise NanoporeError("VCF must contain exactly the same sample ID as the Nanopore BAM.")
            with pysam.VariantFile(str(output), "w", header=variants.header) as passed:
                for record in variants:
                    if record.contig not in fasta.references:
                        raise NanoporeError("VCF contains a contig absent from the pinned reference FASTA.")
                    if fasta.fetch(record.contig, record.start, record.start + len(record.ref)).upper() != record.ref.upper():
                        raise NanoporeError("VCF REF allele does not match the pinned reference FASTA.")
                    counts["records"] += 1
                    if (record.contig not in contigs or not record.alts
                            or any(not re.fullmatch(r"[ACGT]+", allele.upper()) for allele in record.alleles)
                            or max(map(len, record.alleles)) > 51
                            or any(abs(len(alt) - len(record.ref)) > 50 for alt in record.alts)):
                        counts["excluded"]["outside_scope"] += 1
                        continue
                    is_pass = list(record.filter) == ["PASS"]
                    counts["pass_records"] += is_pass
                    call = record.samples[sample_id]
                    genotype = call.get("GT")
                    # Diploid autosomal interpretation only; missing alleles never
                    # become confident carrier calls. Ref-only sites add no findings.
                    called = bool(genotype and len(genotype) == 2 and all(g is not None and 0 <= g < len(record.alleles) for g in genotype))
                    counts["called_genotypes"] += called
                    reason = None
                    if not is_pass:
                        reason = "filter"
                    elif not called:
                        reason = "genotype"
                    elif call.get("DP") is None or call["DP"] < config.min_variant_coverage:
                        reason = "depth"
                    elif call.get("GQ") is None or call["GQ"] < config.min_genotype_quality:
                        reason = "genotype_quality"
                    if reason:
                        counts["excluded"][reason] += 1
                    else:
                        passed.write(record)
                        counts["reported_records"] += 1
    except NanoporeError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise NanoporeError("Cannot read the called variant VCF.") from exc
    return counts


def _coverage(path: Path, contig_lengths: dict[str, int]) -> dict:
    """samtools coverage summary: observed breadth/depth, never a callability mask."""
    bases = sum(contig_lengths.values())
    covered = reads = summarized_bases = 0
    depth_sum = 0.0
    with path.open() as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 9 or fields[0] not in contig_lengths:
                continue
            length = int(fields[2]) - int(fields[1]) + 1
            if length != contig_lengths[fields[0]]:
                raise NanoporeError("Coverage summary contig length differs from pinned reference.")
            summarized_bases += length
            reads += int(fields[3])
            covered += int(fields[4])
            depth_sum += length * float(fields[6])
    if not bases:
        raise NanoporeError("No autosomal coverage summary was produced.")
    return {"autosomal_reference_bases": bases, "autosomal_bases_summarized": summarized_bases,
            "autosomal_covered_bases": covered,
            "autosomal_mean_depth": depth_sum / bases if bases else 0.0,
            "autosomal_breadth_fraction": covered / bases if bases else 0.0,
            "autosomal_reads": reads, "callability_assessed": False}


@contextlib.contextmanager
def prepare_nanopore(path: str, *, config: NanoporeConfig | None = None,
                      sample_id: str | None = None, vcf_path: str | None = None):
    """Yield ephemeral, reference-checked outputs; all scratch is deleted on exit.

    A supplied VCF is a local advanced workflow: matching declared IDs and REF
    alleles are checked, but cannot prove that two independently supplied files
    came from one biological sample. Automatic calling provides a common BAM.
    """
    import pysam
    config = config or NanoporeConfig.from_env()
    source = Path(path).resolve()
    if not source.is_file():
        raise NanoporeError("Provide one BAM or one POD5 file for a single sample.")
    tools, models = _validate_config(config, call_variants=vcf_path is None)
    notes = ["Nanopore variant calling covers autosomal small germline variants only. "
             "Sex chromosomes, mitochondrial variants, structural variants, copy-number changes and repeat expansions were not assessed.",
             "A missing variant is not a reference-genotype or disease-negative result; low coverage and caller limitations can hide variants.",
             "Only explicit PASS, depth-qualified small variants are interpreted; model compatibility requires a separate control-sample validation. Dorado/Clair3 model hashes record identity, not accuracy.",
             "Nanopore 5mC fractions are sequencing measurements; array-trained clock calibration is not established by this integration."]
    deadline = time.monotonic() + config.timeout_seconds
    with tempfile.TemporaryDirectory(prefix="dnareport-ont-", dir=config.scratch_dir) as directory:
        work = Path(directory)
        with pysam.FastaFile(config.reference_fasta) as fasta:
            contigs = [c for c in fasta.references if re.fullmatch(r"(?:chr)?(?:[1-9]|1[0-9]|2[0-2])", c)]
            if not contigs:
                raise NanoporeConfigurationError("Reference contains no named human autosomes (1–22 or chr1–chr22).")
            if len(contigs) != 22:
                notes.append(f"The configured reference contains {len(contigs)} autosomes; this is not a complete whole-genome assessment.")
            run = lambda args, stage, stdout=None: _run(args, stage=stage, config=config,
                work=work, deadline=deadline, stdout=stdout)
            tool_versions = {}
            def capture_version(tool):
                if tool in tool_versions:
                    return
                version_file = work / f"{tool}.version"
                run([tools[tool], "--version"], f"{tool} version capture", version_file)
                output = version_file.read_text(errors="replace")[:4096]
                version = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?", output)
                if tool == "modkit" and (not version or tuple(int(v or 0) for v in version.groups()) < (0, 6, 2)):
                    raise NanoporeConfigurationError("Nanopore preparation requires modkit >= 0.6.2 (strand, threshold and legacy tag fixes).")
                tool_versions[tool] = [tool + " " + version.group(0)] if version else ["version unavailable"]
            # Reject incompatible modification semantics before calling variants.
            capture_version("samtools")
            capture_version("modkit")
            bam_input = str(source)
            if source.suffix.lower() == ".pod5":
                if not sample_id:
                    raise NanoporeError("POD5 preparation requires an explicit single-sample sample_id.")
                tools["dorado"] = _tool(config.dorado, "Dorado")
                capture_version("dorado")
                for name, model in (("dorado", config.dorado_model_path), ("dorado_mod", config.dorado_mod_model_path)):
                    models[name] = {"id": Path(model).name, "sha256": _model_digest(model)}
                bam_input = str(work / "basecalls.bam")
                run([tools["dorado"], "basecaller", str(Path(config.dorado_model_path).resolve()), str(source),
                     "--modified-bases-models", str(Path(config.dorado_mod_model_path).resolve()),
                     "--modified-bases-threshold", "0", "--device", config.device], "basecalling", Path(bam_input))
            before = _bam_info(bam_input, sample_id)
            sample_id = before["sample_id"]
            verified = _reference_matches(before["header"], fasta)
            if not verified:
                tools["dorado"] = _tool(config.dorado, "Dorado (reference identity requires realignment)")
                capture_version("dorado")
                aligned = work / "realigned.bam"
                run([tools["dorado"], "aligner", str(Path(config.reference_fasta).resolve()), bam_input,
                     "--threads", str(config.threads)], "alignment", aligned)
                bam_input = str(aligned)
            sorted_bam = work / "aligned.sorted.bam"
            run([tools["samtools"], "sort", "-@", str(config.threads), "-m", f"{config.sort_memory_mb}M",
                 "-T", str(work / "sort"), "-o", str(sorted_bam), bam_input], "BAM sorting")
            run([tools["samtools"], "index", "-@", str(config.threads), str(sorted_bam)], "BAM indexing")
            after = _bam_info(str(sorted_bam), sample_id)
            if not after["mapped_primary_reads"]:
                raise NanoporeError("No primary reads aligned to the configured reference.")
            if before["primary_reads"] != after["primary_reads"] or before["reads_with_mod_tags"] != after["reads_with_mod_tags"]:
                raise NanoporeError("Read or modification counts changed during preparation; refusing mismatched streams.")
            called = Path(vcf_path).resolve() if vcf_path else work / "clair3" / "merge_output.vcf.gz"
            if not vcf_path:
                run([tools["clair3"], f"--bam_fn={sorted_bam}", f"--ref_fn={Path(config.reference_fasta).resolve()}",
                     f"--threads={config.threads}", "--platform=ont", f"--model_path={Path(config.clair3_model_path).resolve()}",
                     f"--output={work / 'clair3'}", f"--sample_name={sample_id}", f"--ctg_name={','.join(contigs)}",
                     f"--min_coverage={config.min_variant_coverage}"], "Clair3 variant calling")
            else:
                notes.append("VCF supplied separately: matching sample identifiers and reference alleles were checked; biological sample identity is not independently established.")
            passing = work / "variants.pass.vcf"
            variant_qc = _validate_vcf(str(called), sample_id, fasta, contigs, passing, config)
            notes.append(f"Variant interpretation requires PASS, a complete diploid genotype, DP ≥ {config.min_variant_coverage} "
                         f"and GQ ≥ {config.min_genotype_quality}. Missing DP/GQ fails this gate. "
                         f"Retained {variant_qc['reported_records']} of {variant_qc['records']} records; "
                         "these thresholds do not establish clinical validity.")
            coverage_path = work / "coverage.tsv"
            run([tools["samtools"], "coverage", "-o", str(coverage_path), str(sorted_bam)], "alignment coverage")
            coverage = _coverage(coverage_path, {c: fasta.get_reference_length(c) for c in contigs})
            bed = work / "cpg.bed"
            run([tools["modkit"], "pileup", str(sorted_bam), str(bed), "--ref", str(Path(config.reference_fasta).resolve()),
                 "--cpg", "--combine-strands", "--modified-bases", "5mC", "--filter-threshold", str(config.mod_probability),
                 "--threads", str(config.threads)], "modkit CpG pileup")
            if not bed.is_file() or not bed.stat().st_size:
                raise NanoporeError("modkit produced no CpG measurements; combined interpretation is unavailable.")
            reference_bases = sum(fasta.get_reference_length(c) for c in contigs)
            provenance = {"workflow": "nanopore-same-bam-v1", "sample_id": sample_id,
                "sample_identity": before["sample_identity"], "reference_build": config.reference_build,
                "reference_sha256": config.reference_sha256.lower(),
                "reference_verification": "BAM SQ M5 checked" if verified else "realigned to pinned FASTA",
                "input_sha256": sha256(source), "aligned_bam_sha256": sha256(sorted_bam),
                "vcf_sha256": sha256(passing), "raw_vcf_sha256": sha256(called), "bedmethyl_sha256": sha256(bed),
                "variant_source": "supplied VCF" if vcf_path else "Clair3 from shared aligned modBAM",
                "variant_scope": "autosomal small germline variants", "variant_contigs": contigs,
                "models": models, "tools": tool_versions, "alignment_qc": {k: v for k, v in after.items() if k not in {"header", "sample_id", "sample_identity"}},
                "basecall_models": sorted({match for group in before["header"].get("RG", [])
                    for match in re.findall(r"(?:basecall_model|modbase_models)=([A-Za-z0-9_.@+,;-]+)", group.get("DS", ""))}),
                "model_compatibility": "operator-declared; requires control-sample validation",
                "variant_qc": variant_qc, "coverage": coverage, "methylation": {"context": "CpG", "combined_strands": True,
                    "combine_modifications": False, "mod_probability": config.mod_probability},
                "thresholds": {"min_variant_coverage": config.min_variant_coverage, "min_genotype_quality": config.min_genotype_quality},
                "reference_autosomal_bases": reference_bases}
            # Read caller version from its VCF rather than invoking a potentially
            # expensive/unsupported --version on a shell workflow driver.
            with pysam.VariantFile(str(called)) as vcf:
                versions = [str(r).strip() for r in vcf.header.records if r.key == "clair3_version"]
            provenance["tools"]["clair3"] = versions or ["version not present in VCF"]
            artifacts = _archive(config.artifact_dir, sample_id, passing, bed, provenance, notes) if config.artifact_dir else None
            yield PreparedNanopore(str(sorted_bam), str(passing), str(bed), sample_id, provenance, notes, artifacts)


def _archive(root: str, sample_id: str, vcf: Path, bed: Path, provenance: dict, notes: list) -> str:
    """Opt-in local retention, with private permissions and unique directory names."""
    destination = Path(root).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".partial-", dir=destination))
    try:
        name = sample_id + "-" + staging.name.removeprefix(".partial-")
        final = destination / name
        for source, filename in ((vcf, "variants.pass.vcf"), (bed, "cpg.bed")):
            target = staging / filename
            shutil.copyfile(source, target)
            target.chmod(0o600)
        provenance["retained_artifacts"] = {"directory": name,
            "files": ["variants.pass.vcf", "cpg.bed", "provenance.json"], "aligned_bam_retained": False}
        metadata = staging / "provenance.json"
        metadata.write_text(json.dumps({"provenance": provenance, "notes": notes}, indent=2) + "\n")
        metadata.chmod(0o600)
        staging.rename(final)
        return str(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        provenance.pop("retained_artifacts", None)
        raise
