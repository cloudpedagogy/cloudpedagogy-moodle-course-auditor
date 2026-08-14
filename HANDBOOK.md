# Moodle Course Analysis Platform Handbook

## 1. Purpose

This handbook explains how to install, run and interpret the CloudPedagogy Moodle Course Analysis Platform.

The platform is designed for learning technologists, digital education teams, course teams and authorised reviewers who need a repeatable way to examine one or more Moodle courses from Moodle backup (`.mbz`) files.

It supports:

- structural and technical course review;
- redesign and migration planning;
- activity, resource and Moodle Book inventories;
- file/storage analysis;
- Moodle-hosted and externally hosted media analysis;
- external-platform dependency review;
- explicit course/activity capability override review;
- hidden, old and potentially duplicated content review;
- Moodle-hosted file recovery;
- editable course content mapping;
- detailed activity/settings analysis;
- before/after Moodle backup comparison.

The platform is an evidence-gathering toolkit. It does not make final judgements about course quality.

## 2. Architecture

The recommended operational entry point is `src/orchestrator.py`.

The component scripts remain independently usable. The orchestrator decides which of them to run for a requested scenario and checks dependencies between stages.

```text
                                  Moodle .mbz
                                      |
                    +-----------------+------------------+
                    |                 |                  |
                    v                 v                  v
                  Auditor          Extractor      Settings analyser
                    |                 |                  |
                    v                 |                  |
                Dashboard             |                  |
                    |                 |                  |
                    +--------+--------+                  |
                             |                           |
                             v                           |
                       Content mapper                    |
                                                         |
     Earlier .mbz + Later .mbz --> Comparator            |
```

Important dependency:

```text
Auditor outputs + extracted files
              |
              v
       Content mapper
```

`content_mapper.py` does **not** read an `.mbz` directly.

## 3. Repository structure

Recommended structure:

```text
cloudpedagogy-moodle-course-auditor/
|-- README.md
|-- HANDBOOK.md
|-- MOODLE_BACKUP_INSTRUCTIONS.md
|-- requirements.txt
|-- LICENSE
|-- src/
|   |-- orchestrator.py
|   |-- moodle_mbz_course_auditor.py
|   |-- moodle_dashboard_generator.py
|   |-- extract_moodle_files.py
|   |-- content_mapper.py
|   |-- analyse_mbz.py
|   `-- compare_mbz.py
|-- batch_input/
`-- batch_output/
```

`batch_input/` and `batch_output/` are retained because they clearly distinguish the orchestrator's batch workspace from the input/output conventions of individual scripts.

## 4. Script reference

### 4.1 `orchestrator.py`

**Purpose**

The recommended controller for normal use. It accepts one `.mbz` file or a folder containing one or more `.mbz` files.

**Responsibilities**

- discovers backups;
- creates one isolated result folder per backup;
- runs only scripts needed for the selected workflow;
- automatically resolves known dependencies;
- logs commands and results in `processing.log`;
- records stage statuses in `batch_summary.csv`;
- continues to later backups after a failure by default;
- supports suffix, skip and overwrite policies for existing results.

**Default workflow**

```text
Auditor -> Dashboard
```

**Optional workflows**

| Command option | Effective workflow |
|---|---|
| no additional option | Auditor + Dashboard |
| `--extract-files` | Auditor + Dashboard + Extractor |
| `--content-map` | Auditor + Dashboard + Extractor + Content Mapper |
| `--settings` | Auditor + Dashboard + Settings Analyser |
| `--full` | Auditor + Dashboard + Extractor + Content Mapper + Settings Analyser |
| `--no-dashboard` | Auditor without dashboard; other explicitly requested independent stages may still run |

`--content-map` automatically enables extraction.

The orchestrator does not invoke `compare_mbz.py` because comparison requires an explicit before/after pair rather than ordinary per-course batch processing.

### 4.2 `moodle_mbz_course_auditor.py`

**Reads**

- Moodle `.mbz`

**Purpose**

The primary metadata auditor. It reads Moodle backup XML and file metadata without modifying the backup.

**Typical evidence**

