---
name: kicad-board
description: Create, inspect, edit, save, reopen, export and check small KiCad PCB files in a bounded headless workspace.
license: MIT
metadata:
  dcc-mcp:
    dcc: kicad
    version: "0.1.0"
    layer: task
    compatibility: "KiCad 9 pcbnew; Python 3.10+; dcc-mcp-core and dcc-mcp-server 0.20.41"
    tools: tools.yaml
    tags: "pcb,kicad,board,headless,design-rules"
    search-hint: "KiCad PCB board native pcbnew create inspect edit track save reopen SVG DRC"
---

# KiCad board files

This adapter owns an isolated in-memory board. It never connects to an open PCB
Editor. All native operations run on the adapter's one serialized host lane.
The 0.20.41 shared-runtime candidate still requires native KiCad and real MCP
acceptance; the historical 0.20.39 native results do not qualify this candidate.

1. Inspect status and load or create a board. Creation makes an explicitly synthetic
   two-test-point board, not an electrically validated product. The optional
   create_sensor_carrier template adds 4 holes, 7 synthetic footprints, five nets,
   partial concept routing and silkscreen. It requires design review and leaves
   unconnected nets and real DRC findings visible; never call it fabrication-ready.
2. Inspect the current board and use its returned track UUID for width edits.
3. Save to an explicit workspace path. Existing files require overwrite=true.
   Unsaved state cannot be discarded without discard_unsaved=true.
4. Save verifies through native reopening. Export and DRC use isolated snapshots
   of the current board, including unsaved edits, without altering its source file.
5. For async tools, start once and follow the Core job ID with jobs_get_status.
   After a timeout, inspect that same job before retrying. Native calls cannot be
   interrupted mid-call; CLI export/DRC have a hard process timeout and cancellation
   checkpoints before and after the subprocess. No restart persistence is claimed.

DRC is diagnostic evidence, not electrical, fabrication, or safety certification.
No schematic editing, ERC, live IPC, arbitrary scripting, routing, library downloads,
or manufacturing release workflow is exposed. Keep all artifacts in the configured
workspace. Board input and report sizes are bounded to 10 MiB.

For concurrent clients, inspect/status first and echo `expected_revision` for
state-changing work. On `stale_revision`, inspect again; on `stale_file`, reopen
and reconcile external changes before overwrite. All symlink components beneath
the workspace are rejected. Async schemas cover initial Core jobs and terminal
results. `tools/list` is paginated; use its advertised tool names.
