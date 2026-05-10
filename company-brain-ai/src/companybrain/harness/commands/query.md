---
name: query
description: Run a natural-language query against the brain.
args:
  - name: question
    type: string
    required: true
---
Answer the user's question using the brain's stored knowledge.

  1. Use grep_code, glob_files, and read_file to locate any code anchors that
     might be relevant.
  2. Cite your sources by URN where you can. Do not speculate beyond what the
     brain knows.

Question: {question}
