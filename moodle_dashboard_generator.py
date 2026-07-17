#!/usr/bin/env python3
"""
Moodle Audit Plotly Dashboard Generator
======================================

Generates a standalone HTML dashboard from the CSV/JSON outputs produced by
moodle_mbz_course_auditor.py.

This script is intentionally a separate visualisation layer. It does not parse
Moodle .mbz files and it does not change the audit data. It reads an existing
audit output folder and creates dashboard.html. It can optionally enrich the
Moodle Book section with one Moodle access/non-access CSV.

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
import re
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


# ============================================================================
# OPTIONAL DATA INTEGRATION: MOODLE BOOK ACCESS / NON-ACCESS REPORT
# ============================================================================
# The dashboard works normally from the audit output files alone.
#
# If exactly one CSV is present in:
#
#   moodle_data_imports/
#       content_access_distribution/
#           books/
#
# the script will try to match each CSV row to the corresponding Moodle Book
# in book_inventory.csv and add access, non-access and reach information.
#
# The audit output folder and moodle_data_imports folder should be siblings:
#
#   project/
#   ├── moodle_audit_output/
#   └── moodle_data_imports/
#       └── content_access_distribution/
#           └── books/
#               └── any_filename.csv
#
# Missing, empty, ambiguous or incompatible optional data will never stop the
# dashboard from being generated. The original audit-only Book chart is used.
# ============================================================================
BOOK_ACCESS_IMPORT_RELATIVE_PATH = Path(
    "moodle_data_imports/content_access_distribution/books"
)



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



# ============================================================================
# OPTIONAL DATA INTEGRATION HELPERS: MOODLE BOOK ACCESS / NON-ACCESS REPORT
# ============================================================================

def normalise_heading(value: Any) -> str:
    """Normalise a CSV heading so small naming differences can be tolerated."""
    text = clean_label(value).strip().lower().replace("\u00a0", " ")
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalise_book_title(value: Any) -> str:
    """
    Normalise a Moodle Book title for matching.

    Meaningful suffixes such as '(scr)' are retained so similarly named Books
    are not silently combined.
    """
    text = clean_label(value).strip().lower().replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def find_column(
    df: pd.DataFrame,
    exact_aliases: Iterable[str],
    required_tokens: Optional[Iterable[str]] = None,
    forbidden_tokens: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Find a column by exact aliases first, then by required/forbidden tokens."""
    if df.empty:
        return None

    aliases = {normalise_heading(alias) for alias in exact_aliases}
    headings = {column: normalise_heading(column) for column in df.columns}

    for column, heading in headings.items():
        if heading in aliases:
            return column

    if required_tokens:
        required = [normalise_heading(token) for token in required_tokens]
        forbidden = [
            normalise_heading(token) for token in (forbidden_tokens or [])
        ]
        for column, heading in headings.items():
            if all(token in heading for token in required) and not any(
                token in heading for token in forbidden
            ):
                return column

    return None


def read_optional_moodle_csv(path: Path) -> pd.DataFrame:
    """Read a Moodle CSV while tolerating common encodings and delimiters."""
    attempts = [
        {"encoding": "utf-8-sig", "sep": None, "engine": "python"},
        {"encoding": "utf-8", "sep": None, "engine": "python"},
        {"encoding": "cp1252", "sep": None, "engine": "python"},
    ]
    last_error: Optional[Exception] = None

    for options in attempts:
        try:
            return pd.read_csv(path, **options)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Unable to read {path.name}: {last_error}")


def locate_optional_book_access_csv(
    audit_output_folder: Path,
) -> Tuple[Optional[Path], List[str]]:
    """
    Locate one optional Books access CSV.

    The import folder is resolved from the parent of the audit output folder.
    """
    messages: List[str] = []
    project_root = audit_output_folder.resolve().parent
    import_folder = project_root / BOOK_ACCESS_IMPORT_RELATIVE_PATH

    if not import_folder.exists():
        return None, messages

    csv_files = sorted(
        path
        for path in import_folder.glob("*.csv")
        if path.is_file() and path.stat().st_size > 0
    )

    if not csv_files:
        return None, messages

    if len(csv_files) > 1:
        messages.append(
            "More than one CSV was found in "
            f"{import_folder}. Optional Book access integration was skipped. "
            "Leave exactly one CSV in this folder."
        )
        return None, messages

    return csv_files[0], messages