- course metadata;
- sections and activity sequence;
- activity types;
- Moodle Books and chapters;
- hidden activities;
- possible duplicate activities;
- files, sizes and formats;
- modification dates;
- embedded/external URLs;
- Moodle-hosted media;
- Panopto and other external media;
- content placement;
- explicit course/activity role capability overrides;
- enrolment methods recorded in the backup.

**Typical outputs**

- `audit_report.md`
- `audit_report.txt`
- `audit_data.json`
- `course_summary.csv`
- `course_characteristics.csv`
- `course_footprint.csv`
- `sections.csv`
- `activities.csv`
- `section_activity_breakdown.csv`
- `book_inventory.csv`
- `duplicate_activity_inventory.csv`
- `hidden_content_summary.csv`
- `hidden_activity_inventory.csv`
- `files.csv`
- `file_extension_inventory.csv`
- `largest_files.csv`
- `modification_year_summary.csv`
- `activity_age_summary.csv`
- `external_dependency_inventory.csv`
- `external_domain_inventory.csv`
- `content_inventory.csv`
- `video_inventory.csv`
- `audio_inventory.csv`
- `document_inventory.csv`
- `interactive_content_inventory.csv`
- `external_media_inventory.csv`
- `content_category_summary.csv`
- `hosting_summary.csv`
- `content_placement_inventory.csv`
- `course_permissions.csv`
- `course_access_summary.csv`

The exact contents depend on the backup.

### 4.3 `moodle_dashboard_generator.py`

**Reads**

- auditor output folder or supported audit JSON input

**Purpose**

Converts the audit datasets into an interactive Plotly HTML dashboard.

It does not parse the `.mbz` itself and does not change the audit data.

**Output**

- normally `dashboard.html`

Depending on available datasets, the dashboard can visualise structure, activity mix, Moodle Books, files, media, hosting/provider patterns, hidden/duplicate content, external dependencies, modification age, permissions and content-level records.

Optional panels are skipped when supporting evidence is absent.

### 4.4 `extract_moodle_files.py`

**Reads**

- Moodle `.mbz` or an already extracted Moodle backup directory

**Purpose**

Recovers actual Moodle-hosted file content. Moodle stores files by content hash; the extractor reconstructs recognisable filenames and useful organisational views while retaining provenance.

**Views**

- `context` — authoritative Moodle component/file-area/item provenance;
- `course` — best-effort section/activity/chapter organisation;
- `type` — PDFs, documents, data, images, video and other categories;
- `all` — all three views.

**Typical outputs**

```text
extracted_files/
|-- resource_manifest.csv
|-- extraction_report.md
|-- files_by_moodle_context/
|-- resource_bundle/
`-- resource_bundle_by_type/
```

Hash verification can be requested with `--verify-hashes`.

The extractor may return exit code `3` after producing outputs when missing files or hash mismatches are found. The orchestrator records this as a warning rather than automatically treating the entire extraction as unusable.

### 4.5 `content_mapper.py`

**Reads**

A **course-run directory**, not an `.mbz`.

Required structure:

```text
<course-run>/
|-- audit/
|   |-- sections.csv
|   |-- activities.csv
|   `-- content_placement_inventory.csv
`-- extracted_files/
```

**Purpose**

Creates an editable/browsable representation of the existing Moodle course structure, links Moodle resource records to recovered local files, and retains external URLs.

The mapper preserves Moodle section/course-item order and uses accuracy-first file matching. Ambiguous matches are deliberately left unresolved rather than guessed.

**Outputs**

```text
content_map/
|-- index.html
|-- content_map.docx
|-- content_map.csv
|-- unresolved_items.csv
`-- mapping_report.md
```

With `--bundle`, linked recovered resources are copied into `content_map/resources/` to make the content map more portable.

The mapper does not pedagogically reorganise the course; it represents the audited current structure for review/redesign work.

### 4.6 `analyse_mbz.py`

**Reads**

- Moodle `.mbz`

**Purpose**

Provides a focused activity/settings analysis separate from the main course audit.

It can report:

- URL/resource display modes;
- visibility;
- group mode;
- completion mode;
- availability restrictions;
- attached-file counts/sizes;
- explicit role overrides;
- duplicate URL destinations;
- domain-level display-mode consistency;
- optional rules requiring selected domains to open in a new window.

