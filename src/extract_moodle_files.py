#!/usr/bin/env python3
"""Safely extract and organise files from a Moodle .mbz backup.

The script is course-independent and uses only Python's standard library.  It
can produce three independent views of the same recovered content:

* context: Moodle component/file-area/item-id provenance (authoritative)
* course:  best-effort section/activity/chapter organisation
* type:    PDFs, documents, data, images, and other convenient categories

It also writes a CSV manifest and a Markdown report.  Unknown plugins or
incomplete metadata do not stop extraction: files are retained with their
technical context and unresolved course mappings are clearly reported.

Python 3.9+.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import mimetypes
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET


MODES = ("context", "course", "type", "all")
MANIFEST_FIELDS = [
    "file_id", "original_filename", "original_filepath", "component",
    "filearea", "itemid", "contextid", "contenthash", "mimetype",
    "filesize", "author", "license", "source", "timecreated",
    "timemodified", "section_name", "activity_type", "activity_name",
    "subcontext_name", "file_category", "duplicate_kind",
    "context_path", "course_path", "type_path", "status", "note",
]

TYPE_EXTENSIONS = {
    "PDFs": {".pdf"},
    "Word": {".doc", ".docx", ".odt", ".rtf"},
    "PowerPoint": {".ppt", ".pptx", ".odp"},
    "Spreadsheets": {".xls", ".xlsx", ".ods"},
    "Data": {".csv", ".tsv", ".sav", ".dta", ".por", ".sas7bdat",
             ".json", ".xml", ".yaml", ".yml"},
    "R_scripts": {".r", ".rmd", ".qmd"},
    "Code": {".py", ".js", ".ts", ".html", ".htm", ".css", ".sql",
             ".do", ".m", ".ipynb"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
               ".tif", ".tiff", ".bmp"},
    "Audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"},
    "Video": {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"},
    "Archives": {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"},
    "Text": {".txt", ".md", ".tex"},
}


@dataclass
class ContextInfo:
    section: str = ""
    activity_type: str = ""
    activity: str = ""
    subcontext: str = ""
    confidence: str = ""


@dataclass
class Stats:
    records: int = 0
    extracted: int = 0
    missing: int = 0
    hash_mismatches: int = 0
    hashes_verified: int = 0
    directories: int = 0
    unresolved: int = 0
    collisions: int = 0
    duplicate_references: int = 0
    duplicate_names: int = 0
    categories: Counter = field(default_factory=Counter)
    components: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def safe_part(value: str | None, fallback: str = "unknown",
              max_length: int = 140) -> str:
    """Return a portable, non-traversing filesystem path component."""
    value = (value or "").strip().replace("\x00", "")
    value = re.sub(r"[\x00-\x1f\\/:*?\"<>|]", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if value in {"", ".", ".."}:
        value = fallback
    if len(value) > max_length:
        suffix = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        value = value[:max_length - 10].rstrip() + "__" + suffix
    return value


def safe_moodle_path(value: str | None) -> Path:
    parts = [
        safe_part(part) for part in PurePosixPath(value or "/").parts
        if part not in ("/", "", ".", "..")
    ]
    return Path(*parts) if parts else Path()


def unique_path(path: Path, file_id: str) -> tuple[Path, str]:
    if not path.exists():
        return path, ""
    identity = safe_part(file_id, "unknown")
    candidate = path.with_name(f"{path.stem}__file-{identity}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(
            f"{path.stem}__file-{identity}-{counter}{path.suffix}"
        )
        counter += 1
    return candidate, f"Name collision; saved as {candidate.name}"


def is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def extract_archive_safely(archive: Path, destination: Path) -> None:
    """Extract ZIP/TAR without traversal, links, devices, or special files."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = destination / info.filename
                if not is_within(destination, target):
                    raise ValueError(f"Unsafe archive path: {info.filename}")
                mode = info.external_attr >> 16
                entry_type = stat.S_IFMT(mode)
                if entry_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError(
                        f"Archive contains an unsafe special entry: {info.filename}"
                    )
            zf.extractall(destination)
        return
    try:
        with tarfile.open(archive, "r:*") as tf:
            safe_members = []
            for member in tf.getmembers():
                target = destination / member.name
                if not is_within(destination, target):
                    raise ValueError(f"Unsafe archive path: {member.name}")
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError(f"Archive contains an unsafe entry: {member.name}")
                safe_members.append(member)
            # Manual extraction keeps compatibility with Python 3.9.
            for member in safe_members:
                if sys.version_info >= (3, 12):
                    tf.extract(member, destination, filter="data")
                else:
                    tf.extract(member, destination)
    except tarfile.ReadError as exc:
        raise ValueError("Input is not a supported ZIP/TAR Moodle backup") from exc


