#!/usr/bin/env python3
"""Flexible Moodle MBZ workflow orchestrator.

Runs one or more Moodle backup (.mbz) files through a dependency-aware workflow.
Each companion script remains independently runnable; this controller only
coordinates them, records status, and organises outputs.

Default workflow per backup:
    auditor -> dashboard

Optional stages:
    --extract-files    auditor -> dashboard -> extractor
    --content-map      auditor -> dashboard -> extractor -> content mapper
                       (extraction is enabled automatically)
    --settings         also run the standalone settings analyser directly on MBZ
    --full             auditor -> dashboard -> extractor -> content mapper
                       + settings analyser

The content mapper is never run unless its prerequisites exist:
    <course-run>/audit/sections.csv
    <course-run>/audit/activities.csv
    <course-run>/audit/content_placement_inventory.csv
    <course-run>/extracted_files/

Examples:
    python3 src/batch_audit.py
    python3 src/batch_audit.py batch_input -o batch_output
    python3 src/batch_audit.py literature-review-2025.mbz -o batch_output
    python3 src/batch_audit.py batch_input -o batch_output --extract-files
    python3 src/batch_audit.py batch_input -o batch_output --content-map
    python3 src/batch_audit.py batch_input -o batch_output --settings
    python3 src/batch_audit.py batch_input -o batch_output --full

Notes:
- Comparator workflows are intentionally not included here because compare_mbz.py
  requires an explicit before/after pair rather than a normal per-course batch run.
- extract_moodle_files.py may return exit code 3 after producing usable outputs;
  this means extraction completed with missing/hash issues. The controller records
  a warning and may still run the mapper if all mapper prerequisites exist.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Sequence


VERSION = "2.0.0"

SUMMARY_FIELDS = (
    "source_backup",
    "source_path",
    "output_folder",
    "status",
    "audit_status",
    "dashboard_status",
    "extraction_status",
    "content_map_status",
    "settings_status",
    "started_at",
    "finished_at",
    "duration_seconds",
    "dashboard_path",
    "extraction_path",
    "content_map_path",
    "settings_path",
    "message",
)

MAPPER_REQUIRED_AUDIT_FILES = (
    "sections.csv",
    "activities.csv",
    "content_placement_inventory.csv",
)


def safe_folder_name(name: str, max_length: int = 160) -> str:
    """Return a portable folder name while retaining useful identity."""
    cleaned = re.sub(r"[^\w\-.]+", "_", Path(name).stem)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:max_length] or "moodle_backup"


def course_run_folder_name(filename: str) -> str:
    """Derive a concise, traceable run name from a Moodle backup filename."""
    stem = Path(filename).stem
    stem = re.sub(r"^backup-moodle2-course-", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"-nu$", "", stem, flags=re.IGNORECASE)
    return safe_folder_name(stem)


def discover_script(explicit: str | None, filename: str, controller_dir: Path) -> Path:
    """Find a companion script, preferring an explicitly supplied path."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        (
            controller_dir / filename,
            Path.cwd() / "src" / filename,
            Path.cwd() / filename,
        )
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        f"Required script '{filename}' was not found. Searched: {searched}"
    )


def find_backups(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".mbz":
            raise ValueError("Input file must have a .mbz extension.")
        return [input_path]
    pattern = "**/*.mbz" if recursive else "*.mbz"
    return sorted(
        (path for path in input_path.glob(pattern) if path.is_file()),
        key=lambda p: str(p).lower(),
    )


def numbered_destination(parent: Path, base_name: str) -> Path:
    candidate = parent / base_name
    if not candidate.exists():
        return candidate
    number = 2
    while (parent / f"{base_name}_{number}").exists():
        number += 1
    return parent / f"{base_name}_{number}"


def prepare_destination(parent: Path, base_name: str, existing: str) -> tuple[Path, bool]:
    destination = parent / base_name
    if not destination.exists():
        return destination, False
    if existing == "skip":
        return destination, True
    if existing == "overwrite":
        shutil.rmtree(destination)
        return destination, False
    return numbered_destination(parent, base_name), False


def command_text(command: Sequence[str]) -> str:
    """Readable command rendering for logs; never used to execute a shell."""
    return " ".join(
        repr(part) if any(ch.isspace() for ch in part) else part for part in command
    )


def run_command(
    command: Sequence[str], log_file: Path, label: str
) -> subprocess.CompletedProcess[str]:
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{started}] {label}\n$ {command_text(command)}\n")
        if result.stdout:
            handle.write(result.stdout)
            if not result.stdout.endswith("\n"):
                handle.write("\n")
        handle.write(f"Exit code: {result.returncode}\n")
    return result


