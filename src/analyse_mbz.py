#!/usr/bin/env python3
"""Analyse Moodle course settings from an MBZ backup.

Produces a self-contained HTML report plus CSV exports. The analyser reports
URL display modes neutrally; optional domain review rules can identify links
that an institution expects to open in a new window.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import tarfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


DISPLAY_MODES = {
    "0": "Automatic",
    "1": "Embed",
    "2": "Frame",
    "3": "New window",
    "4": "Download",
    "5": "Open",
    "6": "Popup",
}

GROUP_MODES = {"0": "No groups", "1": "Separate groups", "2": "Visible groups"}
COMPLETION_MODES = {"0": "Not tracked", "1": "Manual", "2": "Automatic"}
PERMISSIONS = {"0": "Inherit", "1": "Allow", "-1": "Prevent", "-1000": "Prohibit"}


@dataclass
class Activity:
    cmid: str = ""
    context_id: str = ""
    activity_dir: str = ""
    module_type: str = "Unknown"
    name: str = "Unnamed activity"
    section_id: str = ""
    section_number: str = ""
    section_name: str = ""
    external_url: str = ""
    domain: str = ""
    display_code: str = ""
    display_mode: str = "Not applicable"
    visible: str = "Unknown"
    id_number: str = ""
    show_description: str = ""
    availability: str = ""
    availability_summary: str = "None"
    group_mode: str = ""
    group_mode_label: str = "Unknown"
    grouping_id: str = ""
    completion: str = ""
    completion_label: str = "Unknown"
    file_count: int = 0
    file_size_bytes: int = 0
    file_size: str = "0 B"
    file_names: list[str] = field(default_factory=list)
    file_types: list[str] = field(default_factory=list)
    role_overrides: int = 0
    override_details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def text_of(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    child = node.find(path)
    return (child.text or "").strip() if child is not None else default


def moodle_value(value: str) -> str:
    """Turn Moodle backup's special null marker into an empty value."""
    return "" if value in {"$@NULL@$", "NULL"} else value


def safe_xml(data: bytes, source: str) -> ET.Element | None:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        print(f"Warning: could not parse {source}: {exc}", file=sys.stderr)
        return None


