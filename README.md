# CloudPedagogy Moodle Course Auditor

A Python command-line toolkit for auditing Moodle course backup files
(`.mbz`) using Moodle XML metadata and generating both structured
reports and interactive analytics dashboards.

## Overview

CloudPedagogy Moodle Course Auditor extracts factual metadata from
Moodle backup archives and produces structured outputs to support course
review, quality assurance, migration planning, curriculum review,
content inventories, digital education analysis and governance
workflows.

The platform is intentionally conservative and evidence-based. It
reports only what can be derived from Moodle backup XML files and does
**not** inspect the internal contents of uploaded files such as PDFs,
Word documents, PowerPoint files, images, videos, SCORM packages or H5P
packages.

The project now consists of two complementary components:

1.  **Moodle MBZ Course Auditor**
    -   Extracts Moodle XML metadata.
    -   Produces Markdown, Text, CSV and JSON reports.
2.  **Interactive Dashboard Generator**
    -   Reads the auditor outputs.
    -   Generates a standalone interactive HTML dashboard using Plotly.
    -   Requires no live Moodle server.

------------------------------------------------------------------------

# Recommended Moodle Backup Settings

Enable:

-   ✅ Activities and resources
-   ✅ Question bank
-   ✅ Content

For externally shared backups, consider excluding:

-   Enrolled users
-   User files
-   Activity logs
-   Grades
-   Other personally identifiable information

------------------------------------------------------------------------

# Key Features

## XML Metadata Audit

-   Audit individual Moodle backups
-   Batch processing
-   Recursive processing
-   Course metadata
-   Section analysis
-   Activity inventories
-   Moodle Book inventories
-   Hidden content detection
-   Duplicate activity detection
-   External dependency detection
-   External domain inventories
-   iframe, webCAL and Panopto detection
-   File metadata analysis
-   Question metadata analysis
-   Activity age analysis
-   CSV reporting
-   JSON reporting
-   Markdown reporting
-   Text reporting

## Interactive Dashboard Analytics (NEW)

Generate a standalone interactive Plotly dashboard directly from the
audit outputs.

Features include:

-   Course overview cards
-   Activity type visualisations
-   Section summaries
-   Book analytics
-   File footprint visualisations
-   External dependency analytics
-   Hidden content summaries
-   Activity age visualisations
-   Responsive HTML dashboard
-   Standalone dashboard (no web server required)

The dashboard automatically adapts when optional audit outputs are
absent. For example, courses without Moodle Books simply omit
Book-specific visualisations while retaining the remainder of the
dashboard.

------------------------------------------------------------------------

# What the Tool Analyses

## Course Metadata

-   Course name
-   Short name
-   Format
-   Visibility
-   Dates
-   Modification timestamps

## Sections

-   Names
-   Numbering
-   Visibility
-   Activity counts

## Activities

-   Activity types
-   Names
-   Completion
-   Visibility
-   Modification dates

## Moodle Books

-   Inventories
-   Chapter counts
-   Hidden chapters
-   XML word estimates

## External Dependencies

-   External links
-   iframe usage
-   webCAL
-   Panopto
-   External domains

## Files

-   Counts
-   Extensions
-   MIME types
-   File areas
-   Sizes
-   Largest files

## Questions

-   Counts
-   Question type distributions

## Course Footprint

-   Activity distributions
-   Storage footprint
-   Structural characteristics
-   Content volume estimates

------------------------------------------------------------------------

# Important Limitations

The auditor analyses Moodle XML metadata only.

It does **not**:

-   Open PDFs
-   Open Word documents
-   Open PowerPoint files
-   Inspect images
-   Inspect audio/video
-   Analyse SCORM packages
-   Analyse H5P packages
-   Generate pedagogic ratings
-   Generate accessibility ratings
-   Generate compliance ratings
-   Generate risk or severity scores

------------------------------------------------------------------------

# Moodle Files Analysed

-   course/course.xml
-   sections/\*/section.xml
-   activities/\*/module.xml
-   activities/\*/\[activity_type\].xml
-   files.xml
-   questions.xml

------------------------------------------------------------------------

# Supported Backup Formats

-   Moodle `.mbz`
-   ZIP
-   TAR
-   TAR.GZ

------------------------------------------------------------------------

# Installation

``` bash
git clone https://github.com/cloudpedagogy/cloudpedagogy-moodle-course-auditor.git
cd cloudpedagogy-moodle-course-auditor

python3 -m venv .venv
source .venv/bin/activate

pip install pandas plotly
```

## Python Dependencies

Core auditor: - Python Standard Library only

Dashboard generator: - pandas - plotly

------------------------------------------------------------------------

# Basic Usage

## Audit a Moodle backup

``` bash
python3 moodle_mbz_course_auditor.py course.mbz
```

## Generate an interactive dashboard

``` bash
python3 moodle_dashboard_generator.py moodle_audit_output
```

or

``` bash
python3 moodle_dashboard_generator.py /path/to/moodle_audit_output
```

------------------------------------------------------------------------

# Outputs

## Auditor

-   audit_report.md
-   audit_report.txt
-   course_summary.csv
-   course_characteristics.csv
-   course_footprint.csv
-   section_activity_breakdown.csv
-   book_inventory.csv
-   duplicate_activity_inventory.csv
-   hidden_content_summary.csv
-   hidden_activity_inventory.csv
-   external_dependency_inventory.csv
-   external_domain_inventory.csv
-   file_extension_inventory.csv
-   largest_files.csv
-   modification_year_summary.csv
-   activity_age_summary.csv
-   activities.csv
-   sections.csv
-   files.csv
-   audit_data.json

Batch mode additionally creates:

-   combined_course_summary.csv
-   batch_run_log.csv

## Dashboard

The dashboard generator creates:

-   dashboard.html

This is a standalone interactive HTML report that can be opened locally
in any modern web browser.

------------------------------------------------------------------------

# Example Use Cases

-   Moodle course auditing
-   Quality assurance
-   Curriculum review
-   Learning design review
-   Course migration
-   Digital education analysis
-   Governance
-   Moodle Book inventories
-   Portfolio analysis
-   Interactive analytics dashboards

------------------------------------------------------------------------

# Data Protection

Ensure you have permission to analyse Moodle backups and that outputs
are stored securely. Avoid publishing backups or reports containing
sensitive information.

------------------------------------------------------------------------

# Roadmap

Planned future enhancements include:

-   Portfolio dashboards
-   Cross-course benchmarking
-   Accessibility metadata analysis
-   AI-assisted interpretation
-   Moodle Book deep analysis
-   Institutional benchmarking
-   Integration with additional CloudPedagogy tools

------------------------------------------------------------------------

# Licence

MIT License

------------------------------------------------------------------------

# Disclaimer

CloudPedagogy Moodle Course Auditor provides factual reports derived
from Moodle backup XML metadata.

Interactive dashboards visualise the same factual audit outputs and do
not introduce pedagogic scores, quality ratings or compliance
judgements.

The software supports institutional review processes but does not
replace academic judgement, quality assurance procedures, accessibility
audits or Moodle administration.
