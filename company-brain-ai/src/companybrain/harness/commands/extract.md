---
name: extract
description: Run the canonical extraction pipeline for one HTTP endpoint.
args:
  - name: endpoint
    type: string
    required: true
  - name: method
    type: string
    required: false
    default: GET
---
You are extracting a single HTTP endpoint into the brain. Follow the canonical
pipeline:

  1. discover_routes(repo_path) — confirm the endpoint exists.
  2. find_entry_handler(endpoint={endpoint}, http_method={method},
                        repo_path=context.repo_path)
  3. list_candidate_files(endpoint={endpoint}, repo_path=context.repo_path)
  4. spawn_extractor(files=<one entry per candidate>) — fan out per-file
     extraction sub-agents.
  5. write_to_brain(entities, edges) — persist the extraction results.
  6. finalize_brain(workspace_id) — close the run.

End with one short paragraph summarising what was extracted.

Endpoint: {method} {endpoint}
