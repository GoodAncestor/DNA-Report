import json

from biocore.providers.base import (
    Category, ChainLink, Finding, Health, Interpretation, ProviderStatus, Tier,
)
from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult
from dnareport.serialize import SCHEMA_VERSION, result_to_json


def _brca2():
    return Finding(
        marker="13-32316419-CAG-C", source="clinvar_mirror",
        description="BRCA2: Likely pathogenic (genotype C/CAG, WGS)",
        tier=Tier.ROBUST, categories=[Category.CLINICAL],
        detail={
            "gene": "BRCA2", "topic": "cancer", "modality": "genome",
            "conditions": ["Hereditary breast ovarian cancer syndrome"],
            "condition_ids": ["MedGen:C0677776"], "zygosity": "het",
            "gold_stars": 2,
            "review_status": "criteria provided, multiple submitters, no conflicts",
            "clinical_significance": "Likely pathogenic", "platform": "WGS",
        },
        interpretation=Interpretation(
            found="f", can_mean="m", how_sure="s", next_step="n",
            condition="Hereditary breast and ovarian cancer syndrome",
            condition_ids=["MedGen:C0677776"], zygosity="het",
        ),
        evidence_chain=[ChainLink(kind="gene", label="BRCA2")],
        promoted=True,
        promoted_reason="Several labs agree this change is pathogenic",
    )


def test_json_is_lossless_and_versioned():
    result = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    result.findings = [_brca2()]
    result.provider_status = [
        ProviderStatus(name="clinvar_mirror", health=Health.OK, version="schema 2")
    ]
    doc = result_to_json(result)
    assert SCHEMA_VERSION == "2.0" and doc["schema_version"] == "2.0"
    finding = doc["findings"][0]
    assert finding["detail"]["conditions"] == [
        "Hereditary breast ovarian cancer syndrome"
    ]
    assert finding["detail"]["zygosity"] == "het"
    assert finding["detail"]["gold_stars"] == 2
    assert finding["interpretation"]["next_step"] == "n"
    assert finding["evidence_chain"][0]["label"] == "BRCA2"
    assert finding["promoted"] is True
    assert finding["magnitude"] is not None and finding["topic"] == "cancer"
    assert "links" in finding
    assert doc["important"][0]["marker"] == "13-32316419-CAG-C"
    assert doc["provider_status"][0]["health"] == "ok"
    json.dumps(doc)
