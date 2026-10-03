# Manual installation runbook

**Pre-release manual lifecycle only.** The standard automated Install SOP
install/status/verify/uninstall/upgrade surface, receipts and host-complete
transactions remain unimplemented. This runbook is the canonical repository
installation path; it intentionally has no Install SOP conformance marker or
released artifact URL. Do not run an invented automated installer command.


Prerequisites: KiCad 9 including pcbnew and kicad-cli, the matching Python ABI,
and Python 3.10 or later. KiCad 9.0.2, Linux, Python 3.13.5 and Core 0.20.39 were
used for the native acceptance run. Other operating systems and KiCad builds are
not yet host-qualified. KiCad 10 and Python 3.7 are outside the declared profile.
That acceptance belongs to the previous runtime. The current 0.20.41 candidate
needs separate native and real MCP acceptance; follow the [runtime upgrade and
rollback record](docs/RUNTIME_UPGRADE_0_20_41.md) before adopting it.

Create a virtual environment from the interpreter that already imports pcbnew.
Using --system-site-packages is often needed for packaged Linux pcbnew; do not
copy a binary pcbnew extension into an unrelated interpreter. Install this
repository, then check `python -c 'import pcbnew; print(pcbnew.GetBuildVersion())'`.

Set an explicit existing workspace and start the service:

```sh
DCC_MCP_GATEWAY_PORT=0 dcc-mcp-kicad --workspace /absolute/board-workspace --port 8919
```

Connect a direct MCP client to http://127.0.0.1:8919/mcp. When leaving the port
dynamic, read `mcp_url` from the CLI's JSON readiness line. Do not assume gateway
registry discovery in the default direct-only mode. The CLI keeps Core gateway disabled; a programmatic gateway_port opt-in must
review Core's own bind and access-control configuration; the adapter does not modify security settings.
Use writable XDG_CONFIG_HOME, XDG_CACHE_HOME and XDG_DATA_HOME for disposable
environments. No editor or board should be open under the assumption that this
adapter is connected to it: it is an independent file process.

If startup fails, check Python ABI, pcbnew import/version, workspace permissions,
Core version, and available port. CLI not found is reported by status and export
returns cli_unavailable. A discovered CLI must match the pcbnew KiCad 9 major/minor
version; unsupported host versions and mismatched CLI versions fail closed. No dependency
or host installation is attempted by runtime code.

## Candidate dependency pins

The package pins both `dcc-mcp-core==0.20.41` and `dcc-mcp-server==0.20.41`.
Package resolution will not silently select a future Core/server. Build and
install into a new environment; retain the previous 0.20.39 environment and
wheel until the new runtime passes native and real MCP acceptance. Stop the
candidate process before returning to that preserved environment. Do not reuse
an already stopped server object or replace the user's existing KiCad install.
