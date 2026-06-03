#!/usr/bin/env python3
"""
Moodle MBZ XML Metadata Auditor
===============================

Version: 1.5

Audits Moodle course backup files (.mbz) using XML metadata only.

This version is intentionally factual and conservative. It focuses on information
that can be derived from Moodle backup XML files.

Supports:
- Single-course mode:
    python3 moodle_mbz_course_auditor.py course.mbz

- Batch mode across a folder of MBZ files:
    python3 moodle_mbz_course_auditor.py input_folder --batch --output output_folder

Archive formats supported:
- gzip-compressed tar archives (.mbz, common in Moodle)
- plain tar archives
- zip archives

Important limitation:
This script does NOT inspect the internal contents of binary uploaded files such
as PDFs, Word files, PowerPoints, images, videos, SCORM packages, or H5P packages.

It reads:
- course/course.xml
- sections/*/section.xml
- activities/*/module.xml
- activities/*/[activity_type].xml
- files.xml
- questions.xml

Outputs per course:
- audit_report.md
- audit_report.txt
- course_summary.csv
- course_characteristics.csv
- course_footprint.csv
- section_activity_breakdown.csv
- book_inventory.csv
- duplicate_activity_inventory.csv
- hidden_content_summary.csv
- hidden_activity_inventory.csv
- external_dependency_inventory.csv
- external_domain_inventory.csv
- file_extension_inventory.csv
- largest_files.csv
- modification_year_summary.csv
- activity_age_summary.csv
- activities.csv
- sections.csv
- files.csv
- audit_data.json

Batch mode also outputs:
- combined_course_summary.csv
- batch_run_log.csv
"""

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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse


HTML_TAG_RE = re.compile(r"<[^>]+>")
IFRAME_RE = re.compile(r"<iframe\b[^>]*>", re.IGNORECASE)
IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
A_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
SRC_RE = re.compile(r"\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
HREF_RE = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)

GENERIC_ACTIVITY_XML = {
    "module.xml",
    "roles.xml",
    "grades.xml",
    "grade_history.xml",
    "inforef.xml",
    "grading.xml",
}


def safe_text(element: Optional[ET.Element], default: str = "") -> str:
    if element is None or element.text is None:
        return default
    return element.text.strip()


def child_text(parent: Optional[ET.Element], name: str, default: str = "") -> str:
    if parent is None:
        return default
    return safe_text(parent.find(name), default)


def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", "$@NULL@$", None):
            return default
        return int(value)
    except Exception:
        return default


def unix_to_iso(value: Any) -> str:
    n = parse_int(value, 0)
    if n <= 0:
        return ""
    try:
        return dt.datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def year_from_iso(value: str) -> str:
    if not value:
        return ""
    match = re.match(r"^(\d{4})-", value)
    return match.group(1) if match else ""