def iter_safe_members(archive: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if member.isfile() and not path.is_absolute() and ".." not in path.parts:
            yield member


def read_archive_xml(archive: tarfile.TarFile) -> dict[str, bytes]:
    wanted: dict[str, bytes] = {}
    patterns = (
        re.compile(r"^(?:[^/]+/)*course/course\.xml$"),
        re.compile(r"^(?:[^/]+/)*files\.xml$"),
        re.compile(r"^(?:[^/]+/)*sections/section_[^/]+/section\.xml$"),
        # Moodle stores common settings in module.xml and activity-specific
        # settings in a sibling file such as url.xml, page.xml or folder.xml.
        re.compile(r"^(?:[^/]+/)*activities/[^/]+/[^/]+\.xml$"),
    )
    for member in iter_safe_members(archive):
        normalized = member.name.lstrip("./")
        if any(pattern.match(normalized) for pattern in patterns):
            fileobj = archive.extractfile(member)
            if fileobj:
                wanted[normalized] = fileobj.read()
    return wanted


def course_metadata(files: dict[str, bytes]) -> tuple[str, str]:
    path = next((p for p in files if p.endswith("course/course.xml")), "")
    root = safe_xml(files[path], path) if path else None
    if root is None:
        return "Unknown Moodle course", ""
    return text_of(root, "fullname", "Unknown Moodle course"), text_of(root, "shortname")


def parse_sections(files: dict[str, bytes]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    cm_to_section: dict[str, str] = {}
    for path, data in files.items():
        if "/sections/section_" not in f"/{path}" or not path.endswith("section.xml"):
            continue
        root = safe_xml(data, path)
        if root is None:
            continue
        sid = root.get("id", "") or text_of(root, "id")
        number = text_of(root, "number")
        name = text_of(root, "name") or ("General" if number == "0" else f"Section {number or sid}")
        sections[sid] = {"number": number, "name": name}
        for cmid in filter(None, (x.strip() for x in text_of(root, "sequence").split(","))):
            cm_to_section[cmid] = sid
    return sections, cm_to_section


def parse_role_overrides(files: dict[str, bytes], activity_dir: str) -> list[str]:
    suffix = f"activities/{activity_dir}/roles.xml"
    path = next((p for p in files if p.endswith(suffix)), "")
    if not path:
        return []
    root = safe_xml(files[path], path)
    if root is None:
        return []
    details: list[str] = []
    for node in root.findall(".//*[capability]"):
        capability = text_of(node, "capability")
        if not capability:
            continue
        role = text_of(node, "roleid", "unknown role")
        permission = text_of(node, "permission")
        details.append(
            f"Role {role}: {capability} = {PERMISSIONS.get(permission, permission or 'unknown')}"
        )
    if details:
        return details
    # Fallback for uncommon XML shapes where capability is not under an
    # override container we can describe safely.
    return [f"Capability override recorded: {(n.text or '').strip()}" for n in root.findall(".//capability")]


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def parse_file_inventory(files: dict[str, bytes]) -> dict[str, list[dict[str, object]]]:
    path = next((p for p in files if p == "files.xml" or p.endswith("/files.xml")), "")
    root = safe_xml(files[path], path) if path else None
    inventory: dict[str, list[dict[str, object]]] = defaultdict(list)
    if root is None:
        return inventory
    for node in root.findall(".//file"):
        filename = moodle_value(text_of(node, "filename"))
        context_id = text_of(node, "contextid")
        component = text_of(node, "component")
        if not filename or filename == "." or not context_id or not component.startswith("mod_"):
            continue
        try:
            size = int(text_of(node, "filesize", "0") or 0)
        except ValueError:
            size = 0
        inventory[context_id].append(
            {"filename": filename, "size": size, "mimetype": text_of(node, "mimetype")}
        )
    return inventory


def format_date(timestamp: object) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return str(timestamp or "unknown date")


def availability_summary(raw: str) -> str:
    if not raw:
        return "None"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "Present (unparsed)"

    def condition(node: object) -> str:
        if not isinstance(node, dict):
            return "Unknown condition"
        children = node.get("c")
        if isinstance(children, list):
            parts = [condition(child) for child in children]
            op = " OR " if node.get("op") == "|" else " AND "
            joined = op.join(filter(None, parts)) or "Empty condition"
            return f"NOT ({joined})" if node.get("op") == "!" else joined
        kind = str(node.get("type", "unknown"))
        if kind == "date":
            direction = node.get("d")
            label = "Available from" if direction in {">", ">="} else "Available until"
            return f"{label} {format_date(node.get('t'))}"
        if kind == "group":
            return f"Group ID {node.get('id')}" if node.get("id") else "Member of any group"
        if kind == "grouping":
            return f"Grouping ID {node.get('id', 'unknown')}"
        if kind == "completion":
            expected = {0: "incomplete", 1: "complete", 2: "complete with pass", 3: "complete with fail"}.get(node.get("e"), str(node.get("e", "specified state")))
            return f"Activity {node.get('cm', 'unknown')} is {expected}"
        if kind == "grade":
            bounds = []
            if node.get("min") is not None:
                bounds.append(f"minimum {node.get('min')}")
            if node.get("max") is not None:
                bounds.append(f"maximum {node.get('max')}")
            return f"Grade item {node.get('id', 'unknown')}: {', '.join(bounds) or 'condition set'}"
        if kind == "profile":
            return f"Profile condition: {node.get('sf', node.get('field', 'field'))}"
        return f"{kind.capitalize()} condition"

    return condition(data)


def plugin_content(
    files: dict[str, bytes], activity_dir: str, modtype: str
) -> tuple[ET.Element | None, str, str]:
    """Return the activity-specific element and its archive path.

    In a real MBZ, activities/url_123/module.xml contains common course-module
    settings while activities/url_123/url.xml contains <url> with name,
    externalurl and display. Other activity types follow the same pattern.
    """
    suffix = f"activities/{activity_dir}/{modtype}.xml"
    path = next((p for p in files if p.endswith(suffix)), "")
    if not path:
        return None, "", ""
    root = safe_xml(files[path], path)
    if root is None:
        return None, path, ""
    context_id = root.get("contextid", "") or text_of(root, "contextid")
    if root.tag == modtype:
        return root, path, context_id
    direct = root.find(modtype)
    if direct is not None:
        return direct, path, context_id
    nested = root.find(f".//{modtype}")
    return nested, path, context_id


def domain_consistency_findings(activities: Iterable[Activity]) -> list[dict[str, object]]:
    domains: dict[str, list[Activity]] = defaultdict(list)
    for activity in activities:
        if activity.domain:
            domains[activity.domain].append(activity)
    findings: list[dict[str, object]] = []
    for domain, items in sorted(domains.items()):
        counts = Counter(item.display_mode for item in items)
        if len(counts) > 1:
            findings.append({
                "domain": domain,
                "display_modes": dict(sorted(counts.items())),
                "activities_affected": len(items),
            })
    return findings


def parse_activities(
    files: dict[str, bytes], expect_new_window_for: Iterable[str] = ()
) -> list[Activity]:
    review_domains = tuple(
        rule.strip().lower() for rule in expect_new_window_for if rule.strip()
    )
    sections, cm_to_section = parse_sections(files)
    file_inventory = parse_file_inventory(files)
    activities: list[Activity] = []
    for path, data in sorted(files.items()):
        match = re.search(r"activities/([^/]+)/module\.xml$", path)
        if not match:
            continue
        root = safe_xml(data, path)
        if root is None:
            continue
        activity_dir = match.group(1)
        cmid = root.get("id", "") or text_of(root, "id")
        modtype = text_of(root, "modulename") or text_of(root, "modtype")
        content, plugin_path, plugin_context_id = plugin_content(files, activity_dir, modtype) if modtype else (None, "", "")
        section_id = text_of(root, "sectionid") or cm_to_section.get(cmid, "")
        section = sections.get(section_id, {})
        display_code = text_of(content, "display") if modtype in {"url", "resource"} else ""
        external_url = text_of(content, "externalurl") if modtype == "url" else ""
        try:
            domain = (urlparse(external_url).hostname or "").lower()
        except ValueError:
            domain = ""
        availability = moodle_value(text_of(root, "availability"))
        context_id = root.get("contextid", "") or text_of(root, "contextid") or plugin_context_id
        activity_files = file_inventory.get(context_id, [])
        file_names = sorted({str(item["filename"]) for item in activity_files})
        file_types = sorted({Path(name).suffix.lower().lstrip(".") or "no extension" for name in file_names})
        total_file_size = sum(int(item["size"]) for item in activity_files)
        override_details = parse_role_overrides(files, activity_dir)
        warnings: list[str] = []
        if content is None:
            warnings.append(f"Missing or unreadable {modtype or 'activity'} settings XML")
        if modtype in {"url", "resource"} and content is not None and not display_code:
            warnings.append(f"{modtype.capitalize()} display setting is missing")
        for rule in review_domains:
            if rule in domain and display_code != "3":
                warnings.append(
                    f"Review rule: expected New window for domain matching '{rule}'"
                )
        visible_raw = text_of(root, "visible")
        activity = Activity(
            cmid=cmid,
            context_id=context_id,
            activity_dir=activity_dir,
            module_type="URL" if modtype == "url" else (modtype or "Unknown").capitalize(),
            name=text_of(content, "name", activity_dir),
            section_id=section_id,
            section_number=text_of(root, "sectionnumber") or section.get("number", ""),
            section_name=section.get("name", ""),
            external_url=external_url,
            domain=domain,
            display_code=display_code,
            display_mode=(DISPLAY_MODES.get(display_code, f"Unknown ({display_code})") if display_code else "Missing data") if modtype in {"url", "resource"} else "Not applicable",
            visible={"1": "Visible", "0": "Hidden"}.get(visible_raw, "Unknown"),
            id_number=text_of(root, "idnumber"),
            show_description={"1": "Shown", "0": "Not shown"}.get(text_of(root, "showdescription"), "Unknown"),
            availability=availability,
            availability_summary=availability_summary(availability),
            group_mode=text_of(root, "groupmode"),
            group_mode_label=GROUP_MODES.get(text_of(root, "groupmode"), "Unknown"),
            grouping_id=text_of(root, "groupingid"),
            completion=text_of(root, "completion"),
            completion_label=COMPLETION_MODES.get(text_of(root, "completion"), "Unknown"),
            file_count=len(activity_files),
            file_size_bytes=total_file_size,
            file_size=human_bytes(total_file_size),
            file_names=file_names,
            file_types=file_types,
            role_overrides=len(override_details),
            override_details=override_details,
            warnings=warnings,
        )
        activities.append(activity)
    destinations: dict[str, list[Activity]] = defaultdict(list)
    for activity in activities:
        if activity.external_url:
            destinations[activity.external_url].append(activity)
    for destination, items in destinations.items():
        if len(items) > 1:
            finding = f"Duplicate URL destination ({len(items)} activities)"
            for item in items:
                item.warnings.append(finding)
    return activities


def csv_value(value: object) -> object:
    if isinstance(value, list):
        return "; ".join(str(x) for x in value)
    return value


def write_csv(path: Path, activities: list[Activity]) -> None:
    fields = list(asdict(Activity()).keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for activity in activities:
            writer.writerow({k: csv_value(v) for k, v in asdict(activity).items()})


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def write_html(
    path: Path,
    course_name: str,
    shortname: str,
    source: Path,
    activities: list[Activity],
    expect_new_window_for: Iterable[str] = (),
) -> None:
    urls = [a for a in activities if a.module_type.lower() == "url"]
    resources = [a for a in activities if a.module_type.lower() == "resource"]
    warnings = [a for a in activities if a.warnings]
    domain_patterns = domain_consistency_findings(activities)
    mode_counts = Counter(a.display_mode for a in urls + resources)
    domain_counts = Counter(a.domain or "(unrecognised)" for a in urls)
    attached_files = sum(a.file_count for a in activities)
    attached_bytes = sum(a.file_size_bytes for a in activities)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def cards() -> str:
        values = [
            ("Activities", len(activities)), ("URL resources", len(urls)),
            ("File resources", len(resources)), ("Attached files", attached_files),
            ("File storage", human_bytes(attached_bytes)), ("Row findings", len(warnings)),
            ("Domain patterns", len(domain_patterns)),
        ]
        return "".join(f'<div class="card"><span>{esc(label)}</span><strong>{value}</strong></div>' for label, value in values)

    def table_rows(items: list[Activity]) -> str:
        rows = []
        for a in items:
            classes = " warning" if a.warnings else ""
            search_text = ' '.join([a.name, a.section_name, a.module_type, a.domain,
                a.display_mode, a.group_mode_label, a.completion_label,
                a.availability_summary, ' '.join(a.file_names),
                ' '.join(a.override_details), ' '.join(a.warnings)]).lower()
            file_summary = f"{a.file_count} · {a.file_size}" if a.file_count else "None"
            override_title = "; ".join(a.override_details)
            rows.append(f'''<tr class="data-row{classes}" data-search="{esc(search_text)}">
<td>{esc(a.section_number)}</td><td>{esc(a.section_name)}</td><td>{esc(a.name)}</td>
<td>{esc(a.module_type)}</td><td>{esc(a.domain)}</td><td><span class="pill">{esc(a.display_mode)}</span></td>
<td>{esc(a.visible)}</td><td>{esc(a.group_mode_label)}</td><td>{esc(a.completion_label)}</td>
<td>{esc(a.availability_summary)}</td><td title="{esc(', '.join(a.file_names))}">{esc(file_summary)}</td>
<td title="{esc(override_title)}">{a.role_overrides}</td>
<td>{esc('; '.join(a.warnings))}</td></tr>''')
        return "\n".join(rows) or '<tr><td colspan="13">No matching activities found.</td></tr>'

    mode_rows = "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in sorted(mode_counts.items()))
    domain_rows = "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in domain_counts.most_common())
    consistency_rows = "".join(
        f"<tr><td>{esc(item['domain'])}</td><td>{esc(', '.join(f'{mode}: {count}' for mode, count in item['display_modes'].items()))}</td><td>{item['activities_affected']}</td></tr>"
        for item in domain_patterns
    )
    payload = json.dumps([asdict(a) for a in activities]).replace("</", "<\\/")
    doc = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Moodle Course Settings — {esc(course_name)}</title>
<style>
:root{{--ink:#172033;--muted:#64748b;--line:#dbe2ea;--blue:#165dff;--pale:#f5f8fc;--warn:#fff3cd;--danger:#a33a16}}
*{{box-sizing:border-box}} body{{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink);background:var(--pale)}}
header{{padding:34px max(24px,calc((100% - 1280px)/2));background:#10234a;color:white}} h1{{margin:.15rem 0;font-size:clamp(1.7rem,4vw,2.5rem)}}
header p{{margin:.3rem 0;color:#cbd8f1}} main{{max-width:1280px;margin:auto;padding:24px}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.card,.panel{{background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 7px #1122440d}} .card{{padding:16px}} .card span{{display:block;color:var(--muted)}} .card strong{{font-size:1.8rem}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0}} .panel{{padding:20px;overflow:auto}} h2{{margin-top:0}} table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:top}} th{{background:#eef3f9;position:sticky;top:0}} .warning td{{background:var(--warn)}}
.pill{{white-space:nowrap;background:#e8efff;color:#143d99;padding:3px 7px;border-radius:999px}} input{{width:100%;padding:11px;border:1px solid #aab7c7;border-radius:8px;margin-bottom:12px;font:inherit}}
.sortable{{cursor:pointer;user-select:none;white-space:nowrap}} .sortable:hover{{background:#dfe8f4}} .sort-mark{{color:var(--blue);font-size:.8em}}
.note{{border-left:4px solid var(--blue);padding:10px 14px;background:#edf4ff;margin:16px 0}} footer{{color:var(--muted);padding:12px 0}} @media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
@media print{{body{{background:white}} header{{background:white;color:black;padding:10px 0}} main{{padding:0}} input{{display:none}} .panel,.card{{box-shadow:none}}}}
</style></head><body>
<header><h1>Moodle Course Settings</h1><p>{esc(course_name)}{f' · {esc(shortname)}' if shortname else ''}</p><p>Source: {esc(source.name)} · Generated {generated}</p></header>
<main><section class="cards">{cards()}</section>
<div class="note"><strong>Interpretation:</strong> Display settings, availability restrictions and role permissions are separate concepts. Automatic is reported neutrally, not treated as an error. File totals come from metadata included in the MBZ. Role overrides may not reveal final effective permission without site-level role definitions.{f' Active review rule: expect New window for domains matching {esc(", ".join(expect_new_window_for))}.' if tuple(expect_new_window_for) else ''}</div>
<section class="grid"><div class="panel"><h2>URL and File display modes</h2><table><thead><tr><th>Mode</th><th>Count</th></tr></thead><tbody>{mode_rows}</tbody></table></div>
<div class="panel"><h2>URL domains</h2><table><thead><tr><th>Domain</th><th>Count</th></tr></thead><tbody>{domain_rows}</tbody></table></div></section>
{f'<section class="panel"><h2>Domain consistency</h2><p>Domains using more than one display mode are summarised once here rather than repeated against every activity.</p><table><thead><tr><th>Domain</th><th>Display modes</th><th>Activities</th></tr></thead><tbody>{consistency_rows}</tbody></table></section>' if domain_patterns else ''}
<section class="panel"><h2>Activity settings</h2><input id="search" type="search" placeholder="Filter by activity, section, domain, setting, file or finding…" aria-label="Filter activities">
<div style="overflow:auto;max-height:70vh"><table id="activities"><thead><tr><th class="sortable" data-col="0" data-kind="number"># <span class="sort-mark"></span></th><th class="sortable" data-col="1">Section <span class="sort-mark"></span></th><th class="sortable" data-col="2">Activity <span class="sort-mark"></span></th><th class="sortable" data-col="3">Type <span class="sort-mark"></span></th><th class="sortable" data-col="4">Domain <span class="sort-mark"></span></th><th class="sortable" data-col="5">Display <span class="sort-mark"></span></th><th class="sortable" data-col="6">Visibility <span class="sort-mark"></span></th><th class="sortable" data-col="7">Groups <span class="sort-mark"></span></th><th class="sortable" data-col="8">Completion <span class="sort-mark"></span></th><th class="sortable" data-col="9">Restriction <span class="sort-mark"></span></th><th class="sortable" data-col="10" data-kind="number">Files <span class="sort-mark"></span></th><th class="sortable" data-col="11" data-kind="number">Overrides <span class="sort-mark"></span></th><th class="sortable" data-col="12">Finding <span class="sort-mark"></span></th></tr></thead>
<tbody>{table_rows(activities)}</tbody></table></div></section>
<footer>Generated locally from the MBZ. No course data is transmitted. Detailed values are available in the accompanying CSV and JSON files.</footer></main>
<script>
const input=document.getElementById('search'),tbody=document.querySelector('#activities tbody');
input.addEventListener('input',()=>{{const q=input.value.toLowerCase();document.querySelectorAll('.data-row').forEach(r=>r.hidden=!r.dataset.search.includes(q));}});
document.querySelectorAll('#activities th.sortable').forEach(th=>th.addEventListener('click',()=>{{
  const col=Number(th.dataset.col),kind=th.dataset.kind||'text',next=th.dataset.direction==='asc'?'desc':'asc';
  document.querySelectorAll('#activities th.sortable').forEach(h=>{{h.dataset.direction='';h.querySelector('.sort-mark').textContent='';}});
  th.dataset.direction=next;th.querySelector('.sort-mark').textContent=next==='asc'?'▲':'▼';
  const rows=[...tbody.querySelectorAll('.data-row')];
  rows.sort((a,b)=>{{let av=a.cells[col].textContent.trim(),bv=b.cells[col].textContent.trim();
    if(!av&&!bv)return 0;if(!av)return 1;if(!bv)return -1;
    let result=kind==='number'?(parseFloat(av)||0)-(parseFloat(bv)||0):av.localeCompare(bv,undefined,{{numeric:true,sensitivity:'base'}});
    return next==='asc'?result:-result;}}).forEach(row=>tbody.appendChild(row));
}}));
window.MOODLE_SETTINGS_DATA={payload};
</script>
</body></html>'''
    path.write_text(doc, encoding="utf-8")


def analyse(
    input_path: Path,
    output_dir: Path,
    expect_new_window_for: Iterable[str] = (),
) -> tuple[Path, Path, Path]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Backup not found: {input_path}")
    if not tarfile.is_tarfile(input_path):
        raise ValueError("The input is not a readable tar-based Moodle MBZ backup.")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(input_path, "r:*") as archive:
        files = read_archive_xml(archive)
    if not files:
        raise ValueError("No recognised Moodle course XML was found in the backup.")
    name, shortname = course_metadata(files)
    review_domains = tuple(expect_new_window_for)
    activities = parse_activities(files, review_domains)
    html_path = output_dir / "course-settings-report.html"
    csv_path = output_dir / "course-settings.csv"
    json_path = output_dir / "course-settings.json"
    write_html(html_path, name, shortname, input_path, activities, review_domains)
    write_csv(csv_path, activities)
    json_path.write_text(json.dumps({
        "course": {"fullname": name, "shortname": shortname},
        "domain_consistency": domain_consistency_findings(activities),
        "activities": [asdict(a) for a in activities],
    }, indent=2), encoding="utf-8")
    return html_path, csv_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an HTML report of settings stored in a Moodle MBZ backup.")
    parser.add_argument("backup", type=Path, help="Path to the Moodle .mbz backup")
    parser.add_argument("-o", "--output", type=Path, default=Path("moodle-settings-report"), help="Output directory")
    parser.add_argument(
        "--expect-new-window-for",
        action="append",
        default=[],
        metavar="DOMAIN_TEXT",
        help="Optional review rule; repeat to flag matching URL domains that are not set to New window",
    )
    args = parser.parse_args()
    try:
        outputs = analyse(args.backup, args.output, args.expect_new_window_for)
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.error(str(exc))
    for output in outputs:
        print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
