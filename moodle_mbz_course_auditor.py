#!/usr/bin/env python3
"""
Moodle MBZ XML Metadata Auditor
===============================

Version: 2.3

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
- content_inventory.csv
- video_inventory.csv
- audio_inventory.csv
- document_inventory.csv
- interactive_content_inventory.csv
- external_media_inventory.csv
- content_category_summary.csv
- hosting_summary.csv
- content_placement_inventory.csv

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
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


HTML_TAG_RE = re.compile(r"<[^>]+>")
IFRAME_RE = re.compile(r"<iframe\b[^>]*>", re.IGNORECASE)
IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
A_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
SRC_RE = re.compile(r"\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
HREF_RE = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

VIDEO_EXTENSIONS = {".3gp", ".avi", ".flv", ".m2v", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ogv", ".ts", ".webm", ".wmv"}
AUDIO_EXTENSIONS = {".aac", ".aiff", ".flac", ".m4a", ".mid", ".midi", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".wma"}
IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".epub", ".md", ".odt", ".pdf", ".rtf", ".tex", ".txt"}
PRESENTATION_EXTENSIONS = {".key", ".odp", ".ppt", ".pptx"}
SPREADSHEET_EXTENSIONS = {".csv", ".numbers", ".ods", ".tsv", ".xls", ".xlsm", ".xlsx"}
DATA_CODE_EXTENSIONS = {".do", ".ipynb", ".json", ".m", ".mat", ".por", ".py", ".r", ".rdata", ".rds", ".sav", ".sas", ".sas7bdat", ".sps", ".sql", ".xml"}
ARCHIVE_EXTENSIONS = {".7z", ".bz2", ".gz", ".rar", ".tar", ".tgz", ".xz", ".zip"}
INTERACTIVE_EXTENSIONS = {".h5p", ".html", ".htm", ".swf"}

PROVIDER_RULES = (
    ("panopto", ("panopto.com", "panopto.eu")),
    ("youtube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("vimeo", ("vimeo.com", "player.vimeo.com")),
    ("microsoft_stream", ("stream.microsoft.com", "stream.office.com")),
    ("sharepoint", ("sharepoint.com",)),
    ("onedrive", ("1drv.ms", "onedrive.live.com")),
    ("kaltura", ("kaltura.com", "kaltura.eu")),
    ("zoom", ("zoom.us", "zoom.com")),
    ("teams", ("teams.microsoft.com",)),
)

MOODLE_INTERNAL_PATH_MARKERS = (
    "/pluginfile.php/", "/draftfile.php/", "/webservice/pluginfile.php/",
    "/mod/book/tool/print/", "/mod/resource/view.php", "/mod/page/view.php",
)
TRACKING_QUERY_PARAMETERS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "utm_campaign", "utm_content",
    "utm_medium", "utm_source", "utm_term",
}

GENERIC_ACTIVITY_XML = {
    "module.xml",
    "roles.xml",
    "grades.xml",
    "grade_history.xml",
    "inforef.xml",
    "grading.xml",
}


class MediaReferenceParser(HTMLParser):
    """Extract URL-bearing HTML attributes without executing or repairing HTML."""

    URL_ATTRIBUTES = {"href", "src", "data", "poster"}
    MEDIA_TAGS = {"a", "audio", "embed", "iframe", "img", "object", "source", "track", "video"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: List[Dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        for attribute, value in attrs:
            if attribute.lower() in self.URL_ATTRIBUTES and value:
                self.references.append({
                    "url": value.strip(),
                    "html_element": tag if tag in self.MEDIA_TAGS else "other",
                    "html_attribute": attribute.lower(),
                })


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
    except TypeError:  # Python versions before the filter argument
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
        with zipfile.ZipFile(mbz_path, "r") as z:
            safe_extract_zip(z, extract_dir)
        return "zip"

    if archive_type == "tar":
        with tarfile.open(mbz_path, "r:*") as t:
            safe_extract_tar(t, extract_dir)
        return "tar/tar.gz"

    if archive_type == "gzip":
        try:
            with tarfile.open(mbz_path, "r:gz") as t:
                safe_extract_tar(t, extract_dir)
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


def canonicalise_url(url: str) -> str:
    """Normalise a stored URL for conservative cross-activity deduplication."""
    try:
        parsed = urlparse(html.unescape(url.strip()))
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        port = f":{parsed.port}" if parsed.port and not (
            (scheme == "http" and parsed.port == 80) or (scheme == "https" and parsed.port == 443)
        ) else ""
        netloc = hostname + port
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        query = urlencode(sorted(
            (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_PARAMETERS
        ))
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url.strip()


def is_moodle_internal_reference(url: str) -> bool:
    """Identify standard Moodle-served URLs without relying on a site hostname."""
    try:
        path = urlparse(url).path.lower()
    except Exception:
        path = url.lower()
    return any(marker in path for marker in MOODLE_INTERNAL_PATH_MARKERS)


def extension_from_name_or_url(value: str) -> str:
    if not value:
        return ""
    try:
        path = urlparse(value).path if "://" in value else value
        return Path(path).suffix.lower()
    except Exception:
        return ""


def classify_content(mimetype: str = "", extension: str = "", component: str = "") -> Tuple[str, str, str]:
    """Return category, subtype and evidence using deterministic metadata rules."""
    mime = (mimetype or "").lower().split(";", 1)[0].strip()
    ext = (extension or "").lower()
    component = (component or "").lower()

    # Moodle commonly stores HTML learning packages as ordinary files.  HTML
    # MIME types and .html/.htm extensions are equivalent evidence of
    # interactive web content; treating text/html as a generic text document
    # creates a false MIME/extension conflict for valid packages.
    if (
        component in {"mod_h5pactivity", "mod_scorm"}
        or ext in INTERACTIVE_EXTENSIONS
        or mime in {"text/html", "application/xhtml+xml", "application/x-h5p"}
    ):
        category = "interactive"
    elif mime.startswith("video/") or ext in VIDEO_EXTENSIONS:
        category = "video"
    elif mime.startswith("audio/") or ext in AUDIO_EXTENSIONS:
        category = "audio"
    elif mime.startswith("image/") or ext in IMAGE_EXTENSIONS:
        category = "image"
    elif ext in PRESENTATION_EXTENSIONS or mime in {"application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation"}:
        category = "presentation"
    elif ext in SPREADSHEET_EXTENSIONS or "spreadsheet" in mime or mime in {"text/csv", "text/tab-separated-values"}:
        category = "spreadsheet"
    elif ext in DATA_CODE_EXTENSIONS:
        category = "data_or_code"
    elif ext in DOCUMENT_EXTENSIONS or mime.startswith("text/") or mime in {"application/pdf", "application/msword", "application/rtf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
        category = "document"
    elif ext in ARCHIVE_EXTENSIONS or mime in {"application/zip", "application/x-7z-compressed", "application/x-rar-compressed", "application/gzip"}:
        category = "archive"
    else:
        category = "other"

    subtype = ext.lstrip(".") or (mime.split("/", 1)[1] if "/" in mime else mime) or "unknown"
    if mime and ext:
        evidence = "mime_and_extension"
    elif mime:
        evidence = "mime_type"
    elif ext:
        evidence = "extension"
    elif component in {"mod_h5pactivity", "mod_scorm"}:
        evidence = "moodle_component"
    else:
        evidence = "unclassified_metadata"
    return category, subtype, evidence


def classify_provider(url: str) -> Tuple[str, str]:
    domain = domain_from_url(url)
    if is_moodle_internal_reference(url):
        return "moodle_internal", domain
    for provider, patterns in PROVIDER_RULES:
        if any(domain == pattern or domain.endswith("." + pattern) for pattern in patterns):
            return provider, domain
    # A provider does not have to be present in the built-in catalogue to be
    # valid.  A syntactically valid external hostname is itself useful,
    # directly extracted provider evidence.  Reserve ``other_external`` for a
    # URL whose host cannot be determined rather than treating every unfamiliar
    # platform as uncertain.
    if domain:
        return domain, domain
    return "other_external", ""


def strongest_html_element(elements: str) -> str:
    """Choose the most informative recorded use of a URL deterministically."""
    present = {element.strip().lower() for element in (elements or "").split(";") if element.strip()}
    for element in ("video", "audio", "iframe", "embed", "object", "img", "source", "track", "a", "plain_url", "other"):
        if element in present:
            return element
    return ""


def classify_external_reference(url: str, html_element: str = "") -> Tuple[str, str, str, str]:
    provider, domain = classify_provider(url)
    ext = extension_from_name_or_url(url)
    category, subtype, evidence = classify_content(extension=ext)
    element = (html_element or "").lower()

    if provider in {"panopto", "youtube", "vimeo", "microsoft_stream", "kaltura"}:
        category, subtype, evidence = "video", provider, "provider_domain"
    elif provider == "zoom" and any(marker in url.lower() for marker in ("/rec/", "/recording/", "recordingid", "recording_id")):
        category, subtype, evidence = "video", "zoom_recording", "provider_url_pattern"
    elif provider == "moodle_internal":
        subtype, evidence = "moodle_internal_reference", "moodle_url_pattern"
    elif category == "other" and element in {"video", "audio", "img"}:
        category = {"video": "video", "audio": "audio", "img": "image"}[element]
        subtype, evidence = "embedded_media", "html_element"
    elif category == "other" and element in {"iframe", "embed", "object"}:
        category, subtype = "interactive", "external_embed"
        evidence = "external_domain_and_html_element" if domain else "html_element_without_valid_domain"
    return category, subtype, provider, evidence


def collect_xml_html_signals(raw_html_chunks: List[str]) -> Dict[str, Any]:
    """
    Collect signals from HTML-like text stored directly in Moodle XML fields.

    These are XML-derived signals only. They do not include links, images, or
    content that exists only inside uploaded files such as PDFs, Word documents,
    PowerPoints, SCORM packages, or H5P packages.
    """
    combined = "\n".join([x for x in raw_html_chunks if x])
    decoded = html.unescape(combined)

    parser = MediaReferenceParser()
    try:
        parser.feed(decoded)
    except Exception:
        pass

    references_by_url: Dict[str, Dict[str, Any]] = {}
    for reference in parser.references:
        url = canonicalise_url(reference["url"])
        if not url.startswith(("http://", "https://")):
            continue
        record = references_by_url.setdefault(url, {"url": url, "html_elements": set(), "html_attributes": set()})
        record["html_elements"].add(reference["html_element"])
        record["html_attributes"].add(reference["html_attribute"])
    for url in URL_RE.findall(decoded):
        cleaned = canonicalise_url(url.rstrip(".,;:!?)]}&quot;"))
        references_by_url.setdefault(cleaned, {"url": cleaned, "html_elements": {"plain_url"}, "html_attributes": set()})

    external_references = []
    for url, record in sorted(references_by_url.items()):
        external_references.append({
            "url": url,
            "html_elements": ";".join(sorted(record["html_elements"])),
            "html_attributes": ";".join(sorted(record["html_attributes"])),
        })
    all_url_references = external_references
    external_references = [r for r in all_url_references if not is_moodle_internal_reference(r["url"])]
    internal_references = [r for r in all_url_references if is_moodle_internal_reference(r["url"])]
    unique_external_links = [r["url"] for r in external_references]
    domains = [domain_from_url(url) for url in unique_external_links if domain_from_url(url)]
    webcal_links = [x for x in unique_external_links if "webcal" in x.lower()]
    panopto_links = [x for x in unique_external_links if "panopto" in x.lower()]

    return {
        "xml_text_word_count_estimate": count_words_from_html(combined),
        "xml_iframe_count": len(IFRAME_RE.findall(decoded)),
        "xml_image_tag_count": len(IMG_RE.findall(decoded)),
        "xml_anchor_link_count": len(A_RE.findall(decoded)),
        "xml_external_links": unique_external_links,
        "xml_external_references": external_references,
        "xml_all_url_references": all_url_references,
        "xml_moodle_internal_reference_count": len(internal_references),
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
        "content_inventory": [],
        "video_inventory": [],
        "audio_inventory": [],
        "document_inventory": [],
        "interactive_content_inventory": [],
        "external_media_inventory": [],
        "content_category_summary": [],
        "hosting_summary": [],
        "content_placement_inventory": [],
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

    # moodle_backup.xml often retains activity context identifiers even when an
    # individual module.xml does not. Index both the activity directory and the
    # module id; absent fields simply leave the fallback empty.
    backup_activity_by_directory: Dict[str, Dict[str, str]] = {}
    backup_activity_by_module_id: Dict[str, Dict[str, str]] = {}
    backup_root = parse_xml(root_dir / "moodle_backup.xml")
    if backup_root is not None:
        for backup_activity in backup_root.findall(".//activity"):
            metadata = {
                "module_id": child_text(backup_activity, "moduleid") or child_text(backup_activity, "module_id"),
                "context_id": child_text(backup_activity, "contextid") or child_text(backup_activity, "context_id"),
                "directory": child_text(backup_activity, "directory").strip("/"),
            }
            if metadata["directory"]:
                backup_activity_by_directory[metadata["directory"]] = metadata
                backup_activity_by_directory[Path(metadata["directory"]).name] = metadata
            if metadata["module_id"]:
                backup_activity_by_module_id[metadata["module_id"]] = metadata

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
    external_reference_records: List[Dict[str, Any]] = []

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
        activity_instance_id = ""

        if specific_xml:
            specific_root = parse_xml(specific_xml)
            if specific_root is not None:
                node = specific_root.find(modulename)
                if node is None:
                    node = list(specific_root)[0] if list(specific_root) else specific_root

                activity_instance_id = node.attrib.get("id", "") or child_text(node, "id")

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

        backup_metadata = (
            backup_activity_by_directory.get(activity_dir.name)
            or backup_activity_by_directory.get(str(activity_dir.relative_to(root_dir)))
            or backup_activity_by_module_id.get(module_id)
            or {}
        )
        context_id = (
            child_text(module_root, "contextid")
            or module_root.attrib.get("contextid", "")
            or backup_metadata.get("context_id", "")
        )

        inforef_file_ids: List[str] = []
        inforef_root = parse_xml(activity_dir / "inforef.xml")
        if inforef_root is not None:
            for file_ref in inforef_root.findall(".//fileref/file"):
                file_id = file_ref.attrib.get("id", "") or child_text(file_ref, "id") or safe_text(file_ref)
                if file_id:
                    inforef_file_ids.append(file_id)

        activity = {
            "module_id": module_id,
            "context_id": context_id,
            "activity_instance_id": activity_instance_id,
            "inforef_file_ids": ";".join(sorted(set(inforef_file_ids))),
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

        for reference_index, reference in enumerate(signals["xml_all_url_references"], start=1):
            external_reference_records.append({
                "reference_index": reference_index,
                "url": reference["url"],
                "html_elements": reference["html_elements"],
                "html_attributes": reference["html_attributes"],
                "module_id": module_id,
                "context_id": activity["context_id"],
                "activity_name": activity["activity_name"],
                "activity_type": activity["activity_type"],
                "section_id": sectionid,
                "section_number": activity["section_number"],
                "section_name": activity["section_name"],
                "visible": visible,
            })

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
                "file_id_from_xml": file_node.attrib.get("id", "") or child_text(file_node, "id"),
                "contenthash_from_xml": child_text(file_node, "contenthash"),
                "filename_from_xml": filename,
                "filepath_from_xml": filepath,
                "filesize_from_xml_bytes": filesize,
                "filesize_from_xml_mb": round(filesize / (1024 * 1024), 3),
                "mimetype_from_xml": mimetype,
                "component_from_xml": component,
                "filearea_from_xml": filearea,
                "context_id_from_xml": child_text(file_node, "contextid"),
                "item_id_from_xml": child_text(file_node, "itemid"),
                "user_id_from_xml": child_text(file_node, "userid"),
                "source_from_xml": child_text(file_node, "source"),
                "author_from_xml": child_text(file_node, "author"),
                "license_from_xml": child_text(file_node, "license"),
                "status_from_xml": child_text(file_node, "status"),
                "reference_file_id_from_xml": child_text(file_node, "referencefileid"),
                "sortorder_from_xml": child_text(file_node, "sortorder"),
                "timecreated_from_xml": unix_to_iso(child_text(file_node, "timecreated")),
                "timemodified_from_xml": unix_to_iso(child_text(file_node, "timemodified")),
                "extension_from_filename": ext,
            })

    activities_by_context: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    activities_by_file_id: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    activities_by_component_instance: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for activity in data["activities"]:
        if activity.get("context_id"):
            activities_by_context[str(activity["context_id"])].append(activity)
        for file_id in str(activity.get("inforef_file_ids", "")).split(";"):
            if file_id:
                activities_by_file_id[file_id].append(activity)
        instance_id = str(activity.get("activity_instance_id", ""))
        if instance_id:
            activities_by_component_instance[(f"mod_{activity.get('activity_type', '')}", instance_id)].append(activity)

    for file_index, file_record in enumerate(data["files"], start=1):
        filename = str(file_record.get("filename_from_xml", ""))
        if not filename or filename == ".":
            continue
        context_id = str(file_record.get("context_id_from_xml", ""))
        file_id = str(file_record.get("file_id_from_xml", ""))
        component = str(file_record.get("component_from_xml", ""))
        item_id = str(file_record.get("item_id_from_xml", ""))
        match_method = ""
        matches = activities_by_file_id.get(file_id, [])
        if matches:
            match_method = "direct_inforef_file_match"
        if not matches:
            matches = activities_by_context.get(context_id, [])
            if matches:
                match_method = "module_context_match"
        if not matches and item_id not in {"", "0"}:
            matches = activities_by_component_instance.get((component, item_id), [])
            if matches:
                match_method = "component_instance_match"
        activity = matches[0] if len(matches) == 1 else {}
        category, subtype, detection_method = classify_content(
            str(file_record.get("mimetype_from_xml", "")),
            str(file_record.get("extension_from_filename", "")),
            str(file_record.get("component_from_xml", "")),
        )
        mime_category = classify_content(mimetype=str(file_record.get("mimetype_from_xml", "")))[0]
        extension_category = classify_content(extension=str(file_record.get("extension_from_filename", "")))[0]
        generic_mime = str(file_record.get("mimetype_from_xml", "")).lower().split(";", 1)[0] in {
            "", "application/octet-stream", "text/plain", "text/xml", "application/xml"
        }
        metadata_conflict = (
            not generic_mime
            and mime_category != "other"
            and extension_category != "other"
            and mime_category != extension_category
        )
        if len(matches) == 1:
            association_status = match_method
        elif len(matches) > 1:
            association_status = "ambiguous_activity_match"
        elif component.startswith("mod_"):
            association_status = "activity_location_unresolved"
        else:
            association_status = "course_or_system_context"

        # Classification confidence concerns the content type only. A file can
        # be confirmed as video even if its precise activity location is absent.
        confidence = "confirmed"
        review_reason = ""
        if metadata_conflict:
            confidence = "review"
            review_reason = "MIME type and filename extension indicate different categories."
        elif category == "other" and detection_method == "unclassified_metadata":
            confidence = "review"
            review_reason = "No recognised MIME type, extension, or Moodle component classification."

        association_note = ""
        if len(matches) > 1:
            association_note = "Multiple activities match the available backup identifiers."
        elif association_status == "activity_location_unresolved":
            association_note = "The file is present in a module component, but no reliable activity relationship was found."

        data["content_inventory"].append({
            "content_id": f"moodle_file:{file_record.get('file_id_from_xml') or file_index}",
            "source_type": "moodle_file",
            "content_category": category,
            "content_subtype": subtype,
            "hosting_type": "moodle",
            "provider": "moodle",
            "filename_or_title": filename,
            "url_or_reference": "",
            "mime_type": file_record.get("mimetype_from_xml", ""),
            "extension": file_record.get("extension_from_filename", ""),
            "size_bytes": file_record.get("filesize_from_xml_bytes", 0),
            "size_mb": file_record.get("filesize_from_xml_mb", 0),
            "file_id": file_record.get("file_id_from_xml", ""),
            "contenthash": file_record.get("contenthash_from_xml", ""),
            "component": file_record.get("component_from_xml", ""),
            "filearea": file_record.get("filearea_from_xml", ""),
            "context_id": context_id,
            "item_id": file_record.get("item_id_from_xml", ""),
            "module_id": activity.get("module_id", ""),
            "activity_name": activity.get("activity_name", ""),
            "activity_type": activity.get("activity_type", ""),
            "section_id": activity.get("section_id", ""),
            "section_number": activity.get("section_number", ""),
            "section_name": activity.get("section_name", ""),
            "visible": activity.get("visible", ""),
            "learner_facing_status": "activity_context" if activity else ("unresolved" if str(file_record.get("component_from_xml", "")).startswith("mod_") else "course_or_system_context"),
            "detection_method": detection_method,
            "association_status": association_status,
            "classification_confidence": confidence,
            "association_note": association_note,
            "review_reason": review_reason,
        })

    # Collapse duplicate file-pool binaries in the master inventory while
    # retaining every logical placement separately. Moodle contenthash is the
    # strongest available identity for a stored binary; file id is the fallback.
    file_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in data["content_inventory"]:
        key = str(row.get("contenthash") or row.get("file_id") or row.get("content_id"))
        file_groups[key].append(row)
        data["content_placement_inventory"].append({**row, "reference_scope": "moodle_file"})

    unique_file_rows: List[Dict[str, Any]] = []
    for key, placements in file_groups.items():
        resolved = next((p for p in placements if p.get("activity_name")), placements[0])
        master = dict(resolved)
        locations = sorted({
            f"{p.get('section_name', '')} :: {p.get('activity_name', '')}".strip(" :")
            for p in placements if p.get("activity_name")
        })
        master["content_id"] = f"moodle_content:{key}"
        master["placement_count"] = len(placements)
        master["activity_location_count"] = len(locations)
        master["activity_locations"] = " | ".join(locations)
        master["file_ids"] = ";".join(sorted({str(p.get("file_id", "")) for p in placements if p.get("file_id")}))
        if any(p.get("classification_confidence") == "review" for p in placements):
            review_row = next(p for p in placements if p.get("classification_confidence") == "review")
            master["classification_confidence"] = "review"
            master["review_reason"] = review_row.get("review_reason", "")
        unique_file_rows.append(master)
    data["content_inventory"] = unique_file_rows

    external_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for reference in external_reference_records:
        canonical_url = canonicalise_url(reference["url"])
        reference["canonical_url"] = canonical_url
        elements = reference.get("html_elements", "")
        primary_element = strongest_html_element(elements)
        category, subtype, provider, detection_method = classify_external_reference(canonical_url, primary_element)
        placement = {
            **reference,
            "canonical_url": canonical_url,
            "content_category": category,
            "content_subtype": subtype,
            "provider": provider,
            "detection_method": detection_method,
            "reference_scope": "moodle_internal" if provider == "moodle_internal" else "external",
        }
        data["content_placement_inventory"].append(placement)
        if provider != "moodle_internal":
            external_groups[canonical_url].append(placement)

    for reference_index, (canonical_url, placements) in enumerate(sorted(external_groups.items()), start=1):
        reference = placements[0]
        elements = reference.get("html_elements", "")
        primary_element = strongest_html_element(elements)
        category, subtype, provider, detection_method = classify_external_reference(canonical_url, primary_element)
        activity_labels = sorted({
            f"{p.get('section_name', '')} :: {p.get('activity_name', '')}".strip(" :")
            for p in placements if p.get("activity_name")
        })
        indeterminate_external = provider == "other_external"
        data["content_inventory"].append({
            "content_id": f"external_reference:{reference_index}",
            "source_type": "external_reference",
            "content_category": category,
            "content_subtype": subtype,
            "hosting_type": provider,
            "provider": provider,
            "filename_or_title": reference.get("activity_name", ""),
            "url_or_reference": canonical_url,
            "domain": domain_from_url(canonical_url),
            "mime_type": "",
            "extension": extension_from_name_or_url(canonical_url),
            "size_bytes": "",
            "size_mb": "",
            "file_id": "",
            "contenthash": "",
            "component": "",
            "filearea": "",
            "context_id": reference.get("context_id", ""),
            "item_id": "",
            "module_id": reference.get("module_id", ""),
            "activity_name": reference.get("activity_name", ""),
            "activity_type": reference.get("activity_type", ""),
            "section_id": reference.get("section_id", ""),
            "section_number": reference.get("section_number", ""),
            "section_name": reference.get("section_name", ""),
            "visible": reference.get("visible", ""),
            "learner_facing_status": "referenced_in_activity_xml",
            "html_elements": elements,
            "html_attributes": reference.get("html_attributes", ""),
            "detection_method": detection_method,
            "association_status": "direct_activity_reference",
            "classification_confidence": "review" if indeterminate_external else "confirmed",
            "review_reason": "External URL hostname could not be determined." if indeterminate_external else "",
            "placement_count": len(placements),
            "activity_location_count": len(activity_labels),
            "activity_locations": " | ".join(activity_labels),
        })

    data["video_inventory"] = [r for r in data["content_inventory"] if r["content_category"] == "video"]
    data["audio_inventory"] = [r for r in data["content_inventory"] if r["content_category"] == "audio"]
    data["document_inventory"] = [r for r in data["content_inventory"] if r["content_category"] in {"document", "presentation", "spreadsheet"}]
    data["interactive_content_inventory"] = [r for r in data["content_inventory"] if r["content_category"] == "interactive"]
    data["external_media_inventory"] = [r for r in data["content_inventory"] if r["source_type"] == "external_reference" and r["content_category"] in {"video", "audio", "image", "interactive"}]

    category_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    hosting_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in data["content_inventory"]:
        category_groups[str(record["content_category"])].append(record)
        hosting_groups[str(record["hosting_type"])].append(record)
    data["content_category_summary"] = [
        {
            "content_category": category,
            "item_count": len(records),
            "placement_count": sum(parse_int(r.get("placement_count"), 1) for r in records),
            "moodle_file_count": sum(r["source_type"] == "moodle_file" for r in records),
            "external_reference_count": sum(r["source_type"] == "external_reference" for r in records),
            "moodle_file_size_mb": round(sum(float(r.get("size_mb") or 0) for r in records if r["source_type"] == "moodle_file"), 3),
        }
        for category, records in sorted(category_groups.items())
    ]
    data["hosting_summary"] = [
        {
            "hosting_type": hosting_type,
            "item_count": len(records),
            "placement_count": sum(parse_int(r.get("placement_count"), 1) for r in records),
            "video_count": sum(r["content_category"] == "video" for r in records),
            "audio_count": sum(r["content_category"] == "audio" for r in records),
            "total_file_size_mb": round(sum(float(r.get("size_mb") or 0) for r in records), 3),
        }
        for hosting_type, records in sorted(hosting_groups.items())
    ]

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

    moodle_videos = [r for r in data["video_inventory"] if r["source_type"] == "moodle_file"]
    external_videos = [r for r in data["video_inventory"] if r["source_type"] == "external_reference"]
    panopto_videos = [r for r in external_videos if r["provider"] == "panopto"]
    unresolved_content = [r for r in data["content_inventory"] if r["classification_confidence"] == "review"]
    panopto_placements = sum(parse_int(r.get("placement_count"), 1) for r in panopto_videos)
    other_external_video_placements = sum(
        parse_int(r.get("placement_count"), 1) for r in external_videos if r.get("provider") != "panopto"
    )
    moodle_video_size_mb = round(sum(float(r.get("size_mb") or 0) for r in moodle_videos), 3)

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
        "moodle_hosted_video_count": len(moodle_videos),
        "moodle_hosted_video_size_mb": moodle_video_size_mb,
        "panopto_video_reference_count": panopto_placements,
        "panopto_unique_video_count": len(panopto_videos),
        "other_external_video_reference_count": other_external_video_placements,
        "other_external_unique_video_count": len(external_videos) - len(panopto_videos),
        "content_items_requiring_review": len(unresolved_content),
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
        "content_inventory_item_count": len(data["content_inventory"]),
        "content_placement_count": len(data["content_placement_inventory"]),
        "moodle_hosted_video_count": len(moodle_videos),
        "moodle_hosted_video_size_mb": moodle_video_size_mb,
        "external_video_reference_count": len(external_videos),
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
        f"The content inventory identifies {len(moodle_videos)} Moodle-hosted video files "
        f"({moodle_video_size_mb} MB), {len(panopto_videos)} unique Panopto videos across "
        f"{panopto_placements} placements, and {len(external_videos) - len(panopto_videos)} "
        f"other unique external videos across {other_external_video_placements} placements."
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
        "content_inventory_item_count": len(data["content_inventory"]),
        "content_placement_count": len(data["content_placement_inventory"]),
        "moodle_hosted_video_count": len(moodle_videos),
        "moodle_hosted_video_size_mb": moodle_video_size_mb,
        "panopto_video_reference_count": panopto_placements,
        "panopto_unique_video_count": len(panopto_videos),
        "other_external_video_reference_count": other_external_video_placements,
        "other_external_unique_video_count": len(external_videos) - len(panopto_videos),
        "content_items_requiring_review": len(unresolved_content),
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
        "content_inventory_item_count": summary.get("content_inventory_item_count", 0),
        "content_placement_count": summary.get("content_placement_count", 0),
        "moodle_hosted_video_count": summary.get("moodle_hosted_video_count", 0),
        "moodle_hosted_video_size_mb": summary.get("moodle_hosted_video_size_mb", 0),
        "panopto_video_reference_count": summary.get("panopto_video_reference_count", 0),
        "panopto_unique_video_count": summary.get("panopto_unique_video_count", 0),
        "other_external_video_reference_count": summary.get("other_external_video_reference_count", 0),
        "other_external_unique_video_count": summary.get("other_external_unique_video_count", 0),
        "content_items_requiring_review": summary.get("content_items_requiring_review", 0),
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
    lines.append("MEDIA AND CONTENT INVENTORY")
    lines.append("-" * 27)
    lines.append(f"Content inventory items: {summary.get('content_inventory_item_count', 0)}")
    lines.append(f"Moodle-hosted videos: {summary.get('moodle_hosted_video_count', 0)}")
    lines.append(f"Moodle-hosted video storage (MB): {summary.get('moodle_hosted_video_size_mb', 0)}")
    lines.append(f"Panopto video references: {summary.get('panopto_video_reference_count', 0)}")
    lines.append(f"Other external video references: {summary.get('other_external_video_reference_count', 0)}")
    lines.append(f"Items requiring review or with unresolved location: {summary.get('content_items_requiring_review', 0)}")
    for row in sorted(data.get("video_inventory", []), key=lambda x: float(x.get("size_mb") or 0), reverse=True)[:20]:
        lines.append(
            f"{row.get('filename_or_title', '')} | hosting={row.get('hosting_type', '')} | "
            f"size_mb={row.get('size_mb', '')} | section={row.get('section_number', '')} | "
            f"activity={row.get('activity_name', '')} | association={row.get('association_status', '')}"
        )

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
    lines.append("Content categories and providers are deterministic classifications derived from MIME types, extensions, HTML elements, URLs, and Moodle components.")
    lines.append("External references indicate presence in the backup, not current availability or permissions.")
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

    lines.append("## Media and Content Inventory")
    lines.append("")
    media_summary_rows = [
        ["Content inventory items", summary.get("content_inventory_item_count", 0)],
        ["Moodle-hosted videos", summary.get("moodle_hosted_video_count", 0)],
        ["Moodle-hosted video storage (MB)", summary.get("moodle_hosted_video_size_mb", 0)],
        ["Panopto video references", summary.get("panopto_video_reference_count", 0)],
        ["Other external video references", summary.get("other_external_video_reference_count", 0)],
        ["Review or unresolved items", summary.get("content_items_requiring_review", 0)],
    ]
    lines.append(markdown_table(media_summary_rows, ["Metric", "Value"]))
    lines.append("")
    video_rows = []
    for record in sorted(data.get("video_inventory", []), key=lambda x: float(x.get("size_mb") or 0), reverse=True)[:25]:
        video_rows.append([
            record.get("hosting_type", ""), record.get("filename_or_title", ""),
            record.get("size_mb", ""), record.get("section_number", ""),
            record.get("activity_name", ""), record.get("association_status", ""),
            record.get("classification_confidence", ""),
        ])
    lines.append(markdown_table(video_rows, ["Hosting", "File/title", "Size MB", "Section", "Activity", "Association", "Confidence"]) if video_rows else "_No video items detected._")
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
    lines.append("- Content categories and providers are deterministic classifications derived from MIME types, extensions, HTML elements, URLs, and Moodle components.")
    lines.append("- External references establish that a URL was stored in the backup; they do not test current availability or permissions.")
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
        write_csv(output_dir / "content_inventory.csv", data["content_inventory"])
        write_csv(output_dir / "video_inventory.csv", data["video_inventory"])
        write_csv(output_dir / "audio_inventory.csv", data["audio_inventory"])
        write_csv(output_dir / "document_inventory.csv", data["document_inventory"])
        write_csv(output_dir / "interactive_content_inventory.csv", data["interactive_content_inventory"])
        write_csv(output_dir / "external_media_inventory.csv", data["external_media_inventory"])
        write_csv(output_dir / "content_category_summary.csv", data["content_category_summary"])
        write_csv(output_dir / "hosting_summary.csv", data["hosting_summary"])
        write_csv(output_dir / "content_placement_inventory.csv", data["content_placement_inventory"])
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
    print(f"- {output_dir / 'content_inventory.csv'}")
    print(f"- {output_dir / 'video_inventory.csv'}")
    print(f"- {output_dir / 'audio_inventory.csv'}")
    print(f"- {output_dir / 'document_inventory.csv'}")
    print(f"- {output_dir / 'interactive_content_inventory.csv'}")
    print(f"- {output_dir / 'external_media_inventory.csv'}")
    print(f"- {output_dir / 'content_category_summary.csv'}")
    print(f"- {output_dir / 'hosting_summary.csv'}")
    print(f"- {output_dir / 'content_placement_inventory.csv'}")
    print(f"- {output_dir / 'audit_data.json'}")


if __name__ == "__main__":
    main()
