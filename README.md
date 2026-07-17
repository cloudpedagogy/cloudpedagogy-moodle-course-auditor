# CloudPedagogy Moodle Course Auditor

A local-first Python toolkit for auditing Moodle course backup files (`.mbz`) and turning the resulting data into structured reports and an interactive HTML dashboard.

The platform supports evidence-informed course review, quality assurance, migration planning, curriculum review, content inventories, digital education analysis and governance. It works without access to a live Moodle site.

## What the platform does

The project has two complementary components:

1. **Moodle Course Auditor**
   - Extracts factual metadata from Moodle backup XML.
   - Produces Markdown, text, CSV and JSON outputs.
   - Creates inventories of course structure, activities, Books, files, questions, hidden content and external dependencies.
2. **Interactive Dashboard Generator**
   - Reads the structured audit outputs.
   - Generates a standalone interactive Plotly dashboard (`dashboard.html`).

The auditor is deliberately conservative: it reports what can be supported by the available source data and does not assign pedagogic quality, accessibility, compliance or risk scores.

---

## Key features

### Moodle backup audit

- Audit an individual Moodle backup.
- Process multiple backups in batch mode.
- Recursively locate supported backup archives.
- Extract course and section metadata.
- Inventory activities and resources.
- Inventory Moodle Books and chapters.
- Identify hidden activities, Books and content.
- Identify possible duplicate activities.
- Detect external links, domains, iframes, webCAL and Panopto references.
- Analyse Moodle file metadata, formats and storage footprint.
- Summarise question metadata and question types.
- Summarise activity modification dates and age.
- Estimate XML-based content volume.
- Export reusable CSV, JSON, Markdown and text reports.

### Interactive data visualisation

- Course overview cards.
- Activity-type and section visualisations.
- Moodle Book inventories and chapter summaries.
- File type, size and storage-footprint visualisations.
- External dependency summaries.
- Hidden-content summaries.
- Activity-age visualisations.
- Responsive, standalone HTML output with no web server required.

The dashboard adapts to the audit data available. For example, a course without Moodle Books omits Book-specific charts while retaining the rest of the dashboard.

---

## Source data

### Moodle course backup

The auditor accepts:

- Moodle `.mbz`
- ZIP
- TAR
- TAR.GZ

The backup should include the course activities, resources and content required for the audit. The dashboard is generated from the auditor's structured outputs; it does not require a separate connection to Moodle.

---

## Recommended Moodle backup settings

Include:

- Activities and resources
- Question bank
- Content

For backups used outside their original Moodle environment, normally exclude unless specifically required:

- Enrolled users
- User files
- Activity logs
- Grades
- Other personally identifiable information

Always follow your institution's data-protection and information-governance requirements.

---

## What the auditor analyses

### Course and structure

- Course name and short name
- Course format and visibility
- Course dates and modification timestamps
- Section names, numbering and visibility
- Activity counts by section and type

### Activities and resources

- Activity type and name
- Visibility
- Completion metadata where available
- Modification dates
- Possible duplicates
- Hidden activities and content

### Moodle Books

- Book inventory
- Chapter counts
- Hidden chapters
- XML word estimates

### External dependencies

- External links and domains
- iframe usage
- webCAL references
- Panopto references

### Files and storage

- File counts
- Filename extensions
- MIME types
- Moodle file areas
- File sizes
- Largest files
- Overall course footprint

### Questions

- Question records
- Question-type distribution

### Age and content volume

- Activity modification-year summaries
- Activity-age summaries
- Structural characteristics
- XML-based word estimates

---

## Important limitations

The MBZ auditor analyses Moodle XML and file metadata. It does **not** open or interpret the internal content of uploaded files such as:

- PDFs
- Word documents
- PowerPoint files
- Images
- Audio or video
- SCORM packages
- H5P packages

The platform does not automatically:

