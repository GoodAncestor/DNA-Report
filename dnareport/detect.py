"""File-type detection — decide which engine(s) a user upload routes to.

The router is the whole point of DNA-Report: one upload, correct analysis. It
classifies by extension + content sniff, not by trusting the filename alone.

Routing map:
  23andMe raw text  -> GeneAsk (variants)
  VCF / VCF.gz      -> GeneAsk (variants)
  methylation bedMethyl (modkit) -> MethylAsk (methylation)
  Illumina IDAT / beta-matrix CSV -> MethylAsk (methylation)
  ONT modBAM (MM/ML tags) -> BOTH (bio-core splits it: methylation + variant streams)
"""
from __future__ import annotations
import gzip
from enum import Enum
from pathlib import Path


class InputKind(str, Enum):
    TWENTYTHREE_AND_ME = "23andme"
    ARRAY_GENOTYPE = "array_genotype"   # AncestryDNA/FTDNA/MyHeritage/LivingDNA/MyHappyGenes
    VCF = "vcf"
    BEDMETHYL = "bedmethyl"
    BETA_MATRIX = "beta_matrix"
    IDAT = "idat"
    MODBAM = "modbam"
    UNKNOWN = "unknown"


# which engines each kind feeds
ROUTING = {
    InputKind.TWENTYTHREE_AND_ME: ("geneask",),
    InputKind.ARRAY_GENOTYPE: ("geneask",),
    InputKind.VCF: ("geneask",),
    InputKind.BEDMETHYL: ("methylask",),
    InputKind.BETA_MATRIX: ("methylask",),
    InputKind.IDAT: ("methylask",),
    InputKind.MODBAM: ("bio-core-split", "methylask", "geneask"),  # split, then both
    InputKind.UNKNOWN: (),
}


def _open_text(path):
    return gzip.open(path, "rt") if str(path).endswith(".gz") else open(path, "r", errors="replace")


def _peek_lines(path, n=40):
    try:
        with _open_text(path) as fh:
            return [next(fh) for _ in range(n)]
    except (StopIteration, OSError, UnicodeDecodeError):
        try:
            with _open_text(path) as fh:
                return fh.readlines()[:n]
        except Exception:
            return []


def detect(path: str) -> InputKind:
    """Classify an upload by extension + content sniff."""
    p = Path(path)
    name = p.name.lower()
    suffix = "".join(p.suffixes).lower()

    # binary formats by extension
    if name.endswith(".idat"):
        return InputKind.IDAT
    if name.endswith((".bam", ".modbam")):
        return InputKind.MODBAM  # MM/ML-tag confirmation happens in the modBAM reader
    if ".vcf" in suffix:
        return InputKind.VCF

    lines = _peek_lines(path)
    header = [l for l in lines if l.startswith("#")]
    body = [l for l in lines if not l.startswith("#") and l.strip()]

    # VCF by content (##fileformat=VCF)
    if any("fileformat=vcf" in l.lower() for l in header):
        return InputKind.VCF
    # 23andMe raw: comment header mentions 23andMe; body is rsid\tchrom\tpos\tgenotype
    if any("23andme" in l.lower() for l in header):
        return InputKind.TWENTYTHREE_AND_ME
    if body:
        cols = body[0].rstrip("\n").split("\t")
        # 23andMe: 4 cols, col1 rsID, last col a 1-2 char genotype
        if len(cols) == 4 and cols[0].startswith(("rs", "i")) and len(cols[3].strip()) <= 2:
            return InputKind.TWENTYTHREE_AND_ME
        # modkit bedMethyl: >=11 tab cols, col4 like "m,CG,0"
        if len(cols) >= 11 and "," in cols[3] and cols[3].split(",")[1] in ("CG", "CHG", "CHH"):
            return InputKind.BEDMETHYL
        # beta matrix CSV: comma-separated, a data row whose first token is a
        # cg/ch probe id (header row like "probe,S1" is skipped).
        for bl in body[:5]:
            ccols = bl.rstrip("\n").split(",")
            if len(ccols) >= 2 and ccols[0].lower().startswith(("cg", "ch")):
                return InputKind.BETA_MATRIX

    # other consumer-genotype vendors (AncestryDNA/FTDNA/MyHeritage/LivingDNA/
    # MyHappyGenes): delegate to GeneAsk's parser registry, which sniffs each
    # vendor's header signature. Kept last so the methylation/VCF sniffs win first.
    try:
        from geneask.parsers import detect_parser
        vp = detect_parser(str(path))
        if vp is not None:
            return (InputKind.TWENTYTHREE_AND_ME if vp.name == "23andme"
                    else InputKind.ARRAY_GENOTYPE)
    except Exception:
        pass

    return InputKind.UNKNOWN


def route(path: str):
    """Return (InputKind, engines tuple) for an upload."""
    kind = detect(path)
    return kind, ROUTING[kind]
