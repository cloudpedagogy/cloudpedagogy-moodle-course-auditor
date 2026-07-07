#!/usr/bin/env python3
"""
Moodle Audit Plotly Dashboard Generator
======================================

Generates a standalone HTML dashboard from the CSV/JSON outputs produced by
moodle_mbz_course_auditor.py.

This script is intentionally a separate visualisation layer. It does not parse
Moodle .mbz files and it does not change the audit data. It reads an existing
audit output folder and creates dashboard.html.

Example:
    python3 moodle_dashboard_generator.py moodle_audit_output
    python3 moodle_dashboard_generator.py moodle_audit_output --output dashboard.html

Batch example:
    python3 moodle_dashboard_generator.py batch_output --batch

Expected input:
    A folder containing some or all of the auditor outputs, for example:
    - audit_data.json
    - course_summary.csv
    - course_characteristics.csv
    - course_footprint.csv
    - sections.csv
    - activities.csv
    - book_inventory.csv
    - external_dependency_inventory.csv
    - external_domain_inventory.csv
    - file_extension_inventory.csv
    - largest_files.csv
    - modification_year_summary.csv
    - activity_age_summary.csv
    - hidden_content_summary.csv
    - hidden_activity_inventory.csv
    - duplicate_activity_inventory.csv

Design principles:
    - Fixed dashboard framework.
    - Optional panels: missing or empty files are skipped gracefully.
    - Unknown/new Moodle activity types are handled dynamically.
    - Output is a self-contained HTML file using Plotly from CDN by default.

Dependencies:
    pip install pandas plotly
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.io import to_html
from plotly.subplots import make_subplots


AUDIT_FILES = {
    "audit_data": "audit_data.json",
    "course_summary": "course_summary.csv",
    "course_characteristics": "course_characteristics.csv",
    "course_footprint": "course_footprint.csv",
    "sections": "sections.csv",
    "activities": "activities.csv",
    "section_activity_breakdown": "section_activity_breakdown.csv",
    "book_inventory": "book_inventory.csv",
    "duplicate_activity_inventory": "duplicate_activity_inventory.csv",
    "hidden_content_summary": "hidden_content_summary.csv",
    "hidden_activity_inventory": "hidden_activity_inventory.csv",
    "external_dependency_inventory": "external_dependency_inventory.csv",
    "external_domain_inventory": "external_domain_inventory.csv",
    "file_extension_inventory": "file_extension_inventory.csv",
    "largest_files": "largest_files.csv",
    "modification_year_summary": "modification_year_summary.csv",
    "activity_age_summary": "activity_age_summary.csv",
    "files": "files.csv",
}

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def read_csv_optional(folder: Path, filename: str) -> pd.DataFrame:
    """Read a CSV from folder, returning an empty DataFrame when missing/empty."""
    path = folder / filename
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except Exception as exc:
        print(f"Warning: could not read {path}: {exc}")
        return pd.DataFrame()


def read_json_optional(folder: Path, filename: str) -> Dict[str, Any]:
    path = folder / filename
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Warning: could not read {path}: {exc}")
        return {}


def load_audit_folder(folder: Path) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for key, filename in AUDIT_FILES.items():
        if filename.endswith(".csv"):
            data[key] = read_csv_optional(folder, filename)
        elif filename.endswith(".json"):
            data[key] = read_json_optional(folder, filename)
    return data


def has_columns(df: pd.DataFrame, columns: Iterable[str]) -> bool:
    return not df.empty and all(col in df.columns for col in columns)


def numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").fillna(0)


def clean_label(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def shorten(text: Any, max_len: int = 70) -> str:
    text = clean_label(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def safe_html(text: Any) -> str:
    return html.escape(clean_label(text))


def first_row(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def value_from_sources(data: Dict[str, Any], key: str, default: Any = "") -> Any:
    """Fetch a summary value from course_summary first, then audit_data.summary."""
    summary_df = data.get("course_summary", pd.DataFrame())
    if isinstance(summary_df, pd.DataFrame) and not summary_df.empty and key in summary_df.columns:
        return summary_df.iloc[0].get(key, default)

    audit_data = data.get("audit_data", {})
    if isinstance(audit_data, dict):
        summary = audit_data.get("summary", {}) or {}
        course = audit_data.get("course", {}) or {}
        if key in summary:
            return summary.get(key, default)
        if key in course:
            return course.get(key, default)
    return default


def course_title(data: Dict[str, Any], folder: Path) -> str:
    title = value_from_sources(data, "course_fullname_from_xml", "") or value_from_sources(data, "fullname", "")
    shortname = value_from_sources(data, "course_shortname_from_xml", "") or value_from_sources(data, "shortname", "")
    if title and shortname:
        return f"{title} ({shortname})"
    return title or shortname or folder.name


def make_metric_cards(data: Dict[str, Any]) -> str:
    metrics: List[Tuple[str, str, str]] = [
        ("Sections", "section_count_from_xml", "Course sections detected"),
        ("Activities", "activity_count_from_xml", "Moodle activities/resources"),
        ("Books", "book_activity_count_from_xml", "Moodle Book activities"),
        ("Resources", "resource_activity_count_from_xml", "File/resource activities"),
        ("Hidden activities", "hidden_activity_count_from_xml", "Visible=0 in module metadata"),
        ("Files", "file_record_count_from_files_xml", "File records in files.xml"),
        ("Total file size", "total_file_size_mb_from_files_xml", "Metadata-reported MB"),
        ("Questions", "question_count_from_questions_xml", "Question records"),
        ("External domains", "external_domain_count_from_xml", "Detected in XML links"),
        ("XML words est.", "total_xml_text_word_count_estimate", "Estimated from XML-stored HTML"),
    ]
    cards = []
    for label, key, note in metrics:
        val = value_from_sources(data, key, None)
        if val is None or val == "":
            continue
        if key == "total_file_size_mb_from_files_xml":
            display = f"{float(val):,.1f} MB" if str(val) not in ("", "nan") else "0 MB"
        else:
            try:
                display = f"{float(val):,.0f}"
            except Exception:
                display = safe_html(val)
        cards.append(
            f"""
            <div class="metric-card">
              <div class="metric-label">{safe_html(label)}</div>
              <div class="metric-value">{display}</div>
              <div class="metric-note">{safe_html(note)}</div>
            </div>
            """
        )
    return "\n".join(cards)


def fig_to_div(fig: go.Figure, include_plotlyjs: bool = False) -> str:
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=25, t=55, b=45),
        height=430,
        legend_title_text="",
        font=dict(family="Arial, sans-serif", size=13),
    )
    return to_html(
        fig,
        include_plotlyjs=(PLOTLY_CDN if include_plotlyjs else False),
        full_html=False,
        config={"responsive": True, "displaylogo": False},
    )


def panel(title: str, body: str, note: str = "") -> str:
    note_html = f"<p class=\"panel-note\">{safe_html(note)}</p>" if note else ""
    return f"""
    <section class="panel">
      <h2>{safe_html(title)}</h2>
      {note_html}
      {body}
    </section>
    """


def empty_panel(title: str, message: str) -> str:
    return panel(title, f"<div class=\"empty-state\">{safe_html(message)}</div>")


def make_activity_type_chart(data: Dict[str, Any]) -> Optional[str]:
    activities = data.get("activities", pd.DataFrame())
    if has_columns(activities, ["activity_type"]):
        counts = activities["activity_type"].fillna("unknown").astype(str).value_counts().reset_index()
        counts.columns = ["activity_type", "count"]
    else:
        summary = first_row(data.get("course_summary", pd.DataFrame()))
        rows = []
        for key, value in summary.items():
            if key.startswith("activity_type_") and key.endswith("_from_xml"):
                activity_type = key.replace("activity_type_", "").replace("_from_xml", "")
                rows.append({"activity_type": activity_type, "count": value})
        counts = pd.DataFrame(rows)
    if counts.empty:
        return None
    counts["count"] = pd.to_numeric(counts["count"], errors="coerce").fillna(0)
    counts = counts.sort_values("count", ascending=True)
    fig = px.bar(
        counts,
        x="count",
        y="activity_type",
        orientation="h",
        title="Activity types",
        labels={"count": "Count", "activity_type": "Activity type"},
        text="count",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    return fig_to_div(fig, include_plotlyjs=True)


def make_section_activity_chart(data: Dict[str, Any]) -> Optional[str]:
    sections = data.get("sections", pd.DataFrame())
    if not has_columns(sections, ["section_number", "section_name", "activity_count_from_sequence"]):
        return None
    df = sections.copy()
    df["activity_count_from_sequence"] = numeric_series(df, "activity_count_from_sequence")
    df["section_label"] = df.apply(lambda r: f"{r.get('section_number', '')}. {shorten(r.get('section_name', ''), 45)}", axis=1)
    df = df.sort_values("section_number", ascending=True)
    fig = px.bar(
        df,
        x="section_label",
        y="activity_count_from_sequence",
        title="Activities per section",
        labels={"section_label": "Section", "activity_count_from_sequence": "Activities"},
        hover_data={"section_name": True, "visible": True, "section_label": False},
    )
    fig.update_layout(xaxis_tickangle=-35, height=500)
    return fig_to_div(fig)


def make_section_breakdown_chart(data: Dict[str, Any]) -> Optional[str]:
    breakdown = data.get("section_activity_breakdown", pd.DataFrame())
    if breakdown.empty or "section_number" not in breakdown.columns:
        return None
    activity_cols = [c for c in breakdown.columns if c.startswith("activity_type_")]
    if not activity_cols:
        return None
    rows = []
    for _, row in breakdown.iterrows():
        section_label = f"{row.get('section_number', '')}. {shorten(row.get('section_name', ''), 40)}"
        for col in activity_cols:
            count = pd.to_numeric(row.get(col, 0), errors="coerce")
            if pd.notna(count) and count > 0:
                rows.append({
                    "section": section_label,
                    "activity_type": col.replace("activity_type_", ""),
                    "count": int(count),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    fig = px.bar(
        df,
        x="section",
        y="count",
        color="activity_type",
        title="Activity mix by section",
        labels={"section": "Section", "count": "Activities", "activity_type": "Activity type"},
    )
    fig.update_layout(barmode="stack", xaxis_tickangle=-35, height=540)
    return fig_to_div(fig)


def make_book_chart(data: Dict[str, Any]) -> Optional[str]:
    books = data.get("book_inventory", pd.DataFrame())
    if not has_columns(books, ["activity_name", "book_chapter_count_from_xml"]):
        return None
    df = books.copy()
    df["book_chapter_count_from_xml"] = numeric_series(df, "book_chapter_count_from_xml")
    df["xml_text_word_count_estimate"] = numeric_series(df, "xml_text_word_count_estimate")
    df = df.sort_values("book_chapter_count_from_xml", ascending=False).head(25)
    if df.empty or df["book_chapter_count_from_xml"].sum() == 0:
        return None
    df["activity_label"] = df["activity_name"].map(lambda x: shorten(x, 60))
    fig = px.bar(
        df.sort_values("book_chapter_count_from_xml", ascending=True),
        x="book_chapter_count_from_xml",
        y="activity_label",
        orientation="h",
        title="Top Moodle Books by chapter count",
        labels={"book_chapter_count_from_xml": "Chapters", "activity_label": "Book"},
        hover_data={"activity_name": True, "xml_text_word_count_estimate": True, "visible": True},
    )
    fig.update_layout(height=max(430, min(850, 28 * len(df) + 120)))
    return fig_to_div(fig)


def make_external_domain_chart(data: Dict[str, Any]) -> Optional[str]:
    domains = data.get("external_domain_inventory", pd.DataFrame())
    if not has_columns(domains, ["domain", "reference_count_in_xml"]):
        return None
    df = domains.copy()
    df["reference_count_in_xml"] = numeric_series(df, "reference_count_in_xml")
    df = df[df["reference_count_in_xml"] > 0].sort_values("reference_count_in_xml", ascending=True).tail(20)
    if df.empty:
        return None
    fig = px.bar(
        df,
        x="reference_count_in_xml",
        y="domain",
        orientation="h",
        title="External domains referenced in XML",
        labels={"reference_count_in_xml": "References", "domain": "Domain"},
        text="reference_count_in_xml",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    return fig_to_div(fig)


def make_external_dependency_chart(data: Dict[str, Any]) -> Optional[str]:
    deps = data.get("external_dependency_inventory", pd.DataFrame())
    required = ["activity_name", "xml_external_link_count", "xml_iframe_count", "xml_webcal_link_count", "xml_panopto_link_count"]
    if not has_columns(deps, required):
        return None
    df = deps.copy()
    for col in required[1:]:
        df[col] = numeric_series(df, col)
    df["total_dependency_indicators"] = df[required[1:]].sum(axis=1)
    df = df[df["total_dependency_indicators"] > 0].sort_values("total_dependency_indicators", ascending=False).head(20)
    if df.empty:
        return None
    df["activity_label"] = df["activity_name"].map(lambda x: shorten(x, 55))
    fig = px.bar(
        df.sort_values("total_dependency_indicators", ascending=True),
        x=["xml_external_link_count", "xml_iframe_count", "xml_webcal_link_count", "xml_panopto_link_count"],
        y="activity_label",
        orientation="h",
        title="Activities with the most external dependency indicators",
        labels={"value": "Count", "activity_label": "Activity", "variable": "Indicator"},
        hover_data={"activity_name": True, "activity_type": True, "section_name": True},
    )
    fig.update_layout(height=max(430, min(750, 28 * len(df) + 120)))
    return fig_to_div(fig)


def make_file_extension_chart(data: Dict[str, Any]) -> Optional[str]:
    exts = data.get("file_extension_inventory", pd.DataFrame())
    if not has_columns(exts, ["extension", "file_count"]):
        return None
    df = exts.copy()
    df["file_count"] = numeric_series(df, "file_count")
    df = df[df["file_count"] > 0].sort_values("file_count", ascending=True)
    if df.empty:
        return None
    fig = px.bar(
        df,
        x="file_count",
        y="extension",
        orientation="h",
        title="File extensions",
        labels={"file_count": "Files", "extension": "Extension"},
        text="file_count",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    return fig_to_div(fig)


def make_largest_files_table(data: Dict[str, Any], limit: int = 15) -> Optional[str]:
    largest = data.get("largest_files", pd.DataFrame())
    required = ["filename_from_xml", "filesize_from_xml_mb", "extension_from_filename", "mimetype_from_xml"]
    if not has_columns(largest, required):
        return None
    df = largest.copy()
    df["filesize_from_xml_mb"] = numeric_series(df, "filesize_from_xml_mb")
    df = df.sort_values("filesize_from_xml_mb", ascending=False).head(limit)
    if df.empty:
        return None
    rows = []
    for _, row in df.iterrows():
        rows.append(
            "<tr>"
            f"<td>{safe_html(shorten(row.get('filename_from_xml', ''), 90))}</td>"
            f"<td class=\"num\">{float(row.get('filesize_from_xml_mb', 0)):,.3f}</td>"
            f"<td>{safe_html(row.get('extension_from_filename', ''))}</td>"
            f"<td>{safe_html(row.get('mimetype_from_xml', ''))}</td>"
            "</tr>"
        )
    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr><th>Filename</th><th>MB</th><th>Ext.</th><th>MIME type</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def make_age_chart(data: Dict[str, Any]) -> Optional[str]:
    age = data.get("activity_age_summary", pd.DataFrame())
    if has_columns(age, ["activity_age_band", "activity_count"]):
        df = age.copy()
        df["activity_count"] = numeric_series(df, "activity_count")
        order = [
            "modified_within_1_year",
            "modified_1_to_2_years_ago",
            "modified_2_to_3_years_ago",
            "modified_3_to_5_years_ago",
            "modified_more_than_5_years_ago",
            "future-dated",
            "unknown",
        ]
        df["sort_order"] = df["activity_age_band"].map(lambda x: order.index(x) if x in order else len(order))
        df = df.sort_values("sort_order")
    else:
        activities = data.get("activities", pd.DataFrame())
        if not has_columns(activities, ["activity_age_band"]):
            return None
        df = activities["activity_age_band"].fillna("unknown").value_counts().reset_index()
        df.columns = ["activity_age_band", "activity_count"]
    if df.empty:
        return None
    fig = px.bar(
        df,
        x="activity_age_band",
        y="activity_count",
        title="Activity age bands",
        labels={"activity_age_band": "Age band", "activity_count": "Activities"},
        text="activity_count",
    )
    fig.update_layout(xaxis_tickangle=-25)
    fig.update_traces(textposition="outside", cliponaxis=False)
    return fig_to_div(fig)


def make_modification_year_chart(data: Dict[str, Any]) -> Optional[str]:
    years = data.get("modification_year_summary", pd.DataFrame())
    if has_columns(years, ["modified_year_from_xml", "activity_count"]):
        df = years.copy()
        df["activity_count"] = numeric_series(df, "activity_count")
    else:
        activities = data.get("activities", pd.DataFrame())
        if not has_columns(activities, ["modified_year_from_xml"]):
            return None
        df = activities["modified_year_from_xml"].fillna("unknown").astype(str).value_counts().reset_index()
        df.columns = ["modified_year_from_xml", "activity_count"]
    if df.empty:
        return None
    df["modified_year_from_xml"] = df["modified_year_from_xml"].astype(str)
    df = df.sort_values("modified_year_from_xml")
    fig = px.line(
        df,
        x="modified_year_from_xml",
        y="activity_count",
        markers=True,
        title="Activities by modification year",
        labels={"modified_year_from_xml": "Modified year", "activity_count": "Activities"},
    )
    return fig_to_div(fig)


def make_hidden_content_chart(data: Dict[str, Any]) -> Optional[str]:
    hidden = data.get("hidden_content_summary", pd.DataFrame())
    if not has_columns(hidden, ["activity_type", "hidden_activity_count"]):
        return None
    df = hidden.copy()
    df["hidden_activity_count"] = numeric_series(df, "hidden_activity_count")
    df = df[df["hidden_activity_count"] > 0].sort_values("hidden_activity_count", ascending=True)
    if df.empty:
        return None
    fig = px.bar(
        df,
        x="hidden_activity_count",
        y="activity_type",
        orientation="h",
        title="Hidden activities by type",
        labels={"hidden_activity_count": "Hidden activities", "activity_type": "Activity type"},
        text="hidden_activity_count",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    return fig_to_div(fig)


def make_duplicate_table(data: Dict[str, Any]) -> Optional[str]:
    dup = data.get("duplicate_activity_inventory", pd.DataFrame())
    if not has_columns(dup, ["activity_name", "occurrence_count"]):
        return None
    df = dup.copy()
    df["occurrence_count"] = numeric_series(df, "occurrence_count")
    df = df[df["occurrence_count"] > 1].sort_values("occurrence_count", ascending=False)
    if df.empty:
        return None
    rows = []
    for _, row in df.iterrows():
        rows.append(
            "<tr>"
            f"<td>{safe_html(row.get('activity_name', ''))}</td>"
            f"<td class=\"num\">{int(row.get('occurrence_count', 0))}</td>"
            "</tr>"
        )
    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr><th>Activity name</th><th>Occurrences</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def make_hidden_activity_table(data: Dict[str, Any], limit: int = 25) -> Optional[str]:
    hidden = data.get("hidden_activity_inventory", pd.DataFrame())
    required = ["activity_name", "activity_type", "section_number", "section_name", "last_modified_from_xml"]
    if not has_columns(hidden, required):
        return None
    df = hidden.copy().head(limit)
    if df.empty:
        return None
    rows = []
    for _, row in df.iterrows():
        rows.append(
            "<tr>"
            f"<td>{safe_html(row.get('section_number', ''))}</td>"
            f"<td>{safe_html(shorten(row.get('section_name', ''), 45))}</td>"
            f"<td>{safe_html(row.get('activity_type', ''))}</td>"
            f"<td>{safe_html(shorten(row.get('activity_name', ''), 70))}</td>"
            f"<td>{safe_html(row.get('last_modified_from_xml', ''))}</td>"
            "</tr>"
        )
    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr><th>Section</th><th>Section name</th><th>Type</th><th>Activity</th><th>Modified</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def make_data_quality_notes(data: Dict[str, Any]) -> str:
    notes = [
        "This dashboard uses Moodle backup XML metadata only.",
        "Uploaded binary file contents are not opened or scanned.",
        "Counts for links, iframes, image tags and word estimates refer only to HTML-like content stored directly inside XML fields.",
        "No pedagogic judgement, quality score, risk score or severity score is generated.",
    ]
    audit_data = data.get("audit_data", {})
    if isinstance(audit_data, dict):
        scope = audit_data.get("audit_scope", {}) or {}
        if scope.get("scope"):
            notes.insert(0, f"Audit scope: {scope.get('scope')}.")
    return "<ul>" + "".join(f"<li>{safe_html(note)}</li>" for note in notes) + "</ul>"


def build_dashboard_html(input_folder: Path, data: Dict[str, Any]) -> str:
    title = course_title(data, input_folder)
    shortname = value_from_sources(data, "course_shortname_from_xml", "") or value_from_sources(data, "shortname", "")
    generated_from = value_from_sources(data, "source_backup", "")
    archive_type = value_from_sources(data, "archive_type", "")

    panels: List[str] = []

    activity_chart = make_activity_type_chart(data)
    if activity_chart:
        panels.append(panel("Activity type overview", activity_chart, "Reads activity types dynamically, so new Moodle activity types appear automatically."))
    else:
        panels.append(empty_panel("Activity type overview", "No activity type data detected."))

    section_chart = make_section_activity_chart(data)
    if section_chart:
        panels.append(panel("Course structure", section_chart, "Shows the distribution of activities across Moodle sections."))

    section_breakdown_chart = make_section_breakdown_chart(data)
    if section_breakdown_chart:
        panels.append(panel("Section activity mix", section_breakdown_chart, "Stacked view of activity types per section where breakdown columns are present."))

    book_chart = make_book_chart(data)
    if book_chart:
        panels.append(panel("Moodle Book footprint", book_chart, "Only shown when book_inventory.csv contains Moodle Book records."))
    else:
        panels.append(empty_panel("Moodle Book footprint", "No Moodle Book inventory detected for this course."))

    external_domain_chart = make_external_domain_chart(data)
    external_dependency_chart = make_external_dependency_chart(data)
    if external_domain_chart or external_dependency_chart:
        body = "".join([x for x in [external_domain_chart, external_dependency_chart] if x])
        panels.append(panel("External dependencies", body, "Based on external links, iframe tags, webCAL links, Panopto references and LTI indicators in XML metadata."))
    else:
        panels.append(empty_panel("External dependencies", "No external dependency indicators detected in XML metadata."))

    file_extension_chart = make_file_extension_chart(data)
    largest_files_table = make_largest_files_table(data)
    if file_extension_chart or largest_files_table:
        panels.append(panel("File footprint", "".join([x for x in [file_extension_chart, largest_files_table] if x]), "Uses files.xml metadata. File contents are not opened."))
    else:
        panels.append(empty_panel("File footprint", "No file metadata detected."))

    age_chart = make_age_chart(data)
    modification_chart = make_modification_year_chart(data)
    if age_chart or modification_chart:
        panels.append(panel("Maintenance indicators", "".join([x for x in [age_chart, modification_chart] if x]), "Based on activity modification timestamps available in XML."))

    hidden_chart = make_hidden_content_chart(data)
    hidden_table = make_hidden_activity_table(data)
    duplicate_table = make_duplicate_table(data)
    if hidden_chart or hidden_table or duplicate_table:
        panels.append(panel("Hidden and duplicate content", "".join([x for x in [hidden_chart, hidden_table, duplicate_table] if x]), "Shows hidden activities and duplicate activity names where detected."))
    else:
        panels.append(empty_panel("Hidden and duplicate content", "No hidden activities or duplicate activity names detected."))

    panels.append(panel("Scope notes", make_data_quality_notes(data)))

    header_meta = []
    if shortname:
        header_meta.append(f"Short name: {safe_html(shortname)}")
    if generated_from:
        header_meta.append(f"Source backup: {safe_html(generated_from)}")
    if archive_type:
        header_meta.append(f"Archive type: {safe_html(archive_type)}")
    header_meta.append(f"Input folder: {safe_html(str(input_folder))}")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Moodle Audit Dashboard - {safe_html(title)}</title>
  <style>
    :root {{
      --bg: #f6f7fb;
      --card: #ffffff;
      --text: #172033;
      --muted: #5f6b7a;
      --border: #d9dee8;
      --accent: #3157d5;
    }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    header {{
      padding: 28px 32px 18px;
      background: var(--card);
      border-bottom: 1px solid var(--border);
    }}
    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px 24px 48px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.75rem;
      line-height: 1.2;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 1.2rem;
    }}
    .meta {{
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin: 18px 0 8px;
    }}
    .metric-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 0.82rem;
      margin-bottom: 4px;
    }}
    .metric-value {{
      font-size: 1.55rem;
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .metric-note {{
      color: var(--muted);
      font-size: 0.78rem;
    }}
    .panel {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 18px;
      margin: 18px 0;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    .panel-note {{
      color: var(--muted);
      margin-top: -4px;
      margin-bottom: 14px;
      font-size: 0.92rem;
    }}
    .empty-state {{
      border: 1px dashed var(--border);
      color: var(--muted);
      padding: 18px;
      border-radius: 12px;
      background: #fafbfe;
    }}
    .table-wrap {{
      overflow-x: auto;
      margin-top: 14px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
    }}
    th, td {{
      text-align: left;
      border-bottom: 1px solid var(--border);
      padding: 8px 10px;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 700;
      background: #fafbfe;
    }}
    .num {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    ul {{
      margin: 0;
      padding-left: 1.2rem;
    }}
    @media (max-width: 760px) {{
      header {{ padding: 22px 18px 14px; }}
      main {{ padding: 16px 12px 36px; }}
      .panel {{ padding: 14px; }}
      h1 {{ font-size: 1.35rem; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Moodle Audit Dashboard</h1>
    <div class="meta"><strong>{safe_html(title)}</strong></div>
    <div class="meta">{' · '.join(header_meta)}</div>
  </header>
  <main>
    <section class="metric-grid">
      {make_metric_cards(data)}
    </section>
    {''.join(panels)}
  </main>
</body>
</html>
"""


