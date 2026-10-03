import contextvars
import os
import threading
import time
from types import SimpleNamespace

import pytest
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token
from dcc_mcp_core.host import DispatchError, QueueDispatcher, StandaloneHost

from dcc_mcp_kicad.runtime import BoardError, BoardRuntime, get_runtime, set_runtime
from dcc_mcp_kicad.server import NativeDispatcher


@pytest.mark.parametrize("version, expected", [("9.0.2+dfsg-1", (9, 0, 2)), ("9.0.3", (9, 0, 3))])
def test_version_parser(version, expected):
    assert BoardRuntime._version(version) == expected


@pytest.mark.parametrize("version", ["", "garbage", "9.0", "9.0.2garbage"])
def test_version_parser_rejects_unknown(version):
    with pytest.raises(BoardError):
        BoardRuntime._version(version)


def test_bind_rejects_mismatched_cli(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "pcbnew", SimpleNamespace(GetBuildVersion=lambda: "9.0.2"))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: SimpleNamespace(stdout="10.0.0", returncode=0))
    runtime = BoardRuntime(tmp_path, cli="kicad-cli")
    with pytest.raises(BoardError, match="matching"):
        runtime.bind()
    runtime.close()
    assert runtime.thread_id is None and runtime.pcb is None


@pytest.mark.parametrize("value", [True, -1, "1", 1.2])
def test_revision_types(tmp_path, value):
    with pytest.raises(BoardError) as error:
        BoardRuntime(tmp_path)._revision_guard(value)
    assert error.value.code == "invalid_input"


def test_path_rejects_in_workspace_symlinks(tmp_path):
    runtime = BoardRuntime(tmp_path)
    (tmp_path / "real").mkdir()
    try:
        (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows requires permission to create a symlink")
        raise
    with pytest.raises(BoardError) as error:
        runtime._path("alias/test.kicad_pcb", ".kicad_pcb")
    assert error.value.code == "symlink_path"


def test_path_rejects_special_files_and_overlong_paths(tmp_path):
    runtime = BoardRuntime(tmp_path)
    (tmp_path / "directory.kicad_pcb").mkdir()
    for path, expected in [
        ("directory.kicad_pcb", "invalid_file"),
        ("a" * 4097 + ".kicad_pcb", "invalid_path"),
    ]:
        with pytest.raises(BoardError) as error:
            runtime._path(path, ".kicad_pcb")
        assert error.value.code == expected


def test_workspace_replacement_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = BoardRuntime(workspace)
    workspace.rename(tmp_path / "old")
    workspace.mkdir()
    with pytest.raises(BoardError) as error:
        runtime._path("x.kicad_pcb", ".kicad_pcb")
    assert error.value.code == "workspace_changed"


def test_bounded_artifact(tmp_path):
    target = tmp_path / "large.json"
    with target.open("wb") as f:
        f.truncate(BoardRuntime.MAX_FILE_BYTES + 1)
    with pytest.raises(BoardError) as error:
        BoardRuntime._artifact(target)
    assert error.value.code == "artifact_too_large"


def test_owner_scoped_runtime_release(tmp_path):
    one = BoardRuntime(tmp_path)
    two = BoardRuntime(tmp_path)
    set_runtime(one)
    try:
        with pytest.raises(RuntimeError):
            set_runtime(two)
        set_runtime(None, owner=two)
        assert get_runtime() is one
    finally:
        set_runtime(None, owner=one)


def test_dispatch_timeout_cannot_execute_later(tmp_path):
    queue = QueueDispatcher()
    dispatcher = NativeDispatcher(queue, BoardRuntime(tmp_path))
    effects = []
    with pytest.raises(DispatchError, match="timeout"):
        dispatcher.dispatch_callable(lambda: effects.append("late"), timeout_hint_secs=0.01)
    queue.tick(1)
    assert not effects
    queue.shutdown()


def test_dispatch_preserves_context_and_serializes_concurrency(tmp_path):
    queue = QueueDispatcher()
    dispatcher = NativeDispatcher(queue, BoardRuntime(tmp_path))
    variable = contextvars.ContextVar("test", default="missing")
    variable.set("copied")
    active = 0
    peak = 0
    lock = threading.Lock()
    results = []

    def operation():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        return threading.get_ident()

    with StandaloneHost(queue):
        assert dispatcher.dispatch_callable(variable.get) == "copied"
        workers = [
            threading.Thread(target=lambda: results.append(dispatcher.dispatch_callable(operation))) for _ in range(12)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive()
    assert peak == 1 and len(results) == 12 and len(set(results)) == 1


@pytest.mark.native
def test_native_stale_revision_and_external_file(tmp_path):
    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path)
    runtime.bind()
    created = runtime.create_board()
    track = created["tracks"][0]["uuid"]
    revision = created["revision"]
    runtime.set_track_width(track, 1.2, expected_revision=revision)
    with pytest.raises(BoardError) as error:
        runtime.set_track_width(track, 1.4, expected_revision=revision)
    assert error.value.code == "stale_revision"
    runtime.save_board("one.kicad_pcb")
    target = tmp_path / "one.kicad_pcb"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(BoardError) as error:
        runtime.save_board("one.kicad_pcb", overwrite=True)
    assert error.value.code == "stale_file"
    assert runtime.inspect_board()["tracks"][0]["width_mm"] == 1.2
    runtime.close()


@pytest.mark.native
def test_native_malformed_and_oversized_preserve_state(tmp_path):
    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path)
    runtime.bind()
    runtime.create_board()
    before = runtime.inspect_board()
    (tmp_path / "malformed.kicad_pcb").write_text("(not-a-kicad-board)")
    with pytest.raises(BoardError) as error:
        runtime.open_board("malformed.kicad_pcb", discard_unsaved=True)
    assert error.value.code == "invalid_board"
    with (tmp_path / "huge.kicad_pcb").open("wb") as stream:
        stream.truncate(runtime.MAX_FILE_BYTES + 1)
    with pytest.raises(BoardError) as error:
        runtime.open_board("huge.kicad_pcb", discard_unsaved=True)
    assert error.value.code == "board_too_large"
    assert runtime.inspect_board() == before
    runtime.close()