**Outputs**

```text
settings/
|-- course-settings-report.html
|-- course-settings.csv
`-- course-settings.json
```

Example optional rule:

```bash
python3 src/orchestrator.py \
  --settings \
  --expect-new-window-for "example.org"
```

The rule is a review rule, not a universal Moodle correctness rule.

### 4.7 `compare_mbz.py`

**Reads**

- an earlier `.mbz`;
- a later `.mbz`.

**Purpose**

Compares two Moodle backups without modifying them.

It can detect meaningful differences in:

- course settings;
- sections;
- activity additions/removals;
- activity visibility/completion/availability;
- selected activity settings;
- URLs;
- textual content;
- Moodle Book chapters/content;
- files and content hashes.

Technical-only differences such as backup timestamps are intentionally excluded where appropriate.

**Outputs**

```text
comparison_output/
|-- comparison_report.html
|-- comparison_report.md
|-- comparison_data.json
|-- course_changes.csv
|-- activity_changes.csv
|-- content_changes.csv
`-- file_changes.csv
```

The comparator reports comparison coverage because some Moodle activity types receive deeper content comparison than others.

## 5. Preparing the Moodle backup

Use a standard Moodle backup (`.mbz`), not an IMS Common Cartridge export.

For a normal structural/content audit, the most important selections are:

| Backup item | Recommendation | Why |
|---|---|---|
| Activities and resources | Include | Required for structure and activity/resource analysis. |
| Files | Include | Required for storage/file/media analysis and content mapping. |
| Blocks | Include | Provides a fuller representation of configuration. |
| Filters | Include | Retains relevant embedding/content processing configuration. |
| Custom fields | Include | Retains useful metadata. |
| Content bank | Include when used | Important for H5P/content-bank items. |
| Legacy course files | Include when relevant | Retains older stored resources. |
| Question bank | Optional | Include for question metadata analysis. |
| Enrolled users | Normally exclude | Not required for structural/content analysis and increases privacy risk. |
| Logs, grades, completion | Normally exclude | Not required by the current normal audit workflow. |

See `MOODLE_BACKUP_INSTRUCTIONS.md` for the complete backup-setting table.

## 6. Installation

### macOS / Linux

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The current requirements are:

```text
pandas>=2.0,<3
plotly>=5.18,<7
python-docx>=1.1,<2
```

Confirm the main interface:

```bash
python3 src/orchestrator.py --version
python3 src/orchestrator.py --help
```

## 7. Everyday workflow

### Step 1 — add backups

Place one or more authorised `.mbz` files in:

```text
batch_input/
```

Example:

```text
batch_input/
`-- literature-review-2025.mbz
```

### Step 2 — activate the environment

```bash
source .venv/bin/activate
```

### Step 3 — choose the workflow

#### Standard analytics: audit + dashboard

```bash
python3 src/orchestrator.py
```

#### Analytics + extracted Moodle files

```bash
python3 src/orchestrator.py --extract-files
```

#### Analytics + course content map

```bash
python3 src/orchestrator.py --content-map
```

This automatically runs:

```text
Auditor
  |
  +--> Dashboard
  |
  +--> Extractor
          |
          v
     Content mapper
```

#### Analytics + settings analysis

```bash
python3 src/orchestrator.py --settings
```

#### Complete normal per-course analysis

```bash
python3 src/orchestrator.py --full
```

### Step 4 — inspect `batch_summary.csv`

The orchestrator records:

- source backup/path;
- course output folder;
- overall status;
- audit status;
- dashboard status;
- extraction status;
- content-map status;
- settings status;
- start/finish times;
- duration;
- output paths;
- messages/warnings.

### Step 5 — inspect course results

Start with:

1. `dashboard.html`;
2. `audit/audit_report.md`;
3. specialist outputs requested for the run.

## 8. Example: literature-review-2025.mbz

With:

```text
batch_input/
`-- literature-review-2025.mbz
```

run:

```bash
source .venv/bin/activate
python3 src/orchestrator.py --content-map
```

The orchestrator uses the default input/output folders and automatically enables extraction.

Expected structure:

