"""DNA-Report CLI: `dna-report analyze <file>`."""
from __future__ import annotations
import argparse, json
from .detect import route
from .orchestrate import analyze, render


def main():
    ap = argparse.ArgumentParser(prog="dna-report")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="detect file type + routing")
    d.add_argument("file")

    a = sub.add_parser("analyze", help="analyze an upload -> merged report")
    a.add_argument("file")
    a.add_argument("--traits", help="optional trait table for GeneAsk")
    a.add_argument("--out", default="report.html")

    args = ap.parse_args()
    if args.cmd == "detect":
        kind, engines = route(args.file)
        print(json.dumps({"file": args.file, "kind": kind.value, "engines": list(engines)}))
    elif args.cmd == "analyze":
        res = analyze(args.file, trait_table=args.traits)
        out = render(res, args.out) if res.findings else None
        print(json.dumps({
            "kind": res.kind.value, "engines": list(res.engines),
            "n_findings": len(res.findings), "report": out, "notes": res.notes,
        }))


if __name__ == "__main__":
    main()
