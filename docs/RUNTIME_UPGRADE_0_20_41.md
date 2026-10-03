# Shared runtime 0.20.41 candidate

This candidate changes the exact `dcc-mcp-core` and `dcc-mcp-server` dependencies
from 0.20.39 to 0.20.41 and raises the CLI bootstrap minimum to 0.20.41. It keeps
the public Core composition API, lazy `pcbnew` import, serialized native lane,
bounded operations and existing `kicad = dcc_mcp_kicad:KicadMcpServer` plugin entry
point. The adapter remains version 0.1.0; no release or publishing step is implied.
The optional operator `dcc-mcp-cli` is resolved separately by the shared runtime
installation receipt, rather than added as an adapter runtime dependency.

## Verification scope

The new candidate is checked using a fresh Windows Python 3.12 environment with
the official 0.20.41 Core/server wheels. Portable checks cover lint, formatting,
manifest/schema contracts, the fake native lane and lifecycle, paths, cancellation
and wheel construction. The first attempt passed ruff check, ruff format
--check, pytest and wheel/sdist build on Windows/Python 3.12.10 with actual
Core/server/operator CLI 0.20.41 and MCP SDK 1.30.0. Pytest collected 47 cases:
32 passed and 15 skipped, with no failures. Thirteen skipped cases need native
`pcbnew` (including real host MCP); two need Windows symlink privileges. No new
skips or product compatibility fixes were introduced for this runtime upgrade.

Separate shared installation verification must record actual resolved runtime
versions, installed package origin, plugin loading and CLI help. Importing a
plugin or printing help does not run `pcbnew` or certify KiCad host support.

The bounded Windows test inventory did not find KiCad or its matching `pcbnew` interpreter.
Windows native tests and real host MCP tests therefore remain unqualified. The
older Linux KiCad 9.0.2/Python 3.13.5 evidence in [validation](validation.md) and
[wire cancellation](wire-cancellation-validation.md) used Core/server 0.20.39;
it is historical and is not the evidence for the acceptance below.

## Completed Linux acceptance on the reviewed baseline

Separate Linux acceptance against exact commit
`39cb7f9d4477162a583aa6665004eee6ab8f6346` used Core, server and operator CLI
0.20.41. The source suite passed 54 tests, and the same 54 tests passed after a
fresh wheel install; these are two runs of one suite, not 108 distinct tests.
Lint, formatting, build, skill validation, all nine tools through the real MCP
SDK, and native HTTP acceptance passed.

The native-tested Linux wheel SHA-256 was
`61d01681ebd4931bdb17fc4968c62d5019e4e20560bc9a7304ae275456e2b36b`.
This identifies the Linux acceptance artifact, not a locally retained Windows
wheel. The carrier reported zero DRC violations and 13 unconnected items; it
remains a partial concept, not a complete circuit or fabrication deliverable.

These completed results apply to that exact commit. The subsequent track-width
failure-recovery change has host-independent regression coverage and requires
fresh native source, installed-wheel, SDK and HTTP acceptance before the Linux
results apply to the updated code. Windows native hosts, other operating systems,
new KiCad versions or Python ABIs, full authentication, embedded fallback and
Install SOP certification remain outside the recorded qualification.

The sensor carrier remains a concept with partial routes and unconnected nets.
Passing these runtime checks cannot establish complete circuit, ERC or fabrication
acceptance. Likewise, a newer Core or default localhost binding does not prove that
stock Core administration tools, authorization or an embedded fallback are safe
for untrusted clients. Keep the documented trusted-client boundary until separate
capability and access-control checks pass.

## Preserved baseline and rollback

The exact previous source baseline is commit
`02d76ca68437c1eb0940c21fa99162237f83c4b3` on
`review/release-surface-kicad-20261002`, with Core/server 0.20.39. Its source,
candidate wheel and isolated installation environment are retained unchanged.
The 0.20.41 candidate is developed on a separate review branch and installed in a
new environment. Do not upgrade the prior environment in place or install a new
host as part of this runtime update.

Before switching, stop the old process and retain its workspace files and
artifacts. Use a new disposable synthetic workspace for candidate acceptance.
On failure, stop the candidate, preserve its failure receipt, and launch the
preserved 0.20.39 environment against the intended old workspace. This runtime
change adds no board migration or restart-persistent job recovery; inspect any
published artifact and reconcile external changes before retrying a mutation.
