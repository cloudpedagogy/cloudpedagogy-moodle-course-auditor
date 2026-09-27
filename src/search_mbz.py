#!/usr/bin/env python3
"""
Moodle MBZ Search / Inspector
=============================

Search one Moodle backup (.mbz), or a folder of backups, without restoring them
to Moodle. Designed as an independent companion to the CloudPedagogy Moodle
Course Analysis Platform.

The tool searches Moodle-native XML content and metadata. It does NOT search
inside uploaded binary files such as PDF, DOCX, PPTX, SCORM or H5P packages.

Examples
--------
Single course:
    python3 search_mbz.py course.mbz --text "welcome"

Batch:
    python3 search_mbz.py batch_input --batch --text "welcome" -o search_results

Forums containing welcome text:
    python3 search_mbz.py batch_input --batch --activity-type forum --text "welcome"

Find all forums (inventory-style query):
    python3 search_mbz.py batch_input --batch --activity-type forum

Restrict a search to specific Moodle XML fields (repeat --field for alternatives):
    python3 search_mbz.py batch_input --batch --activity-type forum --text "welcome" --field message
    python3 search_mbz.py batch_input --batch --activity-type forum --text "welcome" --field subject --field message

Find hidden quizzes:
    python3 search_mbz.py batch_input --batch --activity-type quiz --visibility hidden

Find Noticeboard/Discussion forums containing welcome:
    python3 search_mbz.py batch_input --batch --activity-type forum \
        --name-any noticeboard discussion --text "welcome"

Find SharePoint references:
    python3 search_mbz.py batch_input --batch --domain sharepoint.com

Find files by filename or type:
    python3 search_mbz.py batch_input --batch --filename "handbook"
    python3 search_mbz.py batch_input --batch --filetype pdf

Exclude unwanted wording:
    python3 search_mbz.py batch_input --batch --text "assessment" --exclude-text "formative"

Show courses where the requested condition has no match:
    python3 search_mbz.py batch_input --batch --activity-type forum \
        --name-any noticeboard discussion --text "welcome" --result-mode missing

Outputs
-------
- search_report.html       formatted cross-course summary and evidence
- course_summary.csv       one row per Moodle course
- matches.csv              all matching records across all courses
- search_data.json         machine-readable query, course summaries and matches
- run_log.csv              processing status per backup

Interpretation
--------------
FOUND means the requested condition was found in the backup.
NOT FOUND means it was not found in the searchable XML retained in the backup.
UNABLE TO DETERMINE is used when the backup cannot support a reliable answer,
for example when a forum-post search is requested but no forum post/discussion
records are retained in the backup.

Absence in a Moodle backup is not automatically proof of absence from the live
Moodle course. Backup settings can exclude user-generated data such as forum
posts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import html
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

VERSION = "1.4.1"

HTML_TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
GENERIC_ACTIVITY_XML = {
    "module.xml", "roles.xml", "grades.xml", "grade_history.xml",
    "inforef.xml", "grading.xml",
}

# Moodle elements that commonly contain user-generated forum material.
FORUM_POST_MARKERS = {"post", "discussion", "posts", "discussions"}
FORUM_POST_FIELDS = {"message", "subject"}


def safe_text(element: Optional[ET.Element], default: str = "") -> str:
    if element is None or element.text is None:
        return default
    return element.text.strip()


def child_text(parent: Optional[ET.Element], name: str, default: str = "") -> str:
    if parent is None:
        return default
    return safe_text(parent.find(name), default)


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    decoded = html.unescape(raw)
    decoded = HTML_TAG_RE.sub(" ", decoded)
    decoded = re.sub(r"\s+", " ", decoded)
    return decoded.strip()


def parse_xml(path: Path) -> Optional[ET.Element]:
    try:
        return ET.parse(path).getroot()
    except Exception:
        return None


def detect_archive_type(path: Path) -> str:
    if zipfile.is_zipfile(path):
        return "zip"
    if tarfile.is_tarfile(path):
        return "tar"
    try:
        with gzip.open(path, "rb") as handle:
            handle.read(2)
        return "gzip"
    except Exception:
        return "unknown"


def safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if destination != target and destination not in target.parents:
            raise RuntimeError(f"Unsafe archive path rejected: {member.name}")
        if member.issym() or member.islnk():
            raise RuntimeError(f"Archive link rejected: {member.name}")
    try:
        archive.extractall(destination, filter="data")
    except TypeError:
        archive.extractall(destination)


def safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if destination != target and destination not in target.parents:
            raise RuntimeError(f"Unsafe archive path rejected: {member.filename}")
    archive.extractall(destination)


def extract_mbz(mbz_path: Path, extract_dir: Path) -> str:
    archive_type = detect_archive_type(mbz_path)
    if archive_type == "zip":
        with zipfile.ZipFile(mbz_path, "r") as archive:
            safe_extract_zip(archive, extract_dir)
        return "zip"
    if archive_type == "tar":
        with tarfile.open(mbz_path, "r:*") as archive:
            safe_extract_tar(archive, extract_dir)
        return "tar/tar.gz"
    if archive_type == "gzip":
        try:
            with tarfile.open(mbz_path, "r:gz") as archive:
                safe_extract_tar(archive, extract_dir)
            return "tar.gz"
        except Exception as exc:
            raise RuntimeError(
                "File appears gzip-compressed but is not a readable Moodle tar.gz backup."
            ) from exc
    raise RuntimeError("Unsupported archive format. Expected .mbz as zip, tar, or tar.gz.")


def find_mbz_files(input_path: Path, recursive: bool = False) -> List[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() == ".mbz" else []
    pattern = "**/*.mbz" if recursive else "*.mbz"
    return sorted((p for p in input_path.glob(pattern) if p.is_file()), key=lambda p: str(p).lower())


def find_activity_specific_xml(activity_dir: Path, modulename: str) -> Optional[Path]:
    candidate = activity_dir / f"{modulename}.xml"
    if candidate.exists():
        return candidate
    for xml_file in sorted(activity_dir.glob("*.xml")):
        if xml_file.name not in GENERIC_ACTIVITY_XML:
            return xml_file
    return None


def xml_leaf_records(root: ET.Element, source_file: str) -> List[Dict[str, str]]:
    """Flatten non-empty XML leaf/near-leaf text into searchable records."""
    records: List[Dict[str, str]] = []

    def walk(node: ET.Element, ancestry: List[str]) -> None:
        tag = node.tag.split("}")[-1]
        path = ancestry + [tag]
        text = strip_html(safe_text(node))
        if text:
            records.append({
                "field": tag,
                "xml_path": "/".join(path),
                "text": text,
                "source_file": source_file,
            })
        for child in list(node):
            walk(child, path)

    walk(root, [])
    return records


def course_identity(root_dir: Path, backup_name: str) -> Dict[str, str]:
    result = {"fullname": "", "shortname": "", "idnumber": "", "backup": backup_name}
    root = parse_xml(root_dir / "course" / "course.xml")
    if root is not None:
        node = root.find("course") if root.tag != "course" else root
        result.update({
            "fullname": child_text(node, "fullname"),
            "shortname": child_text(node, "shortname"),
            "idnumber": child_text(node, "idnumber"),
        })
    return result


def section_index(root_dir: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    activity_to_section: Dict[str, str] = {}
    sections_dir = root_dir / "sections"
    if not sections_dir.exists():
        return by_id, activity_to_section

    for path in sorted(sections_dir.glob("section_*/section.xml")):
        root = parse_xml(path)
        if root is None:
            continue
        sid = root.attrib.get("id", path.parent.name.replace("section_", ""))
        number = child_text(root, "number")
        name = child_text(root, "name") or f"Section {number}"
        sequence = [x.strip() for x in child_text(root, "sequence").split(",") if x.strip()]
        by_id[sid] = {
            "section_id": sid,
            "section_number": number,
            "section_name": name,
            "visible": child_text(root, "visible", "1"),
        }
        for module_id in sequence:
            activity_to_section[module_id] = sid
    return by_id, activity_to_section


def visibility_label(value: str) -> str:
    return "hidden" if str(value).strip() == "0" else "visible"


def domain_matches(text: str, wanted: Sequence[str]) -> Tuple[bool, List[str]]:
    if not wanted:
        return True, []
    domains = []
    for url in URL_RE.findall(html.unescape(text or "")):
        try:
            domain = (urlparse(url).hostname or "").lower()
        except Exception:
            domain = ""
        if domain:
            domains.append(domain)
    matched = [
        domain for domain in domains
        if any(domain == w.lower() or domain.endswith("." + w.lower()) for w in wanted)
    ]
    return bool(matched), sorted(set(matched))


def contains_any(value: str, needles: Sequence[str], case_sensitive: bool = False) -> bool:
    if not needles:
        return True
    haystack = value if case_sensitive else value.lower()
    for needle in needles:
        n = needle if case_sensitive else needle.lower()
        if n in haystack:
            return True
    return False


def text_match(value: str, terms: Sequence[str], match_all: bool, case_sensitive: bool,
               regex: bool) -> Tuple[bool, List[str]]:
    if not terms:
        return True, []
    matched: List[str] = []
    for term in terms:
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                ok = re.search(term, value, flags) is not None
            except re.error as exc:
                raise ValueError(f"Invalid regular expression {term!r}: {exc}") from exc
        else:
            ok = contains_any(value, [term], case_sensitive)
        if ok:
            matched.append(term)
    return ((len(matched) == len(terms)) if match_all else bool(matched)), matched


def snippet(text: str, terms: Sequence[str], max_len: int = 240) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= max_len:
        return clean
    lower = clean.lower()
    positions = [lower.find(t.lower()) for t in terms if t and lower.find(t.lower()) >= 0]
    start = max(0, (min(positions) if positions else 0) - max_len // 3)
    end = min(len(clean), start + max_len)
    piece = clean[start:end]
    return ("…" if start else "") + piece + ("…" if end < len(clean) else "")


def build_records(root_dir: Path, backup_name: str) -> Tuple[Dict[str, str], List[Dict[str, Any]], Dict[str, Any]]:
    """Build a generic searchable record stream from Moodle-native XML."""
    identity = course_identity(root_dir, backup_name)
    sections, activity_to_section = section_index(root_dir)
    records: List[Dict[str, Any]] = []
    evidence = {
        "forum_activities": 0,
        "forum_post_records": 0,
        "forum_discussion_records": 0,
    }
    activity_contexts: Dict[str, Dict[str, str]] = {}

    # Course fields.
    course_xml = root_dir / "course" / "course.xml"
    course_root = parse_xml(course_xml)
    if course_root is not None:
        for rec in xml_leaf_records(course_root, "course/course.xml"):
            records.append({
                **rec, "scope": "course", "activity_type": "", "activity_name": "",
                "module_id": "", "section_number": "", "section_name": "",
                "visibility": "",
            })

    # Section fields.
    sections_dir = root_dir / "sections"
    if sections_dir.exists():
        for path in sorted(sections_dir.glob("section_*/section.xml")):
            root = parse_xml(path)
            if root is None:
                continue
            sid = root.attrib.get("id", path.parent.name.replace("section_", ""))
            sec = sections.get(sid, {})
            for rec in xml_leaf_records(root, str(path.relative_to(root_dir))):
                records.append({
                    **rec, "scope": "section", "activity_type": "", "activity_name": "",
                    "module_id": "", "section_number": sec.get("section_number", ""),
                    "section_name": sec.get("section_name", ""),
                    "visibility": visibility_label(sec.get("visible", "1")),
                })

    # Activities and their activity-specific XML.
    activities_dir = root_dir / "activities"
    if activities_dir.exists():
        for activity_dir in sorted(p for p in activities_dir.glob("*") if p.is_dir()):
            module_root = parse_xml(activity_dir / "module.xml")
            if module_root is None:
                continue
            module_id = module_root.attrib.get("id", activity_dir.name.split("_")[-1])
            activity_type = child_text(module_root, "modulename") or activity_dir.name.split("_")[0]
            section_id = child_text(module_root, "sectionid") or activity_to_section.get(module_id, "")
            sec = sections.get(section_id, {})
            visible = visibility_label(child_text(module_root, "visible", "1"))

            specific = find_activity_specific_xml(activity_dir, activity_type)
            specific_root = parse_xml(specific) if specific else None
            activity_name = activity_dir.name
            if specific_root is not None:
                node = specific_root.find(activity_type)
                if node is None:
                    node = list(specific_root)[0] if list(specific_root) else specific_root
                activity_name = child_text(node, "name") or activity_name

            if specific_root is not None:
                contextid = specific_root.attrib.get("contextid", "")
                if contextid:
                    activity_contexts[contextid] = {
                        "activity_type": activity_type,
                        "activity_name": activity_name,
                        "module_id": module_id,
                        "section_number": sec.get("section_number", ""),
                        "section_name": sec.get("section_name", ""),
                        "visibility": visible,
                    }

            if activity_type.lower() == "forum":
                evidence["forum_activities"] += 1

            # Synthetic metadata record means activity-type/name inventory searches
            # work even when there is no searchable body text.
            records.append({
                "scope": "activity",
                "field": "activity_metadata",
                "xml_path": "activity_metadata",
                "text": f"{activity_name} {activity_type}".strip(),
                "source_file": str((activity_dir / "module.xml").relative_to(root_dir)),
                "activity_type": activity_type,
                "activity_name": activity_name,
                "module_id": module_id,
                "section_number": sec.get("section_number", ""),
                "section_name": sec.get("section_name", ""),
                "visibility": visible,
            })

            if specific_root is not None and specific is not None:
                for rec in xml_leaf_records(specific_root, str(specific.relative_to(root_dir))):
                    path_lower = rec["xml_path"].lower()
                    field_lower = rec["field"].lower()
                    if activity_type.lower() == "forum":
                        if "/post" in path_lower or "/posts" in path_lower or field_lower in FORUM_POST_FIELDS:
                            evidence["forum_post_records"] += 1
                        if "/discussion" in path_lower or "/discussions" in path_lower:
                            evidence["forum_discussion_records"] += 1
                    records.append({
                        **rec,
                        "scope": "activity_content",
                        "activity_type": activity_type,
                        "activity_name": activity_name,
                        "module_id": module_id,
                        "section_number": sec.get("section_number", ""),
                        "section_name": sec.get("section_name", ""),
                        "visibility": visible,
                    })

    evidence["file_records"] = add_file_metadata_records(root_dir, records, activity_contexts)
    return identity, records, evidence


def add_file_metadata_records(
    root_dir: Path,
    records: List[Dict[str, Any]],
    activity_contexts: Optional[Dict[str, Dict[str, str]]] = None,
) -> int:
    """Add files.xml metadata and, where possible, resolve each file to its Moodle activity/section."""
    files_xml = root_dir / "files.xml"
    root = parse_xml(files_xml)
    if root is None:
        return 0
    count = 0
    for node in root.findall(".//file"):
        filename = child_text(node, "filename")
        if not filename or filename == ".":
            continue
        filepath = child_text(node, "filepath")
        mimetype = child_text(node, "mimetype")
        component = child_text(node, "component")
        filearea = child_text(node, "filearea")
        contenthash = child_text(node, "contenthash")
        contextid = child_text(node, "contextid")
        ctx = (activity_contexts or {}).get(contextid, {})
        ext = Path(filename).suffix.lower().lstrip(".")
        records.append({
            "scope": "file",
            "field": "file_metadata",
            "xml_path": "files/file",
            "text": " ".join(x for x in [filename, filepath, mimetype, component, filearea] if x),
            "source_file": "files.xml",
            "activity_type": ctx.get("activity_type", ""),
            "activity_name": ctx.get("activity_name", ""),
            "module_id": ctx.get("module_id", ""),
            "section_number": ctx.get("section_number", ""),
            "section_name": ctx.get("section_name", ""),
            "visibility": ctx.get("visibility", ""),
            "filename": filename,
            "filetype": ext,
            "mimetype": mimetype,
            "filepath": filepath,
            "contenthash": contenthash,
            "component": component,
            "filearea": filearea,
            "contextid": contextid,
        })
        count += 1
    return count


def record_matches(record: Dict[str, Any], args: argparse.Namespace) -> Tuple[bool, List[str], List[str]]:
    activity_type = str(record.get("activity_type", ""))
    activity_name = str(record.get("activity_name", ""))
    section_name = str(record.get("section_name", ""))
    section_number = str(record.get("section_number", ""))
    visibility = str(record.get("visibility", ""))
    text = str(record.get("text", ""))
    filename = str(record.get("filename", ""))
    filetype = str(record.get("filetype", "")).lower().lstrip(".")

    field = str(record.get("field", ""))

    if args.field and field.lower() not in {x.lower() for x in args.field}:
        return False, [], []

    if args.filename and not contains_any(filename, args.filename, args.case_sensitive):
        return False, [], []
    if args.filetype and filetype not in {x.lower().lstrip(".") for x in args.filetype}:
        return False, [], []

    if args.activity_type and activity_type.lower() not in {x.lower() for x in args.activity_type}:
        return False, [], []
    if args.name_any and not contains_any(activity_name, args.name_any, args.case_sensitive):
        return False, [], []
    if args.section and not (
        contains_any(section_name, args.section, args.case_sensitive)
        or contains_any(section_number, args.section, args.case_sensitive)
    ):
        return False, [], []
    if args.visibility != "any" and visibility != args.visibility:
        return False, [], []
    if args.scope and record.get("scope") not in args.scope:
        return False, [], []

    ok_text, matched_terms = text_match(
        text, args.text, args.match_all_terms, args.case_sensitive, args.regex
    )
    if not ok_text:
        return False, [], []

    if args.exclude_text:
        excluded, _ = text_match(
            text, args.exclude_text, False, args.case_sensitive, args.regex
        )
        if excluded:
            return False, [], []

    ok_domain, matched_domains = domain_matches(text, args.domain)
    if not ok_domain:
        return False, [], []

    return True, matched_terms, matched_domains


def needs_forum_post_evidence(args: argparse.Namespace) -> bool:
    if not args.activity_type or "forum" not in {x.lower() for x in args.activity_type}:
        return False
    # A text query against forums may be intended to find a post. The user can
    # explicitly request --forum-posts to require post/discussion evidence.
    return bool(args.forum_posts)


def deduplicate_matches(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for row in matches:
        key = (
            row.get("backup"), row.get("module_id"), row.get("source_file"),
            row.get("xml_path"), row.get("match_snippet"),
            row.get("filename"), row.get("filepath"),
        )
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def search_one(mbz_path: Path, args: argparse.Namespace) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="moodle_search_") as temp:
        root_dir = Path(temp)
        archive_type = extract_mbz(mbz_path, root_dir)
        identity, records, evidence = build_records(root_dir, mbz_path.name)

        matches: List[Dict[str, Any]] = []
        for record in records:
            ok, terms, domains = record_matches(record, args)
            if not ok:
                continue
            # When explicitly requiring forum posts, reject forum configuration
            # fields and retain only post/discussion-shaped XML.
            if args.forum_posts and record.get("activity_type", "").lower() == "forum":
                p = str(record.get("xml_path", "")).lower()
                f = str(record.get("field", "")).lower()
                if not (
                    "/post" in p or "/posts" in p or "/discussion" in p
                    or "/discussions" in p or f in FORUM_POST_FIELDS
                ):
                    continue

            matches.append({
                "course_fullname": identity["fullname"],
                "course_shortname": identity["shortname"],
                "course_idnumber": identity["idnumber"],
                "backup": mbz_path.name,
                "section_number": record.get("section_number", ""),
                "section_name": record.get("section_name", ""),
                "activity_type": record.get("activity_type", ""),
                "activity_name": record.get("activity_name", ""),
                "module_id": record.get("module_id", ""),
                "visibility": record.get("visibility", ""),
                "scope": record.get("scope", ""),
                "field": record.get("field", ""),
                "matched_terms": "; ".join(terms),
                "matched_domains": "; ".join(domains),
                "match_snippet": snippet(str(record.get("text", "")), terms or args.domain),
                "source_file": record.get("source_file", ""),
                "xml_path": record.get("xml_path", ""),
                "filename": record.get("filename", ""),
                "filetype": record.get("filetype", ""),
                "mimetype": record.get("mimetype", ""),
                "filepath": record.get("filepath", ""),
                "component": record.get("component", ""),
                "filearea": record.get("filearea", ""),
                "contextid": record.get("contextid", ""),
            })

        matches = deduplicate_matches(matches)

        if needs_forum_post_evidence(args) and evidence["forum_activities"] > 0 and (
            evidence["forum_post_records"] + evidence["forum_discussion_records"] == 0
        ):
            status = "UNABLE TO DETERMINE"
            reason = "Forum activity exists, but no forum post/discussion records were detected in the backup XML."
        elif matches:
            status = "FOUND"
            reason = ""
        else:
            status = "NOT FOUND"
            reason = "No record in the searchable backup XML matched the requested condition."

        return {
            "identity": identity,
            "archive_type": archive_type,
            "status": status,
            "reason": reason,
            "match_count": len(matches),
            "activity_count": len({
                m.get("module_id") for m in matches
                if m.get("module_id") and m.get("activity_type")
            }),
            "evidence": evidence,
            "matches": matches,
        }


def query_description(args: argparse.Namespace) -> str:
    parts = []
    if args.text:
        joiner = " AND " if args.match_all_terms else " OR "
        parts.append("text=" + joiner.join(repr(x) for x in args.text))
    if args.exclude_text:
        parts.append("exclude text=" + ", ".join(repr(x) for x in args.exclude_text))
    if args.activity_type:
        parts.append("activity type=" + ", ".join(args.activity_type))
    if args.field:
        parts.append("field=" + ", ".join(args.field))
    if args.filename:
        parts.append("filename contains=" + ", ".join(args.filename))
    if args.filetype:
        parts.append("file type=" + ", ".join(args.filetype))
    if args.name_any:
        parts.append("activity name contains=" + ", ".join(args.name_any))
    if args.section:
        parts.append("section=" + ", ".join(args.section))
    if args.visibility != "any":
        parts.append("visibility=" + args.visibility)
    if args.domain:
        parts.append("domain=" + ", ".join(args.domain))
    if args.scope:
        parts.append("scope=" + ", ".join(args.scope))
    if args.forum_posts:
        parts.append("forum posts/discussions only")
    return "; ".join(parts) if parts else "All Moodle activities/content records"


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and not fieldnames:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def activity_inventory_mode(args: argparse.Namespace) -> bool:
    """True when the query is primarily asking for activities, not content matches."""
    return bool(args.activity_type) and not any([
        args.text, args.domain, args.name_any, args.section,
        args.forum_posts, args.regex, args.filename, args.filetype, args.exclude_text, args.field
    ])


def unique_activity_rows(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse XML-field matches to one human-readable row per Moodle activity."""
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for m in matches:
        module_id = str(m.get("module_id", ""))
        if not module_id:
            continue
        key = (str(m.get("backup", "")), module_id)
        if key not in grouped:
            grouped[key] = dict(m)
            grouped[key]["description"] = ""
        # Prefer the Moodle intro as the human-readable description.
        if str(m.get("field", "")).lower() in {"intro", "description", "content"}:
            candidate = str(m.get("match_snippet", "")).strip()
            if candidate and not grouped[key].get("description"):
                grouped[key]["description"] = candidate
    return list(grouped.values())


