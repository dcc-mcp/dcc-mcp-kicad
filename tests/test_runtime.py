import json
import os
import threading
from pathlib import Path

import pytest

from dcc_mcp_kicad.runtime import BoardError, BoardRuntime
from dcc_mcp_kicad.skill_tools import call


def test_import_does_not_load_native():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import dcc_mcp_kicad; import sys; assert 'pcbnew' not in sys.modules"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_path_guards(tmp_path):
    runtime = BoardRuntime(tmp_path)
    for path, code in [
        ("../escape.kicad_pcb", "outside_workspace"),
        ("sub/../safe.kicad_pcb", "outside_workspace"),
        ("x.txt", "invalid_extension"),
        ("missing/x.kicad_pcb", "missing_directory"),
        ("", "invalid_path"),
    ]:
        with pytest.raises(BoardError, match=".") as error:
            runtime._path(path, ".kicad_pcb")
        assert error.value.code == code


def test_path_guards_reject_escaping_symlink(tmp_path):
    runtime = BoardRuntime(tmp_path)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    try:
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows requires permission to create a symlink")
        raise
    with pytest.raises(BoardError) as error:
        runtime._path("link/x.kicad_pcb", ".kicad_pcb")
    assert error.value.code == "outside_workspace"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "1", -1, 999])
def test_invalid_number(value):
    with pytest.raises(BoardError):
        BoardRuntime._number(value, 0.1, 3, "width")


def test_no_native_on_worker(tmp_path):
    runtime = BoardRuntime(tmp_path)
    runtime.thread_id = -1
    with pytest.raises(BoardError) as error:
        runtime.inspect_board()
    assert error.value.code == "wrong_thread"


def test_unknown_operation():
    result = call("execute_python", code="bad")
    assert result["success"] is False
    assert result["error"] == "unknown_operation"


@pytest.mark.native
def test_native_workflow(tmp_path):
    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path)
    runtime.bind()
    assert runtime.status()["host_thread_id"] == threading.get_ident()
    with pytest.raises(BoardError, match="Create or open"):
        runtime.inspect_board()
    created = runtime.create_board()
    assert created["counts"] == {"footprints": 2, "tracks": 1, "drawings": 4}
    uuid = created["tracks"][0]["uuid"]
    assert runtime.set_track_width(uuid, 1.2)["width_mm"] == 1.2
    with pytest.raises(BoardError) as error:
        runtime.create_board()
    assert error.value.code == "unsaved_changes"
    with pytest.raises(BoardError) as error:
        runtime.set_track_width("00000000-0000-0000-0000-000000000000", 1)
    assert error.value.code == "track_not_found"
    saved = runtime.save_board("toy.kicad_pcb")
    assert saved["reopened"] and saved["bytes"] > 100
    with pytest.raises(BoardError) as error:
        runtime.save_board("toy.kicad_pcb")
    assert error.value.code == "output_exists"
    reopened = runtime.open_board("toy.kicad_pcb")
    assert reopened["tracks"][0]["width_mm"] == 1.2
    svg = runtime.export_svg("toy.svg")
    assert svg["elements"] > 5
    drc = runtime.run_drc("drc.json")
    assert "violations" in drc["counts"]
    assert not drc["certification"]
    assert json.loads(Path(drc["path"]).read_text())["violations"] is not None


@pytest.mark.native
def test_cli_timeout_does_not_publish(tmp_path, monkeypatch):
    import subprocess

    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path, cli="kicad-cli")
    runtime.bind()
    runtime.create_board()

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("kicad-cli", 1)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(BoardError) as error:
        runtime.export_svg("timeout.svg", timeout_secs=1)
    assert error.value.code == "native_timeout"
    assert not (tmp_path / "timeout.svg").exists()


@pytest.mark.native
@pytest.mark.parametrize("dimensions", [(80, 50), (70, 45), (100, 70)])
def test_carrier_native(tmp_path, dimensions):
    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path)
    runtime.bind()
    result = runtime.create_sensor_carrier(width_mm=dimensions[0], height_mm=dimensions[1])
    assert result["counts"]["footprints"] == 11
    assert not result["electrically_validated"]
    assert len(result["net_names"]) == 5
    saved = runtime.save_board("carrier.kicad_pcb")
    assert saved["reopened"]
    drc = runtime.run_drc("carrier-drc.json")
    report = json.loads(Path(drc["path"]).read_text())
    assert report["violations"] == []
    assert len(report["unconnected_items"]) > 0
    assert not any(item["type"] in {"shorting_items", "solder_mask_bridge"} for item in report["violations"])


def test_dispatcher_reentrant_and_serialized(tmp_path):
    from dcc_mcp_core.host import QueueDispatcher, StandaloneHost

    from dcc_mcp_kicad.server import NativeDispatcher

    runtime = BoardRuntime(tmp_path)
    queue = QueueDispatcher()
    dispatcher = NativeDispatcher(queue, runtime)
    with StandaloneHost(queue):
        queue.post(lambda: setattr(runtime, "thread_id", threading.get_ident())).wait(timeout=2)
        outer = dispatcher.dispatch_callable(
            lambda: dispatcher.dispatch_callable(threading.get_ident), timeout_hint_secs=2
        )
        assert outer == runtime.thread_id
        assert outer != threading.get_ident()
