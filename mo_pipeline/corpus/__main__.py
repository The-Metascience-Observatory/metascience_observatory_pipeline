"""
CLI for the corpus catalog + drive migration.

    python -m mo_pipeline.corpus inventory   # READ-ONLY: build migration_plan.csv
    python -m mo_pipeline.corpus migrate      # dry-run the plan (moves nothing)
    python -m mo_pipeline.corpus migrate --execute   # perform the moves
    python -m mo_pipeline.corpus scan         # rebuild corpus.sqlite from papers/
    python -m mo_pipeline.corpus stats        # print catalog stats
"""
from __future__ import annotations

import argparse
import json
import sys

from mo_pipeline.corpus import catalog, migrate_drive


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mo_pipeline.corpus")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inventory", help="READ-ONLY: scan legacy layout -> migration_plan.csv")

    mp = sub.add_parser("migrate", help="execute migration_plan.csv (dry-run unless --execute)")
    mp.add_argument("--execute", action="store_true", help="actually move folders")

    sw = sub.add_parser("sweep", help="move remaining ingested/ + WIP leftovers to legacy/ (run after migrate)")
    sw.add_argument("--execute", action="store_true", help="actually move")

    sub.add_parser("scan", help="rebuild corpus.sqlite from papers/")
    sub.add_parser("stats", help="print catalog stats")

    il = sub.add_parser("include-list",
                        help="emit paper folders matching a query (for extract --include-list)")
    il.add_argument("--status", help="filter by status (e.g. converted, screened)")
    il.add_argument("--not-extracted-tag", help="exclude papers already extracted under this tag")
    il.add_argument("--with-replications", action="store_true",
                    help="only papers whose screening/extraction found replications")
    il.add_argument("--exclude-no-replications", action="store_true",
                    help="skip papers screened as containing no replications")
    il.add_argument("-o", "--output", help="write folder paths here (default stdout)")

    args = ap.parse_args(argv)

    if args.cmd == "inventory":
        entries = migrate_drive.inventory()
        path = migrate_drive.write_plan(entries)
        print(f"Wrote plan: {path}")
        _print(migrate_drive.plan_summary(entries))

    elif args.cmd == "migrate":
        result = migrate_drive.migrate(execute=args.execute)
        _print(result)
        if not args.execute:
            print("\n(dry-run — re-run with --execute to perform moves)", file=sys.stderr)

    elif args.cmd == "sweep":
        result = migrate_drive.sweep_leftovers(execute=args.execute)
        _print(result)
        if not args.execute:
            print("\n(dry-run — re-run with --execute to sweep)", file=sys.stderr)

    elif args.cmd == "scan":
        summary = catalog.scan()
        _print(summary)

    elif args.cmd == "stats":
        conn = catalog.connect()
        _print(catalog.stats(conn))
        conn.close()

    elif args.cmd == "include-list":
        conn = catalog.connect()
        cr = True if args.with_replications else None
        rows = catalog.query(conn, status=args.status, contains_replications=cr,
                             not_extracted_tag=args.not_extracted_tag)
        folders = [r["folder"] for r in rows
                   if not (args.exclude_no_replications and r["contains_replications"] == 0)]
        conn.close()
        text = "\n".join(folders)
        if args.output:
            with open(args.output, "w") as f:
                f.write(text + ("\n" if folders else ""))
            print(f"Wrote {len(folders)} folders to {args.output}", file=sys.stderr)
        else:
            print(text)


if __name__ == "__main__":
    main()
