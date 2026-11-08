#!/usr/bin/env python3
"""Fail when DNA-Report, GeneAsk and MethylAsk do not pin the same bio-core commit.

bio-core holds the shared copy rules (biocore/copy/check.py, report/data/terms.json).
If the app and the two engines pinned different commits, pip would either refuse
the install or one of them would run against rules it was not tested with. This
reads the pins from the INSTALLED package metadata (what pip actually resolved),
so it needs no network:

    python scripts/check_pins.py            # exit 1 on a mismatch
    python scripts/check_pins.py --main     # also report whether bio-core main
                                            # has moved past the pin (git ls-remote;
                                            # informational, never fails)
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import re
import subprocess
import sys

BIOCORE_URL = "https://github.com/GoodAncestor/bio-core.git"
DISTS = ("dna-report", "geneask", "methylask")
_PIN = re.compile(r"^biocore(\[[^\]]*\])?\s*@\s*git\+\S*bio-core(\.git)?@([0-9a-f]{7,40})", re.I)


def biocore_pin(requires: list[str] | None) -> str | None:
    """The bio-core commit a distribution's Requires-Dist pins, or None."""
    for req in requires or []:
        m = _PIN.match(req.strip())
        if m:
            return m.group(3)
    return None


def installed_pins() -> dict[str, str | None]:
    pins = {d: biocore_pin(md.requires(d)) for d in DISTS}
    try:
        direct = json.loads(md.distribution("biocore").read_text("direct_url.json") or "{}")
        pins["biocore (installed)"] = direct.get("vcs_info", {}).get("commit_id")
    except md.PackageNotFoundError:
        pins["biocore (installed)"] = None
    return pins


def problems(pins: dict[str, str | None]) -> list[str]:
    out = [f"{name}: no bio-core pin found" for name, sha in pins.items() if not sha]
    shas = {sha for sha in pins.values() if sha}
    # A short and a full SHA of the same commit are the same pin.
    if len({s[:7] for s in shas}) > 1:
        out.append("bio-core pins differ: " + ", ".join(f"{n}={s}" for n, s in pins.items()))
    return out


def main_head() -> str | None:
    try:
        out = subprocess.run(["git", "ls-remote", BIOCORE_URL, "refs/heads/main"],
                             capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.split()[0] if out.strip() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--main", action="store_true", help="also compare with bio-core main (informational)")
    args = ap.parse_args(argv)
    pins = installed_pins()
    for name, sha in pins.items():
        print(f"{name:22} {sha}")
    bad = problems(pins)
    if args.main:
        head = main_head()
        pin = next((s for s in pins.values() if s), None)
        if head is None:
            print("bio-core main: could not read (git ls-remote failed)")
        elif pin and head.startswith(pin[:7]):
            print(f"bio-core main: {head} (pins are current)")
        else:
            print(f"bio-core main: {head} (pins are behind; not a failure)")
    for p in bad:
        print("FAIL:", p, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
