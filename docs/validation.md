# Latest wire-cancellation follow-through

The newer [wire-cancellation acceptance](wire-cancellation-validation.md) closes
the older missing wire-to-native cancellation check recorded below. The latest
source and installed-wheel suites each have 45 passing tests. Runtime code is
unchanged by that extra test.

# Validation record

Local source implementation, 2026-10-02. This is not a release qualification.

## Exact native environment

- Linux x86-64; distribution Python 3.13.5 with system-site-packages virtual environment
- Native KiCad pcbnew and kicad-cli 9.0.2+dfsg-1
- Released dcc-mcp-core and dcc-mcp-server 0.20.39
- Adapter 0.1.0; no source-head Core patch or reference repository modifications

## Checks performed

- 19 pytest tests passed, including actual native board create/edit/save/reopen,
  SVG and DRC, the bounded carrier template at minimum/default/maximum dimensions with no shorts or
  solder-mask bridges, import safety, workspace traversal and
  symlink escapes, overwrite and unsaved-state guards, invalid finite numbers,
  unknown operations, missing tracks, CLI timeout/no-publish, and wrong-thread checks
- CLI readiness JSON was checked against a real dynamic-port launch: the printed endpoint accepted MCP initialize and SIGTERM exited cleanly
- Reentrant dispatcher test proves nested execution stays on the same dedicated
  lane instead of requeueing and deadlocking
- ruff check and ruff format --check passed; line length 120
- Actual installable skill validated with creator validate_skill_dir and Core public
  validate_skill; no errors or warnings
- Organization adapter contract strict profile: no errors or warnings
- Wheel/sdist built; bundled SKILL.md, tools.yaml and all tool scripts included

## Real MCP acceptance

Run the supplied script with a **new** output directory:

```sh
python scripts/native_acceptance.py /absolute/new-kicad-evidence
```

The script creates and stops an actual Core HTTP MCP server, disables the optional
shared gateway, then uses HTTP JSON-RPC rather than calling skill functions directly.
It records every request and response in mcp-transcript.json and a concise result.json.
The final run made 73 requests (poll counts vary) covering initialize, search, full description, progressive
load, status, create, width edit, asynchronous save, reopen, SVG, DRC, and negative
path/overwrite calls. Async requests were followed to terminal Core job results.
The template was also created, saved, exported and DRC-checked through MCP.

The script inspects /proc/self/fd socket inodes against /proc/net/tcp and asserts
that the process has exactly one TCP listener, at 127.0.0.1 on its advertised MCP
port. It asserts server and host lane stopped after acceptance. No gateway or
LAN listener is created in this configuration. Ephemeral ports and native UUIDs
are intentionally nondeterministic; geometry and output checks are deterministic.

Toy board: 2 footprints, 1 track edited from 0.8 to 1.2 mm, 4 outline segments.
Native DRC reported zero violations and zero unconnected items for this toy.
Carrier concept: 11 footprints (including four mounting holes), five named nets,
13 track segments and 16 native drawing objects. Native DRC reported zero
geometric violations and 13 unconnected items; all findings are preserved;
read its JSON report before any further design work. This is a concept layout,
not an electrically validated device or a manufacturing deliverable.

Artifacts are new editable .kicad_pcb files, native SVG exports, native DRC JSON,
and MCP transcripts. No earlier test artifact, user file or open editor board is
modified. Artifacts and machine-specific transcripts remain outside the repository.

## Known limits and failures resolved

Initial bare QueueDispatcher wiring double-dispatched a main-affinity HTTP call
back onto its own queue. A minimal reentrant facade over the same Core queue fixed
this; the regression test and actual MCP calls prove the correction. Initial save
comparison depended on pcbnew container order; stable sorting fixed false mismatches.
A startup probe used Core's default gateway before the optional gateway was disabled;
that process was stopped and deregistered. Final runs explicitly verify loopback-only.

No GUI, live IPC, schematics, ERC, project-rule DRC, Windows/macOS, KiCad 10,
Python 3.7, production fabrication or distribution release gate was tested.
Native monolithic calls are not claimed to support immediate cancellation. The
workspace boundary assumes trusted local files and no concurrent hostile edits.

## Production-hardening candidate gates (2026-10-02)

This section supersedes the baseline counts above. It qualifies the bounded,
trusted-client headless Linux profile; it is not a published release or a claim
of electrical/manufacturing readiness.

- 44 source pytest cases pass (32 host-independent, 12 native), including native
  version mismatch rejection, malformed and oversized PCB files, stale revisions
  and external file conflicts, every in-root symlink and special-file rejection,
  workspace identity drift, bounded artifact reads, complete-board corruption
  detection, 12 concurrent callers on one native lane, context propagation,
  expired queued callbacks, cancellation before create/save commit, actual CLI
  kill/wait with absence of a late sentinel, and owner-safe stop/recreate cycles
- Ruff lint/format, creator validation (zero warnings/errors), installed skill
  validation and strict organization repository contract pass; wheel and sdist build
- The final wheel is installed into a separate system-site-packages Python 3.13
  environment and imported from site-packages outside the source tree. The full
  test suite is repeated there, with native gates enabled
- Final installed-wheel HTTP acceptance: 72 requests; advertised loopback-only
  listener; async jobs followed to terminal results; native create/edit/save/open,
  SVG and DRC; service and native lane stopped afterward
- Installed CLI readiness JSON accepted a real MCP initialize request, and
  SIGTERM exited with code 0
- Official MCP Python SDK 1.30.0 acceptance: 9 tools discovered across every
  tools/list page; 16 adapter calls; typed input rejection and successful output
  validation; initial asynchronous Core job and terminal skill-envelope schemas;
  stale revision and unknown-job handling; clean shutdown and installed origin
- Native output remains honest: the toy has zero DRC violations/unconnected items;
  the sensor-carrier concept has zero geometric violations and 13 unconnected
  items. Schematic parity was not requested and is not a schematic/ERC pass

Reproduce against an installed wheel using a matching KiCad Python, from outside
this source tree: run `scripts/validate_skill.py`, the copied tests,
`scripts/native_acceptance.py <fresh-dir>`, and
`scripts/sdk_acceptance.py <another-fresh-dir>`. The SDK test dependency is in the
`dev` extra. Preserve both evidence directories and test logs.

CI now repeats host-independent checks against an installed wheel. Its manual
`native-wheel` job requires a pre-provisioned self-hosted Linux `kicad-9` runner
and performs no host upgrade. These workflow definitions were reviewed locally;
no remote GitHub CI run, publication or release was triggered.

### Remaining qualification boundaries

- Only KiCad 9.0.2 / Linux / Python 3.13.5 / Core 0.20.39 is native-qualified;
  Python 3.10 is a declared CI/source profile, not a native result from this run
- CLI reports use default snapshot rules; project-specific DRC, schematics, ERC,
  GUI/IPC, other OSes and KiCad 10 remain outside this adapter's tested scope
- Native calls remain monolithic. Cooperative checkpoint cancellation and actual
  CLI timeouts are tested; immediate interruption inside pcbnew, crash durability,
  and a wire-level Core cancellation round trip are not claimed
- Core's built-in admin/introspection and externally configured skills remain
  trusted-client capabilities; an untrusted-client least-privilege profile needs
  a shared Core public API. There is no adapter-local private-registry patch
- No remote CI, public package, tag, catalog release, installer publication or
  production deployment was performed

Primary references: [KiCad 9 CLI](https://docs.kicad.org/9.0/en/cli/cli.html) and
[official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