def find_backup_root(root: Path) -> Path:
    matches = list(root.rglob("files.xml"))
    if not matches:
        raise FileNotFoundError("files.xml was not found in the backup")
    if len(matches) > 1:
        exact = [p for p in matches if p.parent == root]
        if len(exact) == 1:
            return exact[0].parent
        raise ValueError(f"More than one files.xml found ({len(matches)})")
    return matches[0].parent


def text_of(element: ET.Element, name: str) -> str:
    for child in element:
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def descendant_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for wanted in names:
        for child in element.iter():
            if local_name(child.tag) == wanted and (child.text or "").strip():
                return (child.text or "").strip()
    return ""


def parse_xml(path: Path) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None


def source_path(backup_root: Path, contenthash: str) -> Path | None:
    """Locate bytes in common MBZ and Moodledata filedir layouts."""
    candidates = (
        backup_root / "files" / contenthash,
        backup_root / "files" / contenthash[:2] / contenthash,
        backup_root / "files" / contenthash[:2] / contenthash[2:4] / contenthash,
    )
    return next((p for p in candidates if p.is_file()), None)


def sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_category(filename: str, mimetype: str) -> str:
    ext = Path(filename).suffix.lower()
    for category, extensions in TYPE_EXTENSIONS.items():
        if ext in extensions:
            return category
    mime = (mimetype or mimetypes.guess_type(filename)[0] or "").lower()
    for prefix, category in (
        ("image/", "Images"), ("audio/", "Audio"), ("video/", "Video"),
        ("text/", "Text"),
    ):
        if mime.startswith(prefix):
            return category
    return "Other"


def iter_file_records(files_xml: Path):
    for _event, elem in ET.iterparse(files_xml, events=("end",)):
        if local_name(elem.tag) != "file":
            continue
        yield {
            "file_id": elem.get("id", ""),
            "original_filename": text_of(elem, "filename"),
            "original_filepath": text_of(elem, "filepath"),
            "component": text_of(elem, "component"),
            "filearea": text_of(elem, "filearea"),
            "itemid": text_of(elem, "itemid"),
            "contextid": text_of(elem, "contextid"),
            "contenthash": text_of(elem, "contenthash"),
            "mimetype": text_of(elem, "mimetype"),
            "filesize": text_of(elem, "filesize"),
            "author": text_of(elem, "author"),
            "license": text_of(elem, "license"),
            "source": text_of(elem, "source"),
            "timecreated": text_of(elem, "timecreated"),
            "timemodified": text_of(elem, "timemodified"),
        }
        elem.clear()


def course_title(backup_root: Path) -> str:
    """Best-effort course title, used only in report metadata."""
    for filename in ("course/course.xml", "moodle_backup.xml"):
        root = parse_xml(backup_root / filename)
        if root is not None:
            title = descendant_text(root, ("fullname", "title", "shortname"))
            if title:
                return title
    return backup_root.name


