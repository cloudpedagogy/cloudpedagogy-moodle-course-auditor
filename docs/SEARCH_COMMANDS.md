# Moodle Course Analysis Platform --- Search Command Reference

This file is the practical command reference for `src/search_mbz.py`.

Search / Inspector is currently an independent component of the
CloudPedagogy Moodle Course Analysis Platform. It is not invoked by
`src/orchestrator.py` and is not included in the orchestrator's `--full`
workflow.

The examples assume commands are run from the repository root.

## 1. Check the interface

Always confirm the options supported by the checked-out version:

``` bash
python3 src/search_mbz.py --help
```

## 2. Basic text search

Search one backup:

``` bash
python3 src/search_mbz.py course.mbz \
  --text "assessment" \
  -o output/search
```

Search every `.mbz` in `batch_input/`:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  -o batch_output/search
```

## 3. Multiple search terms

Multiple `--text` / `--term` values use OR behaviour by default:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  --text "feedback" \
  -o batch_output/assessment_or_feedback
```

Require all supplied terms within the matching record:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  --text "feedback" \
  --match-all-terms \
  -o batch_output/assessment_and_feedback
```

## 4. Exclude text

Find records containing one term while excluding another:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  --exclude-text "formative" \
  -o batch_output/assessment_not_formative
```

Exclusions are applied at record level.

## 5. Activity type

Inventory/search a Moodle activity type:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --activity-type forum \
  -o batch_output/forums
```

Another example:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --activity-type quiz \
  -o batch_output/quizzes
```

`--activity-type` is repeatable where multiple activity types are
required.

## 6. Activity name

Search activity names:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --name-any "noticeboard" "discussion" \
  -o batch_output/named_activities
```

## 7. Moodle XML fields

Restrict text matching to a specific captured XML field:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "deadline" \
  --field message \
  -o batch_output/message_deadline
```

Use more than one field:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "deadline" \
  --field subject \
  --field message \
  -o batch_output/subject_or_message_deadline
```

Repeated `--field` values select any of the named fields.

## 8. Forum discussions and posts

Search retained forum-post/message evidence:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --activity-type forum \
  --text "deadline" \
  --forum-posts \
  --field message \
  -o batch_output/forum_deadline
```

Target named forum activities as well:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --activity-type forum \
  --name-any "noticeboard" "discussion" \
  --text "welcome" \
  --forum-posts \
  --field message \
  -o batch_output/forum_welcome
```

Actual discussions/posts are user-generated activity data. If they were
not retained by the Moodle backup, the correct result may be
`UNABLE TO DETERMINE`.

## 9. Visibility

Find hidden activities:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --visibility hidden \
  -o batch_output/hidden
```

Find visible activities:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --visibility visible \
  -o batch_output/visible
```

## 10. Section filtering

Restrict results to a section:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --section "Introduction" \
  --text "assessment" \
  -o batch_output/introduction_assessment
```

Section matching depends on the section evidence/naming retained in the
backup.

## 11. File type

Find PDF file metadata:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --filetype pdf \
  -o batch_output/pdfs
```

Find Word documents:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --filetype docx \
  -o batch_output/docx
```

Find PowerPoint files:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --filetype pptx \
  -o batch_output/pptx
```

These searches inspect file metadata. They do not search inside the
binary contents of the uploaded documents.

## 12. Filename

Search uploaded-file names:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --filename "assessment" \
  -o batch_output/assessment_files
```

`--filename` is repeatable.

## 13. Domain / URL evidence

Search for a domain:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --domain "example.com" \
  -o batch_output/example_domain
```

Domain matching can include relevant subdomain evidence retained in
Moodle content.

## 14. Scope

Restrict a search to activity content:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --scope activity_content \
  --text "assessment" \
  -o batch_output/activity_content_assessment
```

Course-level evidence:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --scope course \
  --text "assessment" \
  -o batch_output/course_assessment
```

Section-level evidence:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --scope section \
  --text "assessment" \
  -o batch_output/section_assessment
```

File metadata:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --scope file \
  --text "assessment" \
  -o batch_output/file_assessment
```

Supported scopes are:

``` text
course
section
activity
activity_content
file
```

## 15. Result modes

Show the normal full result set:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  --result-mode all \
  -o batch_output/result_all
```

Show courses/results where evidence was found:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  --result-mode found \
  -o batch_output/result_found
```

Focus on missing/not-found results:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assessment" \
  --result-mode missing \
  -o batch_output/result_missing
```

## 16. Case sensitivity

Matching is case-insensitive by default.

Require case-sensitive matching:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "Assessment" \
  --case-sensitive \
  -o batch_output/case_sensitive
```

## 17. Regular expressions

Use a regular expression:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --text "assess(ment|ments)" \
  --regex \
  -o batch_output/regex_search
```

Regex changes how the supplied text pattern is interpreted. Evidence
snippets are intended as context and may not always display the entire
matched expression.

## 18. Recursive batch search

Search `.mbz` files in the batch folder and its subfolders:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --recursive \
  --text "assessment" \
  -o batch_output/recursive_search
```

## 19. Combined filters

Filters can be combined to answer more specific questions.

Example: hidden activity content containing `assessment`:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --visibility hidden \
  --scope activity_content \
  --text "assessment" \
  -o batch_output/hidden_assessment
```

Example: retained forum messages containing `deadline`:

``` bash
python3 src/search_mbz.py batch_input \
  --batch \
  --activity-type forum \
  --forum-posts \
  --field message \
  --text "deadline" \
  -o batch_output/forum_message_deadline
```

Combined filters are constraints on the same search. They do **not**
mean "run each filter as a separate search."

## 20. Output files

A normal Search run can produce:

``` text
search_report.html
course_summary.csv
matches.csv
search_data.json
run_log.csv
```

`search_report.html` is the main human-readable report.

`course_summary.csv` provides course-level search status/count
information.

`matches.csv` contains the matching evidence exposed for tabular review.

`search_data.json` retains the fuller structured search evidence.

`run_log.csv` records processing/audit information for the run.

## 21. Evidence states

Search distinguishes:

-   `FOUND` --- matching evidence was found;
-   `NOT FOUND` --- relevant evidence was available but no match was
    found;
-   `UNABLE TO DETERMINE` --- the backup does not contain enough
    evidence for the requested determination;
-   `FAILED` --- processing failed.

Do not automatically convert `UNABLE TO DETERMINE` into `NOT FOUND`.

## 22. Backup requirements

For ordinary structural/activity/metadata searches, the principal Moodle
backup selection is:

-   **Include activities and resources**.

For searches of actual forum discussions/posts or other user-generated
activity content, also consider:

-   **Include enrolled users**;
-   **Anonymize user information**, where appropriate and supported.

Other backup components should be included only when needed for the
analysis being performed.

Search can work with reference-only/file-metadata backups for its
current file-name/type functions because it does not search inside
uploaded binary file contents.

See `MOODLE_BACKUP_INSTRUCTIONS.md` for the platform's full backup
guidance.

## 23. Privacy and safe use

Moodle backups may contain personal, sensitive or copyrighted
information. Use only backups you are authorised to process, minimise
user data, store inputs and outputs securely, and never commit real
institutional `.mbz` files or sensitive generated reports to a public
repository.

Search is local-first: processing is performed on the
machine/environment where the script is run.

## 24. Current search boundary

The current Search component covers Moodle-native XML and uploaded-file
metadata retained in the backup.

It does not currently extract and index the internal text of uploaded
PDF, DOCX, PPTX or other binary documents. That is a separate capability
from metadata search.

For interpretation, architecture and limitations, see `HANDBOOK.md`.