```text
batch_output/
|-- batch_summary.csv
`-- literature-review-2025/
    |-- audit/
    |-- dashboard.html
    |-- extracted_files/
    |-- content_map/
    `-- processing.log
```

If detailed settings are also required:

```bash
python3 src/orchestrator.py --full
```

## 9. Orchestrator options

### Input/output

```bash
python3 src/orchestrator.py batch_input --output-dir batch_output
```

The defaults are already `batch_input` and `batch_output`.

Process one explicit backup:

```bash
python3 src/orchestrator.py "/path/to/course.mbz" \
  --output-dir batch_output
```

Search input subfolders:

```bash
python3 src/orchestrator.py --recursive
```

### Existing output policy

| Policy | Behaviour |
|---|---|
| `suffix` | Default. Preserve existing result and create `_2`, `_3`, etc. |
| `skip` | Do not rerun a course when its derived folder already exists. |
| `overwrite` | Remove/rebuild the matching generated course-run folder. |

Examples:

```bash
python3 src/orchestrator.py --existing suffix
python3 src/orchestrator.py --existing skip
python3 src/orchestrator.py --existing overwrite
```

### Extraction controls

```bash
python3 src/orchestrator.py \
  --extract-files \
  --extraction-mode all \
  --verify-hashes
```

Possible extraction modes:

- `context`
- `course`
- `type`
- `all`

Storage mode:

```bash
--link-mode copy
--link-mode hardlink
```

`copy` is the portable default.

### Content-map controls

Portable map with resource copies:

```bash
python3 src/orchestrator.py \
  --content-map \
  --bundle-map
```

Include hidden course items/sections:

```bash
python3 src/orchestrator.py \
  --content-map \
  --include-hidden-map
```

### Stop after a failed course

The default is to continue to later backups.

To stop:

```bash
python3 src/orchestrator.py --no-keep-going
```

## 10. Orchestrator output structure

A full run can create:

```text
batch_output/
|-- batch_summary.csv
`-- <course-run>/
    |-- audit/
    |-- dashboard.html
    |-- extracted_files/
    |-- content_map/
    |-- settings/
    `-- processing.log
```

A folder is created only when the corresponding stage is requested.

## 11. Independent script use

The orchestrator is recommended for normal workflows, but component tools can be run independently.

### Auditor

```bash
python3 src/moodle_mbz_course_auditor.py course.mbz \
  --output-dir output/course_audit
```

### Dashboard

```bash
python3 src/moodle_dashboard_generator.py output/course_audit \
  --output output/dashboard.html
```

### Extractor

```bash
python3 src/extract_moodle_files.py course.mbz \
  --output output/extracted_files \
  --mode all \
  --verify-hashes
```

### Content mapper

This requires an existing course-run directory containing `audit/` and `extracted_files/`:

```bash
python3 src/content_mapper.py batch_output/course-run \
  --output-dir output/course_map
```

### Settings analyser

```bash
python3 src/analyse_mbz.py course.mbz \
  --output output/settings
```

### Comparator

```bash
python3 src/compare_mbz.py \
  old-course.mbz \
  new-course.mbz \
  --output-dir comparison_output
```

Always check the interface for the checked-out version:

```bash
python3 src/orchestrator.py --help
python3 src/moodle_mbz_course_auditor.py --help
python3 src/moodle_dashboard_generator.py --help
python3 src/extract_moodle_files.py --help
python3 src/content_mapper.py --help
python3 src/analyse_mbz.py --help
python3 src/compare_mbz.py --help
```

## 12. Interpreting key outputs

### Dashboard

Use the dashboard for broad patterns and discussion. Depending on evidence, it may show:

- headline course/section/activity/file totals;
- activity type mix;
- activities by section;
- Moodle Books;
- hidden/duplicate content;
- file formats/storage;
- Moodle-hosted video;
- Panopto/external video;
- hosting/provider summaries;
- external domains/dependencies;
- permissions/access evidence;
- modification age;
- filterable content-level records.

### Content map

The content map is useful for redesign and migration review. It preserves the current Moodle order, shows course items and resource/link associations, and can provide an editable Word working copy.

It should not be interpreted as an automatically improved pedagogical structure.

### Settings report

The settings report is useful for configuration consistency and targeted QA. Display settings, availability conditions and permissions are separate concepts; a setting difference is not automatically an error.

