---
name: explain
description: Generate a natural-language explanation of one method.
args:
  - name: method_qname
    type: string
    required: true
---
Explain the method {method_qname} in plain prose for a new engineer:

  * what does it do (one sentence)?
  * what does it read / write?
  * what calls it?
  * what side effects or invariants are worth knowing?

Use grep_code + read_file to ground the answer in source. Cite the file and
qname in your final output.
