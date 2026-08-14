# Preparing a Moodle Backup for Course Analysis

This guide explains how to create a privacy-conscious Moodle backup containing the evidence needed by the CloudPedagogy Moodle Course Analysis Platform.

The recommended workflow uses Moodle's native `.mbz` backup format.

## Recommended settings

| Moodle backup setting | Select? | Reason |
|---|:---:|---|
| IMS Common Cartridge 1.1 | **No** | The platform expects Moodle's native backup structure. |
| Include enrolled users | **No** | Learner identities are unnecessary for normal structural/content analysis. |
| Anonymize user information | No | Normally unnecessary when enrolled users are excluded. |
| Include user role assignments | No | Individual user-role assignments are not required for the normal audit. |
| Include activities and resources | **Yes** | Essential for course structure, activities, Books, URLs and content mapping. |
| Include blocks | **Yes** | Provides a fuller record of course configuration. |
| Include files | **Yes** | Essential for storage analysis, Moodle-hosted media detection, extraction and content mapping. |
| Include filters | **Yes** | Retains relevant filtering/embedding configuration. |
| Include comments | No | Not used by the normal audit and may contain personal information. |
| Include badges | No | Not required by the current workflow. |
| Include calendar events | No | Not required by the current workflow. |
| Include user completion details | No | Learner data; not required. |
| Include course logs | No | Potentially sensitive and not required by the normal workflow. |
| Include grade history | No | Potentially sensitive and not required. |
| Include question bank | Optional | Select when question metadata analysis is required. |
| Include groups and groupings | Usually No | Select only when there is a specific need to retain/review this configuration. |
| Include custom fields | **Yes** | Retains useful course/activity metadata. |
| Include content bank content | **Yes when used** | Important for H5P and content-bank items. |
| Include user's state in content such as H5P activities | No | User attempts/state are not required. |
| Include legacy course files | **Yes when relevant** | Includes older Moodle-hosted files that may still contribute to the course/storage footprint. |

## Moodle backup wizard

### 1. Initial settings

Apply the settings above and select **Next**.

### 2. Schema settings

For a complete structural/content audit, keep all course sections, activities and resources selected.

Leave separate user-data options unselected unless there is a specific authorised requirement.

### 3. Confirmation and review

Before starting the backup, confirm that:

- the format is Moodle backup (`.mbz`);
- activities/resources are included;
- files are included;
- required sections/course items remain selected;
- enrolled users, logs, completion and grades are excluded unless specifically needed.

Then select **Perform backup**.

### 4. Perform backup

Wait for Moodle to complete the backup. Courses containing large Moodle-hosted media can create large `.mbz` files and take longer.

### 5. Download and store securely

Download the `.mbz` and store it in an authorised location.

Do not commit real Moodle backups to a public Git repository.

## Why these choices matter

The course analysis platform can only report evidence that exists in the supplied backup.

Particularly important dependencies are:

```text
Activities/resources
        |
        +--> course structure and activity analysis
        |
Files --+--> storage/media analysis
        |
        +--> file extraction
                 |
                 +--> content mapper
```

The content mapper needs both:

- auditor datasets describing the Moodle structure and resource placement; and
- actual files recovered by the extractor.

Therefore, excluding files can make both extraction and content mapping incomplete.

## If different options are selected

The tools normally analyse the evidence available rather than treating every missing category as an error. However:

- without files, storage figures and Moodle-hosted-media analysis are incomplete;
- without activities/resources, structure and external-reference analysis are incomplete;
- without the question bank, zero questions does not prove that the live course contains none;
- without content-bank content, H5P/content-bank evidence may be incomplete;
- including users, logs or grades may introduce unnecessary sensitive information even if a script does not use it.

Use consistent backup selections when comparing multiple courses or comparing before/after versions.

## Run the platform

From the repository root, activate the environment:

```bash
source .venv/bin/activate
```

Place one or more backups in:

```text
batch_input/
```

For example:

```text
batch_input/
`-- literature-review-2025.mbz
```

### Standard audit and dashboard

```bash
python3 src/orchestrator.py
```

### Audit, dashboard and file extraction

```bash
python3 src/orchestrator.py --extract-files
```

### Audit, dashboard, extraction and content map

```bash
python3 src/orchestrator.py --content-map
```

The orchestrator automatically enables extraction because `content_mapper.py` depends on the recovered files and auditor outputs.

### Full normal per-course workflow

```bash
python3 src/orchestrator.py --full
```

This runs the main auditor, dashboard, extractor, content mapper and settings analyser.

Generated results are written to:

```text
batch_output/
```

unless another output folder is explicitly supplied.

## Before/after comparison

Comparison is a separate workflow because it needs two specific backups:

```bash
python3 src/compare_mbz.py \
  earlier-course.mbz \
  later-course.mbz \
  --output-dir comparison_output
```

For meaningful comparisons, use equivalent Moodle backup selections for both versions wherever possible.

## Accuracy and responsible use

The platform reports evidence contained in the backup. Results can be affected by:

- Moodle version;
- installed plugins;
- backup selections;
- incomplete/inconsistent metadata.

External references are identified from stored metadata but are not necessarily opened or availability-tested.

The main audit inventories uploaded files using Moodle metadata; it does not semantically interpret uploaded PDFs, Word documents, presentations, videos, H5P or SCORM content.

Treat flags and differences as prompts for checking. Verify material findings against the source Moodle course before consequential decisions.

The platform supports, but does not replace, academic review, accessibility testing, quality assurance, data-protection review or Moodle administration.