def standardise_book_access_report(
    raw: pd.DataFrame,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Convert the optional Moodle export to a standard Book-level schema.

    The expected logical fields are:
      - Book title
      - Access count
      - Non-access count
      - Reach percentage (optional because it can be calculated)

    Moodle report column names vary, so aliases are deliberately flexible.
    """
    messages: List[str] = []
    if raw.empty:
        return pd.DataFrame(), messages

    title_column = find_column(
        raw,
        [
            "book",
            "book name",
            "activity",
            "activity name",
            "content",
            "contents",
            "content name",
            "resource",
            "resource name",
            "name",
            "title",
        ],
    )

    no_access_column = find_column(
        raw,
        [
            "no access",
            "not accessed",
            "non access",
            "non-access",
            "without access",
            "students without access",
            "users without access",
            "number not accessed",
        ],
        required_tokens=["access"],
        forbidden_tokens=[],
    )
    if no_access_column is not None:
        heading = normalise_heading(no_access_column)
        if not any(token in heading for token in ["no", "not", "non", "without"]):
            no_access_column = None

    access_column = find_column(
        raw,
        [
            "access",
            "accessed",
            "with access",
            "students with access",
            "users with access",
            "number accessed",
            "access count",
        ],
        required_tokens=["access"],
        forbidden_tokens=["no", "not", "non", "without", "%", "percent", "percentage"],
    )

    reach_column = find_column(
        raw,
        [
            "reach",
            "reach %",
            "access %",
            "access percentage",
            "percentage accessed",
            "percent accessed",
        ],
    )

    if title_column is None:
        messages.append(
            "The optional Books CSV was found, but no Book-title column could "
            "be identified. Audit-only mode was used."
        )
        return pd.DataFrame(), messages

    if access_column is None and no_access_column is None and reach_column is None:
        messages.append(
            "The optional Books CSV was found, but no access, non-access or "
            "reach columns could be identified. Audit-only mode was used."
        )
        return pd.DataFrame(), messages

    result = pd.DataFrame()
    result["access_book_title"] = raw[title_column].map(clean_label).str.strip()
    result["normalised_book_title"] = result["access_book_title"].map(
        normalise_book_title
    )

    def parse_number(column: Optional[str]) -> pd.Series:
        if column is None:
            return pd.Series([pd.NA] * len(raw), index=raw.index, dtype="Float64")
        cleaned = (
            raw[column]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.strip()
        )
        return pd.to_numeric(cleaned, errors="coerce").astype("Float64")

    result["access_count"] = parse_number(access_column)
    result["no_access_count"] = parse_number(no_access_column)
    result["reach_percent"] = parse_number(reach_column)

    denominator = result["access_count"] + result["no_access_count"]
    calculated_reach = (
        result["access_count"] / denominator.where(denominator > 0)
    ) * 100
    result["reach_percent"] = result["reach_percent"].fillna(calculated_reach)

    result = result[result["normalised_book_title"] != ""].copy()

    duplicate_mask = result["normalised_book_title"].duplicated(keep=False)
    if duplicate_mask.any():
        duplicate_names = (
            result.loc[duplicate_mask, "access_book_title"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
        messages.append(
            "Duplicate Book titles were found in the optional CSV and excluded "
            "from automatic matching: "
            + "; ".join(duplicate_names[:10])
        )
        result = result.loc[~duplicate_mask].copy()

    return result, messages


def merge_book_access_data(
    books: pd.DataFrame,
    access_data: pd.DataFrame,
) -> Tuple[pd.DataFrame, List[str]]:
    """Left-join optional access data onto the audit Book inventory."""
    messages: List[str] = []
    merged_source = books.copy()
    merged_source["normalised_book_title"] = merged_source["activity_name"].map(
        normalise_book_title
    )

    if access_data.empty:
        merged_source["access_book_title"] = pd.NA
        merged_source["access_count"] = pd.NA
        merged_source["no_access_count"] = pd.NA
        merged_source["reach_percent"] = pd.NA
        return merged_source, messages

    duplicate_audit_mask = merged_source["normalised_book_title"].duplicated(
        keep=False
    )
    if duplicate_audit_mask.any():
        duplicate_keys = set(
            merged_source.loc[
                duplicate_audit_mask, "normalised_book_title"
            ].tolist()
        )
        duplicate_names = (
            merged_source.loc[duplicate_audit_mask, "activity_name"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
        messages.append(
            "Some audit Books share the same normalised title and were excluded "
            "from automatic access matching: "
            + "; ".join(duplicate_names[:10])
        )
        access_data = access_data[
            ~access_data["normalised_book_title"].isin(duplicate_keys)
        ].copy()

    merged = merged_source.merge(
        access_data[
            [
                "normalised_book_title",
                "access_book_title",
                "access_count",
                "no_access_count",
                "reach_percent",
            ]
        ],
        on="normalised_book_title",
        how="left",
        validate="many_to_one",
    )

    matched_count = int(merged["access_book_title"].notna().sum())
    unmatched_count = int(
        (~access_data["normalised_book_title"].isin(
            set(merged_source["normalised_book_title"])
        )).sum()
    )

    if matched_count == 0:
        messages.append(
            "The optional Books CSV was loaded, but no Book titles matched "
            "book_inventory.csv. Audit-only values are still displayed."
        )
    if unmatched_count:
        messages.append(
            f"{unmatched_count} row(s) in the optional Books CSV did not match "
            "a Book in book_inventory.csv."
        )

    return merged, messages


def integration_messages_html(messages: Iterable[str]) -> str:
    """Render optional integration messages without treating them as failures."""
    items = [message for message in messages if message]
    if not items:
        return ""
    return (
        '<div class="integration-note">'
        "<strong>Optional Book data integration</strong>"
        "<ul>"
        + "".join(f"<li>{safe_html(message)}</li>" for message in items)
        + "</ul></div>"
    )

def make_book_chart(
    data: Dict[str, Any],
    audit_output_folder: Path,
) -> Tuple[Optional[str], List[str]]:
    """
    Build the Moodle Book visualisation.

    Audit-only mode:
        Shows the original horizontal page-count chart.

    Optional access-data mode:
        Shows four parallel horizontal charts using exactly the same Book order:
          1. Moodle Book pages
          2. Students with access
          3. Students with no access
          4. Reach percentage

    Hidden Books are excluded from these charts but remain available in the
    later Hidden and duplicate content section. The access data is never
    overlaid on the page bars, and no duplicate summary table is produced.
    """
    messages: List[str] = []
    books = data.get("book_inventory", pd.DataFrame())
    if books.empty or "activity_name" not in books.columns:
        return None, messages

    if "book_page_count_from_xml" in books.columns:
        page_count_column = "book_page_count_from_xml"
    elif "book_chapter_count_from_xml" in books.columns:
        page_count_column = "book_chapter_count_from_xml"
    else:
        return None, messages

    df = books.copy()
    df["moodle_book_page_count"] = numeric_series(df, page_count_column)
    df["xml_text_word_count_estimate"] = numeric_series(
        df, "xml_text_word_count_estimate"
    )

    if "book_top_level_chapter_count_from_xml" in df.columns:
        df["book_top_level_chapter_count_from_xml"] = numeric_series(
            df, "book_top_level_chapter_count_from_xml"
        )

    if "book_subchapter_count_from_xml" in df.columns:
        df["book_subchapter_count_from_xml"] = numeric_series(
            df, "book_subchapter_count_from_xml"
        )

    # ========================================================================
    # VISIBLE-BOOK FILTER FOR THE MAIN MOODLE BOOK VISUALISATION
    # ========================================================================
    # Hidden Books are excluded only from this local visualisation dataset.
    # They remain available later in the dashboard through:
    #   - hidden_content_summary.csv
    #   - hidden_activity_inventory.csv
    #   - the overall Hidden activities metric
    #
    # Moodle CSV values may appear as 0, 0.0, False, "false", "hidden", etc.,
    # so both numeric and text representations are handled.
    # ========================================================================
    hidden_book_count = 0

    if "visible" in df.columns:
        visible_numeric = pd.to_numeric(df["visible"], errors="coerce")
        visible_text = (
            df["visible"]
            .astype(str)
            .str.strip()
            .str.lower()
        )

        hidden_mask = visible_numeric.eq(0) | visible_text.isin(
            ["0", "0.0", "false", "no", "hidden"]
        )

        hidden_book_count = int(hidden_mask.sum())
        df = df.loc[~hidden_mask].copy()

        if hidden_book_count:
            messages.append(
                f"{hidden_book_count} hidden Moodle Book"
                f"{'s were' if hidden_book_count != 1 else ' was'} excluded "
                "from the Moodle Book footprint and access charts. "
                "Hidden Books remain available in the later Hidden and "
                "duplicate content section."
            )

    # ========================================================================
    # OPTIONAL DATA INTEGRATION: LOAD AND MATCH THE BOOKS ACCESS CSV
    # ========================================================================
    optional_csv, locate_messages = locate_optional_book_access_csv(
        audit_output_folder
    )
    messages.extend(locate_messages)

    access_data = pd.DataFrame()
    if optional_csv is not None:
        try:
            raw_access = read_optional_moodle_csv(optional_csv)
            access_data, standardise_messages = standardise_book_access_report(
                raw_access
            )
            messages.extend(standardise_messages)
        except Exception as exc:
            messages.append(
                f"Could not load optional Books CSV {optional_csv.name}: {exc}. "
                "Audit-only mode was used."
            )

    df, merge_messages = merge_book_access_data(df, access_data)
    messages.extend(merge_messages)

    # Keep one stable ordering for every parallel chart.
    df = df.sort_values("moodle_book_page_count", ascending=False).head(25)
    if df.empty or df["moodle_book_page_count"].sum() == 0:
        return None, messages

    df["activity_label"] = df["activity_name"].map(
        lambda value: shorten(value, 58)
    )
    ordered = df.sort_values("moodle_book_page_count", ascending=True).copy()
    has_access_data = ordered["access_book_title"].notna().any()

    # ------------------------------------------------------------------------
    # AUDIT-ONLY MODE: preserve the original horizontal Book page chart.
    # ------------------------------------------------------------------------
    if not has_access_data:
        hover_data: Dict[str, Any] = {
            "activity_name": True,
            "moodle_book_page_count": True,
            "xml_text_word_count_estimate": True,
            "visible": True,
            "activity_label": False,
        }

        if "book_top_level_chapter_count_from_xml" in ordered.columns:
            hover_data["book_top_level_chapter_count_from_xml"] = True

        if "book_subchapter_count_from_xml" in ordered.columns:
            hover_data["book_subchapter_count_from_xml"] = True

        fig = px.bar(
            ordered,
            x="moodle_book_page_count",
            y="activity_label",
            orientation="h",
            title="Top Moodle Books by page count",
            labels={
                "moodle_book_page_count": "Moodle Book pages",
                "activity_label": "Book",
                "xml_text_word_count_estimate": "Estimated XML words",
                "book_top_level_chapter_count_from_xml": "Top-level chapters",
                "book_subchapter_count_from_xml": "Subchapters",
                "visible": "Visible",
            },
            hover_data=hover_data,
            text="moodle_book_page_count",
        )
        fig.update_traces(
            texttemplate="%{text:,.0f} pages",
            textposition="outside",
            cliponaxis=False,
        )
        fig.update_layout(
            height=max(460, min(900, 34 * len(ordered) + 150)),
            xaxis_title="Moodle Book pages",
            yaxis_title="Book",
        )
        return fig_to_div(fig), messages

    # ------------------------------------------------------------------------
    # OPTIONAL ENRICHED MODE: four aligned parallel horizontal charts.
    # ------------------------------------------------------------------------
    matched = ordered[ordered["access_book_title"].notna()].copy()

    for column in ["access_count", "no_access_count", "reach_percent"]:
        matched[column] = pd.to_numeric(matched[column], errors="coerce")

    # Retain the audit ordering while excluding unmatched Books from the access
    # panels. Unmatched Books remain visible in the pages panel with blanks in
    # the other panels.
    ordered["access_count"] = pd.to_numeric(
        ordered["access_count"], errors="coerce"
    )
    ordered["no_access_count"] = pd.to_numeric(
        ordered["no_access_count"], errors="coerce"
    )
    ordered["reach_percent"] = pd.to_numeric(
        ordered["reach_percent"], errors="coerce"
    )

    subplot_titles = (
        "Book pages",
        "Students with access",
        "Students with no access",
        "Reach",
    )
    fig = make_subplots(
        rows=1,
        cols=4,
        shared_yaxes=True,
        horizontal_spacing=0.055,
        column_widths=[0.27, 0.25, 0.25, 0.23],
        subplot_titles=subplot_titles,
    )

    page_customdata = ordered[
        ["activity_name", "xml_text_word_count_estimate", "visible"]
    ].where(
        pd.notna(
            ordered[["activity_name", "xml_text_word_count_estimate", "visible"]]
        ),
        None,
    )

    fig.add_trace(
        go.Bar(
            x=ordered["moodle_book_page_count"],
            y=ordered["activity_label"],
            orientation="h",
            name="Pages",
            text=ordered["moodle_book_page_count"],
            texttemplate="%{text:,.0f}",
            textposition="outside",
            cliponaxis=False,
            customdata=page_customdata.to_numpy(),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Moodle Book pages: %{x:,.0f}<br>"
                "Estimated XML words: %{customdata[1]:,.0f}<br>"
                "Visible: %{customdata[2]}"
                "<extra></extra>"
            ),
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=ordered["access_count"],
            y=ordered["activity_label"],
            orientation="h",
            name="Access",
            text=ordered["access_count"],
            texttemplate="%{text:,.0f}",
            textposition="outside",
            cliponaxis=False,
            customdata=ordered[["activity_name"]].to_numpy(),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Students with access: %{x:,.0f}"
                "<extra></extra>"
            ),
            showlegend=False,
        ),
        row=1,
        col=2,
    )

    fig.add_trace(
        go.Bar(
            x=ordered["no_access_count"],
            y=ordered["activity_label"],
            orientation="h",
            name="No access",
            text=ordered["no_access_count"],
            texttemplate="%{text:,.0f}",
            textposition="outside",
            cliponaxis=False,
            customdata=ordered[["activity_name"]].to_numpy(),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Students with no access: %{x:,.0f}"
                "<extra></extra>"
            ),
            showlegend=False,
        ),
        row=1,
        col=3,
    )

    fig.add_trace(
        go.Bar(
            x=ordered["reach_percent"],
            y=ordered["activity_label"],
            orientation="h",
            name="Reach",
            text=ordered["reach_percent"],
            texttemplate="%{text:.1f}%",
            textposition="outside",
            cliponaxis=False,
            customdata=ordered[["activity_name"]].to_numpy(),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Reach: %{x:.1f}%"
                "<extra></extra>"
            ),
            showlegend=False,
        ),
        row=1,
        col=4,
    )

    fig.update_xaxes(title_text="Pages", row=1, col=1, rangemode="tozero")
    fig.update_xaxes(title_text="Students", row=1, col=2, rangemode="tozero")
    fig.update_xaxes(title_text="Students", row=1, col=3, rangemode="tozero")
    fig.update_xaxes(
        title_text="Percent",
        row=1,
        col=4,
        range=[0, 100],
        ticksuffix="%",
    )

    # Show Book labels only on the first panel. Shared y-axes keep every row
    # exactly aligned across all four visualisations.
    fig.update_yaxes(
        title_text="Book",
        showticklabels=True,
        automargin=True,
        row=1,
        col=1,
    )
    for col in (2, 3, 4):
        fig.update_yaxes(showticklabels=False, row=1, col=col)

    fig.update_layout(
        title="Moodle Book footprint and student access",
        height=max(500, min(980, 38 * len(ordered) + 180)),
        margin=dict(l=290, r=45, t=90, b=65),
        bargap=0.28,
    )

    if optional_csv is not None:
        matched_count = int(ordered["access_book_title"].notna().sum())
        messages.insert(
            0,
            f"Loaded {optional_csv.name} and matched access data to "
            f"{matched_count} of {len(ordered)} displayed Moodle Books."
        )

    return fig_to_div(fig), messages

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
        "Core dashboard measures use Moodle backup XML metadata. Optional Moodle report data is used only when explicitly detected.",
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

    # ========================================================================
    # OPTIONAL DATA INTEGRATION: MOODLE BOOK ACCESS / NON-ACCESS REPORT
    # ========================================================================
    # The normal audit-only Book chart is always available.
    # Optional access data is added only when one compatible CSV is detected.
    # ========================================================================
    book_chart, book_integration_messages = make_book_chart(
        data,
        input_folder,
    )
    if book_chart:
        book_body = book_chart
        book_body += integration_messages_html(book_integration_messages)

        panels.append(
            panel(
                "Moodle Book footprint",
                book_body,
                "Visible Moodle Books only are shown here. Moodle Book pages "
                "include both top-level chapters and subchapters. When the optional "
                "Books access CSV is available, four parallel horizontal charts use "
                "the same Book order: pages, access, non-access and reach. Hidden "
                "Books remain available in the later Hidden and duplicate content "
                "section. Older audit outputs remain supported through "
                "book_chapter_count_from_xml.",
            )
        )
    else:
        panels.append(
            empty_panel(
                "Moodle Book footprint",
                "No Moodle Book inventory detected for this course.",
            )
        )

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
    .integration-note {{
      margin-top: 14px;
      padding: 12px 14px;
      border-left: 4px solid var(--accent);
      border-radius: 8px;
      background: #f4f7ff;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .integration-note ul {{
      margin-top: 6px;
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