- Judge pedagogic quality.
- Assess whether learning outcomes, activities and assessments are aligned.
- Determine whether content is accurate or current.
- Perform a full accessibility audit.
- Generate compliance ratings.
- Generate risk or severity scores.
- Prove that a learner completed or understood an activity.

XML word counts are estimates of text represented in the backup, not exact measures of learner-facing reading load. Modification dates indicate recorded Moodle changes, not necessarily the intellectual age or currency of the material.

---

## Moodle XML files used

Depending on what is present in the backup, the auditor reads metadata from files including:

```text
course/course.xml
sections/*/section.xml
activities/*/module.xml
activities/*/[activity_type].xml
files.xml
questions.xml
```

---

## Installation

```bash
git clone https://github.com/cloudpedagogy/cloudpedagogy-moodle-course-auditor.git
cd cloudpedagogy-moodle-course-auditor

python3 -m venv .venv
source .venv/bin/activate

pip install pandas plotly
```


The core auditor uses the Python standard library. Dashboard generation requires `pandas` and `plotly`.

---

## Basic usage

### Audit a Moodle backup

```bash
python3 moodle_mbz_course_auditor.py course.mbz
```

### Generate an interactive dashboard

```bash
python3 moodle_dashboard_generator.py moodle_audit_output
```

You can also pass an absolute or relative path to the audit output directory:

```bash
python3 moodle_dashboard_generator.py /path/to/moodle_audit_output
```

Open the generated `dashboard.html` in a modern web browser.

---

## Outputs

The exact set of files may vary according to the course data and processing mode.

### Auditor outputs

- `audit_report.md`
- `audit_report.txt`
- `course_summary.csv`
- `course_characteristics.csv`
- `course_footprint.csv`
- `section_activity_breakdown.csv`
- `book_inventory.csv`
- `duplicate_activity_inventory.csv`
- `hidden_content_summary.csv`
- `hidden_activity_inventory.csv`
- `external_dependency_inventory.csv`
- `external_domain_inventory.csv`
- `file_extension_inventory.csv`
- `largest_files.csv`
- `modification_year_summary.csv`
- `activity_age_summary.csv`
- `activities.csv`
- `sections.csv`
- `files.csv`
- `audit_data.json`

Batch processing additionally creates:

- `combined_course_summary.csv`
- `batch_run_log.csv`

### Dashboard output

- `dashboard.html`

This is a standalone interactive report. It does not require a Python process or live Moodle connection after generation, although Plotly loading behaviour may depend on how the generator is configured.

---

## Example uses

- Preparing a structured Moodle course review.
- Understanding a course before migration or redesign.
- Creating inventories of activities, resources, Books and files.
- Identifying hidden or potentially duplicated content.
- Reviewing external platform dependencies.
- Examining storage footprint and unusually large files.
- Reviewing the recorded age of Moodle activities.
- Supporting conversations between academics, learning technologists, programme teams and governance groups.

The outputs are evidence for professional review rather than substitutes for academic, learning-design, accessibility or quality-assurance judgement.

---

## Data protection

Moodle backups may contain sensitive or personal data. Only analyse data you are authorised to use, minimise the inclusion of personal data, store inputs and outputs securely, and do not publish raw backups or reports without checking their contents.

The toolkit is designed to run locally, but local processing does not remove the need to follow institutional policies, retention schedules and applicable data-protection law.

---

## Roadmap

Potential future developments include:

- Portfolio and cross-course dashboards
- Cross-course benchmarking
- Deeper Moodle Book analysis
- Accessibility metadata analysis
- AI-assisted interpretation with transparent human review
- Institutional benchmarking
- Integration with other CloudPedagogy tools

---

## Licence

MIT License

---

## Disclaimer

CloudPedagogy Moodle Course Auditor provides factual reports derived from Moodle backup metadata.

The dashboard visualises the structured audit outputs. It does not independently establish pedagogic effectiveness, accessibility compliance, course quality or learner achievement.

The software supports institutional review and decision-making but does not replace academic judgement, learning-design review, accessibility testing, quality-assurance procedures, data-protection review or Moodle administration.