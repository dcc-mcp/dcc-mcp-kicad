"""Real HTTP cancellation after native board staging and before publication.

The controlled test hook pauses at a real native output boundary. It does not
introduce a product tool or claim that running pcbnew calls can be preempted.
"""

import asyncio
import importlib.util
import json
import threading
import time

import pytest

pytestmark = [
    pytest.mark.native,
    pytest.mark.mcp,
    pytest.mark.skipif(importlib.util.find_spec("pcbnew") is None, reason="pcbnew is required"),
]


def test_real_http_cancel_prevents_native_board_publication(tmp_path, monkeypatch):
    for key, value in {
        "HOME": str(tmp_path / "home"),
        "DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS": "1",
        "DCC_MCP_DISABLE_TELEMETRY": "1",
        "DCC_MCP_CHECKPOINT_IN_MEMORY": "1",
    }.items():
        monkeypatch.setenv(key, value)
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from dcc_mcp_kicad.server import KicadMcpServer

    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = KicadMcpServer(
        workspace,
        port=0,
        registry_dir=str(tmp_path / "registry"),
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_telemetry=False,
        enable_checkpoint_persistence=False,
    )
    publish = server.board_runtime._publish

    def controlled_publish(stage, target, overwrite, expected_source_sha256=None):
        assert stage.is_file() and stage.stat().st_size > 0
        entered.set()
        try:
            assert release.wait(10), "Test cancellation barrier timed out"
            return publish(stage, target, overwrite, expected_source_sha256)
        finally:
            exited.set()

    server.board_runtime._publish = controlled_publish

    def payload(result):
        if result.structuredContent is not None:
            return result.structuredContent
        return json.loads(next(item.text for item in result.content if item.type == "text"))

    async def workflow():
        async with httpx.AsyncClient(trust_env=False, timeout=20) as http:
            async with streamable_http_client(server.mcp_url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    loaded = payload(await client.call_tool("load_skill", {"skill_name": "kicad-board"}))
                    assert loaded["loaded"]
                    cursor = None
                    while True:
                        listing = await client.list_tools(cursor=cursor)
                        cursor = listing.nextCursor
                        if cursor is None:
                            break
                    created = payload(await client.call_tool("create_board", {}))
                    assert created["success"], created
                    launched = payload(await client.call_tool("save_board", {"path": "cancelled.kicad_pcb"}))
                    job_id = launched.get("core_job_id") or launched.get("job_id")
                    assert job_id, launched
                    deadline = time.monotonic() + 10
                    while not entered.is_set():
                        assert time.monotonic() < deadline
                        await asyncio.sleep(0.02)
                    response = await http.delete(server.mcp_url.rsplit("/mcp", 1)[0] + "/v1/jobs/" + job_id)
                    assert response.is_success, response.text
                    release.set()
                    while True:
                        state = payload(await client.call_tool("jobs_get_status", {"job_id": job_id}))
                        if state["status"] in {"cancelled", "interrupted"}:
                            break
                        assert time.monotonic() < deadline, state
                        await asyncio.sleep(0.02)
                    # A later typed read queued on the same native lane proves the cancelled
                    # callback actually exited, not just that Core recorded terminal status.
                    inspected = payload(await client.call_tool("inspect_board", {}))
                    assert inspected["success"] and exited.is_set(), inspected
                    assert not (workspace / "cancelled.kicad_pcb").exists()
                    assert not list(workspace.glob(".kicad-save-*"))
                    assert server.board_runtime.dirty is True

    try:
        server.start()
        asyncio.run(asyncio.wait_for(workflow(), timeout=25))
    finally:
        release.set()
        server.stop()
    assert not server.is_running
    assert not server.host_driver.is_running
