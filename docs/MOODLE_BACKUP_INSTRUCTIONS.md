# Preparing a Moodle Backup for Course Analysis

This guide explains how to create a privacy-conscious Moodle backup
containing the evidence needed by the CloudPedagogy Moodle Course
Analysis Platform.

The recommended workflow uses Moodle's native `.mbz` backup format.

## Recommended settings

  --------------------------------------------------------------------------
  Moodle backup                   Select?              Reason
  setting                                              
  ------------------- -------------------------------- ---------------------
  IMS Common                       **No**              The platform expects
  Cartridge 1.1                                        Moodle's native
                                                       backup structure.

  Include enrolled        **No for normal audit**      Learner identities
  users                                                are unnecessary for
                                                       normal
                                                       structural/content
                                                       analysis. Include
                                                       only when an
                                                       authorised
                                                       Search/Inspector task
                                                       requires
                                                       user-generated
                                                       activity evidence
                                                       such as actual forum
                                                       discussions/posts.

  Anonymize user      No for normal audit; **Yes where Normally unnecessary
  information          appropriate/supported if users  when enrolled users
                             must be included**        are excluded. When
                                                       user-generated
                                                       evidence is required,
                                                       anonymisation can
                                                       reduce unnecessary
                                                       exposure of identity
                                                       data while retaining
                                                       analysable content,
                                                       subject to Moodle's
                                                       backup behaviour.

  Include user role                  No                Individual user-role
  assignments                                          assignments are not
                                                       required for the
                                                       normal audit.

  Include activities              **Yes**              Essential for course
  and resources                                        structure,
                                                       activities, Books,
                                                       URLs and content
                                                       mapping.

  Include blocks                  **Yes**              Provides a fuller
                                                       record of course
                                                       configuration.

  Include files                   **Yes**              Essential for storage
                                                       analysis,
                                                       Moodle-hosted media
                                                       detection, extraction
                                                       and content mapping.

  Include filters                 **Yes**              Retains relevant
                                                       filtering/embedding
                                                       configuration.

  Include comments                   No                Not used by the
                                                       normal audit and may
                                                       contain personal
                                                       information.

  Include badges                     No                Not required by the
                                                       current workflow.

  Include calendar                   No                Not required by the
  events                                               current workflow.

  Include user                       No                Learner data; not
  completion details                                   required.

  Include course logs                No                Potentially sensitive
                                                       and not required by
                                                       the normal workflow.

  Include grade                      No                Potentially sensitive
  history                                              and not required.

  Include question                Optional             Select when question
  bank                                                 metadata analysis is
                                                       required.

  Include groups and             Usually No            Select only when
  groupings                                            there is a specific
                                                       need to retain/review
                                                       this configuration.

  Include custom                  **Yes**              Retains useful
  fields                                               course/activity
                                                       metadata.

  Include content            **Yes when used**         Important for H5P and
  bank content                                         content-bank items.

  Include user's                     No                User attempts/state
  state in content                                     are not required.
  such as H5P                                          
  activities                                           

  Include legacy           **Yes when relevant**       Includes older
  course files                                         Moodle-hosted files
                                                       that may still
                                                       contribute to the
                                                       course/storage
                                                       footprint.
  --------------------------------------------------------------------------

## Search / Inspector backup profiles

`search_mbz.py` can often work with a substantially lighter backup than
the complete audit/extraction/content-map workflow. Select backup
evidence according to the question being asked rather than including
data that Search does not need.

### Structural and metadata search

For searches such as:

-   activity type or activity name;
-   section;
-   hidden/visible state;
-   Moodle-native text retained with activities/resources;
-   URLs/domains;
-   uploaded-file names and file types;

the principal selection is:

-   **Include activities and resources --- Yes**.

Other categories should be included only when they are needed for the
particular search or for another platform workflow.

### Forum discussions/posts and other user-generated activity content

A forum activity can exist in a backup even when its actual
discussions/posts are absent. For an authorised search that needs the
content of actual forum discussions/posts or comparable user-generated
activity evidence, use:

  -----------------------------------------------------------------------
  Moodle backup setting               Search recommendation
  ----------------------------------- -----------------------------------
  Include activities and resources    **Yes**

  Include enrolled users              **Yes when required to retain the
                                      user-generated evidence**

  Anonymize user information          **Yes where appropriate and
                                      supported**
  -----------------------------------------------------------------------

If the required post/user-generated evidence was not retained in the
backup, Search may return `UNABLE TO DETERMINE`. This is deliberately
different from `NOT FOUND`: absence from the backup is not proof that
the content did not exist in the live Moodle course.

### Lightweight / reference-only file backups

For current Search functions that inspect uploaded-file metadata, such
as filename and file-type searches, the binary contents of the uploaded
files are not required.

A Moodle backup that retains file references/metadata can therefore
still support searches such as:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --filetype pdf \
  -o batch_output/pdf_search
