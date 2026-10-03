"""Official MCP SDK acceptance against the installed adapter and native KiCad 9."""

import argparse
import asyncio
import importlib.metadata
import json
import os
import time
from datetime import timedelta
from pathlib import Path

import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def unpack(result):
    if result.structuredContent is not None:
        return result.structuredContent
    text = next(item.text for item in result.content if item.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if result.isError:
            return {"success": False, "error": text}
        raise


async def exercise(server, output):
    transcript = []
    async with (
        httpx.AsyncClient(trust_env=False, timeout=170) as http,
        streamable_http_client(server.mcp_url, http_client=http) as (read, write, _),
    ):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=170)) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "dcc-mcp-kicad"
            await session.call_tool("load_skill", {"skill_name": "kicad-board"})
            all_tools = []
            cursor = None
            while True:
                listing = await session.list_tools(cursor=cursor)
                all_tools.extend(listing.tools)
                cursor = listing.nextCursor
                if not cursor:
                    break
            names = {
                "status",
                "create_board",
                "inspect_board",
                "set_track_width",
                "save_board",
                "open_board",
                "export_svg",
                "run_drc",
                "create_sensor_carrier",
            }
            tools = {tool.name: tool for tool in all_tools if tool.name in names}
            assert len(tools) == 9
            for tool in tools.values():
                assert tool.inputSchema["additionalProperties"] is False
                jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                assert tool.outputSchema
                jsonschema.Draft202012Validator.check_schema(tool.outputSchema)
            (output / "sdk-tools.json").write_text(
                json.dumps([tool.model_dump(mode="json") for tool in tools.values()], indent=2)
            )

            async def call(name, arguments=None, success=True):
                result = await session.call_tool(name, arguments or {})
                payload = unpack(result)
                transcript.append(
                    {"tool": name, "arguments": arguments or {}, "response": result.model_dump(mode="json")}
                )
                (output / "sdk-transcript.json").write_text(json.dumps(transcript, indent=2))
                if not result.isError:
                    jsonschema.validate(payload, tools[name].outputSchema)
                if "core_job_id" in payload:
                    job_id = payload["core_job_id"]
                    deadline = time.monotonic() + 170
                    while time.monotonic() < deadline:
                        state = unpack(
                            await session.call_tool("jobs_get_status", {"job_id": job_id, "include_result": True})
                        )
                        if state["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                            if state["status"] != "completed":
                                assert not success and state["status"] == "failed", state
                                payload = {"success": False, "error": state["error"]}
                                transcript[-1]["terminal"] = state
                                break
                            payload = state["result"]
                            if isinstance(payload, str):
                                payload = json.loads(payload)
                            jsonschema.validate(payload, tools[name].outputSchema)
                            transcript[-1]["terminal"] = state
                            break
                        await asyncio.sleep(0.03)
                    else:
                        raise AssertionError("Job failed to reach terminal state")
                assert payload["success"] is success, payload
                return payload

            status = (await call("status"))["context"]
            assert status["cli_compatible"] and status["cli_version"].startswith("9.")
            created = (await call("create_board"))["context"]
            revision = created["revision"]
            uuid = created["tracks"][0]["uuid"]
            edited = (
                await call("set_track_width", {"track_uuid": uuid, "width_mm": 1.2, "expected_revision": revision})
            )["context"]
            stale = await call(
                "set_track_width", {"track_uuid": uuid, "width_mm": 2, "expected_revision": revision}, False
            )
            assert stale["error"] == "stale_revision"
            await call("save_board", {"path": "sdk.kicad_pcb", "expected_revision": edited["revision"]})
            await call("open_board", {"path": "sdk.kicad_pcb"})
            await call("inspect_board")
            await call("export_svg", {"output_path": "sdk.svg"})
            await call("run_drc", {"output_path": "sdk-drc.json"})
            await call("create_sensor_carrier")
            await call("save_board", {"path": "sdk-carrier.kicad_pcb"})
            await call("export_svg", {"output_path": "sdk-carrier.svg"})
            drc = await call("run_drc", {"output_path": "sdk-carrier-drc.json"})
            assert drc["context"]["counts"] == {"violations": 0, "unconnected_items": 13, "schematic_parity": 0}
            for name, args in [
                ("status", {"unknown": True}),
                ("set_track_width", {"track_uuid": uuid, "width_mm": "bad"}),
                ("export_svg", {"output_path": "too-long.svg", "timeout_secs": 121}),
            ]:
                await call(name, args, success=False)
            assert not (output / "too-long.svg").exists()
            unknown = unpack(
                await session.call_tool("jobs_get_status", {"job_id": "00000000-0000-0000-0000-000000000000"})
            )
            assert unknown.get("success") is False or unknown.get("error"), unknown
            (output / "sdk-transcript.json").write_text(json.dumps(transcript, indent=2))
            return {
                "sdk_version": importlib.metadata.version("mcp"),
                "negotiated_protocol_version": initialized.protocolVersion,
                "tools_validated": len(tools),
                "calls": len(transcript),
                "host": status,
                "carrier_drc": drc["context"],
                "checks": [
                    "official-sdk-initialize",
                    "list-9-tools",
                    "closed-input-schema",
                    "output-schema",
                    "all-9-tools",
                    "async-envelope-and-terminal",
                    "stale-revision",
                    "invalid-input",
                    "unknown-job",
                    "native-artifacts",
                ],
            }


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for key, name in [("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")]:
        (output / name).mkdir()
        os.environ[key] = str(output / name)
    import dcc_mcp_kicad
    from dcc_mcp_kicad.server import KicadMcpServer

    server = KicadMcpServer(
        output,
        port=0,
        registry_dir=str(output / "registry"),
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_telemetry=False,
        enable_checkpoint_persistence=False,
    )
    try:
        server.start()
        report = asyncio.run(exercise(server, output))
        report["loaded_adapter"] = str(Path(dcc_mcp_kicad.__file__).resolve())
    finally:
        server.stop()
        assert not server.is_running and not server.host_driver.is_running
        assert server.board_runtime.board is None and server.board_runtime.pcb is None
    report["clean_shutdown"] = True
    (output / "sdk-result.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", help="A fresh evidence directory")
    print(json.dumps(run(parser.parse_args().output), indent=2))
