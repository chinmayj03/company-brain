---
name: verify
description: Spawn a verifier sub-agent on a single entity URN.
args:
  - name: urn
    type: string
    required: true
---
Run spawn_verifier with one claim spec for the entity {urn}. The verifier
should:

  * read the entity from the brain to recover its qname + file,
  * grep + read primary sources to re-derive the claim,
  * emit a VERDICT / EVIDENCE block.

Return the resulting verdict and evidence in your final text.
