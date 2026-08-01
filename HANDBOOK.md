# Moodle Course Analysis Platform Handbook

## 1. Purpose

This handbook explains how to prepare, run and interpret the CloudPedagogy Moodle Course Analysis Platform. It is intended for learning technologists, digital education teams, course teams and authorised reviewers who need a repeatable overview of one or more Moodle courses from backup files.

The platform supports:

- course review and quality-assurance preparation;
- migration and redesign planning;
- inventories of activities, resources, Books and uploaded files;
- storage and large-file analysis;
- detection of Moodle-hosted and externally hosted video;
- external-platform dependency review;
- review of explicit course- and activity-level role capability overrides and recorded enrolment methods;
- identification of hidden, old or potentially duplicated content;
- recovery or migration of Moodle-hosted files when explicitly requested.

It is an evidence-gathering tool. It does not make final judgements about course quality.

## 2. How the system works

The normal end-to-end workflow is:

1. An authorised user creates a Moodle course backup.
2. One or more `.mbz` files are placed in `batch_input/`.
3. `batch_audit.py` discovers and sorts the backups.
4. Each backup is processed by the XML metadata auditor.
5. The structured outputs are passed to the dashboard generator.
6. If `--extract-files` is selected, Moodle-hosted files are reconstructed into a separate folder.
7. Each backup receives an independent, traceable results folder.
8. `batch_summary.csv` records the outcome of the run.

The source `.mbz` is read but not modified.

## 3. System components

### 3.1 Batch controller

`batch_audit.py` is the recommended operational entry point. It accepts either:

- the path to one `.mbz`; or
- a folder containing one or more `.mbz` files.

It coordinates the established scripts, creates an isolated result folder for each backup, logs processing, continues after an individual failure by default, and returns a non-zero exit code if the run contains failures.

### 3.2 Moodle backup auditor

`moodle_mbz_course_auditor.py` reads XML and file metadata from the backup. It produces human-readable reports and machine-readable CSV/JSON datasets. It can also be run independently in single-course or audit-only batch mode.

### 3.3 Dashboard generator

`moodle_dashboard_generator.py` reads the audit dataset and generates `dashboard.html`. The HTML is designed for local review and sharing in an appropriately secured location. The dashboard does not re-audit the course; it visualises the auditor's results.

### 3.4 File extractor

`extract_moodle_files.py` is optional. Moodle stores uploaded files under content hashes rather than a directly usable folder structure. The extractor reads `files.xml`, finds the corresponding stored content, restores recognisable filenames and organises copies by Moodle context, course structure, file type, or all available modes.

Extraction is appropriate for recovery, migration or detailed file inspection. It is not necessary for an ordinary metadata audit and can substantially increase disk use.

## 4. Preparing the Moodle backup

Create a standard Moodle backup (`.mbz`), not an IMS Common Cartridge export.

Recommended selections:

| Backup item | Recommendation | Why |
|---|---|---|
| Activities and resources | Include | Required for the course structure and activity inventory. |
| Files | Include | Required for storage, file-format and Moodle-hosted-video analysis. |
| Blocks | Include | Gives a more complete representation of course configuration. |
| Filters | Include | Preserves relevant embedding/content-processing configuration. |
| Custom fields | Include | Retains useful course and activity metadata. |
| Content bank | Include when used | Important for H5P or content-bank items. |
| Legacy course files | Include when relevant | Allows older stored resources to appear in inventories. |
| Question bank | Optional | Include when question-type analysis is required. |
| Enrolled users and user data | Normally exclude | Reduces privacy risk and is not required for a structural audit. |
| Logs, grades and completion data | Normally exclude | The current audit is not a learner-analytics tool. |

On the Schema settings page, retain all sections and activities that should appear in the audit.

The backup choices directly affect results. For example, if Files is excluded, a displayed value of zero Moodle-hosted videos is not reliable evidence that the live course contains none.

## 5. Installation

Recommended project structure:

```text
repository/
|-- README.md
|-- HANDBOOK.md
|-- requirements.txt
|-- src/
|   |-- batch_audit.py
|   |-- moodle_mbz_course_auditor.py
|   |-- moodle_dashboard_generator.py
|   `-- extract_moodle_files.py
|-- batch_input/
`-- batch_output/
```

On macOS or Linux:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
mkdir -p batch_input batch_output
```

On Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
New-Item -ItemType Directory -Force batch_input, batch_output
```

Confirm the installed tools:

```bash
python src/batch_audit.py --version
python src/batch_audit.py --help
```