def write_summary(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def add_message(row: dict[str, object], message: str) -> None:
    """Append a human-readable message without overwriting earlier findings."""
    previous = str(row.get("message", "") or "").strip()
    row["message"] = f"{previous}; {message}" if previous else message


def mark_failed(row: dict[str, object], stage_field: str, message: str) -> None:
    row[stage_field] = "failed"
    row["status"] = "failed"
    add_message(row, message)


def mark_warning(row: dict[str, object], stage_field: str, message: str) -> None:
    row[stage_field] = "warning"
    if row.get("status") not in {"failed", "skipped"}:
        row["status"] = "warning"
    add_message(row, message)


def mapper_prerequisites(run_dir: Path) -> tuple[bool, list[str]]:
    audit_dir = run_dir / "audit"
    extracted_dir = run_dir / "extracted_files"
    missing: list[str] = []
    for filename in MAPPER_REQUIRED_AUDIT_FILES:
        if not (audit_dir / filename).is_file():
            missing.append(str(audit_dir / filename))
    if not extracted_dir.is_dir():
        missing.append(str(extracted_dir))
    return not missing, missing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run dependency-aware Moodle MBZ audit workflows for one backup or a folder of backups."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Workflow examples:
  Default audit + dashboard:
    %(prog)s
    %(prog)s batch_input -o batch_output

  Audit + dashboard + extracted files:
    %(prog)s batch_input -o batch_output --extract-files

  Audit + dashboard + extraction + course content map:
    %(prog)s batch_input -o batch_output --content-map

  Audit + dashboard + settings analysis:
    %(prog)s batch_input -o batch_output --settings

  Full per-course workflow:
    %(prog)s batch_input -o batch_output --full

--content-map automatically enables file extraction because the mapper requires
both audit outputs and extracted_files/. The comparator remains a separate
before/after workflow and is not invoked by this batch controller.
""",
    )

    parser.add_argument(
        "input",
        nargs="?",
        default="batch_input",
        help="One .mbz file or a folder containing .mbz files (default: batch_input)",
    )
    parser.add_argument(
        "--output-dir",
        "--output",
        "-o",
        default="batch_output",
        help="Root results folder (default: batch_output)",
    )
    parser.add_argument("--recursive", action="store_true", help="Search input subfolders recursively")
    parser.add_argument(
        "--existing",
        choices=("suffix", "skip", "overwrite"),
        default="suffix",
        help="Existing course folder: add suffix (default), skip, or overwrite",
    )

    # Script discovery overrides.
    parser.add_argument("--auditor-script", help="Path to moodle_mbz_course_auditor.py")
    parser.add_argument("--dashboard-script", help="Path to moodle_dashboard_generator.py")
    parser.add_argument("--extractor-script", help="Path to extract_moodle_files.py")
    parser.add_argument("--mapper-script", help="Path to content_mapper.py")
    parser.add_argument("--settings-script", help="Path to analyse_mbz.py")
    parser.add_argument("--python", default=sys.executable, help="Python executable for companion scripts")

    # Workflow selection.
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Do not generate the dashboard (the auditor still runs)",
    )
    parser.add_argument(
        "--extract-files",
        action="store_true",
        help="Also extract and organise Moodle-hosted files",
    )
    parser.add_argument(
        "--content-map",
        action="store_true",
        help="Also create the course content map; automatically enables extraction",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Also run analyse_mbz.py and write a settings/ report folder",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run dashboard, extraction, content mapping and settings analysis",
    )

    # Extraction options.
    parser.add_argument(
        "--extraction-mode",
        choices=("context", "course", "type", "all"),
        default="all",
        help="Extractor folder view (default: all)",
    )
    parser.add_argument(
        "--verify-hashes",
        action="store_true",
        help="Verify extracted files against Moodle SHA-1 hashes",
    )
    parser.add_argument(
        "--link-mode",
        choices=("copy", "hardlink"),
        default="copy",
        help="Extractor storage mode (default: copy)",
    )

    # Mapper options.
    parser.add_argument(
        "--bundle-map",
        action="store_true",
        help="Ask content_mapper.py to copy linked resources into content_map/resources",
    )
    parser.add_argument(
        "--include-hidden-map",
        action="store_true",
        help="Include hidden sections/course items in the content map",
    )

    # Settings analyser options.
    parser.add_argument(
        "--expect-new-window-for",
        action="append",
        default=[],
        metavar="DOMAIN_TEXT",
        help=(
            "Pass an optional domain rule to analyse_mbz.py; repeat for multiple domains"
        ),
    )

    # Dashboard compatibility options retained from the previous controller.
    parser.add_argument(
        "--dashboard-input",
        choices=("audit-dir", "audit-json"),
        default="audit-dir",
        help="Pass the audit directory (default) or audit_data.json to the dashboard",
    )
    parser.add_argument(
        "--dashboard-output-flag",
        default="--output",
        help='Dashboard output option (default: --output); use "" if unsupported',
    )
    parser.add_argument(
        "--dashboard-extra-arg",
        action="append",
        default=[],
        help="Additional dashboard argument; may be repeated",
    )

    parser.add_argument(
        "--keep-going",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Continue with later backups after a failed course (default: true)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Resolve dependency-aware workflow requests.
    if args.full:
        args.no_dashboard = False
        args.extract_files = True
        args.content_map = True
        args.settings = True
    if args.content_map:
        args.extract_files = True

    input_path = Path(args.input).expanduser().resolve()
    output_root = Path(args.output_dir).expanduser().resolve()
    controller_dir = Path(__file__).resolve().parent

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    backups = find_backups(input_path, args.recursive)
    if not backups:
        raise FileNotFoundError(f"No .mbz files found in: {input_path}")

    # Discover only the scripts required by the selected workflow.
    auditor = discover_script(args.auditor_script, "moodle_mbz_course_auditor.py", controller_dir)
    dashboard = None
    if not args.no_dashboard:
        dashboard = discover_script(
            args.dashboard_script, "moodle_dashboard_generator.py", controller_dir
        )
    extractor = None
    if args.extract_files:
        extractor = discover_script(
            args.extractor_script, "extract_moodle_files.py", controller_dir
        )
    mapper = None
    if args.content_map:
        mapper = discover_script(args.mapper_script, "content_mapper.py", controller_dir)
    settings_analyser = None
    if args.settings:
        settings_analyser = discover_script(args.settings_script, "analyse_mbz.py", controller_dir)

    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    failures = 0
    warnings = 0

    for index, backup in enumerate(backups, start=1):
        started_at = dt.datetime.now().astimezone()
        timer = time.monotonic()
        run_dir, skipped = prepare_destination(
            output_root, course_run_folder_name(backup.name), args.existing
        )

        row: dict[str, object] = {
            "source_backup": backup.name,
            "source_path": str(backup),
            "output_folder": str(run_dir),
            "status": "skipped" if skipped else "running",
            "audit_status": "not_run",
            "dashboard_status": "not_requested" if args.no_dashboard else "not_run",
            "extraction_status": "not_requested" if not args.extract_files else "not_run",
            "content_map_status": "not_requested" if not args.content_map else "not_run",
            "settings_status": "not_requested" if not args.settings else "not_run",
            "started_at": started_at.isoformat(timespec="seconds"),
            "dashboard_path": "",
            "extraction_path": "",
            "content_map_path": "",
            "settings_path": "",
            "message": "Output folder already exists." if skipped else "",
        }

        if skipped:
            row["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            row["duration_seconds"] = 0
            rows.append(row)
            write_summary(output_root / "batch_summary.csv", rows)
            print(f"[{index}/{len(backups)}] Skipped: {backup.name}")
            continue

        run_dir.mkdir(parents=True, exist_ok=False)
        audit_dir = run_dir / "audit"
        dashboard_path = run_dir / "dashboard.html"
        extraction_dir = run_dir / "extracted_files"
        content_map_dir = run_dir / "content_map"
        settings_dir = run_dir / "settings"
        log_file = run_dir / "processing.log"

        print(f"[{index}/{len(backups)}] Processing: {backup.name}")

        # ------------------------------------------------------------------
        # 1. AUDITOR — baseline stage for the normal workflow.
        # ------------------------------------------------------------------
        audit_command = [
            args.python,
            str(auditor),
            str(backup),
            "--output-dir",
            str(audit_dir),
        ]
        audit_result = run_command(audit_command, log_file, "Moodle audit")
        audit_ok = audit_result.returncode == 0
        if audit_ok:
            row["audit_status"] = "success"
        else:
            mark_failed(
                row,
                "audit_status",
                f"Auditor returned exit code {audit_result.returncode}.",
            )

        # ------------------------------------------------------------------
        # 2. DASHBOARD — depends on a successful audit.
        # ------------------------------------------------------------------
        if dashboard is not None:
            if not audit_ok:
                row["dashboard_status"] = "skipped_dependency"
                add_message(row, "Dashboard skipped because the audit failed.")
            else:
                dashboard_input = (
                    audit_dir / "audit_data.json"
                    if args.dashboard_input == "audit-json"
                    else audit_dir
                )
                dashboard_command = [args.python, str(dashboard), str(dashboard_input)]
                if args.dashboard_output_flag:
                    dashboard_command.extend(
                        (args.dashboard_output_flag, str(dashboard_path))
                    )
                dashboard_command.extend(args.dashboard_extra_arg)
                dashboard_result = run_command(
                    dashboard_command, log_file, "Dashboard generation"
                )
                if dashboard_result.returncode == 0 and dashboard_path.is_file():
                    row["dashboard_status"] = "success"
                    row["dashboard_path"] = str(dashboard_path)
                elif dashboard_result.returncode == 0:
                    mark_failed(
                        row,
                        "dashboard_status",
                        "Dashboard command returned success but dashboard.html was not found.",
                    )
                else:
                    mark_failed(
                        row,
                        "dashboard_status",
                        f"Dashboard generator returned exit code {dashboard_result.returncode}.",
                    )

        # ------------------------------------------------------------------
        # 3. EXTRACTION — independent of dashboard; required by content mapper.
        # ------------------------------------------------------------------
        extraction_usable = False
        if extractor is not None:
            extraction_command = [
                args.python,
                str(extractor),
                str(backup),
                "--output",
                str(extraction_dir),
                "--mode",
                args.extraction_mode,
                "--link-mode",
                args.link_mode,
            ]
            if args.verify_hashes:
                extraction_command.append("--verify-hashes")

            extraction_result = run_command(
                extraction_command, log_file, "File extraction"
            )
            row["extraction_path"] = str(extraction_dir) if extraction_dir.exists() else ""

            if extraction_result.returncode == 0:
                row["extraction_status"] = "success"
                extraction_usable = extraction_dir.is_dir()
            elif extraction_result.returncode == 3:
                # extract_moodle_files.py documents 3 as completed with
                # missing/hash exceptions, not a hard crash.
                extraction_usable = extraction_dir.is_dir()
                mark_warning(
                    row,
                    "extraction_status",
                    "Extraction completed with missing files and/or hash issues (exit code 3).",
                )
            else:
                mark_failed(
                    row,
                    "extraction_status",
                    f"File extractor returned exit code {extraction_result.returncode}.",
                )

        # ------------------------------------------------------------------
        # 4. CONTENT MAPPER — requires BOTH auditor outputs and extraction.
        # ------------------------------------------------------------------
        if mapper is not None:
            prerequisites_ok, missing = mapper_prerequisites(run_dir)
            if not prerequisites_ok:
                row["content_map_status"] = "skipped_dependency"
                add_message(
                    row,
                    "Content mapper skipped; missing prerequisite(s): "
                    + ", ".join(missing),
                )
                if row.get("status") not in {"failed", "skipped"}:
                    row["status"] = "failed"
            elif not extraction_usable:
                row["content_map_status"] = "skipped_dependency"
                add_message(row, "Content mapper skipped because extraction was not usable.")
                if row.get("status") not in {"failed", "skipped"}:
                    row["status"] = "failed"
            else:
                mapper_command = [
                    args.python,
                    str(mapper),
                    str(run_dir),
                    "--output-dir",
                    str(content_map_dir),
                ]
                if args.bundle_map:
                    mapper_command.append("--bundle")
                if args.include_hidden_map:
                    mapper_command.append("--include-hidden")

                mapper_result = run_command(
                    mapper_command, log_file, "Course content mapping"
                )
                expected_map = content_map_dir / "index.html"
                if mapper_result.returncode == 0 and expected_map.is_file():
                    row["content_map_status"] = "success"
                    row["content_map_path"] = str(expected_map)
                elif mapper_result.returncode == 0:
                    mark_failed(
                        row,
                        "content_map_status",
                        "Content mapper returned success but content_map/index.html was not found.",
                    )
                else:
                    mark_failed(
                        row,
                        "content_map_status",
                        f"Content mapper returned exit code {mapper_result.returncode}.",
                    )

        # ------------------------------------------------------------------
        # 5. SETTINGS ANALYSER — reads MBZ directly, so it is independent of
        #    audit/dashboard/extraction success.
        # ------------------------------------------------------------------
        if settings_analyser is not None:
            settings_command = [
                args.python,
                str(settings_analyser),
                str(backup),
                "--output",
                str(settings_dir),
            ]
            for domain_rule in args.expect_new_window_for:
                settings_command.extend(("--expect-new-window-for", domain_rule))

            settings_result = run_command(
                settings_command, log_file, "Settings analysis"
            )
            expected_settings = settings_dir / "course-settings-report.html"
            if settings_result.returncode == 0 and expected_settings.is_file():
                row["settings_status"] = "success"
                row["settings_path"] = str(expected_settings)
            elif settings_result.returncode == 0:
                mark_failed(
                    row,
                    "settings_status",
                    "Settings analyser returned success but its HTML report was not found.",
                )
            else:
                mark_failed(
                    row,
                    "settings_status",
                    f"Settings analyser returned exit code {settings_result.returncode}.",
                )

        if row["status"] == "running":
            row["status"] = "success"

        finished_at = dt.datetime.now().astimezone()
        row["finished_at"] = finished_at.isoformat(timespec="seconds")
        row["duration_seconds"] = round(time.monotonic() - timer, 2)
        rows.append(row)
        write_summary(output_root / "batch_summary.csv", rows)

        if row["status"] == "failed":
            failures += 1
            print(f"  Failed; see {log_file}")
            if not args.keep_going:
                break
        elif row["status"] == "warning":
            warnings += 1
            print(f"  Completed with warnings: {run_dir}")
        else:
            print(f"  Completed: {run_dir}")

    write_summary(output_root / "batch_summary.csv", rows)
    succeeded = sum(row["status"] == "success" for row in rows)
    skipped_count = sum(row["status"] == "skipped" for row in rows)

    print(f"\nBatch complete: {output_root}")
    print(
        f"Successful: {succeeded}; warnings: {warnings}; "
        f"failed: {failures}; skipped: {skipped_count}"
    )
    print(f"Summary: {output_root / 'batch_summary.csv'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
