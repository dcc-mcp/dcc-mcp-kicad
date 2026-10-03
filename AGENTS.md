# Contribution instructions

- Read README.md and docs/architecture.md before changes.
- Keep all pcbnew imports and calls on the Core-owned serialized native lane.
- Keep discovery import-safe without KiCad. No HTTP worker native calls.
- Use DccServerBase, DccServerOptions.from_env, HostExecutionBridge, Core catalog,
  lifecycle and canonical skill envelopes. Do not parse manifests at runtime.
- Prefer bounded typed operations. Do not add a raw script execution surface.
- Preserve workspace containment, no-overwrite defaults and dirty-state guards.
- Run ruff check, ruff format --check, pytest, skill validation and wheel build.
- Native acceptance requires KiCad 9 and its matching Python. Distinguish unit,
  native and real MCP evidence. Do not invent host compatibility or DRC certification.
- Keep test artifacts outside the repository or in ignored build/ directories.
- Do not add publishing workflows; publication is a separate reviewed action.