## 6. Everyday operating procedure

### Step 1: add backups

Copy one or more authorised `.mbz` files into `batch_input/`.

```bash
ls -lh batch_input
```

### Step 2: activate the environment

```bash
source .venv/bin/activate
```

### Step 3: run the standard process

```bash
python src/batch_audit.py batch_input --output-dir batch_output
```

This creates the audit and dashboard. It works when the folder contains one backup or several backups.

### Step 4: inspect the outcome

```bash
open batch_output
```

Review `batch_summary.csv` first, then open each course's `dashboard.html` and `audit/audit_report.md`.

## 7. Optional operating modes

### Include file extraction

```bash
python src/batch_audit.py batch_input \
  --output-dir batch_output \
  --extract-files
```

Add hash verification:

```bash
python src/batch_audit.py batch_input \
  --output-dir batch_output \
  --extract-files \
  --verify-hashes
```

Hash verification checks reconstructed content against the SHA-1 values recorded by Moodle. It increases processing time but is useful for migration or recovery work.

### Audit without a dashboard

```bash
python src/batch_audit.py batch_input \
  --output-dir batch_output \
  --no-dashboard
```

### Search subfolders

```bash
python src/batch_audit.py batch_input \
  --output-dir batch_output \
  --recursive
```

### Process one explicit backup

```bash
python src/batch_audit.py "/path/to/course-backup.mbz" \
  --output-dir batch_output
```

## 8. Existing-output policies

| Policy | Behaviour | Appropriate use |
|---|---|---|
| `suffix` | Preserves the earlier folder and creates `_2`, `_3`, etc. | Default and safest for exploratory work. |
| `skip` | Does not process a backup when its derived folder already exists. | Resuming a run after checking existing results. |
| `overwrite` | Removes and rebuilds the matching course-run folder. | Deliberate replacement of results; use with care. |

Examples:

```bash
python src/batch_audit.py batch_input --output-dir batch_output --existing suffix
python src/batch_audit.py batch_input --output-dir batch_output --existing skip
python src/batch_audit.py batch_input --output-dir batch_output --existing overwrite
```

An existing folder alone does not necessarily prove that an earlier run completed successfully. Check its `processing.log`, outputs and the relevant summary row before relying on `skip`.

## 9. Folder naming and traceability

The filename:

```text
backup-moodle2-course-5729-lshtm_2489_2025-20260731-2245-nu.mbz
```

becomes:

```text
5729-lshtm_2489_2025-20260731-2245/
```

This retains:

- `5729`: Moodle course ID;
- `lshtm_2489_2025`: recognisable course identity/year;
- `20260731-2245`: backup date and time.

Using only `5729` would be less useful to staff and could confuse separate backup versions of the same course.

## 10. Results structure

```text
batch_output/
|-- batch_summary.csv
`-- <course-run>/
    |-- audit/
    |   |-- audit_report.md
    |   |-- audit_report.txt
    |   |-- audit_data.json
    |   `-- CSV datasets
    |-- dashboard.html
    |-- processing.log
    `-- extracted_files/       # optional
