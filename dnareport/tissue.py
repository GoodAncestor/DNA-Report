"""Infer a sample's tissue, so the clock engine can flag tissue mismatch.

The clocks need to know the sample tissue to decide whether a blood-trained
clock is being misapplied (see methylask.clocks). We cannot always know the
tissue from a bare beta file, so this module makes a best-effort INFERENCE that
the UI presents as a default the user can override — never a silent assumption.

Inference signals, in priority order:
  1. explicit hint in the filename / column header (vendor exports often name it)
  2. vendor conventions (e.g. most consumer epigenetic-age kits are whole blood)
  3. fall back to 'blood' as the most common consumer sample, clearly marked as
     a guess so the UI prompts the user to confirm.

The result carries `confident=False` whenever it fell back, so the front end
knows to nudge the user to pick.
"""
from __future__ import annotations
from dataclasses import dataclass

TISSUES = ["blood", "saliva", "buccal", "other"]

_HINTS = {
    "blood":  ["blood", "wholeblood", "whole_blood", "pbmc", "leuko", "trudiagnostic"],
    "saliva": ["saliva", "salivary", "spit", "oragene"],
    "buccal": ["buccal", "cheek", "swab"],
}


@dataclass
class TissueGuess:
    tissue: str          # one of TISSUES
    confident: bool      # True only if a positive signal was found
    reason: str          # human-readable basis for the guess / prompt


def infer_tissue(filename: str = "", header: str = "") -> TissueGuess:
    """Best-effort tissue inference from filename + first header line."""
    hay = f"{filename} {header}".lower()
    for tissue, needles in _HINTS.items():
        for n in needles:
            if n in hay:
                return TissueGuess(tissue, True,
                                   f"detected '{n}' in the file — assuming {tissue}")
    # no signal: default to the most common consumer sample, but not confidently
    return TissueGuess("blood", False,
                       "no tissue detected in the file — defaulting to blood; "
                       "please confirm or change if your sample is saliva/buccal")
