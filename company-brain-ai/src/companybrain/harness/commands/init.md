---
name: init
description: Bootstrap a new repo's .brain/BRAIN.md and .brain/hooks/ directory.
---
You are bootstrapping a fresh company-brain workspace. Steps:

  1. Detect the framework (use the harness skill loader's logic — call
     read_file on pom.xml / package.json / pyproject.toml / Gemfile to read
     the markers).
  2. Create .brain/BRAIN.md with two sections:
       ## Curated notes (human-edited)
       <auto-section marker>
     Seed the curated section with one suggested note based on the framework
     detection (e.g. "JsonKeyMapping is a constants table for Spring repos —
     don't extract as code entity").
  3. Create .brain/hooks/ directory (empty placeholder files for
     pre_extraction.sh and post_extraction.sh — make them executable + just
     `#!/bin/sh\nexit 0\n`).

Final output: a one-paragraph summary of what was created and the framework
detected. Do not call write_to_brain or finalize_brain.