@pytest.mark.native
def test_cancellation_barrier_prevents_save_publication(tmp_path, monkeypatch):
    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path)
    runtime.bind()
    runtime.create_board()
    token = CancelToken()
    context = set_cancel_token(token)
    original = runtime._publish

    def cancel_then_publish(*args):
        token.cancel()
        return original(*args)

    monkeypatch.setattr(runtime, "_publish", cancel_then_publish)
    try:
        with pytest.raises(DccMcpCancelledError):
            runtime.save_board("cancelled.kicad_pcb")
        assert not (tmp_path / "cancelled.kicad_pcb").exists()
        assert not list(tmp_path.glob(".kicad-save-*"))
        assert runtime.dirty and runtime.revision == 1
    finally:
        reset_cancel_token(context)
        runtime.close()


@pytest.mark.native
def test_native_full_readback_protects_unlisted_geometry(tmp_path, monkeypatch):
    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path)
    runtime.bind()
    runtime.create_board()
    original = runtime.pcb.LoadBoard

    def corrupt(*args):
        board = original(*args)
        list(board.GetDrawings())[0].SetWidth(runtime.pcb.FromMM(0.9))
        return board

    monkeypatch.setattr(runtime.pcb, "LoadBoard", corrupt)
    with pytest.raises(BoardError) as error:
        runtime.save_board("bad.kicad_pcb")
    assert error.value.code == "verification_failed"
    assert not (tmp_path / "bad.kicad_pcb").exists()
    runtime.close()


@pytest.mark.native
def test_native_server_recreation_and_owner_cleanup(tmp_path, monkeypatch):
    pytest.importorskip("pcbnew")
    from dcc_mcp_kicad.server import KicadMcpServer

    for key, name in [("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")]:
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setenv(key, str(directory))

    def new_server():
        return KicadMcpServer(
            tmp_path,
            port=0,
            registry_dir=str(tmp_path / "registry"),
            enable_file_logging=False,
            enable_job_persistence=False,
            enable_checkpoint_persistence=False,
            enable_telemetry=False,
        )

    first = new_server()
    first.start()
    first.start()
    second = new_server()
    try:
        with pytest.raises(RuntimeError, match="one KiCad"):
            second.start()
        second.stop()
        assert get_runtime() is first.board_runtime
        assert first.host_queue.post(first.board_runtime.create_board).wait(timeout=10)["dirty"]
    finally:
        first.stop()
        first.stop()
    assert first.board_runtime.thread_id is None and first.board_runtime.pcb is None
    with pytest.raises(RuntimeError, match="cannot restart"):
        first.start()
    replacement = new_server()
    try:
        replacement.start()
        assert replacement.host_queue.post(replacement.board_runtime.status).wait(timeout=5)["board_loaded"] is False
    finally:
        replacement.stop()
    with pytest.raises(BoardError, match="Start the KiCad"):
        get_runtime()


@pytest.mark.native
def test_cancelled_create_preserves_existing_board(tmp_path, monkeypatch):
    pytest.importorskip("pcbnew")
    runtime = BoardRuntime(tmp_path)
    runtime.bind()
    runtime.create_board()
    before = runtime.inspect_board()
    token = CancelToken()
    context = set_cancel_token(token)
    summarize = runtime._summary

    def cancelled_summary(board):
        result = summarize(board)
        token.cancel()
        return result

    monkeypatch.setattr(runtime, "_summary", cancelled_summary)
    try:
        with pytest.raises(DccMcpCancelledError):
            runtime.create_board(discard_unsaved=True)
    finally:
        reset_cancel_token(context)
    monkeypatch.setattr(runtime, "_summary", summarize)
    assert runtime.inspect_board() == before
    runtime.close()


@pytest.mark.native
def test_real_cli_timeout_reaps_process_and_no_late_output(tmp_path):
    import sys

    pytest.importorskip("pcbnew")
    executable = tmp_path / "fake-cli"
    late = tmp_path / "late-sentinel"
    executable.write_text(
        f"#!{sys.executable}\nimport sys,time,pathlib\n"
        "if sys.argv[1:] == ['version']:\n print('9.0.2')\nelse:\n time.sleep(2)\n"
        f" pathlib.Path({str(late)!r}).write_text('late')\n"
    )
    executable.chmod(0o700)
    runtime = BoardRuntime(tmp_path, cli=str(executable))
    runtime.bind()
    runtime.create_board()
    with pytest.raises(BoardError) as error:
        runtime.export_svg("timeout.svg", timeout_secs=1)
    assert error.value.code == "native_timeout"
    time.sleep(1.2)
    assert not late.exists() and not (tmp_path / "timeout.svg").exists()
    assert not list(tmp_path.glob(".kicad-cli-*"))
    runtime.close()