def build_context_index(backup_root: Path) -> tuple[dict[tuple[str, str], ContextInfo],
                                                    dict[str, ContextInfo],
                                                    list[str]]:
    """Build generic indexes from activity, section, Book, and question XML.

    Moodle backup variants use different identifiers.  Exact
    (component,itemid) matches are preferred; contextid is a secondary fallback.
    """
    exact: dict[tuple[str, str], ContextInfo] = {}
    by_context: dict[str, ContextInfo] = {}
    warnings: list[str] = []
    section_names: dict[str, str] = {}

    sections_root = backup_root / "sections"
    if sections_root.exists():
        for xml_path in sections_root.rglob("section.xml"):
            root = parse_xml(xml_path)
            if root is None:
                warnings.append(f"Could not parse {xml_path.relative_to(backup_root)}")
                continue
            sid = root.get("id", "") or descendant_text(root, ("sectionid", "id"))
            name = descendant_text(root, ("name", "title"))
            number = descendant_text(root, ("section", "sectionnumber", "number"))
            if not name:
                name = f"Section {number}" if number else f"Section {sid}"
            if sid:
                section_names[sid] = name

    activities_root = backup_root / "activities"
    if activities_root.exists():
        for activity_dir in activities_root.iterdir():
            if not activity_dir.is_dir():
                continue
            module = parse_xml(activity_dir / "module.xml")
            activity_xmls = [
                p for p in activity_dir.glob("*.xml")
                if p.name not in {"module.xml", "grades.xml", "roles.xml",
                                  "inforef.xml", "calendar.xml", "completion.xml"}
            ]
            content = next((parse_xml(p) for p in activity_xmls), None)
            component = ""
            activity_type = activity_dir.name.split("_", 1)[0]
            activity_name = activity_dir.name
            section_id = context_id = module_id = ""
            if module is not None:
                activity_type = descendant_text(module, ("modulename", "modulename")) or activity_type
                component = f"mod_{activity_type}"
                section_id = descendant_text(module, ("sectionid",))
                context_id = descendant_text(module, ("contextid",))
                module_id = module.get("id", "") or descendant_text(module, ("moduleid", "id"))
            if content is not None:
                activity_name = descendant_text(content, ("name", "title")) or activity_name
                instance_id = content.get("id", "") or descendant_text(content, ("id",))
            else:
                instance_id = ""
            info = ContextInfo(
                section=section_names.get(section_id, ""),
                activity_type=activity_type,
                activity=activity_name,
                confidence="activity metadata",
            )
            for key in {module_id, instance_id} - {""}:
                if component:
                    exact[(component, key)] = info
            if context_id:
                by_context[context_id] = info

            # Book chapter itemids normally refer to chapter IDs.
            if activity_type == "book":
                for xml_path in activity_dir.rglob("*.xml"):
                    root = parse_xml(xml_path)
                    if root is None:
                        continue
                    for elem in root.iter():
                        if local_name(elem.tag) != "chapter":
                            continue
                        cid = elem.get("id", "") or text_of(elem, "id")
                        chapter_name = descendant_text(elem, ("title", "name"))
                        if cid:
                            exact[("mod_book", cid)] = ContextInfo(
                                section=info.section, activity_type="book",
                                activity=info.activity,
                                subcontext=chapter_name or f"Chapter {cid}",
                                confidence="book chapter metadata",
                            )

    # Quiz question media uses question id as itemid.
    questions = backup_root / "questions.xml"
    root = parse_xml(questions) if questions.exists() else None
    if root is not None:
        for elem in root.iter():
            if local_name(elem.tag) != "question":
                continue
            qid = elem.get("id", "") or text_of(elem, "id")
            qname = descendant_text(elem, ("name",))
            if qid:
                exact[("question", qid)] = ContextInfo(
                    activity_type="question", activity="Question bank",
                    subcontext=qname or f"Question {qid}",
                    confidence="question metadata",
                )
    return exact, by_context, warnings


def resolve_context(record: dict[str, str],
                    exact: dict[tuple[str, str], ContextInfo],
                    by_context: dict[str, ContextInfo]) -> ContextInfo:
    key = (record["component"], record["itemid"])
    if key in exact:
        return exact[key]
    if record["contextid"] in by_context:
        return by_context[record["contextid"]]
    # Technical-but-readable fallback for core areas that lack visible metadata.
    component = record["component"]
    if component == "course":
        return ContextInfo(section="Course-level content",
                           activity_type="course", activity=record["filearea"],
                           confidence="technical fallback")
    return ContextInfo(activity_type=component.removeprefix("mod_"),
                       confidence="unresolved")