def make_html_report(results: List[Dict[str, Any]], args: argparse.Namespace) -> str:
    total = len(results)
    found = sum(r["status"] == "FOUND" for r in results)
    missing = sum(r["status"] == "NOT FOUND" for r in results)
    unknown = sum(r["status"] == "UNABLE TO DETERMINE" for r in results)
    failed = sum(r["status"] == "FAILED" for r in results)
    coverage = (found / total * 100) if total else 0

    course_rows = []
    detail_sections = []
    for i, result in enumerate(results):
        ident = result["identity"]
        title = ident.get("fullname") or ident.get("shortname") or ident.get("backup")
        status_class = {
            "FOUND": "found", "NOT FOUND": "missing",
            "UNABLE TO DETERMINE": "unknown", "FAILED": "failed",
        }.get(result["status"], "")
        inventory = activity_inventory_mode(args)
        file_mode = bool(args.filename or args.filetype) and not inventory
        entity_count = result.get("activity_count", 0)
        count_display = (
            f"{entity_count} activities"
            if inventory else (
                f"{result.get('match_count', 0)} files"
                if file_mode else str(result.get("match_count", 0))
            )
        )
        course_rows.append(
            f"<tr data-status='{esc(result['status'])}'><td><a href='#course-{i}'>{esc(title)}</a>"
            f"<div class='muted'>{esc(ident.get('shortname',''))}</div></td>"
            f"<td><span class='badge {status_class}'>{esc(result['status'])}</span></td>"
            f"<td>{esc(count_display)}</td>"
            f"<td>{esc(result.get('reason',''))}</td></tr>"
        )

        match_rows = []
        display_matches = (
            unique_activity_rows(result.get("matches", []))
            if inventory else result.get("matches", [])
        )
        for m in display_matches:
            if inventory:
                match_rows.append(
                    "<tr>"
                    f"<td>{esc(m.get('section_number'))}. {esc(m.get('section_name'))}</td>"
                    f"<td>{esc(m.get('activity_type'))}</td>"
                    f"<td>{esc(m.get('activity_name'))}</td>"
                    f"<td>{esc(m.get('visibility'))}</td>"
                    f"<td class='snippet'>{esc(m.get('description',''))}</td>"
                    "</tr>"
                )
            elif file_mode:
                section_label = " — ".join(
                    x for x in [
                        str(m.get("section_number", "")).strip(),
                        str(m.get("section_name", "")).strip()
                    ] if x
                )
                match_rows.append(
                    "<tr>"
                    f"<td class='snippet'>{esc(m.get('filename'))}</td>"
                    f"<td>{esc(m.get('filetype','').upper())}</td>"
                    f"<td>{esc(m.get('component'))}</td>"
                    f"<td>{esc(m.get('filearea'))}</td>"
                    f"<td>{esc(section_label)}</td>"
                    f"<td>{esc(m.get('activity_name'))}</td>"
                    f"<td>{esc(m.get('visibility'))}</td>"
                    "</tr>"
                )
            else:
                match_rows.append(
                    "<tr>"
                    f"<td>{esc(m.get('section_number'))}. {esc(m.get('section_name'))}</td>"
                    f"<td>{esc(m.get('activity_type'))}</td>"
                    f"<td>{esc(m.get('activity_name'))}</td>"
                    f"<td>{esc(m.get('visibility'))}</td>"
                    f"<td>{esc(m.get('field'))}</td>"
                    f"<td class='snippet'>{esc(m.get('match_snippet'))}</td>"
                    f"<td><code>{esc(m.get('source_file'))}</code></td>"
                    "</tr>"
                )
        if inventory:
            evidence_html = (
                "<p><strong>Activities found: " + esc(entity_count) + "</strong>"
                f" <span class='muted'>({esc(result.get('match_count',0))} underlying XML records)</span></p>"
                "<table class='matches'><thead><tr><th>Section</th><th>Type</th><th>Activity</th>"
                "<th>Visibility</th><th>Description</th></tr></thead><tbody>"
                + "".join(match_rows) + "</tbody></table>"
                if match_rows else "<p class='muted'>No matching activities.</p>"
            )
        elif file_mode:
            evidence_html = (
                "<p><strong>Files found: " + esc(result.get("match_count", 0)) + "</strong></p>"
                "<table class='matches'><thead><tr><th>Filename</th><th>Type</th>"
                "<th>Moodle component</th><th>File area</th><th>Section</th>"
                "<th>Associated activity</th><th>Visibility</th></tr></thead><tbody>"
                + "".join(match_rows) + "</tbody></table>"
                if match_rows else "<p class='muted'>No matching file metadata.</p>"
            )
        else:
            evidence_html = (
                "<table class='matches'><thead><tr><th>Section</th><th>Type</th><th>Activity</th>"
                "<th>Visibility</th><th>Field</th><th>Evidence</th><th>Source XML</th></tr></thead><tbody>"
                + "".join(match_rows) + "</tbody></table>"
                if match_rows else "<p class='muted'>No matching XML records.</p>"
            )
        detail_sections.append(
            f"<section id='course-{i}' class='course-detail' data-status='{esc(result['status'])}'>"
            f"<h2>{esc(title)} <span class='badge {status_class}'>{esc(result['status'])}</span></h2>"
            f"<p class='muted'>Backup: {esc(ident.get('backup'))}"
            + (f" · {esc(result.get('reason'))}" if result.get("reason") else "") + "</p>"
            f"{evidence_html}</section>"
        )

    generated = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Moodle Course Search Report</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --text:#17202a; --muted:#667085; --line:#e4e7ec; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Arial,Helvetica,sans-serif; color:var(--text); background:var(--bg); }}
