---
name: diff
description: Show the brain diff between two repo refs.
args:
  - name: commit_a
    type: string
    required: true
  - name: commit_b
    type: string
    required: true
---
Compute the file-level diff between {commit_a} and {commit_b} using the
git_branch_diff tool, then for each changed source file:

  * note any entity that points at the file (use grep_code over .brain/),
  * call out functions whose code_snippet has changed.

Final output: a short bulleted list of (file, qname, "changed signature" |
"new" | "deleted" | "body changed").
