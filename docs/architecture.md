# Architecture and safety boundaries

This is a standalone process with a headless **KiCad 9 pcbnew file runtime**.
It has no external GUI host PID, no editor IPC and no editor lifetime binding.
The adapter service owns its lifetime and its one in-memory board.

## Execution

DccServerOptions.from_env composes DccServerBase with the bundled skill path and
HostExecutionBridge before discovery. Core's native QueueDispatcher and
StandaloneHost provide one persistent serialized native lane. A small
NativeDispatcher facade recognizes re-entry on that same thread, so a native
HTTP main-affinity request does not queue itself again while running a skill.
No second scheduler, job registry, HTTP server or per-request thread is added.
Every native operation checks its owning thread and Core cancellation checkpoint.

Core owns discovery/search/description/loading, HTTP JSON-RPC, canonical jobs,
result routing and shutdown. Runtime never parses its SKILL.md or tools.yaml.
Native imports are lazy. Server version is adapter 0.1.0; host version is captured
from pcbnew on the native lane before startup registration. The board is destroyed
on its owner lane on shutdown, then that lane is stopped and runtime state cleared.

## State and files

One runtime per process is supported. Each state mutation is read back. Saving
stages into a temporary sibling directory, reopens through pcbnew and compares
stable summaries, publishes atomically, then reopens the published board.
Summaries sort UUID/reference fields to avoid container-order false mismatches.
No-overwrite publication uses exclusive hard linking on the same filesystem;
explicit overwrite uses atomic replacement. Filesystems must support hard links.

The workspace is mandatory. Canonical resolution rejects escapes and symlinks
that resolve outside it. Paths are local, suffix-restricted, with existing parent
directories. Treat the workspace as trusted; no defense against a hostile process
concurrently replacing path components is promised. Existing files require an
explicit overwrite option. Dirty boards require explicit discard authorization.
Input files and produced reports are limited to 10 MiB. Loaded boards are capped
at 10,000 top-level items and inspection returns at most 1,000 tracks/footprints.
Native parsing still occurs before that item-count check; use trusted PCB files.

SVG and DRC run against isolated current-board snapshots, using argument vectors,
no shell and an isolated XDG configuration/cache/data root. CLI subprocesses have
1–120 second hard timeouts. Reports are parsed and SHA-256 checked before success.
The original board's project settings are not loaded into the temporary snapshot:
DRC uses KiCad defaults and does not claim project-specific rule coverage.

## Cancellation and recovery

Native calls are monolithic and cannot be safely interrupted mid-call. Core
cancellation is checked before operations, and before/after CLI processing; CLI
process timeouts kill and wait for that child. Queued work follows Core admission
and cancellation handling. A timed-out transport does not cancel or prove absence
of mutations. Poll the same Core job ID before resubmission. In-memory state and
jobs do not promise durability across process restarts.

## Network

The adapter endpoint is Core's loopback HTTP listener. The CLI disables Core's optional shared
gateway. Programmatic callers can explicitly supply gateway_port to opt in to
Core's independent gateway binding settings. Native acceptance explicitly disables gateway election,
then verifies process-owned TCP listeners from /proc match exactly 127.0.0.1 on
the advertised MCP port. No firewall, credentials or security settings are changed.

## Hardened execution profile

The runtime probes `kicad-cli version` with a five-second timeout on the native
lane. A discovered CLI must match pcbnew's KiCad 9 major/minor version; patch
versions may differ. An absent CLI permits board operations, while reports fail
closed. Status includes both versions, compatibility and Python version.

The nine typed operations expose closed input and output schemas. Async output
schemas cover both Core's initial job envelope and the terminal canonical skill
result. Core's names advertised by `tools/list` are local names; clients must
exhaust pagination. Canonical `kicad_board__...` aliases remain routable.

All state-changing operations accept optional `expected_revision`. Inspection
and status return the current revision. Echo it to reject stale concurrent work;
if omitted, existing last-writer-wins session semantics apply. Reopening or
successful save increments revision. Saving over the currently loaded file also
checks its original SHA-256 before staging and again immediately before publication,
and refuses detected external changes or deletion, even with `overwrite=true`;
reopen/reconcile before replacing that file. Hash checking and replacement are
separate filesystem operations. Pause external writers during saving; this is not
an atomic compare-and-swap against concurrent editors.

The workspace identity is checked on every path admission. Paths are limited to
4,096 characters and reject every symlink beneath the workspace, including
in-root aliases, plus non-regular files. Atomic no-overwrite publication remains
exclusive. Before publication, the staged file is flushed with fsync. Save
verification compares the complete board's canonical native reopen/resave bytes,
not just the bounded inspection summary, and checks the 10 MiB artifact limit.
This is atomic visibility, not a guarantee of durability through power failure.

Cancellation checkpoints precede state replacement and final artifact publication.
A dispatcher waiter that times out marks its not-yet-started callback expired;
subsequent queue processing rejects it without calling native code. Contextvars,
including cancellation context, are copied into the owner lane. Cancellation is
cooperative: there is a race after the last checkpoint, native calls already in
progress cannot be forcibly interrupted, and cancellation after publication does
not roll it back. The actual CLI timeout gate kills and waits for its subprocess;
no artifact is published on timeout. Core job status is authoritative after a
transport timeout. None of these guarantees imply crash-durable job recovery.

Shutdown releases the board, pcbnew module reference, path and thread identity
on the owning lane, then shuts down Core's queue. Cleanup is owner-scoped, so a
rejected second server cannot clear an active runtime. A stopped server object
is deliberately single-use; restarting means constructing a fresh server or
launching a fresh process. This avoids reusing a shutdown Core queue.

## Trust boundary of Core's built-in tools

The following capability review describes the historically measured 0.20.39
artifact. The [0.20.41 upgrade record](RUNTIME_UPGRADE_0_20_41.md) tracks the current
candidate separately; a new runtime or loopback default does not establish
untrusted-client authorization or qualify an embedded fallback.

The workspace boundary described here applies to the nine typed KiCad tools.
Core 0.20.39 additionally registers stock introspection, dynamic-tool registration,
admin and skill-loading capabilities. Empty `MinimalModeConfig` does not remove
those capabilities. The adapter adds no raw execution tool of its own, but the
complete Core endpoint must only be used by trusted local clients and trusted
skill paths. It is not an authorization sandbox for untrusted clients. No public
Core least-privilege opt-out was found in the qualified artifact; any future
untrusted-client deployment requires that shared capability, review and tests.
Do not work around it by editing private Core registry internals.
