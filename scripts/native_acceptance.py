"""Real HTTP MCP acceptance. Only touches a fresh supplied evidence directory."""

import argparse
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for key, name in [("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")]:
        (output / name).mkdir()
        os.environ[key] = str(output / name)
    from dcc_mcp_kicad.server import KicadMcpServer

    server = KicadMcpServer(
        output,
        port=0,
        gateway_port=0,
        registry_dir=str(output / "registry"),
        enable_gateway_failover=False,
        enable_file_logging=False,
        enable_job_persistence=False,
        enable_telemetry=False,
        enable_checkpoint_persistence=False,
    )
    transcript = []

    def rpc(method, params):
        request = {"jsonrpc": "2.0", "id": len(transcript) + 1, "method": method, "params": params}
        req = urllib.request.Request(
            server.mcp_url,
            data=json.dumps(request).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=170) as response:
            wire = json.loads(response.read())
        transcript.append({"request": request, "response": wire})
        (output / "mcp-transcript.json").write_text(json.dumps(transcript, indent=2))
        assert "error" not in wire, wire
        result = wire["result"]
        if "structuredContent" in result:
            return result["structuredContent"]
        if "content" in result:
            return json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))
        return result

    def call(name, args=None, success=True):
        result = rpc("tools/call", {"name": name, "arguments": args or {}})
        if "core_job_id" in result:
            job_id = result["core_job_id"]
            deadline = time.monotonic() + 170
            while time.monotonic() < deadline:
                status = rpc(
                    "tools/call", {"name": "jobs_get_status", "arguments": {"job_id": job_id, "include_result": True}}
                )
                if status.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                    assert status["status"] == "completed", status
                    result = status["result"]
                    if isinstance(result, str):
                        result = json.loads(result)
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Core job did not reach terminal state")
        assert result.get("success") is success, result
        return result

    try:
        server.start()
        port = urlsplit(server.mcp_url).port
        socket_inodes = set()
        for descriptor in Path("/proc/self/fd").iterdir():
            try:
                target = descriptor.readlink().as_posix()
                if target.startswith("socket:["):
                    socket_inodes.add(target[8:-1])
            except FileNotFoundError:
                pass
        listeners = []
        for row in Path("/proc/net/tcp").read_text().splitlines()[1:]:
            parts = row.split()
            if parts[3] == "0A" and parts[9] in socket_inodes:
                listeners.append(parts[1])
        assert listeners == [f"0100007F:{port:04X}"], listeners
        initialization = rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "kicad-native-acceptance", "version": "0.1.0"},
            },
        )
        assert initialization["serverInfo"]["name"] == "dcc-mcp-kicad"
        search = rpc("tools/call", {"name": "search_tools", "arguments": {"query": "kicad board"}})
        assert "kicad-board" in json.dumps(search)
        info = rpc("tools/call", {"name": "get_skill_info", "arguments": {"skill_name": "kicad-board"}})
        assert "set_track_width" in json.dumps(info)
        rpc("tools/call", {"name": "load_skill", "arguments": {"skill_name": "kicad-board"}})
        status = call("kicad_board__status")["context"]
        assert status["host_thread_id"] != threading.get_ident()
        created = call("kicad_board__create_board")["context"]
        uuid = created["tracks"][0]["uuid"]
        call("kicad_board__set_track_width", {"track_uuid": uuid, "width_mm": 1.2})
        call("kicad_board__save_board", {"path": "toy.kicad_pcb"})
        reopened = call("kicad_board__open_board", {"path": "toy.kicad_pcb"})["context"]
        assert reopened["tracks"][0]["width_mm"] == 1.2
        svg = call("kicad_board__export_svg", {"output_path": "toy.svg"})["context"]
        drc = call("kicad_board__run_drc", {"output_path": "drc.json"})["context"]
        negative = call("kicad_board__save_board", {"path": "../escape.kicad_pcb"}, success=False)
        assert negative["error"] == "outside_workspace"
        overwrite = call("kicad_board__save_board", {"path": "toy.kicad_pcb"}, success=False)
        assert overwrite["error"] == "output_exists"
        carrier = call("kicad_board__create_sensor_carrier")["context"]
        call("kicad_board__save_board", {"path": "sensor-carrier.kicad_pcb"})
        carrier_svg = call("kicad_board__export_svg", {"output_path": "sensor-carrier.svg"})["context"]
        carrier_drc = call("kicad_board__run_drc", {"output_path": "sensor-carrier-drc.json"})["context"]
        assert carrier_drc["counts"]["violations"] == 0
        assert carrier_drc["counts"]["unconnected_items"] > 0
        report = {
            "listener_evidence": {"source": "/proc/net/tcp matched to own socket inodes", "listeners": listeners},
            "carrier": {"board": carrier, "svg": carrier_svg, "drc": carrier_drc},
            "host": status,
            "board": reopened,
            "svg": svg,
            "drc": drc,
            "checks": [
                "initialize",
                "search",
                "describe",
                "load",
                "host-lane",
                "create",
                "edit",
                "save",
                "reopen",
                "svg",
                "drc",
                "path-negative",
                "overwrite-negative",
            ],
            "mcp_requests": len(transcript),
            "scope": "synthetic board; no GUI or fabrication qualification",
        }
        (output / "result.json").write_text(json.dumps(report, indent=2))
        return report
    finally:
        server.stop()
        assert not server.is_running
        assert not server.host_driver.is_running


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", help="New directory for isolated board, SVG, DRC and complete MCP transcript")
    print(json.dumps(run(parser.parse_args().output), indent=2))
