# Native wire-cancellation acceptance

A test-only control point pauses after KiCad actually saves a staged board,
loads it again, and verifies a complete native re-save hash. The official MCP
SDK starts `save_board`, receives the real Core job ID, and issues HTTP DELETE
on the corresponding Core job endpoint. After release of that control point,
the adapter's existing cancellation checkpoint rejects final publication.

A later typed `inspect_board` call on the same serialized native lane proves
the callback exited. The destination file and staging directory are absent,
the in-memory board remains dirty, and owned listener/host shutdown completes.
This closes the prior missing wire-to-native cancellation gate without changing
runtime code, tool schemas or the installed wheel.

- Source suite: 45 passed
- Existing hardened installed-wheel suite: 45 passed
- Native KiCad 9.0.2/Python 3.13.5, Core/server 0.20.39, official MCP SDK 1.30.0
- Wheel SHA256: acf4dc139064f75f0384da2f79975e5b9a9c4b23847808617b1946f28fa92bab

This is cooperative cancellation at a publication boundary. It does not
preempt an in-progress native call, undo already-published work or qualify
other operating systems. It does not change the carrier's 13 unconnected
items, incomplete electrical validation, or separate Install SOP/remote CI gates.
