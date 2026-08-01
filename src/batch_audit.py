#!/usr/bin/env python3
"""Run the Moodle auditor, dashboard generator, and optional file extractor.

The input may be one Moodle .mbz file or a directory containing one or more
.mbz files. Backups are processed sequentially and isolated in separate output
directories. Existing standalone scripts remain usable independently.

Default per-backup layout::

    output/<course-run>/
        audit/
        dashboard.html
        extracted_files/       # only with --extract-files
        processing.log

The dashboard command convention is::

    python moodle_dashboard_generator.py AUDIT_DIR --output DASHBOARD_HTML

Use --dashboard-input audit-json if the generator expects audit_data.json, or
--dashboard-output-flag "" if it writes dashboard.html into the audit folder.
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


VERSION = "1.0.0"
SUMMARY_FIELDS = (
    "source_backup",
    "source_path",
    "output_folder",
    "status",
    "audit_status",
    "dashboard_status",
    "extraction_status",
    "started_at",
    "finished_at",
    "duration_seconds",
    "dashboard_path",
    "message",
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
    candidates.extend((controller_dir / filename, Path.cwd() / "src" / filename, Path.cwd() / filename))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Required script '{filename}' was not found. Searched: {searched}")


def find_backups(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".mbz":
            raise ValueError("Input file must have a .mbz extension.")
        return [input_path]
    pattern = "**/*.mbz" if recursive else "*.mbz"
    return sorted((path for path in input_path.glob(pattern) if path.is_file()), key=lambda p: str(p).lower())


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
    return " ".join(repr(part) if any(ch.isspace() for ch in part) else part for part in command)


def run_command(command: Sequence[str], log_file: Path, label: str) -> subprocess.CompletedProcess[str]:
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sequentially audit one or more Moodle .mbz backups and generate dashboards."
    )
    parser.add_argument("input", help="One .mbz file, or a folder containing one or more .mbz files")
    parser.add_argument("--output-dir", "--output", "-o", default="output", help="Root results folder (default: output)")
    parser.add_argument("--recursive", action="store_true", help="Search input subfolders recursively")
    parser.add_argument(
        "--existing", choices=("suffix", "skip", "overwrite"), default="suffix",
        help="For an existing course-run folder: add a suffix (default), skip, or overwrite",
    )
    parser.add_argument("--auditor-script", help="Path to moodle_mbz_course_auditor.py")
    parser.add_argument("--dashboard-script", help="Path to moodle_dashboard_generator.py")
    parser.add_argument("--extractor-script", help="Path to extract_moodle_files.py")
    parser.add_argument("--python", default=sys.executable, help="Python executable for companion scripts")
    parser.add_argument("--no-dashboard", action="store_true", help="Run audits only; do not generate dashboards")
    parser.add_argument("--extract-files", action="store_true", help="Also export Moodle-hosted files")
    parser.add_argument("--extraction-mode", choices=("context", "course", "type", "all"), default="all")
    parser.add_argument("--verify-hashes", action="store_true", help="Verify extracted files against Moodle SHA-1 hashes")
    parser.add_argument("--link-mode", choices=("copy", "hardlink"), default="copy")
    parser.add_argument(
        "--dashboard-input", choices=("audit-dir", "audit-json"), default="audit-dir",
        help="Pass the audit directory (default) or audit_data.json to the dashboard generator",
    )
    parser.add_argument(
        "--dashboard-output-flag", default="--output",
        help='Dashboard output option (default: --output); use "" if the generator has no output option',
    )
    parser.add_argument(
        "--dashboard-extra-arg", action="append", default=[],
        help="Additional dashboard argument; may be repeated",
    )
    parser.add_argument("--keep-going", action=argparse.BooleanOptionalAction, default=True,
                        help="Continue after a failed backup (default: true)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input).expanduser().resolve()
    output_root = Path(args.output_dir).expanduser().resolve()
    controller_dir = Path(__file__).resolve().parent

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    backups = find_backups(input_path, args.recursive)
    if not backups:
        raise FileNotFoundError(f"No .mbz files found in: {input_path}")

    auditor = discover_script(args.auditor_script, "moodle_mbz_course_auditor.py", controller_dir)
    dashboard = None if args.no_dashboard else discover_script(
        args.dashboard_script, "moodle_dashboard_generator.py", controller_dir
    )
    extractor = None
    if args.extract_files:
        extractor = discover_script(args.extractor_script, "extract_moodle_files.py", controller_dir)

    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    failures = 0

    for index, backup in enumerate(backups, start=1):
        started_at = dt.datetime.now().astimezone()
        timer = time.monotonic()
        run_dir, skipped = prepare_destination(output_root, course_run_folder_name(backup.name), args.existing)
        row: dict[str, object] = {
            "source_backup": backup.name,
            "source_path": str(backup),
            "output_folder": str(run_dir),
            "status": "skipped" if skipped else "running",
            "audit_status": "not_run",
            "dashboard_status": "not_requested" if args.no_dashboard else "not_run",
            "extraction_status": "not_requested" if not args.extract_files else "not_run",
            "started_at": started_at.isoformat(timespec="seconds"),
            "dashboard_path": "",
            "message": "Output folder already exists." if skipped else "",
        }

        if skipped:
            row["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            row["duration_seconds"] = 0
            rows.append(row)
            print(f"[{index}/{len(backups)}] Skipped: {backup.name}")
            continue

        run_dir.mkdir(parents=True, exist_ok=False)
        audit_dir = run_dir / "audit"
        dashboard_path = run_dir / "dashboard.html"
        log_file = run_dir / "processing.log"
        print(f"[{index}/{len(backups)}] Processing: {backup.name}")

        audit_command = [args.python, str(auditor), str(backup), "--output-dir", str(audit_dir)]
        audit_result = run_command(audit_command, log_file, "Moodle audit")
        if audit_result.returncode != 0:
            row.update(status="failed", audit_status="failed", message="Auditor returned a non-zero exit code.")
        else:
            row["audit_status"] = "success"

            if dashboard is not None:
                dashboard_input = audit_dir / "audit_data.json" if args.dashboard_input == "audit-json" else audit_dir
                dashboard_command = [args.python, str(dashboard), str(dashboard_input)]
                if args.dashboard_output_flag:
                    dashboard_command.extend((args.dashboard_output_flag, str(dashboard_path)))
                dashboard_command.extend(args.dashboard_extra_arg)
                dashboard_result = run_command(dashboard_command, log_file, "Dashboard generation")
                if dashboard_result.returncode == 0:
                    row["dashboard_status"] = "success"
                    if dashboard_path.exists():
                        row["dashboard_path"] = str(dashboard_path)
                else:
                    row.update(status="failed", dashboard_status="failed", message="Dashboard generator returned a non-zero exit code.")

            if extractor is not None:
                extraction_dir = run_dir / "extracted_files"
                extraction_command = [
                    args.python, str(extractor), str(backup), "--output", str(extraction_dir),
                    "--mode", args.extraction_mode, "--link-mode", args.link_mode,
                ]
                if args.verify_hashes:
                    extraction_command.append("--verify-hashes")
                extraction_result = run_command(extraction_command, log_file, "File extraction")
                if extraction_result.returncode == 0:
                    row["extraction_status"] = "success"
                else:
                    row.update(status="failed", extraction_status="failed", message="File extractor returned a non-zero exit code.")

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
        else:
            print(f"  Completed: {run_dir}")

    write_summary(output_root / "batch_summary.csv", rows)
    succeeded = sum(row["status"] == "success" for row in rows)
    skipped_count = sum(row["status"] == "skipped" for row in rows)
    print(f"\nBatch complete: {output_root}")
    print(f"Successful: {succeeded}; failed: {failures}; skipped: {skipped_count}")
    print(f"Summary: {output_root / 'batch_summary.csv'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