def parse_datetime_from_iso(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    decoded = html.unescape(raw)
    decoded = HTML_TAG_RE.sub(" ", decoded)
    decoded = re.sub(r"\s+", " ", decoded)
    return decoded.strip()


def count_words_from_html(raw: str) -> int:
    text = strip_html(raw)
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


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
        with gzip.open(path, "rb") as f:
            f.read(2)
        return "gzip"
    except Exception:
        return "unknown"


def extract_mbz(mbz_path: Path, extract_dir: Path) -> str:
    archive_type = detect_archive_type(mbz_path)

    if archive_type == "zip":
        with zipfile.ZipFile(mbz_path, "r") as z:
            z.extractall(extract_dir)
        return "zip"

    if archive_type == "tar":
        with tarfile.open(mbz_path, "r:*") as t:
            t.extractall(extract_dir)
        return "tar/tar.gz"

    if archive_type == "gzip":
        try:
            with tarfile.open(mbz_path, "r:gz") as t:
                t.extractall(extract_dir)
            return "tar.gz"
        except Exception as exc:
            raise RuntimeError(
                "File appears to be gzip-compressed, but not a readable tar.gz Moodle backup."
            ) from exc

    raise RuntimeError("Unsupported archive format. Expected .mbz as zip, tar, or tar.gz.")


def safe_folder_name(name: str, max_length: int = 120) -> str:
    name = Path(name).stem
    name = re.sub(r"[^\w\-\.]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:max_length] or "moodle_backup"


def find_mbz_files(input_path: Path, recursive: bool = False) -> List[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() == ".mbz" else []

    pattern = "**/*.mbz" if recursive else "*.mbz"
    return sorted(input_path.glob(pattern))


def find_activity_specific_xml(activity_dir: Path, modulename: str) -> Optional[Path]:
    candidate = activity_dir / f"{modulename}.xml"
    if candidate.exists():
        return candidate

    for xml_file in activity_dir.glob("*.xml"):
        if xml_file.name not in GENERIC_ACTIVITY_XML:
            return xml_file
    return None


def domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def collect_xml_html_signals(raw_html_chunks: List[str]) -> Dict[str, Any]:
    """
    Collect signals from HTML-like text stored directly in Moodle XML fields.

    These are XML-derived signals only. They do not include links, images, or
    content that exists only inside uploaded files such as PDFs, Word documents,
    PowerPoints, SCORM packages, or H5P packages.
    """
    combined = "\n".join([x for x in raw_html_chunks if x])
    decoded = html.unescape(combined)

    external_links = []
    for href in HREF_RE.findall(decoded):
        if href.startswith(("http://", "https://")):
            external_links.append(href)
    for src in SRC_RE.findall(decoded):
        if src.startswith(("http://", "https://")):
            external_links.append(src)

    unique_external_links = sorted(set(external_links))
    domains = [domain_from_url(url) for url in unique_external_links if domain_from_url(url)]
    webcal_links = [x for x in unique_external_links if "webcal" in x.lower()]
    panopto_links = [x for x in unique_external_links if "panopto" in x.lower()]

    return {
        "xml_text_word_count_estimate": count_words_from_html(combined),
        "xml_iframe_count": len(IFRAME_RE.findall(decoded)),
        "xml_image_tag_count": len(IMG_RE.findall(decoded)),
        "xml_anchor_link_count": len(A_RE.findall(decoded)),
        "xml_external_links": unique_external_links,
        "xml_external_domains": domains,
        "xml_external_link_count": len(unique_external_links),
        "xml_webcal_link_count": len(set(webcal_links)),
        "xml_panopto_link_count": len(set(panopto_links)),
        "xml_pluginfile_reference_count": decoded.count("@@PLUGINFILE@@"),
    }


def choose_primary_delivery_pattern(activity_type_counts: Counter) -> str:
    if not activity_type_counts:
        return "No activity metadata detected"

    total = sum(activity_type_counts.values())
    if total == 0:
        return "No activity metadata detected"

    top_type, top_count = activity_type_counts.most_common(1)[0]
    top_share = top_count / total

    if top_type == "book" and top_share >= 0.25:
        return "Book-centric"
    if top_type == "resource" and top_share >= 0.25:
        return "Resource-centric"
    if top_type == "quiz" and top_share >= 0.20:
        return "Quiz-centric"
    if top_type == "forum" and top_share >= 0.20:
        return "Forum-centric"
    return f"Mixed, led by {top_type}"


def age_band(last_modified: str, now: Optional[dt.datetime] = None) -> str:
    if now is None:
        now = dt.datetime.now()
    modified = parse_datetime_from_iso(last_modified)
    if modified is None:
        return "unknown"

    days = (now - modified).days
    if days < 0:
        return "future-dated"
    if days <= 365:
        return "modified_within_1_year"
    if days <= 730:
        return "modified_1_to_2_years_ago"
    if days <= 1095:
        return "modified_2_to_3_years_ago"
    if days <= 1825:
        return "modified_3_to_5_years_ago"
    return "modified_more_than_5_years_ago"


def audit_course(root_dir: Path) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "audit_scope": {
            "scope": "Moodle backup XML metadata only",
            "binary_file_content_scanned": False,
            "pedagogic_scoring_included": False,
            "quality_rating_included": False,
            "severity_scoring_included": False,
        },
        "course": {},
        "sections": [],
        "activities": [],
        "activity_type_counts": {},
        "section_activity_breakdown": [],
        "book_inventory": [],
        "duplicate_activity_inventory": [],
        "hidden_content_summary": [],
        "hidden_activity_inventory": [],
        "external_dependency_inventory": [],
        "external_domain_inventory": [],
        "file_extension_inventory": [],
        "largest_files": [],
        "modification_year_summary": [],
        "activity_age_summary": [],
        "course_characteristics": [],
        "course_footprint": [],
        "files": [],
        "questions": {},
        "xml_findings": [],
        "factual_observations": [],
        "summary": {},
    }

    course_xml = root_dir / "course" / "course.xml"
    course_root = parse_xml(course_xml)
    if course_root is not None:
        course_node = course_root.find("course") if course_root.tag != "course" else course_root
        data["course"] = {
            "fullname": child_text(course_node, "fullname"),
            "shortname": child_text(course_node, "shortname"),
            "idnumber": child_text(course_node, "idnumber"),
            "summary_text_from_xml": strip_html(child_text(course_node, "summary")),
            "format": child_text(course_node, "format"),
            "visible": child_text(course_node, "visible"),
            "startdate": unix_to_iso(child_text(course_node, "startdate")),
            "enddate": unix_to_iso(child_text(course_node, "enddate")),
            "timemodified": unix_to_iso(child_text(course_node, "timemodified")),
        }

    section_by_id: Dict[str, Dict[str, Any]] = {}
    section_order_by_id: Dict[str, int] = {}
    activity_to_section_id: Dict[str, str] = {}

    sections_dir = root_dir / "sections"
    for section_xml in sorted(sections_dir.glob("section_*/section.xml")) if sections_dir.exists() else []:
        section_root = parse_xml(section_xml)
        if section_root is None:
            continue

        sid = section_root.attrib.get("id", section_xml.parent.name.replace("section_", ""))
        sequence = child_text(section_root, "sequence")
        activity_ids = [x.strip() for x in sequence.split(",") if x.strip()]

        for module_id in activity_ids:
            activity_to_section_id[module_id] = sid

        section_summary_raw = child_text(section_root, "summary")
        section = {
            "section_id": sid,
            "section_number": parse_int(child_text(section_root, "number")),
            "section_name": child_text(section_root, "name") or f"Section {child_text(section_root, 'number')}",
            "section_summary_text_from_xml": strip_html(section_summary_raw),
            "section_summary_word_count_estimate": count_words_from_html(section_summary_raw),
            "visible": child_text(section_root, "visible", "1"),
            "activity_sequence": ",".join(activity_ids),
            "activity_count_from_sequence": len(activity_ids),
            "timemodified": unix_to_iso(child_text(section_root, "timemodified")),
            "xml_path": str(section_xml.relative_to(root_dir)),
        }
        data["sections"].append(section)
        section_by_id[sid] = section
        section_order_by_id[sid] = section["section_number"]

    type_counts = Counter()
    section_type_counts = defaultdict(Counter)
    all_external_domains = Counter()

    activities_dir = root_dir / "activities"
    for activity_dir in sorted(activities_dir.glob("*")) if activities_dir.exists() else []:
        if not activity_dir.is_dir():
            continue

        module_xml = activity_dir / "module.xml"
        module_root = parse_xml(module_xml)
        if module_root is None:
            continue

        module_id = module_root.attrib.get("id", activity_dir.name.split("_")[-1])
        modulename = child_text(module_root, "modulename") or activity_dir.name.split("_")[0]
        sectionid = child_text(module_root, "sectionid") or activity_to_section_id.get(module_id, "")
        visible = child_text(module_root, "visible", "1")
        visible_on_course_page = child_text(module_root, "visibleoncoursepage", "1")
        completion = child_text(module_root, "completion", "0")

        specific_xml = find_activity_specific_xml(activity_dir, modulename)
        activity_name = ""
        book_chapter_count_from_xml = 0
        hidden_book_chapter_count_from_xml = 0
        last_modified = unix_to_iso(child_text(module_root, "added"))
        raw_html_chunks: List[str] = []

        if specific_xml:
            specific_root = parse_xml(specific_xml)
            if specific_root is not None:
                node = specific_root.find(modulename)
                if node is None:
                    node = list(specific_root)[0] if list(specific_root) else specific_root

                activity_name = child_text(node, "name")
                raw_html_chunks.append(child_text(node, "intro"))
                last_modified = unix_to_iso(child_text(node, "timemodified")) or last_modified

                chapters_node = node.find("chapters")
                if chapters_node is not None:
                    for chapter in chapters_node.findall("chapter"):
                        book_chapter_count_from_xml += 1
                        if child_text(chapter, "hidden", "0") == "1":
                            hidden_book_chapter_count_from_xml += 1
                        raw_html_chunks.append(child_text(chapter, "title"))
                        raw_html_chunks.append(child_text(chapter, "content"))

                for field in ["content", "summary", "intro", "externalurl", "reference"]:
                    value = child_text(node, field)
                    if value:
                        raw_html_chunks.append(value)

        signals = collect_xml_html_signals(raw_html_chunks)
        xml_external_links = signals["xml_external_links"]
        xml_external_domains = signals["xml_external_domains"]
        all_external_domains.update(xml_external_domains)

        type_counts[modulename] += 1
        section_type_counts[sectionid][modulename] += 1

        activity = {
            "module_id": module_id,
            "activity_folder": activity_dir.name,
            "activity_type": modulename,
            "activity_name": activity_name or activity_dir.name,
            "section_id": sectionid,
            "section_number": section_order_by_id.get(sectionid, ""),
            "section_name": section_by_id.get(sectionid, {}).get("section_name", ""),
            "visible": visible,
            "visible_on_course_page": visible_on_course_page,
            "completion_setting": completion,
            "showdescription": child_text(module_root, "showdescription", "0"),
            "downloadcontent": child_text(module_root, "downloadcontent", ""),
            "book_chapter_count_from_xml": book_chapter_count_from_xml,
            "hidden_book_chapter_count_from_xml": hidden_book_chapter_count_from_xml,
            "xml_text_word_count_estimate": signals["xml_text_word_count_estimate"],
            "xml_iframe_count": signals["xml_iframe_count"],
            "xml_image_tag_count": signals["xml_image_tag_count"],
            "xml_anchor_link_count": signals["xml_anchor_link_count"],
            "xml_external_link_count": signals["xml_external_link_count"],
            "xml_webcal_link_count": signals["xml_webcal_link_count"],
            "xml_panopto_link_count": signals["xml_panopto_link_count"],
            "xml_pluginfile_reference_count": signals["xml_pluginfile_reference_count"],
            "xml_external_domains": "; ".join(sorted(set(xml_external_domains))),
            "xml_external_links_sample": "; ".join(xml_external_links[:10]),
            "last_modified_from_xml": last_modified,
            "modified_year_from_xml": year_from_iso(last_modified),
            "activity_age_band": age_band(last_modified),
            "activity_xml_path": str(specific_xml.relative_to(root_dir)) if specific_xml else "",
            "module_xml_path": str(module_xml.relative_to(root_dir)),
        }
        data["activities"].append(activity)

    data["activity_type_counts"] = dict(type_counts)

    for section in sorted(data["sections"], key=lambda x: x.get("section_number", 0)):
        sid = section["section_id"]
        counts = section_type_counts.get(sid, Counter())
        row = {
            "section_id": sid,
            "section_number": section.get("section_number", ""),
            "section_name": section.get("section_name", ""),
            "activity_count_from_sequence": section.get("activity_count_from_sequence", 0),
            "visible": section.get("visible", "1"),
            "activity_type_breakdown_from_xml": "; ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
        }
        for activity_type, count in counts.items():
            row[f"activity_type_{activity_type}"] = count
        data["section_activity_breakdown"].append(row)

    files_xml = root_dir / "files.xml"
    files_root = parse_xml(files_xml)
    file_ext_counts = Counter()
    file_mimetype_counts = Counter()
    file_component_counts = Counter()
    total_file_size = 0

    if files_root is not None:
        for file_node in files_root.findall(".//file"):
            filename = child_text(file_node, "filename")
            filepath = child_text(file_node, "filepath")
            mimetype = child_text(file_node, "mimetype")
            filesize = parse_int(child_text(file_node, "filesize"))
            component = child_text(file_node, "component")
            filearea = child_text(file_node, "filearea")
            ext = Path(filename).suffix.lower() if filename and filename != "." else ""

            if ext:
                file_ext_counts[ext] += 1
            if mimetype:
                file_mimetype_counts[mimetype] += 1
            if component:
                file_component_counts[component] += 1
            total_file_size += filesize

            data["files"].append({
                "contenthash_from_xml": child_text(file_node, "contenthash"),
                "filename_from_xml": filename,
                "filepath_from_xml": filepath,
                "filesize_from_xml_bytes": filesize,
                "filesize_from_xml_mb": round(filesize / (1024 * 1024), 3),
                "mimetype_from_xml": mimetype,
                "component_from_xml": component,
                "filearea_from_xml": filearea,
                "timemodified_from_xml": unix_to_iso(child_text(file_node, "timemodified")),
                "extension_from_filename": ext,
            })

    questions_xml = root_dir / "questions.xml"
    questions_root = parse_xml(questions_xml)
    question_type_counts = Counter()
    if questions_root is not None:
        for q in questions_root.findall(".//question"):
            qtype = q.attrib.get("type") or child_text(q, "qtype") or "unknown"
            question_type_counts[qtype] += 1

    data["questions"] = {
        "question_count_from_questions_xml": sum(question_type_counts.values()),
        "question_type_counts_from_questions_xml": dict(question_type_counts),
        "questions_xml_present": questions_xml.exists(),
    }

    hidden_sections = [s for s in data["sections"] if s["visible"] == "0"]
    empty_sections = [s for s in data["sections"] if s["activity_count_from_sequence"] == 0]
    hidden_activities = [a for a in data["activities"] if a["visible"] == "0"]
    hidden_chapters_total = sum(parse_int(a.get("hidden_book_chapter_count_from_xml", 0)) for a in data["activities"])

    duplicate_name_counts = Counter(a["activity_name"] for a in data["activities"] if a["activity_name"])
    duplicate_names = {name: count for name, count in duplicate_name_counts.items() if count > 1}

    iframe_activities = [a for a in data["activities"] if parse_int(a["xml_iframe_count"]) > 0]
    external_link_activities = [a for a in data["activities"] if parse_int(a["xml_external_link_count"]) > 0]
    pluginfile_activities = [a for a in data["activities"] if parse_int(a["xml_pluginfile_reference_count"]) > 0]
    webcal_activities = [a for a in data["activities"] if parse_int(a["xml_webcal_link_count"]) > 0]
    lti_activities = [a for a in data["activities"] if a["activity_type"] == "lti"]
    book_activities = [a for a in data["activities"] if a["activity_type"] == "book"]
    resource_activities = [a for a in data["activities"] if a["activity_type"] == "resource"]
    downloadable_activities = [a for a in data["activities"] if str(a.get("downloadcontent", "")) == "1"]

    total_xml_text_word_estimate = sum(parse_int(a.get("xml_text_word_count_estimate", 0)) for a in data["activities"])
    total_book_chapters = sum(parse_int(a.get("book_chapter_count_from_xml", 0)) for a in data["activities"])

    data["book_inventory"] = sorted(
        [
            {
                "activity_name": a["activity_name"],
                "section_number": a["section_number"],
                "section_name": a["section_name"],
                "visible": a["visible"],
                "book_chapter_count_from_xml": a["book_chapter_count_from_xml"],
                "hidden_book_chapter_count_from_xml": a["hidden_book_chapter_count_from_xml"],
                "xml_text_word_count_estimate": a["xml_text_word_count_estimate"],
                "xml_iframe_count": a["xml_iframe_count"],
                "xml_webcal_link_count": a["xml_webcal_link_count"],
                "xml_pluginfile_reference_count": a["xml_pluginfile_reference_count"],
                "downloadcontent": a["downloadcontent"],
                "last_modified_from_xml": a["last_modified_from_xml"],
                "activity_xml_path": a["activity_xml_path"],
            }
            for a in book_activities
        ],
        key=lambda x: (parse_int(x.get("section_number", 0)), x.get("activity_name", "")),
    )

    data["duplicate_activity_inventory"] = [
        {"activity_name": name, "occurrence_count": count}
        for name, count in sorted(duplicate_names.items(), key=lambda x: (-x[1], x[0]))
    ]

    hidden_type_counts = Counter(a["activity_type"] for a in hidden_activities)
    data["hidden_content_summary"] = [
        {"activity_type": activity_type, "hidden_activity_count": count}
        for activity_type, count in sorted(hidden_type_counts.items(), key=lambda x: (-x[1], x[0]))
    ]

    data["hidden_activity_inventory"] = sorted(
        [
            {
                "activity_name": a["activity_name"],
                "activity_type": a["activity_type"],
                "section_number": a["section_number"],
                "section_name": a["section_name"],
                "visible": a["visible"],
                "visible_on_course_page": a["visible_on_course_page"],
                "last_modified_from_xml": a["last_modified_from_xml"],
                "module_xml_path": a["module_xml_path"],
            }
            for a in hidden_activities
        ],
        key=lambda x: (parse_int(x.get("section_number", 0)), x.get("activity_type", ""), x.get("activity_name", "")),
    )

    data["external_dependency_inventory"] = sorted(
        [
            {
                "activity_name": a["activity_name"],
                "activity_type": a["activity_type"],
                "section_number": a["section_number"],
                "section_name": a["section_name"],
                "visible": a["visible"],
                "xml_external_link_count": a["xml_external_link_count"],
                "xml_iframe_count": a["xml_iframe_count"],
                "xml_webcal_link_count": a["xml_webcal_link_count"],
                "xml_panopto_link_count": a["xml_panopto_link_count"],
                "xml_external_domains": a["xml_external_domains"],
                "xml_external_links_sample": a["xml_external_links_sample"],
                "last_modified_from_xml": a["last_modified_from_xml"],
            }
            for a in data["activities"]
            if parse_int(a["xml_external_link_count"]) > 0 or parse_int(a["xml_iframe_count"]) > 0 or a["activity_type"] == "lti"
        ],
        key=lambda x: (
            -parse_int(x.get("xml_external_link_count", 0)),
            -parse_int(x.get("xml_iframe_count", 0)),
            x.get("activity_name", ""),
        ),
    )

    data["external_domain_inventory"] = [
        {"domain": domain, "reference_count_in_xml": count}
        for domain, count in sorted(all_external_domains.items(), key=lambda x: (-x[1], x[0]))
    ]

    data["file_extension_inventory"] = [
        {"extension": ext, "file_count": count}
        for ext, count in sorted(file_ext_counts.items(), key=lambda x: (-x[1], x[0]))
    ]

    data["largest_files"] = sorted(
        [
            {
                "filename_from_xml": f.get("filename_from_xml", ""),
                "extension_from_filename": f.get("extension_from_filename", ""),
                "filesize_from_xml_mb": f.get("filesize_from_xml_mb", 0),
                "mimetype_from_xml": f.get("mimetype_from_xml", ""),
                "component_from_xml": f.get("component_from_xml", ""),
                "filearea_from_xml": f.get("filearea_from_xml", ""),
                "timemodified_from_xml": f.get("timemodified_from_xml", ""),
            }
            for f in data["files"]
            if f.get("filename_from_xml") and f.get("filename_from_xml") != "."
        ],
        key=lambda x: float(x.get("filesize_from_xml_mb", 0)),
        reverse=True,
    )[:30]

    modified_year_counts = Counter(a.get("modified_year_from_xml", "") or "unknown" for a in data["activities"])
    data["modification_year_summary"] = [
        {"modified_year_from_xml": year, "activity_count": count}
        for year, count in sorted(modified_year_counts.items(), key=lambda x: x[0])
    ]

    age_counts = Counter(a.get("activity_age_band", "unknown") for a in data["activities"])
    data["activity_age_summary"] = [
        {"activity_age_band": band, "activity_count": count}
        for band, count in sorted(age_counts.items(), key=lambda x: x[0])
    ]

    primary_delivery_pattern = choose_primary_delivery_pattern(type_counts)
    dominant_activity_types = "; ".join(f"{k}: {v}" for k, v in type_counts.most_common(5))

    data["course_characteristics"] = [{
        "primary_delivery_pattern_from_xml": primary_delivery_pattern,
        "dominant_activity_types_from_xml": dominant_activity_types,
        "section_count_from_xml": len(data["sections"]),
        "activity_count_from_xml": len(data["activities"]),
        "book_activity_count_from_xml": len(book_activities),
        "resource_activity_count_from_xml": len(resource_activities),
        "question_count_from_questions_xml": data["questions"]["question_count_from_questions_xml"],
        "hidden_section_count_from_xml": len(hidden_sections),
        "hidden_activity_count_from_xml": len(hidden_activities),
        "activities_with_external_links_in_xml": len(external_link_activities),
        "activities_with_iframes_in_xml": len(iframe_activities),
        "activities_with_webcal_links_in_xml": len(webcal_activities),
        "lti_activity_count_from_xml": len(lti_activities),
        "file_record_count_from_files_xml": len(data["files"]),
        "total_file_size_mb_from_files_xml": round(total_file_size / (1024 * 1024), 2),
    }]

    data["course_footprint"] = [{
        "section_count_from_xml": len(data["sections"]),
        "activity_count_from_xml": len(data["activities"]),
        "book_activity_count_from_xml": len(book_activities),
        "resource_activity_count_from_xml": len(resource_activities),
        "forum_activity_count_from_xml": type_counts.get("forum", 0),
        "quiz_activity_count_from_xml": type_counts.get("quiz", 0),
        "question_count_from_questions_xml": data["questions"]["question_count_from_questions_xml"],
        "file_record_count_from_files_xml": len(data["files"]),
        "total_file_size_mb_from_files_xml": round(total_file_size / (1024 * 1024), 2),
        "total_book_chapter_count_from_xml": total_book_chapters,
        "total_xml_text_word_count_estimate": total_xml_text_word_estimate,
        "external_domain_count_from_xml": len(all_external_domains),
    }]

    findings = []
    if hidden_sections:
        findings.append(f"Hidden sections: {len(hidden_sections)}.")
    if hidden_activities:
        findings.append(f"Hidden activities: {len(hidden_activities)}.")
    if hidden_chapters_total:
        findings.append(f"Hidden book chapters: {hidden_chapters_total}.")
    if empty_sections:
        findings.append(f"Sections with empty activity sequence: {len(empty_sections)}.")
    if duplicate_names:
        findings.append(f"Duplicated activity names: {len(duplicate_names)}.")
    if iframe_activities:
        findings.append(f"Activities with iframe tags in XML-stored HTML: {len(iframe_activities)}.")
    if webcal_activities:
        findings.append(f"Activities with webCAL links in XML-stored HTML: {len(webcal_activities)}.")
    if lti_activities:
        findings.append(f"LTI activities: {len(lti_activities)}.")
    if pluginfile_activities:
        findings.append(f"Activities with @@PLUGINFILE@@ references in XML-stored HTML: {len(pluginfile_activities)}.")

    data["xml_findings"] = findings

    observations = []
    observations.append(f"XML metadata contains {len(data['sections'])} sections and {len(data['activities'])} Moodle activities.")
    if type_counts:
        top_types = ", ".join(f"{k}: {v}" for k, v in type_counts.most_common(6))
        observations.append(f"Activity type counts: {top_types}.")
    observations.append(
        f"Visibility metadata shows {len(hidden_sections)} hidden sections, "
        f"{len(hidden_activities)} hidden activities, and {hidden_chapters_total} hidden book chapters."
    )
    observations.append(
        f"XML-stored HTML contains {len(external_link_activities)} activities with external links, "
        f"{len(iframe_activities)} activities with iframe tags, and {len(webcal_activities)} activities with webCAL links."
    )
    observations.append(
        f"files.xml contains {len(data['files'])} file records with metadata-reported total size of "
        f"{round(total_file_size / (1024 * 1024), 2)} MB."
    )
    observations.append(
        f"questions.xml contains {data['questions']['question_count_from_questions_xml']} question records."
    )

    data["factual_observations"] = observations

    data["summary"] = {
        "course_fullname_from_xml": data["course"].get("fullname", ""),
        "course_shortname_from_xml": data["course"].get("shortname", ""),
        "course_format_from_xml": data["course"].get("format", ""),
        "primary_delivery_pattern_from_xml": primary_delivery_pattern,
        "section_count_from_xml": len(data["sections"]),
        "activity_count_from_xml": len(data["activities"]),
        "activity_type_counts_from_xml": dict(type_counts),
        "hidden_section_count_from_xml": len(hidden_sections),
        "hidden_activity_count_from_xml": len(hidden_activities),
        "hidden_book_chapter_count_from_xml": hidden_chapters_total,
        "empty_section_count_from_xml": len(empty_sections),
        "duplicate_activity_name_count_from_xml": len(duplicate_names),
        "book_activity_count_from_xml": len(book_activities),
        "resource_activity_count_from_xml": len(resource_activities),
        "downloadcontent_enabled_activity_count_from_xml": len(downloadable_activities),
        "file_record_count_from_files_xml": len(data["files"]),
        "file_extension_counts_from_files_xml": dict(file_ext_counts),
        "file_mimetype_counts_from_files_xml": dict(file_mimetype_counts),
        "file_component_counts_from_files_xml": dict(file_component_counts),
        "total_file_size_mb_from_files_xml": round(total_file_size / (1024 * 1024), 2),
        "question_count_from_questions_xml": data["questions"]["question_count_from_questions_xml"],
        "activities_with_iframes_in_xml": len(iframe_activities),
        "activities_with_external_links_in_xml": len(external_link_activities),
        "activities_with_webcal_links_in_xml": len(webcal_activities),
        "lti_activity_count_from_xml": len(lti_activities),
        "activities_with_pluginfile_refs_in_xml": len(pluginfile_activities),
        "external_domain_count_from_xml": len(all_external_domains),
        "total_book_chapter_count_from_xml": total_book_chapters,
        "total_xml_text_word_count_estimate": total_xml_text_word_estimate,
    }

    return data


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def flatten_course_summary(data: Dict[str, Any], source_name: str, archive_type: str, output_folder: str = "") -> Dict[str, Any]:
    summary = data.get("summary", {})
    row = {
        "source_backup": source_name,
        "output_folder": output_folder,
        "archive_type": archive_type,
        "audit_scope": "XML metadata only",
        "binary_file_content_scanned": False,
        "course_fullname_from_xml": summary.get("course_fullname_from_xml", ""),
        "course_shortname_from_xml": summary.get("course_shortname_from_xml", ""),
        "course_format_from_xml": summary.get("course_format_from_xml", ""),
        "primary_delivery_pattern_from_xml": summary.get("primary_delivery_pattern_from_xml", ""),
        "section_count_from_xml": summary.get("section_count_from_xml", 0),
        "activity_count_from_xml": summary.get("activity_count_from_xml", 0),
        "hidden_section_count_from_xml": summary.get("hidden_section_count_from_xml", 0),
        "hidden_activity_count_from_xml": summary.get("hidden_activity_count_from_xml", 0),
        "hidden_book_chapter_count_from_xml": summary.get("hidden_book_chapter_count_from_xml", 0),
        "empty_section_count_from_xml": summary.get("empty_section_count_from_xml", 0),
        "duplicate_activity_name_count_from_xml": summary.get("duplicate_activity_name_count_from_xml", 0),
        "book_activity_count_from_xml": summary.get("book_activity_count_from_xml", 0),
        "resource_activity_count_from_xml": summary.get("resource_activity_count_from_xml", 0),
        "downloadcontent_enabled_activity_count_from_xml": summary.get("downloadcontent_enabled_activity_count_from_xml", 0),
        "file_record_count_from_files_xml": summary.get("file_record_count_from_files_xml", 0),
        "total_file_size_mb_from_files_xml": summary.get("total_file_size_mb_from_files_xml", 0),
        "question_count_from_questions_xml": summary.get("question_count_from_questions_xml", 0),
        "activities_with_iframes_in_xml": summary.get("activities_with_iframes_in_xml", 0),
        "activities_with_external_links_in_xml": summary.get("activities_with_external_links_in_xml", 0),
        "activities_with_webcal_links_in_xml": summary.get("activities_with_webcal_links_in_xml", 0),
        "lti_activity_count_from_xml": summary.get("lti_activity_count_from_xml", 0),
        "activities_with_pluginfile_refs_in_xml": summary.get("activities_with_pluginfile_refs_in_xml", 0),
        "external_domain_count_from_xml": summary.get("external_domain_count_from_xml", 0),
        "total_book_chapter_count_from_xml": summary.get("total_book_chapter_count_from_xml", 0),
        "total_xml_text_word_count_estimate": summary.get("total_xml_text_word_count_estimate", 0),
    }

    for activity_type, count in summary.get("activity_type_counts_from_xml", {}).items():
        row[f"activity_type_{activity_type}_from_xml"] = count

    return row


def write_course_summary_csv(path: Path, data: Dict[str, Any], source_name: str, archive_type: str, output_folder: str = "") -> Dict[str, Any]:
    row = flatten_course_summary(data, source_name, archive_type, output_folder)
    write_csv(path, [row])
    return row


def markdown_table(rows: List[List[Any]], headers: List[str]) -> str:
    out = []
    out.append("| " + " | ".join(headers) + " |")
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        cleaned = [str(x).replace("\n", " ").replace("|", "\\|") for x in row]
        out.append("| " + " | ".join(cleaned) + " |")
    return "\n".join(out)


def top_rows(rows: List[Dict[str, Any]], key: str, limit: int = 15) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda x: parse_int(x.get(key, 0)), reverse=True)[:limit]


def write_text_report(path: Path, data: Dict[str, Any], source_name: str, archive_type: str) -> None:
    course = data.get("course", {})
    summary = data.get("summary", {})
    characteristics = data.get("course_characteristics", [{}])[0] if data.get("course_characteristics") else {}
    footprint = data.get("course_footprint", [{}])[0] if data.get("course_footprint") else {}
    lines = []

    lines.append("MOODLE MBZ XML METADATA AUDIT REPORT")
    lines.append("=" * 38)
    lines.append("")
    lines.append(f"Source backup: {source_name}")
    lines.append(f"Archive type: {archive_type}")
    lines.append("Audit scope: XML metadata only")
    lines.append("Binary file content scanned: No")
    lines.append(f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("COURSE CHARACTERISTICS")
    lines.append("-" * 22)
    lines.append(f"Primary delivery pattern: {characteristics.get('primary_delivery_pattern_from_xml', '')}")
    lines.append(f"Dominant activity types: {characteristics.get('dominant_activity_types_from_xml', '')}")
    lines.append(f"Sections: {characteristics.get('section_count_from_xml', 0)}")
    lines.append(f"Activities: {characteristics.get('activity_count_from_xml', 0)}")
    lines.append(f"Books: {characteristics.get('book_activity_count_from_xml', 0)}")
    lines.append(f"Resources: {characteristics.get('resource_activity_count_from_xml', 0)}")
    lines.append(f"Question records: {characteristics.get('question_count_from_questions_xml', 0)}")
    lines.append(f"Hidden sections: {characteristics.get('hidden_section_count_from_xml', 0)}")
    lines.append(f"Hidden activities: {characteristics.get('hidden_activity_count_from_xml', 0)}")
    lines.append(f"External-link activities: {characteristics.get('activities_with_external_links_in_xml', 0)}")
    lines.append(f"Iframe activities: {characteristics.get('activities_with_iframes_in_xml', 0)}")
    lines.append(f"webCAL activities: {characteristics.get('activities_with_webcal_links_in_xml', 0)}")
    lines.append(f"LTI activities: {characteristics.get('lti_activity_count_from_xml', 0)}")
    lines.append(f"File records: {characteristics.get('file_record_count_from_files_xml', 0)}")
    lines.append(f"Total file size from metadata (MB): {characteristics.get('total_file_size_mb_from_files_xml', 0)}")

    lines.append("")
    lines.append("COURSE OVERVIEW")
    lines.append("-" * 15)
    lines.append(f"Full name: {course.get('fullname', '')}")
    lines.append(f"Short name: {course.get('shortname', '')}")
    lines.append(f"Format: {course.get('format', '')}")
    lines.append(f"Visible: {course.get('visible', '')}")
    lines.append(f"Start date: {course.get('startdate', '')}")
    lines.append(f"End date: {course.get('enddate', '')}")
    lines.append(f"Last modified: {course.get('timemodified', '')}")

    lines.append("")
    lines.append("SUMMARY COUNTS")
    lines.append("-" * 14)
    readable_counts = [
        ("Sections", summary.get("section_count_from_xml", 0)),
        ("Activities", summary.get("activity_count_from_xml", 0)),
        ("Books", summary.get("book_activity_count_from_xml", 0)),
        ("Resources", summary.get("resource_activity_count_from_xml", 0)),
        ("Hidden sections", summary.get("hidden_section_count_from_xml", 0)),
        ("Hidden activities", summary.get("hidden_activity_count_from_xml", 0)),
        ("Hidden book chapters", summary.get("hidden_book_chapter_count_from_xml", 0)),
        ("Empty sections", summary.get("empty_section_count_from_xml", 0)),
        ("Duplicated activity names", summary.get("duplicate_activity_name_count_from_xml", 0)),
        ("File records in files.xml", summary.get("file_record_count_from_files_xml", 0)),
        ("Total file size from files.xml metadata (MB)", summary.get("total_file_size_mb_from_files_xml", 0)),
        ("Question records in questions.xml", summary.get("question_count_from_questions_xml", 0)),
        ("Activities with iframe tags in XML", summary.get("activities_with_iframes_in_xml", 0)),
        ("Activities with external links in XML", summary.get("activities_with_external_links_in_xml", 0)),
        ("Activities with webCAL links in XML", summary.get("activities_with_webcal_links_in_xml", 0)),
        ("LTI activities", summary.get("lti_activity_count_from_xml", 0)),
        ("Activities with @@PLUGINFILE@@ refs in XML", summary.get("activities_with_pluginfile_refs_in_xml", 0)),
        ("External domains in XML", summary.get("external_domain_count_from_xml", 0)),
        ("Total book chapters", summary.get("total_book_chapter_count_from_xml", 0)),
        ("Total XML text word estimate", summary.get("total_xml_text_word_count_estimate", 0)),
    ]
    for label, value in readable_counts:
        lines.append(f"{label}: {value}")

    lines.append("")
    lines.append("ACTIVITY TYPE COUNTS")
    lines.append("-" * 20)
    for k, v in sorted(data.get("activity_type_counts", {}).items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"{k}: {v}")

    lines.append("")
    lines.append("XML FINDINGS")
    lines.append("-" * 12)
    if data.get("xml_findings"):
        for finding in data["xml_findings"]:
            lines.append(f"- {finding}")
    else:
        lines.append("No XML findings generated.")

    lines.append("")
    lines.append("DUPLICATE ACTIVITY INVENTORY")
    lines.append("-" * 28)
    if data.get("duplicate_activity_inventory"):
        for row in data["duplicate_activity_inventory"]:
            lines.append(f"{row.get('activity_name', '')}: {row.get('occurrence_count', 0)}")
    else:
        lines.append("No duplicate activity names detected.")

    lines.append("")
    lines.append("SECTION MAP")
    lines.append("-" * 11)
    for s in sorted(data.get("sections", []), key=lambda x: x.get("section_number", 0)):
        lines.append(
            f"{s.get('section_number', '')}. {s.get('section_name', '')} | visible={s.get('visible', '')} | "
            f"activities={s.get('activity_count_from_sequence', '')}"
        )

    lines.append("")
    lines.append("SECTION ACTIVITY BREAKDOWN")
    lines.append("-" * 26)
    for row in sorted(data.get("section_activity_breakdown", []), key=lambda x: x.get("section_number", 0)):
        lines.append(
            f"{row.get('section_number', '')}. {row.get('section_name', '')} | "
            f"{row.get('activity_type_breakdown_from_xml', '')}"
        )

    lines.append("")
    lines.append("BOOK INVENTORY")
    lines.append("-" * 14)
    for b in top_rows(data.get("book_inventory", []), "book_chapter_count_from_xml", limit=20):
        lines.append(
            f"{b.get('activity_name', '')} | section={b.get('section_number', '')} | "
            f"chapters={b.get('book_chapter_count_from_xml', 0)} | "
            f"words_est={b.get('xml_text_word_count_estimate', 0)} | "
            f"iframes={b.get('xml_iframe_count', 0)} | webCAL={b.get('xml_webcal_link_count', 0)}"
        )

    lines.append("")
    lines.append("HIDDEN CONTENT SUMMARY")
    lines.append("-" * 22)
    if data.get("hidden_content_summary"):
        for row in data["hidden_content_summary"]:
            lines.append(f"{row.get('activity_type', '')}: {row.get('hidden_activity_count', 0)} hidden activities")
    else:
        lines.append("No hidden activities detected.")

    lines.append("")
    lines.append("HIDDEN ACTIVITY INVENTORY")
    lines.append("-" * 25)
    if data.get("hidden_activity_inventory"):
        for h in data["hidden_activity_inventory"]:
            lines.append(
                f"{h.get('activity_name', '')} | type={h.get('activity_type', '')} | "
                f"section={h.get('section_number', '')} {h.get('section_name', '')} | "
                f"modified={h.get('last_modified_from_xml', '')}"
            )
    else:
        lines.append("No hidden activities detected from module.xml visible=0.")

    lines.append("")
    lines.append("EXTERNAL DEPENDENCY INVENTORY")
    lines.append("-" * 29)
    for e in data.get("external_dependency_inventory", [])[:20]:
        lines.append(
            f"{e.get('activity_name', '')} | type={e.get('activity_type', '')} | "
            f"external_links={e.get('xml_external_link_count', 0)} | "
            f"iframes={e.get('xml_iframe_count', 0)} | webCAL={e.get('xml_webcal_link_count', 0)}"
        )

    lines.append("")
    lines.append("EXTERNAL DOMAIN INVENTORY")
    lines.append("-" * 25)
    for row in data.get("external_domain_inventory", [])[:30]:
        lines.append(f"{row.get('domain', '')}: {row.get('reference_count_in_xml', 0)} references")

    lines.append("")
    lines.append("FILE EXTENSION INVENTORY")
    lines.append("-" * 24)
    for row in data.get("file_extension_inventory", []):
        lines.append(f"{row.get('extension', '')}: {row.get('file_count', 0)} files")

    lines.append("")
    lines.append("LARGEST FILES")
    lines.append("-" * 13)
    for row in data.get("largest_files", [])[:20]:
        lines.append(
            f"{row.get('filename_from_xml', '')} | {row.get('filesize_from_xml_mb', 0)} MB | "
            f"{row.get('extension_from_filename', '')} | {row.get('mimetype_from_xml', '')}"
        )

    lines.append("")
    lines.append("MODIFICATION YEAR SUMMARY")
    lines.append("-" * 25)
    for row in data.get("modification_year_summary", []):
        lines.append(f"{row.get('modified_year_from_xml', '')}: {row.get('activity_count', 0)} activities")

    lines.append("")
    lines.append("ACTIVITY AGE ANALYSIS")
    lines.append("-" * 21)
    for row in data.get("activity_age_summary", []):
        lines.append(f"{row.get('activity_age_band', '')}: {row.get('activity_count', 0)} activities")

    lines.append("")
    lines.append("COURSE FOOTPRINT")
    lines.append("-" * 16)
    for key, value in footprint.items():
        lines.append(f"{key}: {value}")

    lines.append("")
    lines.append("NOTES")
    lines.append("-" * 5)
    lines.append("All reported values are derived from Moodle backup XML files.")
    lines.append("Counts for links, iframes, images, and word estimates refer only to content stored directly in XML fields.")
    lines.append("Uploaded file contents are not opened or scanned.")
    lines.append("No quality score, risk score, severity score, or pedagogic rating is generated.")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_markdown_report(path: Path, data: Dict[str, Any], source_name: str, archive_type: str) -> None:
    course = data.get("course", {})
    summary = data.get("summary", {})
    characteristics = data.get("course_characteristics", [{}])[0] if data.get("course_characteristics") else {}
    footprint = data.get("course_footprint", [{}])[0] if data.get("course_footprint") else {}

    lines = []
    lines.append("# Moodle MBZ XML Metadata Audit Report")
    lines.append("")
    lines.append(f"**Source backup:** `{source_name}`")
    lines.append(f"**Archive type:** `{archive_type}`")
    lines.append("**Audit scope:** XML metadata only")
    lines.append("**Binary file content scanned:** No")
    lines.append(f"**Generated:** {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("> This report is based only on Moodle backup XML files. It does not inspect uploaded PDFs, Word documents, PowerPoint files, images, videos, SCORM packages, H5P packages, or other binary resources. No quality score, risk score, severity score, or pedagogic rating is generated.")
    lines.append("")

    lines.append("## 1. Course Characteristics")
    lines.append("")
    characteristic_rows = [
        ["Primary delivery pattern", characteristics.get("primary_delivery_pattern_from_xml", "")],
        ["Dominant activity types", characteristics.get("dominant_activity_types_from_xml", "")],
        ["Sections", characteristics.get("section_count_from_xml", 0)],
        ["Activities", characteristics.get("activity_count_from_xml", 0)],
        ["Books", characteristics.get("book_activity_count_from_xml", 0)],
        ["Resources", characteristics.get("resource_activity_count_from_xml", 0)],
        ["Question records", characteristics.get("question_count_from_questions_xml", 0)],
        ["Hidden sections", characteristics.get("hidden_section_count_from_xml", 0)],
        ["Hidden activities", characteristics.get("hidden_activity_count_from_xml", 0)],
        ["External-link activities", characteristics.get("activities_with_external_links_in_xml", 0)],
        ["Iframe activities", characteristics.get("activities_with_iframes_in_xml", 0)],
        ["webCAL activities", characteristics.get("activities_with_webcal_links_in_xml", 0)],
        ["LTI activities", characteristics.get("lti_activity_count_from_xml", 0)],
        ["File records", characteristics.get("file_record_count_from_files_xml", 0)],
        ["Total file size from metadata (MB)", characteristics.get("total_file_size_mb_from_files_xml", 0)],
    ]
    lines.append(markdown_table(characteristic_rows, ["Characteristic", "Value"]))
    lines.append("")

    lines.append("## 2. Factual Summary")
    lines.append("")
    for obs in data.get("factual_observations", []):
        lines.append(f"- {obs}")
    lines.append("")

    lines.append("## 3. Course Overview")
    lines.append("")
    overview_rows = [
        ["Full name", course.get("fullname", "")],
        ["Short name", course.get("shortname", "")],
        ["Format", course.get("format", "")],
        ["Visible", course.get("visible", "")],
        ["Start date", course.get("startdate", "")],
        ["End date", course.get("enddate", "")],
        ["Last modified", course.get("timemodified", "")],
    ]
    lines.append(markdown_table(overview_rows, ["Field", "Value"]))
    lines.append("")

    lines.append("## 4. Summary Counts")
    lines.append("")
    count_rows = [
        ["Sections", summary.get("section_count_from_xml", 0)],
        ["Activities", summary.get("activity_count_from_xml", 0)],
        ["Books", summary.get("book_activity_count_from_xml", 0)],
        ["Resources", summary.get("resource_activity_count_from_xml", 0)],
        ["Hidden sections", summary.get("hidden_section_count_from_xml", 0)],
        ["Hidden activities", summary.get("hidden_activity_count_from_xml", 0)],
        ["Hidden book chapters", summary.get("hidden_book_chapter_count_from_xml", 0)],
        ["Empty sections", summary.get("empty_section_count_from_xml", 0)],
        ["Duplicated activity names", summary.get("duplicate_activity_name_count_from_xml", 0)],
        ["File records in files.xml", summary.get("file_record_count_from_files_xml", 0)],
        ["Total file size from files.xml metadata (MB)", summary.get("total_file_size_mb_from_files_xml", 0)],
        ["Question records in questions.xml", summary.get("question_count_from_questions_xml", 0)],
        ["Activities with iframe tags in XML", summary.get("activities_with_iframes_in_xml", 0)],
        ["Activities with external links in XML", summary.get("activities_with_external_links_in_xml", 0)],
        ["Activities with webCAL links in XML", summary.get("activities_with_webcal_links_in_xml", 0)],
        ["LTI activities", summary.get("lti_activity_count_from_xml", 0)],
        ["Activities with @@PLUGINFILE@@ refs in XML", summary.get("activities_with_pluginfile_refs_in_xml", 0)],
        ["External domains in XML", summary.get("external_domain_count_from_xml", 0)],
        ["Total book chapters", summary.get("total_book_chapter_count_from_xml", 0)],
        ["Total XML text word estimate", summary.get("total_xml_text_word_count_estimate", 0)],
    ]
    lines.append(markdown_table(count_rows, ["Metric", "Value"]))
    lines.append("")

    lines.append("## 5. Activity Type Counts")
    lines.append("")
    type_rows = [[k, v] for k, v in sorted(data.get("activity_type_counts", {}).items(), key=lambda x: (-x[1], x[0]))]
    lines.append(markdown_table(type_rows, ["Activity type", "Count"]) if type_rows else "_No activities detected._")
    lines.append("")

    lines.append("## 6. XML Findings")
    lines.append("")
    if data.get("xml_findings"):
        for finding in data["xml_findings"]:
            lines.append(f"- {finding}")
    else:
        lines.append("_No XML findings generated._")
    lines.append("")

    lines.append("## 7. Duplicate Activity Inventory")
    lines.append("")
    duplicate_rows = [[r.get("activity_name", ""), r.get("occurrence_count", 0)] for r in data.get("duplicate_activity_inventory", [])]
    lines.append(markdown_table(duplicate_rows, ["Activity name", "Occurrences"]) if duplicate_rows else "_No duplicate activity names detected._")
    lines.append("")

    lines.append("## 8. Section Map")
    lines.append("")
    section_rows = []
    for s in sorted(data.get("sections", []), key=lambda x: x.get("section_number", 0)):
        section_rows.append([
            s.get("section_number", ""),
            s.get("section_name", ""),
            s.get("visible", ""),
            s.get("activity_count_from_sequence", ""),
            s.get("timemodified", ""),
        ])
    lines.append(markdown_table(section_rows, ["No.", "Section", "Visible", "Activities", "Modified"]) if section_rows else "_No sections detected._")
    lines.append("")

    lines.append("## 9. Section Activity Breakdown")
    lines.append("")
    breakdown_rows = []
    for row in sorted(data.get("section_activity_breakdown", []), key=lambda x: x.get("section_number", 0)):
        breakdown_rows.append([
            row.get("section_number", ""),
            row.get("section_name", ""),
            row.get("activity_count_from_sequence", ""),
            row.get("activity_type_breakdown_from_xml", ""),
        ])
    lines.append(markdown_table(breakdown_rows, ["No.", "Section", "Activities", "Breakdown"]) if breakdown_rows else "_No section breakdown detected._")
    lines.append("")

    lines.append("## 10. Book Inventory")
    lines.append("")
    book_rows = []
    for b in top_rows(data.get("book_inventory", []), "book_chapter_count_from_xml", limit=25):
        book_rows.append([
            b.get("section_number", ""),
            b.get("activity_name", ""),
            b.get("visible", ""),
            b.get("book_chapter_count_from_xml", 0),
            b.get("xml_text_word_count_estimate", 0),
            b.get("xml_iframe_count", 0),
            b.get("xml_webcal_link_count", 0),
            b.get("last_modified_from_xml", ""),
        ])
    lines.append(markdown_table(book_rows, ["Section", "Book", "Visible", "Chapters", "XML text words est.", "Iframes", "webCAL links", "Modified"]) if book_rows else "_No book activities detected._")
    lines.append("")

    lines.append("## 11. Hidden Content Summary")
    lines.append("")
    hidden_summary_rows = [[r.get("activity_type", ""), r.get("hidden_activity_count", 0)] for r in data.get("hidden_content_summary", [])]
    lines.append(markdown_table(hidden_summary_rows, ["Activity type", "Hidden activities"]) if hidden_summary_rows else "_No hidden activities detected._")
    lines.append("")

    lines.append("## 12. Hidden Activity Inventory")
    lines.append("")
    hidden_rows = []
    for h in data.get("hidden_activity_inventory", []):
        hidden_rows.append([
            h.get("section_number", ""),
            h.get("section_name", ""),
            h.get("activity_type", ""),
            h.get("activity_name", ""),
            h.get("last_modified_from_xml", ""),
        ])
    lines.append(markdown_table(hidden_rows, ["Section", "Section name", "Type", "Activity", "Modified"]) if hidden_rows else "_No hidden activities detected from module.xml visible=0._")
    lines.append("")

    lines.append("## 13. External Dependency Inventory")
    lines.append("")
    dependency_rows = []
    for e in data.get("external_dependency_inventory", [])[:30]:
        dependency_rows.append([
            e.get("section_number", ""),
            e.get("activity_type", ""),
            e.get("activity_name", ""),
            e.get("xml_external_link_count", 0),
            e.get("xml_iframe_count", 0),
            e.get("xml_webcal_link_count", 0),
            e.get("xml_panopto_link_count", 0),
        ])
    lines.append(markdown_table(dependency_rows, ["Section", "Type", "Activity", "External links", "Iframes", "webCAL", "Panopto"]) if dependency_rows else "_No external dependency indicators detected in XML-stored HTML._")
    lines.append("")

    lines.append("## 14. External Domain Inventory")
    lines.append("")
    domain_rows = [[r.get("domain", ""), r.get("reference_count_in_xml", 0)] for r in data.get("external_domain_inventory", [])[:30]]
    lines.append(markdown_table(domain_rows, ["Domain", "References in XML"]) if domain_rows else "_No external domains detected in XML-stored links._")
    lines.append("")

    lines.append("## 15. File Extension Inventory")
    lines.append("")
    ext_rows = [[r.get("extension", ""), r.get("file_count", 0)] for r in data.get("file_extension_inventory", [])]
    lines.append(markdown_table(ext_rows, ["Extension", "Count"]) if ext_rows else "_No file metadata detected._")
    lines.append("")

    lines.append("## 16. Largest Files")
    lines.append("")
    largest_rows = []
    for f in data.get("largest_files", [])[:25]:
        largest_rows.append([
            f.get("filename_from_xml", ""),
            f.get("filesize_from_xml_mb", 0),
            f.get("extension_from_filename", ""),
            f.get("mimetype_from_xml", ""),
            f.get("component_from_xml", ""),
            f.get("filearea_from_xml", ""),
        ])
    lines.append(markdown_table(largest_rows, ["Filename", "Size MB", "Ext.", "MIME type", "Component", "File area"]) if largest_rows else "_No file metadata detected._")
    lines.append("")

    lines.append("## 17. Question Metadata from questions.xml")
    lines.append("")
    q_rows = [[k, v] for k, v in sorted(data.get("questions", {}).get("question_type_counts_from_questions_xml", {}).items(), key=lambda x: (-x[1], x[0]))]
    lines.append(markdown_table(q_rows, ["Question type", "Count"]) if q_rows else "_No question records detected._")
    lines.append("")

    lines.append("## 18. Activity Modification Year Summary")
    lines.append("")
    mod_rows = [[r.get("modified_year_from_xml", ""), r.get("activity_count", 0)] for r in data.get("modification_year_summary", [])]
    lines.append(markdown_table(mod_rows, ["Modified year", "Activity count"]) if mod_rows else "_No activity modification dates detected._")
    lines.append("")

    lines.append("## 19. Activity Age Analysis")
    lines.append("")
    age_rows = [[r.get("activity_age_band", ""), r.get("activity_count", 0)] for r in data.get("activity_age_summary", [])]
    lines.append(markdown_table(age_rows, ["Age band", "Activity count"]) if age_rows else "_No activity age bands calculated._")
    lines.append("")

    lines.append("## 20. Course Footprint")
    lines.append("")
    footprint_rows = [[k, v] for k, v in footprint.items()]
    lines.append(markdown_table(footprint_rows, ["Metric", "Value"]) if footprint_rows else "_No course footprint generated._")
    lines.append("")

    lines.append("## 21. Scope Notes")
    lines.append("")
    lines.append("- This report is intentionally limited to Moodle backup XML metadata.")
    lines.append("- Link, iframe, image-tag, and word-count estimates are based only on HTML-like content stored directly inside XML fields.")
    lines.append("- Uploaded file contents are not opened or scanned.")
    lines.append("- No pedagogic judgement, quality score, risk score, or severity score is generated.")
    lines.append("- CSV and JSON outputs retain explicit `_from_xml` field names for machine-readable clarity.")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def process_single_mbz(mbz_path: Path, output_dir: Path, keep_extracted: bool = False) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        archive_type = extract_mbz(mbz_path, extract_dir)
        data = audit_course(extract_dir)

        (output_dir / "audit_data.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        write_csv(output_dir / "sections.csv", data["sections"])
        write_csv(output_dir / "activities.csv", data["activities"])
        write_csv(output_dir / "files.csv", data["files"])
        write_csv(output_dir / "section_activity_breakdown.csv", data["section_activity_breakdown"])
        write_csv(output_dir / "book_inventory.csv", data["book_inventory"])
        write_csv(output_dir / "duplicate_activity_inventory.csv", data["duplicate_activity_inventory"])
        write_csv(output_dir / "hidden_content_summary.csv", data["hidden_content_summary"])
        write_csv(output_dir / "hidden_activity_inventory.csv", data["hidden_activity_inventory"])
        write_csv(output_dir / "external_dependency_inventory.csv", data["external_dependency_inventory"])
        write_csv(output_dir / "external_domain_inventory.csv", data["external_domain_inventory"])
        write_csv(output_dir / "file_extension_inventory.csv", data["file_extension_inventory"])
        write_csv(output_dir / "largest_files.csv", data["largest_files"])
        write_csv(output_dir / "modification_year_summary.csv", data["modification_year_summary"])
        write_csv(output_dir / "activity_age_summary.csv", data["activity_age_summary"])
        write_csv(output_dir / "course_characteristics.csv", data["course_characteristics"])
        write_csv(output_dir / "course_footprint.csv", data["course_footprint"])
        summary_row = write_course_summary_csv(output_dir / "course_summary.csv", data, mbz_path.name, archive_type, str(output_dir))
        write_markdown_report(output_dir / "audit_report.md", data, mbz_path.name, archive_type)
        write_text_report(output_dir / "audit_report.txt", data, mbz_path.name, archive_type)

        if keep_extracted:
            extracted_target = output_dir / "extracted_backup"
            if extracted_target.exists():
                shutil.rmtree(extracted_target)
            shutil.copytree(extract_dir, extracted_target)

    return summary_row


def run_batch(input_dir: Path, output_dir: Path, recursive: bool = False, keep_extracted: bool = False) -> None:
    mbz_files = find_mbz_files(input_dir, recursive=recursive)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not mbz_files:
        raise FileNotFoundError(f"No .mbz files found in: {input_dir}")

    combined_rows: List[Dict[str, Any]] = []
    log_rows: List[Dict[str, Any]] = []

    for index, mbz_path in enumerate(mbz_files, start=1):
        folder_name = safe_folder_name(mbz_path.name)
        course_output_dir = output_dir / folder_name

        if course_output_dir.exists():
            suffix = 2
            while (output_dir / f"{folder_name}_{suffix}").exists():
                suffix += 1
            course_output_dir = output_dir / f"{folder_name}_{suffix}"

        print(f"[{index}/{len(mbz_files)}] Auditing XML metadata: {mbz_path.name}")

        try:
            summary_row = process_single_mbz(mbz_path, course_output_dir, keep_extracted=keep_extracted)
            combined_rows.append(summary_row)
            log_rows.append({
                "source_backup": mbz_path.name,
                "source_path": str(mbz_path),
                "output_folder": str(course_output_dir),
                "status": "success",
                "message": "",
            })
        except Exception as exc:
            log_rows.append({
                "source_backup": mbz_path.name,
                "source_path": str(mbz_path),
                "output_folder": str(course_output_dir),
                "status": "failed",
                "message": str(exc),
            })
            print(f"  FAILED: {mbz_path.name} — {exc}")

    write_csv(output_dir / "combined_course_summary.csv", combined_rows)
    write_csv(output_dir / "batch_run_log.csv", log_rows)

    print("")
    print(f"Batch XML metadata audit complete: {output_dir}")
    print(f"- {output_dir / 'combined_course_summary.csv'}")
    print(f"- {output_dir / 'batch_run_log.csv'}")
    print(f"Courses processed successfully: {len(combined_rows)}")
    print(f"Courses failed: {sum(1 for row in log_rows if row['status'] == 'failed')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Moodle .mbz course backups using XML metadata only.")
    parser.add_argument(
        "input",
        help="Path to a single Moodle .mbz backup file, or a folder containing .mbz files when --batch is used.",
    )
    parser.add_argument("--output", "-o", default="moodle_audit_output", help="Output folder")
    parser.add_argument("--batch", action="store_true", help="Process all .mbz files in the input folder")
    parser.add_argument("--recursive", action="store_true", help="In batch mode, search subfolders recursively for .mbz files")
    parser.add_argument("--keep-extracted", action="store_true", help="Keep extracted backup files in each output folder")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    if args.batch:
        if not input_path.is_dir():
            raise ValueError("--batch requires input to be a folder containing .mbz files.")
        run_batch(input_path, output_dir, recursive=args.recursive, keep_extracted=args.keep_extracted)
        return

    if input_path.is_dir():
        raise ValueError("Input is a folder. Use --batch to process a folder of .mbz files.")

    if input_path.suffix.lower() != ".mbz":
        raise ValueError("Input file must have a .mbz extension.")

    process_single_mbz(input_path, output_dir, keep_extracted=args.keep_extracted)

    print(f"XML metadata audit complete: {output_dir}")
    print(f"- {output_dir / 'audit_report.md'}")
    print(f"- {output_dir / 'audit_report.txt'}")
    print(f"- {output_dir / 'course_summary.csv'}")
    print(f"- {output_dir / 'course_characteristics.csv'}")
    print(f"- {output_dir / 'course_footprint.csv'}")
    print(f"- {output_dir / 'section_activity_breakdown.csv'}")
    print(f"- {output_dir / 'book_inventory.csv'}")
    print(f"- {output_dir / 'duplicate_activity_inventory.csv'}")
    print(f"- {output_dir / 'hidden_content_summary.csv'}")
    print(f"- {output_dir / 'hidden_activity_inventory.csv'}")
    print(f"- {output_dir / 'external_dependency_inventory.csv'}")
    print(f"- {output_dir / 'external_domain_inventory.csv'}")
    print(f"- {output_dir / 'file_extension_inventory.csv'}")
    print(f"- {output_dir / 'largest_files.csv'}")
    print(f"- {output_dir / 'modification_year_summary.csv'}")
    print(f"- {output_dir / 'activity_age_summary.csv'}")
    print(f"- {output_dir / 'activities.csv'}")
    print(f"- {output_dir / 'sections.csv'}")
    print(f"- {output_dir / 'files.csv'}")
    print(f"- {output_dir / 'audit_data.json'}")


if __name__ == "__main__":
    main()
