# CloudPedagogy Moodle Course Auditor

A Python command-line tool for auditing Moodle course backup files (`.mbz`) using Moodle XML metadata.

CloudPedagogy Moodle Course Auditor extracts factual metadata from Moodle backup archives and generates structured reports to support course review, quality assurance, migration planning, content inventory, digital education analysis, and course governance workflows.

The tool is intentionally conservative and evidence-based. It reports what can be derived from Moodle backup XML files and does not inspect the internal contents of uploaded files such as PDFs, Word documents, PowerPoint files, images, videos, SCORM packages, or H5P packages.

---

## Overview

Moodle course backups contain a rich set of XML metadata describing course structure, activities, sections, files, questions, visibility settings, and content relationships.

This tool analyses that metadata and produces structured outputs that help educators, learning designers, learning technologists, quality assurance teams, and Moodle administrators understand the composition and characteristics of a Moodle course without requiring access to a live Moodle environment.

The auditor operates entirely from Moodle backup files and does not require direct access to a Moodle server.

---

## Key Features

- Audit individual Moodle course backups
- Batch process multiple Moodle backups
- Recursive processing of backup collections
- Course structure and section analysis
- Activity inventories and activity type summaries
- Moodle Book inventories
- Hidden content detection
- Duplicate activity detection
- External dependency identification
- External domain inventories
- webCAL, iframe and Panopto usage detection
- File metadata and storage footprint reporting
- Question metadata analysis
- Activity modification and age analysis
- CSV, JSON, Markdown and Text reporting
- Optional extraction and retention of backup contents

---

## What the Tool Analyses

The auditor extracts information including:

### Course Metadata

- Course name
- Short name
- Course format
- Visibility settings
- Start date
- End date
- Modification dates

### Sections

- Section names
- Section numbering
- Visibility settings
- Activity counts
- Section summaries

### Activities

- Activity types
- Activity names
- Visibility settings
- Completion settings
- Modification dates
- Section placement

### Moodle Books

- Book activity inventories
- Chapter counts
- Hidden chapter counts
- Content volume estimates

### External Dependencies

- External links
- Embedded iframes
- webCAL references
- Panopto references
- External domains

### Files

- File counts
- File extensions
- MIME types
- File areas
- File sizes
- Largest files

### Questions

- Question counts
- Question type distributions

### Course Footprint

- Content volume estimates
- Activity distributions
- Storage footprint
- Structural characteristics

---

## Important Limitations

This tool audits Moodle backup XML metadata only.

It does not:

- Open PDF files
- Open Word documents
- Open PowerPoint files
- Inspect images
- Inspect audio files
- Inspect video files
- Analyse SCORM package contents
- Analyse H5P package contents
- Evaluate accessibility compliance
- Generate pedagogic quality ratings
- Generate compliance ratings
- Generate risk scores
- Generate severity scores

The outputs are intended to be factual and descriptive rather than evaluative.

---

## Moodle Files Analysed

The auditor reads metadata from files such as:

```text
course/course.xml
sections/*/section.xml
activities/*/module.xml
activities/*/[activity_type].xml
files.xml
questions.xml
```

---

## Supported Backup Formats

The tool supports:

- Moodle .mbz backups
- ZIP archives
- TAR archives
- TAR.GZ archives

---

## Installation

Clone the repository:

```bash
git clone https://github.com/YOUR-USERNAME/cloudpedagogy-moodle-course-auditor.git
cd cloudpedagogy-moodle-course-auditor
```

Create and activate a virtual environment if desired:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

The current version uses only Python standard library modules and has no external Python package dependencies.

---

## Basic Usage

Audit a single Moodle backup:

```bash
python3 moodle_mbz_course_auditor.py course.mbz
```

Default output folder:

```text
moodle_audit_output/
```

---

## Specify an Output Folder

```bash
python3 moodle_mbz_course_auditor.py course.mbz --output output/course_audit
```

or