main {{ max-width:1400px; margin:0 auto; padding:32px 24px 60px; }}
header {{ margin-bottom:24px; }}
h1 {{ margin:0 0 8px; font-size:30px; }}
h2 {{ margin-top:0; }}
.query {{ background:#eef2f6; padding:12px 14px; border-radius:8px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:20px 0; }}
.card, section {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px; }}
.metric {{ font-size:28px; font-weight:700; }}
.label,.muted {{ color:var(--muted); font-size:13px; }}
.controls {{ display:flex; gap:8px; flex-wrap:wrap; margin:18px 0; }}
button {{ border:1px solid var(--line); background:#fff; border-radius:7px; padding:8px 11px; cursor:pointer; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{ text-align:left; vertical-align:top; padding:9px 10px; border-bottom:1px solid var(--line); font-size:13px; }}
th {{ background:#f9fafb; position:sticky; top:0; }}
.badge {{ display:inline-block; border-radius:999px; padding:4px 8px; font-size:11px; font-weight:700; }}
.found {{ background:#dcfae6; color:#067647; }}
.missing {{ background:#fee4e2; color:#b42318; }}
.unknown {{ background:#fef0c7; color:#b54708; }}
.failed {{ background:#eaecf0; color:#344054; }}
.course-detail {{ margin-top:18px; overflow:auto; }}
.snippet {{ min-width:320px; max-width:600px; }}
code {{ white-space:normal; font-size:11px; }}
a {{ color:inherit; }}
.hidden-row {{ display:none !important; }}
@media (max-width:700px) {{ main {{ padding:20px 10px; }} .snippet {{ min-width:220px; }} }}
</style>
</head>
<body><main>
<header>
<h1>Moodle Course Search Report</h1>
<p class="query"><strong>Query:</strong> {esc(query_description(args))}</p>
<p class="muted">Generated {esc(generated)}. Search covers Moodle-native XML and uploaded-file metadata retained in each backup; uploaded binary file contents are not searched.</p>
</header>
<div class="cards">
<div class="card"><div class="metric">{total}</div><div class="label">Courses analysed</div></div>
<div class="card"><div class="metric">{found}</div><div class="label">Found</div></div>
<div class="card"><div class="metric">{missing}</div><div class="label">Not found</div></div>
<div class="card"><div class="metric">{unknown}</div><div class="label">Unable to determine</div></div>
<div class="card"><div class="metric">{coverage:.1f}%</div><div class="label">Courses with matches</div></div>
</div>
<div class="controls">
<button onclick="filterStatus('ALL')">All</button>
<button onclick="filterStatus('FOUND')">Found</button>
<button onclick="filterStatus('NOT FOUND')">Not found</button>
<button onclick="filterStatus('UNABLE TO DETERMINE')">Unable to determine</button>
<button onclick="filterStatus('FAILED')">Failed</button>
</div>
<section>
<h2>Course summary</h2>
<table><thead><tr><th>Moodle course</th><th>Result</th><th>Matches</th><th>Note</th></tr></thead>
<tbody>{''.join(course_rows)}</tbody></table>
</section>
{''.join(detail_sections)}
<script>
function filterStatus(status) {{
 document.querySelectorAll('[data-status]').forEach(function(el) {{
   el.classList.toggle('hidden-row', status !== 'ALL' && el.dataset.status !== status);
 }});
}}
</script>
</main></body></html>"""


def result_visible(result: Dict[str, Any], mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "found":
        return result["status"] == "FOUND"
    if mode == "missing":
        return result["status"] in {"NOT FOUND", "UNABLE TO DETERMINE"}
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search and inspect Moodle .mbz backups using Moodle-native XML.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="One .mbz file, or a folder when --batch is used")
    parser.add_argument("--batch", action="store_true", help="Search all .mbz files in the input folder")
    parser.add_argument("--recursive", action="store_true", help="Include .mbz files in subfolders")
    parser.add_argument("-o", "--output-dir", "--output", default="moodle_search_output",
                        help="Output folder (default: moodle_search_output)")

    parser.add_argument("--text", "--term", action="append", default=[],
                        help="Text/phrase/excerpt to find; repeat for multiple terms")
    parser.add_argument("--exclude-text", action="append", default=[],
                        help="Exclude records containing this text; repeatable")
    parser.add_argument("--filename", action="append", default=[],
                        help="Find uploaded files whose filename contains this text; repeatable")
    parser.add_argument("--filetype", action="append", default=[],
                        help="Restrict uploaded file metadata by extension, e.g. pdf or docx; repeatable")
    parser.add_argument("--match-all-terms", action="store_true",
                        help="Require every --text term in a record (default: any term)")
    parser.add_argument("--regex", action="store_true", help="Treat --text values as regular expressions")
    parser.add_argument("--case-sensitive", action="store_true", help="Use case-sensitive matching")
    parser.add_argument("--activity-type", action="append", default=[],
                        help="Restrict to Moodle activity type, e.g. forum or quiz; repeatable")
    parser.add_argument("--field", action="append", default=[],
                        help="Restrict matches to an XML field/tag, e.g. message, subject, name or intro; repeatable")
    parser.add_argument("--name-any", nargs="+", default=[],
                        help="Activity name must contain at least one supplied phrase")
    parser.add_argument("--section", action="append", default=[],
                        help="Restrict by section name or number; repeatable")
    parser.add_argument("--visibility", choices=("any", "visible", "hidden"), default="any",
                        help="Restrict by Moodle visibility")
    parser.add_argument("--domain", action="append", default=[],
                        help="Find URLs on a domain, e.g. sharepoint.com; repeatable")
    parser.add_argument("--scope", action="append",
                        choices=("course", "section", "activity", "activity_content", "file"),
                        help="Restrict record scope; repeatable")
    parser.add_argument("--forum-posts", action="store_true",
                        help="For forum searches, require post/discussion-shaped XML evidence")
    parser.add_argument("--result-mode", choices=("all", "found", "missing"), default="all",
                        help="HTML/CSV course summary emphasis: all, found, or missing")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    if args.batch and not input_path.is_dir():
        raise ValueError("--batch requires a folder.")
    if not args.batch and input_path.is_dir():
        raise ValueError("Input is a folder. Use --batch.")
    if not args.batch and input_path.suffix.lower() != ".mbz":
        raise ValueError("Input file must have a .mbz extension.")

    backups = find_mbz_files(input_path, args.recursive) if args.batch else [input_path]
    if not backups:
        raise FileNotFoundError(f"No .mbz files found in: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    run_log: List[Dict[str, Any]] = []

    for index, backup in enumerate(backups, 1):
        print(f"[{index}/{len(backups)}] Searching: {backup.name}")
        try:
            result = search_one(backup, args)
            results.append(result)
            run_log.append({
                "backup": backup.name, "status": "success",
                "search_result": result["status"], "matches": result["match_count"], "message": result["reason"],
            })
        except Exception as exc:
            identity = {"fullname": "", "shortname": "", "idnumber": "", "backup": backup.name}
            results.append({
                "identity": identity, "archive_type": "", "status": "FAILED",
                "reason": str(exc), "match_count": 0, "activity_count": 0,
                "evidence": {}, "matches": [],
            })
            run_log.append({
                "backup": backup.name, "status": "failed",
                "search_result": "FAILED", "matches": 0, "message": str(exc),
            })

    # Apply --result-mode consistently to user-facing report/CSV outputs.
    # Keep search_data.json and run_log.csv complete as the full audit trail.
    visible_results = [r for r in results if result_visible(r, args.result_mode)]
    all_matches = [m for r in visible_results for m in r.get("matches", [])]

    summary_rows = []
    for r in visible_results:
        ident = r["identity"]
        summary_rows.append({
            "course_fullname": ident.get("fullname", ""),
            "course_shortname": ident.get("shortname", ""),
            "course_idnumber": ident.get("idnumber", ""),
            "backup": ident.get("backup", ""),
            "result": r["status"],
            "match_count": r["match_count"],
            "activity_count": r.get("activity_count", 0),
            "reason": r["reason"],
            "forum_activities_detected": r.get("evidence", {}).get("forum_activities", ""),
            "forum_post_records_detected": r.get("evidence", {}).get("forum_post_records", ""),
            "forum_discussion_records_detected": r.get("evidence", {}).get("forum_discussion_records", ""),
        })

    write_csv(output_dir / "course_summary.csv", summary_rows, [
        "course_fullname", "course_shortname", "course_idnumber", "backup",
        "result", "match_count", "activity_count", "reason", "forum_activities_detected",
        "forum_post_records_detected", "forum_discussion_records_detected",
    ])
    write_csv(output_dir / "matches.csv", all_matches, [
        "course_fullname", "course_shortname", "course_idnumber", "backup",
        "section_number", "section_name", "activity_type", "activity_name",
        "module_id", "visibility", "scope", "field", "matched_terms",
        "matched_domains", "match_snippet", "source_file", "xml_path",
        "filename", "filetype", "mimetype", "filepath", "component", "filearea", "contextid",
    ])
    write_csv(output_dir / "run_log.csv", run_log, [
        "backup", "status", "search_result", "matches", "message",
    ])

    serialisable = {
        "tool": "Moodle MBZ Search / Inspector",
        "version": VERSION,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": query_description(args),
        "scope_note": (
            "Moodle-native XML retained in the backup. Uploaded binary file contents "
            "such as PDF/DOCX/PPTX/SCORM/H5P are not searched."
        ),
        "courses": results,
    }
    (output_dir / "search_data.json").write_text(
        json.dumps(serialisable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "search_report.html").write_text(
        make_html_report(visible_results, args), encoding="utf-8"
    )

    failures = sum(r["status"] == "FAILED" for r in results)
    print(f"\nSearch complete: {output_dir}")
    print(f"- {output_dir / 'search_report.html'}")
    print(f"- {output_dir / 'course_summary.csv'}")
    print(f"- {output_dir / 'matches.csv'}")
    print(f"- {output_dir / 'search_data.json'}")
    print(f"- {output_dir / 'run_log.csv'}")
    print(f"Courses: {len(results)}; found: {sum(r['status']=='FOUND' for r in results)}; "
          f"not found: {sum(r['status']=='NOT FOUND' for r in results)}; "
          f"unable to determine: {sum(r['status']=='UNABLE TO DETERMINE' for r in results)}; "
          f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        raise SystemExit(2)
