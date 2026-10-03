import json
import sys


def test_cli_publishes_ready_endpoint_and_stops(monkeypatch, tmp_path, capsys):
    from dcc_mcp_kicad import server

    lifecycle = []

    class FakeServer:
        mcp_url = "http://127.0.0.1:12345/mcp"

        def __init__(self, workspace, port):
            assert workspace == str(tmp_path)
            assert port is None

        def start(self):
            lifecycle.append("start")

        def stop(self):
            lifecycle.append("stop")

    class ImmediateEvent:
        def wait(self):
            return True

    monkeypatch.setattr(server, "KicadMcpServer", FakeServer)
    monkeypatch.setattr(server.threading, "Event", ImmediateEvent)
    monkeypatch.setattr(server.signal, "signal", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["dcc-mcp-kicad", "--workspace", str(tmp_path)])
    server.main()
    assert json.loads(capsys.readouterr().out) == {"ready": True, "mcp_url": FakeServer.mcp_url}
    assert lifecycle == ["start", "stop"]
