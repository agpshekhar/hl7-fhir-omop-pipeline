"""Command-line entry point for labpipe."""

import argparse
import sys
from pathlib import Path

from labpipe import __version__
from labpipe.common.db import connect
from labpipe.common.migrate import apply_migrations, migrations_dir
from labpipe.common.vocab import load_vocabulary, missing_files, vocab_dir
from labpipe.hop1.store import run_hop1
from labpipe.lis.sample import load_panels, sample_dir
from labpipe.lis.staging import stage_panels


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"labpipe {__version__}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    with connect() as conn:
        applied = apply_migrations(conn, migrations_dir())
    if applied:
        for name in applied:
            print(f"applied {name}")
    else:
        print("no migrations to apply")
    return 0


def _cmd_vocab_load(args: argparse.Namespace) -> int:
    directory = args.dir or vocab_dir()
    missing = missing_files(directory)
    if missing:
        print(f"labpipe vocab load: missing in {directory}: {', '.join(missing)}", file=sys.stderr)
        return 1
    with connect() as conn:
        counts = load_vocabulary(conn, directory)
    print(f"loaded vocabulary from {directory}")
    for table, count in counts.items():
        print(f"  cdm.{table}: {count} rows")
    return 0


def _cmd_lis(args: argparse.Namespace) -> int:
    directory = args.dir or sample_dir()
    panels = load_panels(directory)
    with connect() as conn:
        added = stage_panels(conn, panels)
    print(f"built {len(panels)} ORU^R01 messages from {directory}")
    print(f"  staged {added} new, skipped {len(panels) - added} already in staging")
    return 0


def _cmd_hop1(args: argparse.Namespace) -> int:
    with connect() as conn:
        counts = run_hop1(conn)
    print(f"hop1: {counts.parsed} messages parsed, {counts.errors} errors")
    print(f"  upserted {counts.resources} FHIR resources into staging.fhir_resource")
    return 1 if counts.errors else 0


def _cmd_run(args: argparse.Namespace) -> int:
    for step in (_cmd_lis, _cmd_hop1):
        exit_code = step(argparse.Namespace(dir=None))
        if exit_code:
            return exit_code
    _not_built_yet(6)(argparse.Namespace(command="hop2"))
    return 0


def _not_built_yet(phase: int):
    """Return a handler for a job that a later phase will build.

    The Compose stack runs these jobs in order, so each one must exist and exit 0
    before its real code lands.
    """

    def handler(args: argparse.Namespace) -> int:
        print(f"labpipe {args.command}: not built yet (Phase {phase}). Nothing to do.")
        return 0

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="labpipe",
        description="Synthetic HL7 v2 lab results to FHIR R4 to OMOP CDM 5.4.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    version_parser = subparsers.add_parser("version", help="Print the labpipe version and exit.")
    version_parser.set_defaults(func=_cmd_version)

    migrate_parser = subparsers.add_parser(
        "migrate", help="Apply database migrations (safe to rerun)."
    )
    migrate_parser.set_defaults(func=_cmd_migrate)

    vocab_parser = subparsers.add_parser("vocab", help="Load OMOP vocabularies.")
    vocab_parser.add_argument("action", choices=["load"], help="What to do.")
    vocab_parser.add_argument("--dir", type=Path, help="Directory containing the vocabulary files.")
    vocab_parser.set_defaults(func=_cmd_vocab_load)

    lis_parser = subparsers.add_parser(
        "lis", help="Mock LIS: build one ORU^R01 per panel and write it to staging"
    )
    lis_parser.add_argument("--dir", type=Path, help="Dictionary containing the sample files.")
    lis_parser.set_defaults(func=_cmd_lis)

    hop1_parser = subparsers.add_parser(
        "hop1", help="Hop 1: turn new staged HL7 messages into FHIR R4 resources"
    )
    hop1_parser.set_defaults(func=_cmd_hop1)

    run_parser = subparsers.add_parser("run", help="Run the pipeline end to end.")
    run_parser.add_argument("--all", action="store_true", help="Run every stage in order.")
    run_parser.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
