# CloudPedagogy Moodle Course Auditor

A local-first Python toolkit for auditing complete Moodle course backups and turning the resulting metadata into structured reports, reusable data files and an interactive HTML dashboard.

The platform supports evidence-informed course review, quality assurance, migration planning, curriculum review, content inventories, digital education analysis and governance. It works without access to a live Moodle site.

## What the platform does

The project has two complementary components:

1. **Moodle Course Auditor**
   - Extracts factual metadata from Moodle backup XML.
   - Produces Markdown, text, CSV and JSON outputs.
   - Creates inventories of course structure, activities, Moodle Books, files, questions, hidden content and external dependencies.
2. **Interactive Dashboard Generator**
   - Reads the structured audit outputs.
   - Generates a standalone interactive Plotly dashboard (`dashboard.html`).

The auditor is deliberately conservative. It reports what can be supported by the backup metadata and does not assign pedagogic-quality, accessibility, compliance or risk scores.

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

The auditor accepts Moodle backup archives in the following formats:

- Moodle `.mbz`
- ZIP
- TAR
- TAR.GZ

The backup should include the course activities, resources and content required for the audit. The dashboard is generated from the auditor's structured outputs and does not require a connection to a live Moodle site.

---

## Recommended Moodle backup settings

Include:

- Activities and resources
- Question bank
- Content

For backups processed outside their original Moodle environment, normally exclude the following unless they are specifically required and you are authorised to process them:

- Enrolled users
- User files
- Activity logs
- Grades
- Other personally identifiable information

Use the minimum data required for the audit and follow your institution's data-protection, retention and information-governance requirements.

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

## Requirements

- Python 3.10 or later
- `pandas>=2.0`
- `plotly>=6.0`

The core XML auditor primarily uses the Python standard library. Dashboard generation requires `pandas` and `plotly`.

## Installation

```bash
git clone https://github.com/cloudpedagogy/cloudpedagogy-moodle-course-auditor.git
cd cloudpedagogy-moodle-course-auditor

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Verify that the virtual environment and dependencies are available:

```bash
which python
python --version
python -c "import pandas, plotly; print('Dependencies installed successfully')"
```

On Windows PowerShell, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

After activation, use `python` and `python -m pip` consistently so that installation and execution use the same interpreter.

---

## Basic usage

### Audit a Moodle backup

```bash
python moodle_mbz_course_auditor.py course.mbz
```

To specify an output directory:

```bash
python moodle_mbz_course_auditor.py course.mbz \
  --output output/course_audit
```

### Generate an interactive dashboard

```bash
python moodle_dashboard_generator.py output/course_audit
```

You can also pass an absolute or relative path to the audit output directory:

```bash
python moodle_dashboard_generator.py /path/to/course_audit
```

By default, the generator writes `dashboard.html` into the supplied audit-output directory. Open the file in a modern web browser:

```bash
open output/course_audit/dashboard.html
```

On platforms without the macOS `open` command, open `dashboard.html` from the file manager or browser.

Check the command-line interface supported by the installed version before using additional options:

```bash
python moodle_mbz_course_auditor.py --help
python moodle_dashboard_generator.py --help
```

The help output is authoritative for the version of the scripts you are running.

---

## Typical workflow

```text
Moodle course backup
        │
        ▼
Run Moodle course auditor
        │
        ▼
Review Markdown, text, CSV and JSON outputs
        │
        ▼
Run dashboard generator
        │
        ▼
Review dashboard with the course or programme team
        │
        ▼
Agree any investigation, clean-up, migration or redesign work
```

The source backup is read but not modified.

---

## Outputs

The exact set of files may vary according to the course data, auditor version and processing mode.

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

This is a standalone interactive report. It does not require a running Python process or a live Moodle connection after generation. Whether the report requires internet access depends on whether Plotly JavaScript is embedded, loaded locally or loaded from a content-delivery network. Use the dashboard generator's `--help` output to check the options available in your version.

---

## Re-running and comparing audits

When a newer backup is received, retain the original backup and write the new results to a dated or versioned output directory if historical comparison is required:

```text
output/
├── course_2025_26_audit/
└── course_2026_27_audit/
```

Only compare outputs generated with compatible auditor and output-schema versions. A modification date indicates a recorded Moodle change; it does not prove that the underlying academic content changed on that date.

---

## Troubleshooting

### `python: command not found`

Confirm that the virtual environment is active:

```bash
source .venv/bin/activate
which python
```

The reported path should end in `.venv/bin/python`.

### `externally-managed-environment`

This normally means `pip` is using a system- or Homebrew-managed Python rather than the project virtual environment. Do not use `--break-system-packages`. Create or reactivate a virtual environment, then install with:

```bash
python -m pip install -r requirements.txt
```

### Missing `pandas` or `plotly`

With the virtual environment active, run:

```bash
python -m pip install -r requirements.txt
```

### Dashboard not generated

Confirm that the audit completed successfully and that `audit_data.json` and the expected CSV files exist in the audit-output directory. Then check:

```bash
python moodle_dashboard_generator.py --help
```

and rerun the generator using the audit-output directory as its input.

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

The outputs provide evidence for professional review rather than substitutes for academic, learning-design, accessibility or quality-assurance judgement.

---

## Data protection

Moodle backups may contain sensitive or personal data. Only analyse data you are authorised to use, minimise the inclusion of personal data, store inputs and outputs securely, and do not publish raw backups or reports without checking their contents.

The toolkit is designed to run locally, but local processing does not remove the need to follow institutional policies, retention schedules and applicable data-protection law. Do not commit Moodle backups, extracted course data or potentially sensitive audit outputs to a public repository.

---

## Roadmap

Possible future developments include:

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