```bash
python3 moodle_mbz_course_auditor.py course.mbz -o output/course_audit
```

---

## Batch Processing

Process all Moodle backups within a folder:

```bash
python3 moodle_mbz_course_auditor.py backups --batch --output reports
```

---

## Recursive Batch Processing

Search subfolders recursively:

```bash
python3 moodle_mbz_course_auditor.py backups --batch --recursive --output reports
```

---

## Keep Extracted Backup Files

By default extracted backup files are deleted after processing.

To retain extracted files:

```bash
python3 moodle_mbz_course_auditor.py course.mbz --keep-extracted
```

or

```bash
python3 moodle_mbz_course_auditor.py backups --batch --keep-extracted
```

This creates an:

```text
extracted_backup/
```

folder within each course audit folder.

---

## Output Files

### Single Course Outputs

```text
audit_report.md
audit_report.txt
course_summary.csv
course_characteristics.csv
course_footprint.csv
section_activity_breakdown.csv
book_inventory.csv
duplicate_activity_inventory.csv
hidden_content_summary.csv
hidden_activity_inventory.csv
external_dependency_inventory.csv
external_domain_inventory.csv
file_extension_inventory.csv
largest_files.csv
modification_year_summary.csv
activity_age_summary.csv
activities.csv
sections.csv
files.csv
audit_data.json
```

### Batch Outputs

```text
combined_course_summary.csv
batch_run_log.csv
```

---

## Example Commands

Single course:

```bash
python3 moodle_mbz_course_auditor.py my_course.mbz
```

Single course with output folder:

```bash
python3 moodle_mbz_course_auditor.py my_course.mbz --output reports/my_course
```

Batch audit:

```bash
python3 moodle_mbz_course_auditor.py backups --batch --output reports
```

Recursive batch audit:

```bash
python3 moodle_mbz_course_auditor.py backups --batch --recursive --output reports
```

Batch audit retaining extracted files:

```bash
python3 moodle_mbz_course_auditor.py backups --batch --recursive --keep-extracted --output reports
```

---

## Example Use Cases

The tool can support:

- Moodle course auditing
- Quality assurance reviews
- Curriculum review activities
- Course migration planning
- Platform migration projects
- Digital education audits
- Learning design analysis
- Moodle Book inventories
- Legacy content discovery
- Course governance reviews
- Course redesign projects
- Portfolio-level Moodle analysis

---

## Suggested Repository Structure

```text
cloudpedagogy-moodle-course-auditor/
├── moodle_mbz_course_auditor.py
├── README.md
├── LICENSE
├── .gitignore
├── examples/
│   └── sample_output/
└── docs/
    └── user-guide.md
```

---

## Suggested .gitignore

```gitignore
__pycache__/
*.pyc
.venv/
venv/
.DS_Store

moodle_audit_output/
batch_audit_output/
output/
reports/

*.mbz
extracted_backup/
```

---

## Data Protection and Privacy

Moodle backups may contain sensitive institutional information.

Before using this tool ensure that:

- You have permission to access the backup
- You comply with institutional policies
- Outputs are stored securely
- Sensitive outputs are not committed to public repositories
- Shared examples are anonymised where appropriate

---

## Licence

Recommended:

MIT License

The MIT License provides a simple and permissive framework for reuse, modification and redistribution.

---

## Roadmap

Potential future enhancements include:

- Interactive HTML dashboards
- Visual analytics
- Moodle Book specific reporting
- Accessibility metadata analysis
- AI-assisted report interpretation
- Portfolio-wide benchmarking
- Integration with other CloudPedagogy tools

---

## Disclaimer

CloudPedagogy Moodle Course Auditor provides factual reports derived from Moodle backup XML metadata.

The tool does not replace academic judgement, institutional review processes, accessibility audits, data protection assessments, quality assurance procedures, or Moodle administration activities.

Users remain responsible for interpreting and applying the outputs appropriately within their own organisational context.