def generate_dashboard(input_folder: Path, output_path: Optional[Path] = None) -> Path:
    input_folder = input_folder.expanduser().resolve()
    if not input_folder.exists() or not input_folder.is_dir():
        raise FileNotFoundError(f"Input folder not found or not a directory: {input_folder}")

    data = load_audit_folder(input_folder)
    if all((isinstance(v, pd.DataFrame) and v.empty) or (isinstance(v, dict) and not v) for v in data.values()):
        raise FileNotFoundError(f"No recognised Moodle audit output files found in: {input_folder}")

    if output_path is None:
        output_path = input_folder / "dashboard.html"
    else:
        output_path = output_path.expanduser().resolve()
        if output_path.is_dir():
            output_path = output_path / "dashboard.html"

    html_text = build_dashboard_html(input_folder, data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


def find_audit_output_folders(root: Path, recursive: bool = False) -> List[Path]:
    root = root.expanduser().resolve()
    candidates: List[Path] = []
    folders = [p for p in (root.rglob("*") if recursive else root.iterdir()) if p.is_dir()]
    if any((root / filename).exists() for filename in AUDIT_FILES.values()):
        candidates.append(root)
    for folder in folders:
        if any((folder / filename).exists() for filename in AUDIT_FILES.values()):
            candidates.append(folder)
    return sorted(set(candidates))


def run_batch(root: Path, recursive: bool = False) -> None:
    folders = find_audit_output_folders(root, recursive=recursive)
    if not folders:
        raise FileNotFoundError(f"No audit output folders found in: {root}")
    successes = 0
    failures = 0
    for folder in folders:
        try:
            out = generate_dashboard(folder)
            successes += 1
            print(f"Generated: {out}")
        except Exception as exc:
            failures += 1
            print(f"Failed: {folder} — {exc}")
    print(f"Batch dashboard generation complete. Successes: {successes}; failures: {failures}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Plotly HTML dashboard from Moodle audit CSV/JSON outputs.")
    parser.add_argument("input", help="Path to an audit output folder, or parent folder when --batch is used.")
    parser.add_argument("--output", "-o", help="Output HTML path. Defaults to dashboard.html inside the input folder.")
    parser.add_argument("--batch", action="store_true", help="Generate dashboard.html for each audit output folder inside the input folder.")
    parser.add_argument("--recursive", action="store_true", help="In batch mode, search subfolders recursively.")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()

    if args.batch:
        run_batch(input_path, recursive=args.recursive)
        return

    output_path = Path(args.output).expanduser().resolve() if args.output else None
    out = generate_dashboard(input_path, output_path=output_path)
    print(f"Dashboard generated: {out}")


if __name__ == "__main__":
    main()
