# Preparing a Moodle Backup for Course Audit

This guide explains how to create a privacy-conscious Moodle backup containing the evidence needed by CloudPedagogy Moodle Course Auditor.

## Recommended settings

Create a normal Moodle `.mbz` backup using these initial settings.

| Moodle backup setting | Select? | Reason |
|---|:---:|---|
| IMS Common Cartridge 1.1 | No | The auditor expects Moodle's native backup structure. |
| Include enrolled users | No | Learner identities are unnecessary for a course-content audit. |
| Anonymize user information | No | Normally unnecessary when enrolled users are excluded. |
| Include user role assignments | No | Individual role assignments are not needed. |
| Include activities and resources | **Yes** | Essential for sections, activities, Books, URLs and embedded content. |
| Include blocks | **Yes** | Provides a fuller record of course configuration. |
| Include files | **Yes** | Essential for file counts, storage and Moodle-hosted video detection. |
| Include filters | **Yes** | Retains relevant filtering and embedding configuration. |
| Include comments | No | Not used and may contain personal information. |
| Include badges | No | Not required by the current audit. |
| Include calendar events | No | Not required by the current audit. |
| Include user completion details | No | Learner data; not needed. |
| Include course logs | No | Potentially sensitive and not used. |
| Include grade history | No | Potentially sensitive and not used. |
| Include question bank | Optional | Select when question metadata is required. |
| Include groups and groupings | No | Select only if group configuration is specifically required. |
| Include custom fields | **Yes** | Retains useful course and activity metadata. |
| Include content bank content | **Yes** | Important for H5P and content-bank items. |
| Include user's state in content such as H5P activities | No | User attempts/state are not required. |
| Include legacy course files | **Yes** | Includes older files that may contribute to storage or course content. |

## Moodle backup wizard

### 1. Initial settings

Apply the settings above, then select **Next**.

### 2. Schema settings

Keep all course sections, activities and resources selected for a complete audit. Leave separate user-data options unselected.

### 3. Confirmation and review

Confirm that the format is Moodle backup (`.mbz`), activities/resources and files are included, required course items remain selected, and enrolled users, logs, completion and grades are excluded. Then select **Perform backup**.

### 4. Perform backup

Wait for Moodle to finish. Courses containing uploaded video can create large backups and take longer.

### 5. Complete

Download the `.mbz` and store it securely. Do not commit Moodle backups to a public Git repository.

## Why these choices matter

This configuration captures course design, content, files, storage and hosting evidence while avoiding unnecessary learner data. The critical selections are activities/resources, files and filters. Blocks, custom fields, content-bank content and legacy files improve completeness.

## If different options are selected

The auditor should normally analyse the evidence available rather than fail. However, excluded content cannot be reported reliably:

- Without files, storage and Moodle-hosted-video findings are incomplete.
- Without activities, structure and external-reference analysis is incomplete.
- Without the question bank, zero questions does not prove that the live course contains none.
- Without content-bank content, H5P/content-bank findings may be incomplete.
- Including users, grades or logs may add unnecessary sensitive data even if the auditor ignores it.

Use consistent selections when comparing several courses.

## Accuracy and responsible use

The auditor reports evidence in the supplied backup. Results can be affected by Moodle version, installed plugins, backup selections and inconsistent metadata. External references are identified but not opened or availability-tested. Uploaded documents, videos, H5P and SCORM packages are inventoried from metadata rather than interpreted internally.

Treat review flags as prompts for checking and verify material findings against Moodle before consequential decisions. The audit supports—but does not replace—academic review, accessibility testing, quality assurance, data-protection review or Moodle administration.

## Run the audit

```bash
source .venv/bin/activate

python moodle_mbz_course_auditor.py "course-backup.mbz" \
  --output "output/course_audit"

python moodle_dashboard_generator.py "output/course_audit"

open "output/course_audit/dashboard.html"
```

On Windows or Linux, open `dashboard.html` from the file manager or browser if the macOS `open` command is unavailable.
