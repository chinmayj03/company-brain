---
name: cost
description: Show the most recent run's cost summary.
---
Look up the last completed harness session via the session registry. Print:

  * total cost in USD (4 dp)
  * cost by tool (top 5)
  * total LLM input + output tokens

If no recent session exists, say so plainly.