### Comparator

Comparison findings should be interpreted alongside the reported comparison coverage. A "no change" result for an unsupported/deeply nested plugin structure should not be treated as proof that every internal field is identical.

## 13. Permissions interpretation

The main auditor reports explicit capability overrides found in the backup's course- and activity-level `roles.xml` files.

Typical evidence includes:

- context;
- role;
- capability;
- `Allow`, `Prevent` or `Prohibit`;
- activity/course location;
- selected Student restrictions for review.

These are not a complete effective-permission calculation. Site-level role definitions, assignments, group membership and other wider Moodle configuration may not be represented completely in the `.mbz`.

## 14. Accuracy and limitations

The platform analyses Moodle backup XML and file metadata.

The main auditor does not semantically interpret the internal contents of uploaded:

- PDFs;
- Word files;
- presentations;
- images;
- audio/video;
- SCORM packages;
- H5P packages.

It cannot by itself determine:

- pedagogic effectiveness;
- learning-outcome alignment;
- academic accuracy;
- accessibility compliance;
- copyright compliance;
- whether a learner understood content;
- whether an external URL still works;
- every user's final effective permissions.

Findings are affected by:

- Moodle version;
- installed plugins;
- backup selections;
- metadata conventions;
- unusual third-party activity structures.

Verify consequential findings against the live/source Moodle course.

## 15. Data protection and governance

Before processing:

- confirm authority to use the course backup;
- exclude unnecessary learner/user data;
- use an approved, access-controlled environment;
- determine appropriate retention.

After processing:

- protect `.mbz` files and generated results;
- inspect titles, URLs and filenames before sharing;
- do not publish real backups or extracted resources to public GitHub;
- remove working data when no longer required.

Local processing improves control but does not remove data-protection responsibilities.

## 16. Troubleshooting

### Input folder not found

The orchestrator defaults to:

```text
batch_input/
```

Confirm it exists and contains `.mbz` files.

### No backups found

Check:

```bash
ls -lh batch_input
```

Use `--recursive` if backups are in subdirectories.

### Dependency error

Activate the environment and install requirements:

```bash
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

### Dashboard fails but audit succeeds

Review:

```text
<course-run>/processing.log
```

Confirm that the expected audit datasets exist in `audit/`.

### Content mapper skipped

The mapper requires:

```text
audit/sections.csv
audit/activities.csv
audit/content_placement_inventory.csv
extracted_files/
```

The orchestrator reports missing prerequisites in `batch_summary.csv` and `processing.log`.

### Extraction completed with warning

Exit code `3` can mean the extractor completed but found missing content and/or hash mismatches. Review:

```text
extracted_files/extraction_report.md
extracted_files/resource_manifest.csv
```

The orchestrator may still run the content mapper when all mapper prerequisites remain usable.

### One course fails in a batch

Later backups continue by default. Review the failed row in `batch_summary.csv` and the relevant `processing.log`.

## 17. Recommended review procedure

For each course:

1. Confirm stage statuses in `batch_summary.csv`.
2. Confirm course identity and headline totals.
3. Review the dashboard.
4. Review Moodle-hosted media and large files.
5. Review external platforms/domains.
6. Review hidden/old/duplicate items in context.
7. Review explicit permissions where relevant.
8. Use the content map for redesign/migration discussion when generated.
9. Use the settings report for configuration-focused QA when generated.
10. Validate important findings against the source course.
11. Record actions and decisions separately from the generated evidence.

## 18. Maintenance

Keep compatible versions of the component scripts together.

When output schemas or command interfaces change:

- update `README.md`;
- update this handbook;
- update `MOODLE_BACKUP_INSTRUCTIONS.md` when backup evidence requirements change;
- update `requirements.txt` when dependencies change.

Real Moodle backups should not be stored as public test fixtures. Prefer small anonymised or synthetic fixtures for automated testing.

## 19. Disclaimer

Reasonable care is taken in extraction, classification and comparison, but Moodle versions, plugins, backup choices and metadata conventions vary.

The platform does not replace academic judgement, learning-design review, accessibility testing, quality-assurance processes, copyright review, data-protection review or Moodle administration.
