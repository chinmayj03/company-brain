---
name: wipe
description: Clear the workspace's brain data (with confirm).
---
This command is destructive. Before doing anything:

  1. Print the absolute path of the .brain directory you would delete.
  2. Count how many entity files would be removed (use glob_files).
  3. Ask the human to confirm by replying "yes wipe".

You MUST NOT call write_to_brain or finalize_brain in this command. Surface the
plan and stop — the actual rm runs from the CLI side.