```

### Batch summary

`batch_summary.csv` records the source backup, output folder, overall status, audit status, dashboard status, extraction status, start/finish times, duration, dashboard path and messages.

### Human-readable audit reports

- `audit_report.md`: primary report for review in Markdown.
- `audit_report.txt`: plain-text equivalent for simple access and archiving.

### Core datasets

The exact files vary by auditor version and course contents. Typical datasets include:

- `course_summary.csv`, `course_characteristics.csv`, `course_footprint.csv`;
- `sections.csv`, `section_activity_breakdown.csv`, `activities.csv`;
- `book_inventory.csv`, `hidden_activity_inventory.csv`, `duplicate_activity_inventory.csv`;
- `files.csv`, `file_extension_inventory.csv`, `largest_files.csv`;
- `content_inventory.csv`, `video_inventory.csv`, `audio_inventory.csv`, `document_inventory.csv`;
- `interactive_content_inventory.csv`, `external_media_inventory.csv`;
- `external_dependency_inventory.csv`, `external_domain_inventory.csv`;
- `hosting_summary.csv`, `content_category_summary.csv`, `content_placement_inventory.csv`;
- `course_permissions.csv`, containing one row for each explicit role capability override found at course or activity level;
- `course_access_summary.csv`, summarising affected contexts and roles, permission decisions, important Student restrictions and recorded enrolment methods;
- `modification_year_summary.csv`, `activity_age_summary.csv`;
- `audit_data.json`.

`audit_data.json` is the consolidated machine-readable representation used by downstream processes where supported.

### Extracted-file results

When extraction is enabled, `extracted_files/` may contain:

- `resource_manifest.csv`;
- `extraction_report.md`;
- folders arranged by Moodle context;
- a course-oriented resource bundle;
- a file-type-oriented resource bundle.

The extractor reports missing content, duplicate references, filename collisions and unresolved mappings where detected.

## 11. What the dashboard communicates

The dashboard is intended to make structural and technical patterns easier to discuss. Depending on available data, it may show:

- course, section and activity totals;
- activity composition and distribution across sections;
- Moodle Book/chapter inventory;
- visible and hidden content;
- file formats, large files and total footprint;
- Moodle-hosted video count and storage size;
- Panopto and other external-video references;
- hosting/provider/content-format summaries;
- external domains and platform dependencies;
- explicit course- and activity-level role capability overrides;
- affected roles, `Allow`, `Prevent` and `Prohibit` decisions, and recorded enrolment methods;
- dynamically worded Student-restriction prompts based on recognised Moodle capabilities;
- content placements and items requiring review;
- modification-age patterns;
- filterable content-level records.

The visualisation adapts to the available evidence, so two courses may display different panels.

## 12. Interpreting key findings

### Moodle-hosted video

A Moodle-hosted video is normally identified through the backup's file records and video MIME type or file extension. Its count and storage use can support migration or storage discussions. It does not show whether the video is pedagogically effective, accessible, captioned or actively used.

### External video and Panopto

External-video references are derived from URLs and embedded content. Distinguish unique content references from placements: the same video may be embedded more than once. A URL detected in metadata is not proof that it remains accessible.

### Hidden content

Hidden items can represent obsolete material, work in progress, conditional teaching arrangements or deliberate staff-only resources. They require contextual review and should not automatically be treated as errors.

### Modification age

Modification dates show recorded Moodle changes. They do not prove when the underlying academic material was written or last intellectually reviewed.

### XML word estimates

Word figures estimate text represented in Moodle XML. They do not include reliable counts from uploaded documents and should not be treated as exact learner reading workload.

### Review flags and confidence

These indicate incomplete, ambiguous or conflicting metadata that may need human checking. They are not compliance findings or risk ratings.

### Course access and permissions

The permissions panel reports explicit capability overrides found in the backup's course- and activity-level `roles.xml` files. Its summary shows:

- the total number of explicit overrides;
- course-level and activity-level counts;
- the number of individual activities with overrides;
- roles affected;
- `Allow`, `Prevent` and `Prohibit` decisions;
- enrolment methods recorded in `course/enrolments.xml`;
- important Student restrictions selected for review.

The expandable table retains the authoritative evidence: context, course or activity location, role, Moodle capability, permission decision and review flag.

Where a recognised Student capability is explicitly set to `Prevent` or `Prohibit`, the dashboard describes the likely scope dynamically. For example, `mod/forum:...` restrictions are described as affecting forum participation, quiz capabilities as affecting quiz participation, and resource/file capabilities as affecting resource access. When classification is uncertain, it deliberately falls back to “course access or participation” rather than guessing.

Interpret Moodle decisions carefully:

- `Allow` explicitly grants a capability at that context;
- `Prevent` denies it at that context but can normally be overridden by an `Allow` in a more specific lower context;
- `Prohibit` is a stronger denial that normally cannot be overridden lower in the context hierarchy.

This analysis is useful for detecting unexpected restrictions, understanding why an activity behaves differently from institutional defaults, reviewing locally customised roles before rollover or redesign, comparing backups, and supporting Moodle troubleshooting. A flagged restriction is a prompt for review, not proof of an error.

The panel is not a complete permission matrix and does not calculate the effective permission of every user. Capabilities not listed continue to inherit Moodle's site-level role configuration. Site role definitions, role assignments, group membership and other wider configuration may be absent from the `.mbz`.

## 13. Limitations and accuracy

The platform analyses Moodle backup XML and file metadata. It does not open and semantically interpret uploaded PDFs, Word files, slides, images, audio/video, SCORM packages or H5P packages.

It cannot by itself determine:

- pedagogic effectiveness;
- learning-outcome and assessment alignment;
- academic accuracy or currency;
- accessibility compliance;
- copyright/licensing compliance;
- whether a learner accessed, completed or understood content;
- whether an external service or link is still available.
- every user's effective Moodle permissions or the complete site-level permission matrix.

Findings depend on the Moodle version, installed plugins, backup selections and metadata conventions. Third-party activity types may not be completely recognised. Results should be checked against the live course before consequential action.

Permission findings represent explicit overrides and enrolment information stored in the backup at the time it was created. They do not reconstruct inherited role definitions, every role assignment, or wider site configuration. A zero override count means that no explicit override was found in the parsed backup evidence; it does not mean that every user has unrestricted access.

## 14. Data protection and governance

Before processing:

- confirm authority to access and analyse the course backup;
- exclude enrolled users and unnecessary user data where possible;
- use an approved, access-controlled device or workspace;
- determine an appropriate retention period for backups and results.

After processing:

- restrict access to `.mbz`, reports and extracted content;
- inspect outputs before sharing because titles, URLs or filenames may reveal sensitive information;
- do not publish backups, extracted content or raw reports to public GitHub repositories;
- securely remove working data when it is no longer required under institutional policy.

Local processing improves control but does not remove data-protection responsibilities.

## 15. Troubleshooting

### Input folder not found

If the command reports:

```text
Error: Input not found: .../input
```

use the folder that actually exists:

```bash
python src/batch_audit.py batch_input --output-dir batch_output
```

### No backups found

Check that files end in `.mbz` and are directly inside `batch_input/`. If they are in subfolders, add `--recursive`.

### `python: command not found`

Activate the environment:

```bash
source .venv/bin/activate
which python
```

### Missing Plotly or another dependency

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Dashboard fails but audit succeeds

Open the course `processing.log`, confirm that the expected files exist in `audit/`, and check the dashboard interface:

```bash
python src/moodle_dashboard_generator.py --help
```

The controller supports dashboard compatibility options such as `--dashboard-input`, `--dashboard-output-flag` and repeatable `--dashboard-extra-arg` when the generator's interface differs from its default convention.

### One backup fails

By default, later backups continue. Review the failed row in `batch_summary.csv` and its `processing.log`. A non-zero command exit means that at least one requested operation failed; it is useful for future CI or VM automation.

### Extracted files use unexpected names or locations

Review `extraction_report.md` and `resource_manifest.csv`. Moodle metadata can contain duplicate references, missing stored content, filename collisions or ambiguous activity mappings.

## 16. Recommended review procedure

For each course:

1. Confirm that processing completed successfully.
2. Check the course name, section count and activity totals against Moodle.
3. Review the dashboard for broad structural patterns.
4. Inspect Moodle-hosted video and large-file findings.
5. Review external platforms and domain dependencies.
6. Review explicit course and activity permission overrides for unexpected restrictions or locally customised roles.
7. Confirm important access findings against Moodle's live role and enrolment configuration.
8. Examine hidden, old and potentially duplicated items in context.
9. Use CSV inventories for detailed follow-up.
10. Validate other important findings against the live course.
11. Record agreed actions, owners and review dates outside the audit output.

The audit provides evidence for a professional conversation; it is not itself a formal approval or remediation workflow.

## 17. Independent script use

The controller does not replace the component tools.

Audit one backup:

```bash
python src/moodle_mbz_course_auditor.py course.mbz \
  --output-dir output/course_audit
