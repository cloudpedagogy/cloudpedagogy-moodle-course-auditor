#!/usr/bin/env python3
"""Compare two Moodle course backup (.mbz) files without modifying them.

Outputs:
  comparison_report.md, comparison_report.html, comparison_data.json,
  course_changes.csv, activity_changes.csv, content_changes.csv,
  and file_changes.csv.

Only Python's standard library is required.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import json
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


TECHNICAL_FIELDS = {
    "id", "contextid", "backupid", "timecreated", "timemodified",
    "added", "revision", "version", "previousid", "userid",
}
SENSITIVE_OR_VOLATILE_FIELDS = {
    "password", "resourcekey", "secret", "token", "apikey", "consumerkey",
    "servicesalt", "enrolpassword",
}
NULL_VALUES = {"$@NULL@$", "@NULL@", "NULL"}
MAX_XML_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 1_000_000
MAX_EXPANDED_BYTES = 100 * 1024 * 1024 * 1024
CONTENT_TAGS = {
    "intro", "content", "summary", "description", "externalurl",
    "name", "title", "subchapter", "hidden", "questiontext",
}

# These types receive content-aware comparison. Other activity types are still
# inventoried and compared for title/location/visibility/completion, and are
# clearly listed as "structural only" in the report rather than silently lost.
DEEP_ACTIVITY_TYPES = {"book", "page", "label", "url"}
INTRO_ACTIVITY_TYPES = {
    "assign", "chat", "choice", "data", "feedback", "folder", "forum",
    "glossary", "h5pactivity", "imscp", "lesson", "lti", "quiz", "resource",
    "scorm", "survey", "wiki", "workshop",
}


@dataclass
class Chapter:
    key: str
    title: str
    content: str
    order: int
    subchapter: bool = False


@dataclass
class Activity:
    key: str
    moduleid: str
    modtype: str
    title: str
    section_key: str
    section_name: str
    order: int
    visible: str = ""
    completion: str = ""
    availability: str = ""
    content: str = ""
    url: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    settings: dict[str, str] = field(default_factory=dict)
    comparison_depth: str = "structural"


@dataclass
class Section:
    key: str
    number: str
    name: str
    summary: str
    order: int


@dataclass
class StoredFile:
    key: str
    filename: str
    filepath: str
    component: str
    filearea: str
    mimetype: str
    size: int
    contenthash: str


@dataclass
class CourseSnapshot:
    source: str
    course: dict[str, str]
    sections: list[Section]
    activities: list[Activity]
    files: list[StoredFile]
    warnings: list[str] = field(default_factory=list)


def text_of(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    value = node.findtext(path)
    cleaned = (value or default).strip()
    return default if cleaned in NULL_VALUES else cleaned


def normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def plain_text(value: str) -> str:
    value = re.sub(r"<\s*br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"</(?:p|div|li|h[1-6])\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return "\n".join(line.strip() for line in html.unescape(value).splitlines() if line.strip())


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalise_space(value).lower()).strip()


def safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_target(destination: Path, member_name: str) -> Path:
    target = (destination / member_name).resolve()
    base = destination.resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"Unsafe path in MBZ: {member_name}")
    return target


def extract_mbz(path: Path, destination: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"MBZ not found: {path}")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("MBZ contains an unreasonable number of archive members")
            if sum(member.file_size for member in members) > MAX_EXPANDED_BYTES:
                raise ValueError("MBZ expands beyond the 100 GiB safety limit")
            for member in members:
                _safe_target(destination, member.filename)
            archive.extractall(destination)
        return
    try:
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("MBZ contains an unreasonable number of archive members")
            if sum(max(member.size, 0) for member in members) > MAX_EXPANDED_BYTES:
                raise ValueError("MBZ expands beyond the 100 GiB safety limit")
            for member in members:
                _safe_target(destination, member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError(f"Unsafe special member in MBZ: {member.name}")
            try:
                archive.extractall(destination, members=members, filter="data")
            except TypeError:  # Python 3.11: validated regular members only.
                archive.extractall(destination, members=members)
    except tarfile.TarError as exc:
        raise ValueError(f"Unsupported or damaged MBZ archive: {path}") from exc


def parse_xml(path: Path) -> ET.Element | None:
    try:
        if path.stat().st_size > MAX_XML_BYTES:
            raise ValueError(f"XML file exceeds the 256 MiB safety limit: {path.name}")
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None


def locate_root(extracted: Path) -> Path:
    manifests = list(extracted.rglob("moodle_backup.xml"))
    if not manifests:
        raise ValueError("moodle_backup.xml is missing")
    roots = {manifest.parent.resolve() for manifest in manifests}
    if len(roots) != 1:
        raise ValueError("MBZ contains more than one Moodle backup manifest")
    return manifests[0].parent


def manifest_activity_titles(root: Path) -> dict[str, str]:
    xml = parse_xml(root / "moodle_backup.xml")
    result: dict[str, str] = {}
    if xml is None:
        return result
    for item in xml.findall(".//information/contents/activities/activity"):
        moduleid = text_of(item, "moduleid")
        if moduleid:
            result[moduleid] = text_of(item, "title")
    return result


def parse_course(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    xml = parse_xml(root / "course" / "course.xml")
    if xml is not None:
        for key in ("fullname", "shortname", "idnumber", "summary", "format", "numsections", "visible", "lang", "enablecompletion"):
            result[key] = text_of(xml, key)
    manifest = parse_xml(root / "moodle_backup.xml")
    if manifest is not None:
        info = manifest.find("information")
        if info is not None:
            for key in ("moodle_version", "moodle_release", "backup_name", "backup_type", "original_course_fullname", "original_course_shortname"):
                if key not in result or not result[key]:
                    result[key] = text_of(info, key)
    return result


def parse_sections(root: Path) -> tuple[list[Section], dict[str, Section], dict[str, tuple[Section, int]]]:
    sections: list[Section] = []
    by_id: dict[str, Section] = {}
    module_positions: dict[str, tuple[Section, int]] = {}
    paths = sorted((root / "sections").glob("section_*/section.xml")) if (root / "sections").exists() else []
    for fallback_order, path in enumerate(paths):
        xml = parse_xml(path)
        if xml is None:
            continue
        sid = text_of(xml, "id", path.parent.name.removeprefix("section_"))
        number = text_of(xml, "number", str(fallback_order))
        section = Section(
            key=sid or number,
            number=number,
            name=text_of(xml, "name") or ("General" if number == "0" else f"Section {number}"),
            summary=text_of(xml, "summary"),
            order=safe_int(number, fallback_order),
        )
        sections.append(section)
        by_id[section.key] = section
        sequence = [x.strip() for x in text_of(xml, "sequence").split(",") if x.strip()]
        for pos, moduleid in enumerate(sequence):
            module_positions[moduleid] = (section, pos)
    sections.sort(key=lambda s: s.order)
    return sections, by_id, module_positions


def meaningful_xml(root: ET.Element | None) -> dict[str, str]:
    if root is None:
        return {}
    values: dict[str, str] = {}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in TECHNICAL_FIELDS or tag not in CONTENT_TAGS:
            continue
        value = normalise_space(node.text or "")
        if value:
            values[tag] = value
    return values


def direct_settings(root: ET.Element | None) -> dict[str, str]:
    """Return stable top-level activity settings without user/nested records."""
    if root is None:
        return {}
    # Moodle activity XML normally wraps the activity entity in one root node.
    # Restrict comparison to that entity's direct scalar fields: descending
    # through every node can accidentally compare forum posts, attempts,
    # submissions or other user data as if they were activity configuration.
    container = next((node for node in root.iter() if node is not root), root)
    values: dict[str, str] = {}
    for node in list(container):
        tag = node.tag.rsplit("}", 1)[-1].lower()
        compact = re.sub(r"[^a-z0-9]", "", tag)
        if (tag in TECHNICAL_FIELDS or tag in CONTENT_TAGS or tag.endswith("id")
                or list(node) or any(secret in compact for secret in SENSITIVE_OR_VOLATILE_FIELDS)):
            continue
        value = normalise_space(node.text or "")
        if value and value not in NULL_VALUES and len(value) <= 10_000:
            values[tag] = value
    return values


def parse_book(activity_root: Path) -> tuple[str, str, list[Chapter]]:
    xml = parse_xml(activity_root / "book.xml")
    if xml is None:
        return "", "", []
    found_book = xml.find(".//book")
    book = found_book if found_book is not None else xml
    title = text_of(book, "name")
    intro = text_of(book, "intro")
    chapters: list[Chapter] = []
    for order, node in enumerate(book.findall(".//chapters/chapter")):
        # Moodle stores chapter IDs as an XML attribute in current backups
        # (<chapter id="123">), although older/exported variants may use an
        # <id> child. Read both so a renamed chapter remains the same chapter.
        cid = (node.get("id") or text_of(node, "id")).strip()
        ctitle = text_of(node, "title")
        chapters.append(Chapter(
            key=cid or f"{slug(ctitle)}:{order}", title=ctitle,
            content=text_of(node, "content"), order=order,
            subchapter=text_of(node, "subchapter") == "1",
        ))
    return title, intro, chapters


def parse_activity(activity_root: Path, titles: dict[str, str], positions: dict[str, tuple[Section, int]], warnings: list[str]) -> Activity:
    match = re.match(r"(.+)_([0-9]+)$", activity_root.name)
    modtype, folder_moduleid = (match.group(1), match.group(2)) if match else (activity_root.name, "")
    module = parse_xml(activity_root / "module.xml")
    moduleid = text_of(module, "id", folder_moduleid)
    section_id = text_of(module, "sectionid")
    section, order = positions.get(moduleid, (Section(section_id, "", "Unknown section", "", 9999), 9999))
    visible = text_of(module, "visible")
    completion = text_of(module, "completion")
    availability = text_of(module, "availability")
    title = titles.get(moduleid, "")
    content = ""
    url = ""
    chapters: list[Chapter] = []
    settings: dict[str, str] = {}
    depth = "structural"
    if modtype == "book":
        parsed_title, content, chapters = parse_book(activity_root)
        title = parsed_title or title
        depth = "deep"
    else:
        # Always prefer the activity's own XML file. Alphabetical selection can
        # otherwise pick inforef.xml before page.xml, label.xml or url.xml and
        # silently lose the actual content and external URL.
        primary = activity_root / f"{modtype}.xml"
        candidates = [
            p for p in sorted(activity_root.glob("*.xml"))
            if p.name not in {"module.xml", "inforef.xml", "roles.xml",
                              "grades.xml", "grade_history.xml"}
        ]
        chosen = primary if primary.is_file() else (candidates[0] if candidates else None)
        activity_xml = parse_xml(chosen) if chosen else None
        if chosen is None:
            warnings.append(f"{activity_root.name}: no activity-specific XML found; structural comparison only")
        elif activity_xml is None:
            warnings.append(f"{activity_root.name}: could not parse {chosen.name}; structural comparison only")
        values = meaningful_xml(activity_xml)
        title = values.get("name") or values.get("title") or title
        content = values.get("content") or values.get("intro") or values.get("description") or ""
        url = values.get("externalurl", "")
        settings = direct_settings(activity_xml)
        if modtype in DEEP_ACTIVITY_TYPES:
            depth = "deep"
        elif modtype in INTRO_ACTIVITY_TYPES:
            depth = "intro/settings"
        else:
            warnings.append(f"{modtype}: unsupported activity type; title and module settings compared only")
    key = f"{modtype}:{moduleid}" if moduleid else f"{modtype}:{slug(section.name)}:{slug(title)}:{order}"
    return Activity(key, moduleid, modtype, title, section.key, section.name, order, visible, completion, availability, content, url, chapters, settings, depth)


def parse_files(root: Path) -> list[StoredFile]:
    xml = parse_xml(root / "files.xml")
    files: list[StoredFile] = []
    if xml is None:
        return files
    for node in xml.findall(".//file"):
        filename = text_of(node, "filename")
        if not filename or filename == ".":
            continue
        filepath = text_of(node, "filepath", "/")
        component = text_of(node, "component")
        area = text_of(node, "filearea")
        contenthash = text_of(node, "contenthash")
        size_text = text_of(node, "filesize", "0")
        key = "|".join((component, area, filepath, filename)).lower()
        files.append(StoredFile(key, filename, filepath, component, area, text_of(node, "mimetype"), safe_int(size_text), contenthash))
    return files


def load_snapshot(path: Path) -> CourseSnapshot:
    with tempfile.TemporaryDirectory(prefix="mbz_compare_") as tmp:
        extracted = Path(tmp)
        extract_mbz(path, extracted)
        root = locate_root(extracted)
        titles = manifest_activity_titles(root)
        sections, _, positions = parse_sections(root)
        activities = []
        warnings: list[str] = []
        activities_dir = root / "activities"
        if activities_dir.exists():
            for item in sorted(p for p in activities_dir.iterdir() if p.is_dir()):
                activities.append(parse_activity(item, titles, positions, warnings))
        return CourseSnapshot(str(path), parse_course(root), sections, activities, parse_files(root), sorted(set(warnings)))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, slug(a), slug(b)).ratio()


def _semantic_score(a: Any, b: Any, kind: str) -> float | None:
    if kind == "activity" and a.modtype != b.modtype:
        return None
    if kind == "file":
        if (a.component, a.filearea) != (b.component, b.filearea):
            return None
        score = similarity(a.filename, b.filename)
        if a.filepath == b.filepath: score += .20
        if a.contenthash and a.contenthash == b.contenthash: score += .40
        return score
    name_a = getattr(a, "title", getattr(a, "name", ""))
    name_b = getattr(b, "title", getattr(b, "name", ""))
    score = similarity(name_a, name_b)
    if kind == "section" and getattr(a, "number", "") == getattr(b, "number", ""):
        score += .35
    if kind == "activity":
        if a.section_name == b.section_name: score += .15
        if normalise_space(a.content) and normalise_space(a.content) == normalise_space(b.content): score += .20
    if kind == "chapter" and normalise_space(a.content) == normalise_space(b.content):
        score += .35
    return score


def match_items(before: list[Any], after: list[Any], kind: str) -> tuple[list[tuple[Any, Any]], list[Any], list[Any]]:
    """Match stable IDs first, then semantic names, while avoiding false matches."""
    matched: list[tuple[Any, Any]] = []
    remaining_a = list(before)
    remaining_b = list(after)
    key_counts_a = Counter(getattr(x, "key", "") for x in remaining_a)
    key_counts_b = Counter(getattr(x, "key", "") for x in remaining_b)
    for a in list(remaining_a):
        key = getattr(a, "key", "")
        candidate = next((b for b in remaining_b if key and key == getattr(b, "key", "")), None)
        if candidate and key_counts_a[key] == key_counts_b[key] == 1:
            matched.append((a, candidate)); remaining_a.remove(a); remaining_b.remove(candidate)
    for a in list(remaining_a):
        scored: list[tuple[float, Any]] = []
        for b in remaining_b:
            score = _semantic_score(a, b, kind)
            if score is not None:
                scored.append((score, b))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            score, candidate = scored[0]
            runner_up = scored[1][0] if len(scored) > 1 else -1.0
            threshold = .90 if kind == "file" else .72
            # Do not guess when two candidates are effectively tied.
            if score >= threshold and score - runner_up >= .05:
                matched.append((a, candidate)); remaining_a.remove(a); remaining_b.remove(candidate)
    return matched, remaining_a, remaining_b


def diff_text(before: str, after: str, max_lines: int = 80) -> str:
    a = plain_text(before).splitlines()
    b = plain_text(after).splitlines()
    lines = list(difflib.unified_diff(a, b, fromfile="before", tofile="after", lineterm=""))
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... diff truncated ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)


def add_change(rows: list[dict[str, Any]], category: str, status: str, item_type: str, item: str, field: str, before: Any = "", after: Any = "", detail: str = "") -> None:
    rows.append({"category": category, "status": status, "item_type": item_type, "item": item, "field": field, "before": before, "after": after, "detail": detail})


def compare_snapshots(before: CourseSnapshot, after: CourseSnapshot) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    activity_changes: list[dict[str, Any]] = []
    content_changes: list[dict[str, Any]] = []
    file_changes: list[dict[str, Any]] = []

    for key in sorted(set(before.course) | set(after.course)):
        if key in TECHNICAL_FIELDS or key in {"backup_name"}:
            continue
        a, b = before.course.get(key, ""), after.course.get(key, "")
        if normalise_space(a) != normalise_space(b):
            add_change(changes, "course", "modified", "course", before.course.get("fullname", "Course"), key, a, b)

    section_pairs, removed_sections, added_sections = match_items(before.sections, after.sections, "section")
    for section in removed_sections:
        add_change(changes, "structure", "removed", "section", section.name, "section", section.name, "")
    for section in added_sections:
        add_change(changes, "structure", "added", "section", section.name, "section", "", section.name)
    for a, b in section_pairs:
        if a.name != b.name:
            add_change(changes, "structure", "modified", "section", b.name, "name", a.name, b.name)
        if normalise_space(a.summary) != normalise_space(b.summary):
            add_change(content_changes, "content", "modified", "section", b.name, "summary", plain_text(a.summary), plain_text(b.summary), diff_text(a.summary, b.summary))
        if a.order != b.order:
            add_change(changes, "structure", "moved", "section", b.name, "order", a.order, b.order)

    activity_pairs, removed_activities, added_activities = match_items(before.activities, after.activities, "activity")
    for item in removed_activities:
        add_change(activity_changes, "activity", "removed", item.modtype, item.title, "activity", item.section_name, "")
    for item in added_activities:
        add_change(activity_changes, "activity", "added", item.modtype, item.title, "activity", "", item.section_name)
    for a, b in activity_pairs:
        label = f"{b.modtype}: {b.title}"
        for fld in ("title", "visible", "completion", "availability", "url"):
            av, bv = getattr(a, fld), getattr(b, fld)
            if normalise_space(str(av)) != normalise_space(str(bv)):
                add_change(activity_changes, "configuration" if fld not in {"title", "url"} else "activity", "modified", b.modtype, label, fld, av, bv)
        for setting in sorted(set(a.settings) | set(b.settings)):
            av, bv = a.settings.get(setting, ""), b.settings.get(setting, "")
            if normalise_space(av) != normalise_space(bv):
                add_change(activity_changes, "configuration", "modified", b.modtype, label, setting, av, bv)
        # A position number often shifts merely because a neighbouring item was
        # added/removed. Treat a cross-section change as a move; report explicit
        # reordering only when relative-order analysis is added in a later pass.
        if a.section_name != b.section_name:
            add_change(activity_changes, "structure", "moved", b.modtype, label, "location", f"{a.section_name} / {a.order + 1}", f"{b.section_name} / {b.order + 1}")
        if normalise_space(a.content) != normalise_space(b.content):
            add_change(content_changes, "content", "modified", b.modtype, label, "content", plain_text(a.content), plain_text(b.content), diff_text(a.content, b.content))
        chapter_pairs, removed_chapters, added_chapters = match_items(a.chapters, b.chapters, "chapter")
        for chapter in removed_chapters:
            add_change(content_changes, "content", "removed", "book_chapter", f"{b.title} > {chapter.title}", "chapter", chapter.title, "")
        for chapter in added_chapters:
            add_change(content_changes, "content", "added", "book_chapter", f"{b.title} > {chapter.title}", "chapter", "", chapter.title)
        for ca, cb in chapter_pairs:
            chapter_label = f"{b.title} > {cb.title}"
            if ca.title != cb.title:
                add_change(content_changes, "content", "modified", "book_chapter", chapter_label, "title", ca.title, cb.title)
            if ca.order != cb.order:
                add_change(content_changes, "structure", "moved", "book_chapter", chapter_label, "order", ca.order + 1, cb.order + 1)
            if ca.subchapter != cb.subchapter:
                add_change(content_changes, "structure", "modified", "book_chapter", chapter_label, "subchapter", ca.subchapter, cb.subchapter)
            if normalise_space(ca.content) != normalise_space(cb.content):
                add_change(content_changes, "content", "modified", "book_chapter", chapter_label, "content", plain_text(ca.content), plain_text(cb.content), diff_text(ca.content, cb.content))

    file_pairs, removed_files, added_files = match_items(before.files, after.files, "file")
    for item in removed_files:
        add_change(file_changes, "file", "removed", "file", item.filename, "file", f"{item.size} bytes", "")
    for item in added_files:
        add_change(file_changes, "file", "added", "file", item.filename, "file", "", f"{item.size} bytes")
    for a, b in file_pairs:
        if a.contenthash != b.contenthash:
            add_change(file_changes, "file", "modified", "file", b.filename, "content", f"{a.contenthash} ({a.size} bytes)", f"{b.contenthash} ({b.size} bytes)")

    all_changes = changes + activity_changes + content_changes + file_changes
    counts: dict[str, int] = {}
    for row in all_changes:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    depth_rank = {"structural": 0, "intro/settings": 1, "deep": 2}
    all_activities = before.activities + after.activities
    changed_elements = {
        (row["item_type"], row["item"])
        for row in all_changes
    }
    return {
        "before": asdict(before), "after": asdict(after), "summary": counts,
        "course_changes": changes, "activity_changes": activity_changes,
        "content_changes": content_changes, "file_changes": file_changes,
        "total_changes": len(all_changes),
        "changed_elements": len(changed_elements),
        "coverage": {
            "activity_types_before": sorted({a.modtype for a in before.activities}),
            "activity_types_after": sorted({a.modtype for a in after.activities}),
            "depth_by_type": {
                kind: max(
                    (a.comparison_depth for a in before.activities + after.activities if a.modtype == kind),
                    default="structural",
                    key=lambda depth: depth_rank.get(depth, 0),
                )
                for kind in sorted({a.modtype for a in all_activities})
            },
            "warnings": sorted(set(before.warnings + after.warnings)),
        },
    }


CSV_FIELDS = ["category", "status", "item_type", "item", "field", "before", "after", "detail"]


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(data: dict[str, Any]) -> str:
    before, after = data["before"], data["after"]
    lines = [
        "# Moodle course comparison", "",
        f"- Before: `{before['source']}`", f"- After: `{after['source']}`",
        f"- Change records detected: **{data['total_changes']}** across **{data['changed_elements']}** course elements", "",
        "## Summary", "",
        "| Status | Count |", "|---|---:|",
    ]
    if data["summary"]:
        lines.extend(f"| {k.title()} | {v} |" for k, v in sorted(data["summary"].items()))
    else:
        lines.append("| No meaningful changes | 0 |")
    lines += ["", "## Comparison coverage", "", "| Activity type | Depth |", "|---|---|"]
    depth_labels = {"deep": "Content and settings", "intro/settings": "Introduction and settings", "structural": "Structure and module settings only"}
    for kind, depth in data["coverage"]["depth_by_type"].items():
        lines.append(f"| {md_cell(kind)} | {depth_labels.get(depth, depth)} |")
    if not data["coverage"]["depth_by_type"]:
        lines.append("| No activities found | — |")
    if data["coverage"]["warnings"]:
        lines += ["", "### Coverage warnings", ""]
        lines.extend(f"- {warning}" for warning in data["coverage"]["warnings"])
    sections = [("Course and sections", "course_changes"), ("Activities and configuration", "activity_changes"), ("Content and Book chapters", "content_changes"), ("Files", "file_changes")]
    for title, key in sections:
        lines += ["", f"## {title}", ""]
        rows = data[key]
        if not rows:
            lines.append("No meaningful changes detected.")
            continue
        lines += ["| Status | Type | Item | Field | Before | After |", "|---|---|---|---|---|---|"]
        for row in rows:
            lines.append("| " + " | ".join(md_cell(row[k]) for k in ("status", "item_type", "item", "field", "before", "after")) + " |")
            if row.get("detail"):
                lines += ["", f"### Text difference: {row['item']}", "", "```diff", row["detail"], "```", ""]
    lines += ["", "---", "Technical-only changes such as Moodle IDs, backup names and timestamps are intentionally excluded.", ""]
    return "\n".join(lines)


def render_html(markdown_text: str, data: dict[str, Any]) -> str:
    # Self-contained, deliberately simple HTML report; no third-party dependency.
    body: list[str] = []
    in_table = False
    in_pre = False
    for raw in markdown_text.splitlines():
        if raw.startswith("```"):
            body.append("</code></pre>" if in_pre else "<pre><code>"); in_pre = not in_pre; continue
        if in_pre:
            body.append(html.escape(raw)); continue
        if raw.startswith("|---"):
            continue
        if raw.startswith("|"):
            cells = [html.escape(x.strip()) for x in raw.strip("|").split("|")]
            if not in_table:
                body.append("<table>"); in_table = True
            body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>"); in_table = False
        if raw.startswith("### "): body.append(f"<h3>{html.escape(raw[4:])}</h3>")
        elif raw.startswith("## "): body.append(f"<h2>{html.escape(raw[3:])}</h2>")
        elif raw.startswith("# "): body.append(f"<h1>{html.escape(raw[2:])}</h1>")
        elif raw.startswith("- "): body.append(f"<p>{html.escape(raw[2:])}</p>")
        elif raw == "---": body.append("<hr>")
        elif raw: body.append(f"<p>{html.escape(raw)}</p>")
    if in_table: body.append("</table>")
    style = "body{font:15px system-ui;max-width:1200px;margin:40px auto;padding:0 24px;color:#1f2937}h1,h2{color:#17365d}table{border-collapse:collapse;width:100%;margin:12px 0 28px}td{border:1px solid #d1d5db;padding:8px;vertical-align:top}tr:first-child{font-weight:700;background:#eef4fa}pre{background:#111827;color:#f9fafb;padding:14px;overflow:auto;border-radius:6px}code{white-space:pre-wrap}"
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Moodle course comparison</title><style>{style}</style></head><body>{''.join(body)}</body></html>"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two Moodle-generated MBZ course backups.")
    parser.add_argument("before", nargs="?", type=Path, default=Path("input/before.mbz"), help="Earlier MBZ (default: input/before.mbz)")
    parser.add_argument("after", nargs="?", type=Path, default=Path("input/after.mbz"), help="Later MBZ (default: input/after.mbz)")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Report directory (default: output)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
        data = compare_snapshots(before, after)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "comparison_data.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        write_csv(args.output_dir / "course_changes.csv", data["course_changes"])
        write_csv(args.output_dir / "activity_changes.csv", data["activity_changes"])
        write_csv(args.output_dir / "content_changes.csv", data["content_changes"])
        write_csv(args.output_dir / "file_changes.csv", data["file_changes"])
        markdown = render_markdown(data)
        (args.output_dir / "comparison_report.md").write_text(markdown, encoding="utf-8")
        (args.output_dir / "comparison_report.html").write_text(render_html(markdown, data), encoding="utf-8")
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Compared: {args.before} -> {args.after}")
    print(f"Change records detected: {data['total_changes']} across {data['changed_elements']} course elements")
    print(f"Reports created in: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