```

Search currently does **not** search inside the binary contents of
uploaded PDF, DOCX, PPTX or other files.

This lightweight Search profile is different from the
extraction/content-map workflow. If actual Moodle-hosted files need to
be recovered by `extract_moodle_files.py`, or bundled into a content
map, use the fuller backup settings described above.

See `SEARCH_COMMANDS.md` for Search/Inspector command examples.

## Moodle backup wizard

### 1. Initial settings

Apply the settings above and select **Next**.

### 2. Schema settings

For a complete structural/content audit, keep all course sections,
activities and resources selected.

Leave separate user-data options unselected unless there is a specific
authorised requirement. One such exception is a Search/Inspector task
that requires actual forum discussions/posts or other user-generated
activity evidence.

### 3. Confirmation and review

Before starting the backup, confirm that:

-   the format is Moodle backup (`.mbz`);
-   activities/resources are included;
-   files are included;
-   required sections/course items remain selected;
-   enrolled users, logs, completion and grades are excluded unless
    specifically needed;
-   if Search must inspect actual forum discussions/posts or other
    user-generated activity content, the required user-data option has
    been deliberately selected and anonymisation considered.

Then select **Perform backup**.

### 4. Perform backup

Wait for Moodle to complete the backup. Courses containing large
Moodle-hosted media can create large `.mbz` files and take longer.

### 5. Download and store securely

Download the `.mbz` and store it in an authorised location.

Do not commit real Moodle backups to a public Git repository.

## Why these choices matter

The course analysis platform can only report evidence that exists in the
supplied backup.

Particularly important dependencies are:

``` text
Activities/resources
        |
        +--> course structure and activity analysis
        |
        +--> Search / Inspector (structure, activity and Moodle-native metadata)
        |
Files --+--> storage/media analysis
        |
        +--> file extraction
        |        |
        |        +--> content mapper
        |
        +--> Search / Inspector (file metadata)

Enrolled users (only when specifically required)
        |
        +--> retained forum posts / other user-generated activity evidence
```

The content mapper needs both:

-   auditor datasets describing the Moodle structure and resource
    placement; and
-   actual files recovered by the extractor.

Therefore, excluding files can make both extraction and content mapping
incomplete.

## If different options are selected

The tools normally analyse the evidence available rather than treating
every missing category as an error. However:

-   without files, storage figures and Moodle-hosted-media analysis are
    incomplete;
-   without activities/resources, structure and external-reference
    analysis are incomplete;
-   without the question bank, zero questions does not prove that the
    live course contains none;
-   without content-bank content, H5P/content-bank evidence may be
    incomplete;
-   including users, logs or grades may introduce unnecessary sensitive
    information even if a script does not use it;
-   without retained user-generated activity evidence, a forum-post
    Search may be `UNABLE TO DETERMINE` rather than `NOT FOUND`.

Use consistent backup selections when comparing multiple courses or
comparing before/after versions.

## Run the platform

From the repository root, activate the environment:

``` bash
source .venv/bin/activate
```

Place one or more backups in:

``` text
batch_input/
```

For example:

``` text
batch_input/
`-- literature-review-2025.mbz
```

### Standard audit and dashboard

``` bash
python3 src/orchestrator.py
```

### Audit, dashboard and file extraction

``` bash
python3 src/orchestrator.py --extract-files
```

### Audit, dashboard, extraction and content map

``` bash
python3 src/orchestrator.py --content-map
```

The orchestrator automatically enables extraction because
`content_mapper.py` depends on the recovered files and auditor outputs.

### Full normal per-course workflow

``` bash
python3 src/orchestrator.py --full
```

This runs the main auditor, dashboard, extractor, content mapper and
settings analyser.

Generated results are written to:

``` text
batch_output/
```

unless another output folder is explicitly supplied.

### Search / Inspector

Search is currently run independently of the orchestrator.

For example:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  -o batch_output/search
```

For retained forum-message content:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --activity-type forum \
  --text "deadline" \
  --forum-posts \
  --field message \
  -o batch_output/forum_search
```

See `SEARCH_COMMANDS.md` for the complete command reference.

## Before/after comparison

Comparison is a separate workflow because it needs two specific backups:

``` bash
python3 src/compare_mbz.py \
  earlier-course.mbz \
  later-course.mbz \
  --output-dir comparison_output
```

For meaningful comparisons, use equivalent Moodle backup selections for
both versions wherever possible.

## Accuracy and responsible use

The platform reports evidence contained in the backup. Results can be
affected by:

-   Moodle version;
-   installed plugins;
-   backup selections;
-   incomplete/inconsistent metadata.

External references are identified from stored metadata but are not
necessarily opened or availability-tested.

The main audit inventories uploaded files using Moodle metadata. Search
can inspect retained Moodle XML and uploaded-file metadata, including
filenames and file types. Neither component semantically interprets or
searches inside uploaded PDFs, Word documents, presentations, videos,
H5P or SCORM content.

Treat flags and differences as prompts for checking. Verify material
findings against the source Moodle course before consequential
decisions.

The platform supports, but does not replace, academic review,
accessibility testing, quality assurance, data-protection review or
Moodle administration.
