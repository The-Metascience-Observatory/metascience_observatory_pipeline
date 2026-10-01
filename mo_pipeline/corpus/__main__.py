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
from pathlib import Path

from mo_pipeline.corpus import adopt, catalog, migrate_drive


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

    rp = sub.add_parser("repair-names",
                        help="rename legacy/ambiguous DOI folder names (dry-run unless --apply)")
    rp.add_argument("--apply", action="store_true", help="actually rename")
    rp.add_argument("--check", action="store_true",
                    help="verify every folder's doi.txt round-trips to its name")

    ad = sub.add_parser("adopt-structured",
                        help="move inbox XML/HTML (+ sidecars) into papers/{doi}/ "
                             "after conversion (dry-run unless --execute)")
    ad.add_argument("--execute", action="store_true", help="actually move the files")

    ib = sub.add_parser("inbox-subfolders",
                        help="move flat inbox/{doi}.* files into inbox/{doi}/ (one folder "
                             "per record, as stage 5 now writes; dry-run unless --execute)")
    ib.add_argument("--execute", action="store_true", help="actually move the files")

    rm = sub.add_parser("render-markdown",
                        help="write the missing {stem}_from_xml.md / _from_html.md for "
                             "XML/HTML already on the drive (dry-run unless --execute)")
    rm.add_argument("--execute", action="store_true", help="actually write the markdown")
    rm.add_argument("--limit", type=int, default=None,
                    help="convert at most N records (start small)")
    rm.add_argument("--overwrite", action="store_true",
                    help="re-render artifacts that already have a rendition")
    rm.add_argument("-v", "--verbose", action="store_true",
                    help="per-file conversion detail (table and figure counts)")

    sub.add_parser("scan", help="rebuild corpus.sqlite from papers/")
    sub.add_parser("stats", help="print catalog stats")
    sub.add_parser("coverage", help="database->corpus markdown coverage")

    mi = sub.add_parser("mark-ingested",
                        help="stamp catalog+paper.json ingested from a collated CSV (after the manual ingest)")
    mi.add_argument("csv", help="collated CSV whose replication_url DOIs were ingested")
    mi.add_argument("--db-version", required=True, help="replications_database_*.csv filename")

    il = sub.add_parser("include-list",
                        help="emit paper folders matching a query (for extract --include-list)")
    il.add_argument("--status", help="filter by status (e.g. converted, screened)")
    il.add_argument("--not-extracted-tag", help="exclude papers already extracted under this tag")
    il.add_argument("--with-replications", action="store_true",
                    help="only papers whose screening/extraction found replications")
    il.add_argument("--exclude-no-replications", action="store_true",
                    help="skip papers screened as containing no replications")
    il.add_argument("-o", "--output",
                    help="write folder names here, one per line, ready for extract --include-list "
                         "(default stdout)")

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

    elif args.cmd == "repair-names":
        from mo_pipeline.corpus import repair_folder_names
        if args.check:
            sys.exit(1 if repair_folder_names.check() else 0)
        result = repair_folder_names.repair(apply=args.apply)
        _print(result)
        if not args.apply:
            print("\n(dry-run — re-run with --apply to rename)", file=sys.stderr)

    elif args.cmd == "adopt-structured":
        result = adopt.adopt_structured(execute=args.execute)
        _print(result)
        if not args.execute and result["adopted"]:
            print(f"dry run — re-run with --execute to move {result['files']} files",
                  file=sys.stderr)

    elif args.cmd == "inbox-subfolders":
        from mo_pipeline.corpus import inbox_layout
        result = inbox_layout.migrate_inbox(execute=args.execute)
        _print(result)
        if not args.execute and result["records"]:
            print(f"dry run — re-run with --execute to move {result['files']} files "
                  f"into {result['records']} record folders", file=sys.stderr)

    elif args.cmd == "render-markdown":
        from mo_pipeline.corpus import render
        result = render.render_markdown(execute=args.execute, limit=args.limit,
                                        overwrite=args.overwrite, verbose=args.verbose)
        _print(result)
        if not args.execute and result["pending"]:
            print(f"dry run — re-run with --execute to render {result['pending']} records",
                  file=sys.stderr)

    elif args.cmd == "scan":
        summary = catalog.scan()
        _print(summary)

    elif args.cmd == "stats":
        conn = catalog.connect()
        _print(catalog.stats(conn))
        conn.close()

    elif args.cmd == "coverage":
        from mo_pipeline.corpus import backfill
        backfill.coverage_report()

    elif args.cmd == "mark-ingested":
        import csv as _csv
        _csv.field_size_limit(2**31 - 1)
        with open(args.csv, newline="") as f:
            dois = [r.get("replication_url", "") for r in _csv.DictReader(f)]
        dois = [d for d in dois if d]
        conn = catalog.connect()
        result = catalog.mark_ingested(conn, dois, db_version=args.db_version)
        conn.close()
        print(f"marked {result['matched']} ingested; {len(result['unmatched'])} unmatched")
        if result["unmatched"][:5]:
            print("  e.g. unmatched:", result["unmatched"][:5], file=sys.stderr)

    elif args.cmd == "include-list":
        conn = catalog.connect()
        cr = True if args.with_replications else None
        rows = catalog.query(conn, status=args.status, contains_replications=cr,
                             not_extracted_tag=args.not_extracted_tag)
        # Folder NAMES, not the catalog's absolute paths: both extractors match
        # an include list against `p.name`, so absolute paths matched nothing at
        # all. doi_runs and the benchmark harness already write names.
        folders = [Path(r["folder"]).name for r in rows
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
