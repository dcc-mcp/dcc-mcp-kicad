# dcc-mcp-kicad

![DCC-MCP](https://raw.githubusercontent.com/dcc-mcp/.github/main/profile/dcc-mcp-logo.png)

Typed DCC-MCP adapter for **KiCad 9 headless PCB files**, using the installed
native `pcbnew` Python API and `kicad-cli`. This service owns an isolated board;
it does not connect to PCB Editor, manipulate an open board, or provide IPC/UI control.

## Install and start

Use a Python interpreter that can import the **KiCad 9** `pcbnew` module.
On Debian-based Linux the distribution's `/usr/bin/python3` is often required:

```sh
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install .
mkdir -p workspace
DCC_MCP_GATEWAY_PORT=0 .venv/bin/dcc-mcp-kicad --workspace "$PWD/workspace"
```

Python 3.10+ is declared because this adapter targets the KiCad 9 runtime rather
than the organization's older embedded-Python LTS profile. No Python 3.7 support
or release qualification is claimed. This candidate pins Core and server to
0.20.41. The native KiCad 9.0.2/Linux acceptance recorded for 0.20.39 is historical;
it does not qualify the upgraded runtime. See the [runtime upgrade and rollback
record](docs/RUNTIME_UPGRADE_0_20_41.md) for the new verification scope and remaining gates.

Workspace is mandatory (`--workspace` or `DCC_MCP_KICAD_WORKSPACE`). Port defaults
to an OS-assigned loopback port, printed as `mcp_url` in a JSON readiness line.
Connect a direct MCP client to that URL. The optional shared gateway is disabled by default. Set `--port` or
`DCC_MCP_KICAD_PORT` only when a fixed endpoint is required. Core owns MCP;
this adapter adds no external network service or independent gateway.

## Tools and workflow

Search/load skill `kicad-board`, then use:

- `status`: native version, lane identity, workspace and board state
- `create_board`: bounded 20–200 mm synthetic test board with two test points
- `create_sensor_carrier`: bounded 70–100 × 45–70 mm concept with four M3 holes,
  seven synthetic component footprints, five nets, partial routes and silkscreen;
  deliberately not electrically validated or a fabrication design
- `inspect_board`: item counts, stable track UUIDs and widths
- `set_track_width`: one UUID-selected straight track, 0.1–3 mm
- `save_board`: atomic save, native reopen, state comparison and SHA-256
- `open_board`: load an existing workspace `.kicad_pcb`
- `export_svg`: current snapshot's front copper, silkscreen and outline
- `run_drc`: native JSON DRC report and counts, including actual violations

All native tools use main affinity and in-process Core dispatch. Save/open/export/
DRC are async monolithic jobs: retain the returned job ID and poll Core
`jobs_get_status` to terminal completion. Never blindly repeat after a timeout.

The nine adapter tools add no arbitrary script execution. Core also exposes its own
admin/introspection tools, so this endpoint is for trusted local clients only.
Existing output needs `overwrite=true`;
unsaved session state needs `discard_unsaved=true` before replacement. Paths are
resolved against a single explicit root and reject traversal and all symlinks beneath the workspace.
Do not grant the workspace to untrusted users who can concurrently replace its
path components. This is a local trusted-workspace boundary, not an OS sandbox.

DRC results are diagnostics, **not electrical or manufacturing certification**.
Schematic editing, ERC, live IPC, autorouting, project configuration, production
fabrication outputs and GUI behavior are outside version 0.1 scope.

See [architecture](docs/architecture.md), [validation](docs/validation.md), and
[installation details](install.md). No package release is automated.

## Concurrency and recovery

Use `expected_revision` from status/inspection on edits and outputs to reject stale
work. Saving rechecks the loaded file's SHA-256 after staging and before publication;
detected external changes or deletion fail closed. Reopen and reconcile it first.
Pause external writers during saving: the final check and replacement are separate
filesystem operations. Stopped server objects cannot be reused; create a new server
instance or restart the process. The complete native/MCP gate is described in
[validation](docs/validation.md), including the official MCP Python SDK check.