```

Run the auditor's audit-only folder mode:

```bash
python src/moodle_mbz_course_auditor.py batch_input \
  --batch \
  --output-dir audit_only_output
```

Generate a dashboard independently:

```bash
python src/moodle_dashboard_generator.py output/course_audit
```

Extract files independently:

```bash
python src/extract_moodle_files.py course.mbz \
  --output output/extracted_files \
  --mode all \
  --verify-hashes
```

Use each script's `--help` output as the authoritative interface for the checked-out version.

## 18. Maintenance and future automation

Retain compatible versions of all four scripts together. When logic or output schemas change, record the version used for historic audits so comparisons remain meaningful.

Automated tests are helpful but not required for ordinary operation. They become valuable when the repository is updated regularly or connected to GitHub Actions/VM automation. Real Moodle backups should not be stored as public test fixtures; use small, anonymised or synthetic fixtures.

The controller's status logging and non-zero failure exit make it suitable for a future scheduled or SharePoint-triggered processing service, subject to institutional security and operational approval.

## 19. Disclaimer

Reasonable care is taken in extraction and classification, but outputs may be incomplete or occasionally misclassified because Moodle versions, plugins, backup choices and metadata conventions vary.

The platform does not replace academic judgement, learning-design review, accessibility testing, quality-assurance procedures, copyright review, data-protection review or Moodle administration. Check important findings against the source course before decisions are made.
