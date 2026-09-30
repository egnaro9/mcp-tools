# Security policy

An MCP server is a tool surface reached by a model, and the model's input comes
from whoever is talking to it. So every argument that arrives at a tool here is
attacker-controlled text, and the server is the boundary.

## Threat model

Assume the caller is hostile and can put any string in any tool argument. The
model relaying it is not a filter. The server is stdlib-only and holds no
credentials of its own, so the assets at risk are the host process and the
files the process can reach, not a secret store.

## In scope

- **Code execution or import through `calc`.** It parses to an AST and
  allow-lists arithmetic nodes, so `__import__('os')` is rejected rather than
  evaluated. Any input that reaches an attribute access, a name lookup, a call,
  or a comprehension is a finding.
- **Cost exhaustion through an allow-listed expression.** Rejecting injection
  and bounding cost are different jobs, and `9 ** 9 ** 9` allow-lists clean.
  The exponent is bounded by result width; an arithmetic expression that still
  hangs, allocates without limit, or takes superlinear time in its own length
  is a finding.
- **Path escape.** Any tool argument that reads or writes outside the
  configured corpus, via traversal, an absolute path, or a symlink.
- **Disclosure through an error.** Exception text that carries a filesystem
  path, an environment value, or any part of a URL query string. Note that
  truncating an error can defeat exact-match masking, so a masked value that
  reappears because the string was cut is a finding.
- **Outbound request abuse.** The live model-drift and eval-run lookups fetch
  over the network; getting them to fetch an attacker-chosen host, or a local
  address, is a finding.
- **Protocol-level confusion**: a malformed or oversized JSON-RPC frame on
  stdio that crashes the loop or desynchronizes it rather than returning an
  error.

## Out of scope

- **A tool returning a wrong answer.** The grader is deterministic and its
  scoring is a correctness concern, not a security one. File it as a normal
  issue; it is still worth reporting.
- **The model choosing to call a tool it should not have.** That is the host
  application's authorization decision, not this server's.
- **Resource use proportional to a legitimately large input**, as long as it is
  bounded and interruptible.

## Reporting

- **GitHub private advisory**, preferred:
  <https://github.com/egnaro9/mcp-tools/security/advisories/new>
- **Email**: erik@erikhill.dev

Send the exact tool call. Acknowledgement within 3 days, an answer within 14,
credit in the fix commit unless you would rather not. Accepted findings get a
test confirmed to fail against the pre-fix code.

## Known and fixed

- **Unbounded `ast.Pow`**, fixed 2026-09-28. The AST allow-list rejected
  injection but placed no bound on cost, so `calc('9**9**9')` allow-listed
  clean and then hung, under a README sentence claiming the tools were safe by
  construction. The bound now estimates the result's decimal width and refuses
  above 1,000 digits, and the README distinguishes the two jobs instead of
  claiming one covers both. The identical mapping in
  [agent-graph](https://github.com/egnaro9/agent-graph) was fixed in the same
  pass.

## Supported versions

`main`.
