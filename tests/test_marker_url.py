"""Every marker id the report can show should resolve to a public record.

Genome findings are keyed 'chrom-pos-ref-alt' (the ClinVar/callset key). That
form matched none of the resolver's cases, so every clinical variant in a genome
report rendered its marker as bare text while methylation probes and rsIDs got
linkouts — the one modality where a reader most wants to look the call up was the
one that offered nowhere to go.
"""
import pytest
from dnareport.orchestrate import _marker_url


def test_cpg_probe_goes_to_the_ewas_catalog():
    assert _marker_url("cg00000029") == "https://www.ewascatalog.org/?query=cg00000029"


def test_non_cpg_probe_goes_to_the_ewas_catalog():
    assert _marker_url("ch.2.2242072R") == "https://www.ewascatalog.org/?query=ch.2.2242072R"


def test_chrom_pos_marker_reaches_the_ucsc_branch():
    """This branch existed but was unreachable: the probe test above it matched a
    bare 'ch' prefix, so every 'chr…' marker was answered by the methylation
    catalogue before the coordinate case was ever consulted."""
    assert _marker_url("chr13:32340301") == (
        "https://genome.ucsc.edu/cgi-bin/hgTracks?db=hg38&position=chr13:32340301")


def test_rsid_goes_to_dbsnp():
    assert _marker_url("rs1801133") == "https://www.ncbi.nlm.nih.gov/snp/rs1801133"


@pytest.mark.parametrize("marker,pos", [
    ("13-32340301-A-G", "chr13:32340301-32340301"),
    ("X-153296550-C-T", "chrX:153296550-153296550"),
    # already-prefixed chromosomes must not become 'chrchr13'
    ("chr13-32340301-A-G", "chr13:32340301-32340301"),
])
def test_genome_variant_goes_to_the_ucsc_browser(marker, pos):
    assert _marker_url(marker) == (
        f"https://genome.ucsc.edu/cgi-bin/hgTracks?db=hg38&position={pos}")


def test_indel_alleles_do_not_break_the_position():
    """Long ref/alt sequences carry dashes nowhere, but the allele fields are
    still free text — the resolver must key off chrom and pos alone."""
    url = _marker_url("2-47403214-AGGTC-A")
    assert url.endswith("position=chr2:47403214-47403214")


def test_unresolvable_marker_stays_unlinked():
    """A marker the resolver cannot place must return None so the renderer shows
    plain text, rather than a link that lands somewhere wrong."""
    assert _marker_url("some-free-text") is None
    assert _marker_url("") is None