def relative_context_path(record: dict[str, str]) -> Path:
    return (
        Path(safe_part(record["component"]))
        / safe_part(record["filearea"])
        / safe_part(record["itemid"], "0")
        / safe_moodle_path(record["original_filepath"])
        / safe_part(record["original_filename"], f"file-{record['file_id']}")
    )


def relative_course_path(record: dict[str, str], info: ContextInfo) -> Path:
    filename = safe_part(record["original_filename"], f"file-{record['file_id']}")
    if info.confidence == "unresolved":
        return (
            Path("Unresolved")
            / safe_part(record["component"])
            / safe_part(record["filearea"])
            / safe_part(record["itemid"], "0")
            / safe_moodle_path(record["original_filepath"])
            / filename
        )
    path = Path(safe_part(info.section, "Course-level content"))
    activity_label = info.activity
    if info.activity_type and info.activity:
        activity_label = f"{info.activity_type} - {info.activity}"
    path /= safe_part(activity_label, record["component"])
    if info.subcontext:
        path /= safe_part(info.subcontext)
    return path / safe_moodle_path(record["original_filepath"]) / filename


def copy_to_view(source: Path, root: Path, relative: Path, file_id: str,
                 link_mode: str) -> tuple[str, str]:
    destination, note = unique_path(root / relative, file_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if link_mode == "hardlink":
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
            note = (note + "; " if note else "") + "Hard link unavailable; copied"
    else:
        shutil.copy2(source, destination)
    return str(destination.relative_to(root.parent)), note


def write_report(path: Path, title: str, modes: set[str], stats: Stats,
                 verify_hashes: bool) -> None:
    lines = [
        f"# Moodle file extraction report — {title}", "",
        "## Summary", "",
        f"- File records: {stats.records}",
        f"- Recovered files: {stats.extracted}",
        f"- Missing content-hash files: {stats.missing}",
        f"- SHA-1 hashes verified: {stats.hashes_verified}",
        f"- SHA-1 mismatches: {stats.hash_mismatches}",
        f"- Directory records found: {stats.directories}",
        f"- Course mappings unresolved: {stats.unresolved}",
        f"- Filename collisions safely renamed: {stats.collisions}",
        f"- Duplicate content references: {stats.duplicate_references}",
        f"- Repeated filenames: {stats.duplicate_names}",
        f"- Hash verification requested: {'yes' if verify_hashes else 'no'}",
        f"- Folder views generated: {', '.join(sorted(modes))}", "",
        "No source record was intentionally overwritten. Missing and unresolved "
        "records remain listed in `resource_manifest.csv`.", "",
    ]
    if stats.categories:
        lines += ["## Recovered file categories", ""]
        lines += [f"- {name}: {count}" for name, count in stats.categories.most_common()]
        lines.append("")
    if stats.components:
        lines += ["## Moodle components", ""]
        lines += [f"- `{name or 'unknown'}`: {count}"
                  for name, count in stats.components.most_common()]
        lines.append("")
    if stats.warnings:
        lines += ["## Warnings", ""]
        lines += [f"- {warning}" for warning in dict.fromkeys(stats.warnings)]
        lines.append("")
    if stats.missing == 0 and stats.hash_mismatches == 0:
        lines += ["## Result", "",
                  "All non-directory file records were recovered. Course-based "
                  "organisation is best-effort; use the context view and manifest "
                  "as the authoritative provenance.", ""]
    else:
        lines += ["## Result", "",
                  "Extraction completed with exceptions. Review rows marked "
                  "`missing` or `hash_mismatch` in the manifest.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def extract_files(backup_root: Path, output: Path, modes: set[str],
                  include_directories: bool, verify_hashes: bool,
                  link_mode: str) -> Stats:
    output.mkdir(parents=True, exist_ok=True)
    exact, by_context, metadata_warnings = build_context_index(backup_root)
    stats = Stats(warnings=metadata_warnings)
    seen_hashes: Counter = Counter()
    seen_names: Counter = Counter()
    manifest = output / "resource_manifest.csv"
    context_root = output / "files_by_moodle_context"
    course_root = output / "resource_bundle"
    type_root = output / "resource_bundle_by_type"
    for mode, root in (
        ("context", context_root),
        ("course", course_root),
        ("type", type_root),
    ):
        if mode in modes:
            root.mkdir(parents=True, exist_ok=True)

    with manifest.open("w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for record in iter_file_records(backup_root / "files.xml"):
            stats.records += 1
            record.update({
                "section_name": "", "activity_type": "", "activity_name": "",
                "subcontext_name": "", "file_category": "",
                "duplicate_kind": "", "context_path": "", "course_path": "",
                "type_path": "", "status": "", "note": "",
            })
            if record["original_filename"] == ".":
                stats.directories += 1
                record["status"] = "directory_record"
                if include_directories:
                    writer.writerow(record)
                continue

            info = resolve_context(record, exact, by_context)
            category = file_category(record["original_filename"], record["mimetype"])
            record.update({
                "section_name": info.section,
                "activity_type": info.activity_type,
                "activity_name": info.activity,
                "subcontext_name": info.subcontext,
                "file_category": category,
            })
            if info.confidence == "unresolved":
                stats.unresolved += 1
            stats.categories[category] += 1
            stats.components[record["component"]] += 1

            contenthash = record["contenthash"]
            duplicate_notes = []
            if contenthash:
                seen_hashes[contenthash] += 1
                if seen_hashes[contenthash] > 1:
                    duplicate_notes.append(
                        "same content hash referenced more than once"
                    )
                    stats.duplicate_references += 1
            folded_name = record["original_filename"].casefold()
            if folded_name:
                seen_names[folded_name] += 1
                if seen_names[folded_name] > 1:
                    duplicate_notes.append("filename occurs more than once")
                    stats.duplicate_names += 1
            record["duplicate_kind"] = "; ".join(duplicate_notes)

            source = source_path(backup_root, contenthash) if len(contenthash) >= 4 else None
            if source is None:
                record["status"] = "missing"
                record["note"] = "Content-hash file is absent from the backup"
                stats.missing += 1
                writer.writerow(record)
                continue

            notes = []
            if verify_hashes:
                if sha1(source) != contenthash:
                    record["status"] = "hash_mismatch"
                    notes.append("Stored SHA-1 differs from files.xml")
                    stats.hash_mismatches += 1
                else:
                    record["status"] = "extracted"
                    stats.hashes_verified += 1
            else:
                record["status"] = "extracted"

            view_specs = []
            if "context" in modes:
                view_specs.append(("context_path", context_root,
                                   relative_context_path(record)))
            if "course" in modes:
                view_specs.append(("course_path", course_root,
                                   relative_course_path(record, info)))
            if "type" in modes:
                view_specs.append(("type_path", type_root,
                                   Path(category) /
                                   safe_part(record["original_filename"],
                                             f"file-{record['file_id']}")))
            for field_name, root, relative in view_specs:
                saved_path, note = copy_to_view(
                    source, root, relative, record["file_id"], link_mode
                )
                record[field_name] = saved_path
                if note:
                    notes.append(f"{field_name}: {note}")
                    if "Name collision" in note:
                        stats.collisions += 1

            record["note"] = "; ".join(notes)
            stats.extracted += 1
            writer.writerow(record)

    write_report(output / "extraction_report.md", course_title(backup_root),
                 modes, stats, verify_hashes)
    return stats


def prepare_output(output: Path, overwrite: bool) -> None:
    """Create an empty result area without deleting unrelated user files."""
    generated = (
        output / "files_by_moodle_context",
        output / "resource_bundle",
        output / "resource_bundle_by_type",
        output / "resource_manifest.csv",
        output / "extraction_report.md",
    )
    if output.exists() and any(output.iterdir()):
        if not overwrite:
            raise ValueError(
                f"output directory is not empty: {output}\n"
                "Use --overwrite-output or choose another directory."
            )
        unknown = [path for path in output.iterdir() if path not in generated]
        if unknown:
            names = ", ".join(path.name for path in unknown[:5])
            extra = " ..." if len(unknown) > 5 else ""
            raise ValueError(
                "Refusing to clean an output directory containing unrecognised "
                f"items: {names}{extra}"
            )
        for path in generated:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    output.mkdir(parents=True, exist_ok=True)


def validate_outputs(output: Path, modes: set[str]) -> None:
    expected = [
        output / "resource_manifest.csv",
        output / "extraction_report.md",
    ]
    roots = {
        "context": output / "files_by_moodle_context",
        "course": output / "resource_bundle",
        "type": output / "resource_bundle_by_type",
    }
    expected.extend(roots[mode] for mode in modes)
    missing = [str(path) for path in expected if not path.exists()]
    if missing:
        raise OSError("Expected output was not created: " + ", ".join(missing))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract and organise files from a Moodle .mbz backup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s course.mbz -o output/course --mode all --verify-hashes
  %(prog)s course.mbz -o output/course --mode course
  %(prog)s extracted_backup/ -o output/course --mode context

Every mode writes resource_manifest.csv and extraction_report.md.
""",
    )
    parser.add_argument("backup", type=Path,
                        help="Moodle .mbz file or extracted backup directory")
    parser.add_argument("--output", "-o", type=Path,
                        default=Path("moodle_extracted_files"))
    parser.add_argument("--mode", choices=MODES, default="context",
                        help="Folder view to generate (default: context)")
    parser.add_argument("--by-context", action="store_true",
                        help="Also generate files_by_moodle_context/")
    parser.add_argument("--by-course", action="store_true",
                        help="Also generate resource_bundle/")
    parser.add_argument("--by-type", action="store_true",
                        help="Also generate resource_bundle_by_type/")
    parser.add_argument("--flat", action="store_true",
                        help="Deprecated alias for --mode type")
    parser.add_argument("--include-directories", action="store_true",
                        help="Retain directory records in the manifest")
    parser.add_argument("--verify-hashes", action="store_true",
                        help="Recalculate and compare every SHA-1 content hash")
    parser.add_argument("--link-mode", choices=("copy", "hardlink"),
                        default="copy",
                        help="Use copies (portable) or hard links (space-saving)")
    parser.add_argument("--overwrite-output", action="store_true",
                        help="Replace this tool's outputs in the destination; "
                             "refuse if unrelated items are present")
    return parser.parse_args(argv)


def selected_modes(args: argparse.Namespace) -> set[str]:
    modes = {"context", "course", "type"} if args.mode == "all" else {args.mode}
    if args.by_context:
        modes.add("context")
    if args.by_course:
        modes.add("course")
    if args.by_type or args.flat:
        modes.add("type")
    return modes


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    backup = args.backup.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not backup.exists():
        print(f"Error: backup not found: {backup}", file=sys.stderr)
        return 2
    try:
        prepare_output(output, args.overwrite_output)
        if backup.is_dir():
            root = find_backup_root(backup)
            stats = extract_files(
                root, output, selected_modes(args), args.include_directories,
                args.verify_hashes, args.link_mode,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="moodle_mbz_") as temp:
                temp_path = Path(temp)
                extract_archive_safely(backup, temp_path)
                root = find_backup_root(temp_path)
                stats = extract_files(
                    root, output, selected_modes(args), args.include_directories,
                    args.verify_hashes, args.link_mode,
                )
        validate_outputs(output, selected_modes(args))
    except (OSError, ValueError, ET.ParseError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Recovered {stats.extracted} files; {stats.missing} missing; "
        f"{stats.hashes_verified} hashes verified; "
        f"{stats.hash_mismatches} hash mismatches; "
        f"{stats.unresolved} course mappings unresolved."
    )
    print(f"Manifest: {output / 'resource_manifest.csv'}")
    print(f"Report:   {output / 'extraction_report.md'}")
    return 0 if stats.missing == 0 and stats.hash_mismatches == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
