"""Portable publication regressions using a serializer, without a native host."""

import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token

from dcc_mcp_kicad.runtime import BoardError, BoardRuntime


def serializer_runtime(tmp_path, monkeypatch, action=None, action_on_save=1):
    runtime = BoardRuntime(tmp_path)
    runtime.thread_id = threading.get_ident()
    runtime.board = object()
    runtime.path = tmp_path / "loaded.kicad_pcb"
    runtime.path.write_bytes(b"original board")
    runtime.source_sha256 = hashlib.sha256(b"original board").hexdigest()
    runtime.dirty = True
    runtime.revision = 7
    saves = 0

    def save(path, _board):
        nonlocal saves
        saves += 1
        Path(path).write_bytes(b"serialized board")
        if action is not None and saves == action_on_save:
            action(runtime.path)
        return True

    runtime.pcb = SimpleNamespace(SaveBoard=save, LoadBoard=lambda _path: object())
    monkeypatch.setattr(runtime, "_summary", lambda _board: {"counts": {"tracks": 0}})
    return runtime


@pytest.mark.parametrize("action_on_save", [1, 2])
@pytest.mark.parametrize("external_action", ["edit", "delete"])
def test_external_changes_during_serialization_preserve_loaded_state(
    tmp_path, monkeypatch, action_on_save, external_action
):
    def change(path):
        if external_action == "edit":
            path.write_bytes(b"external edit")
        else:
            path.unlink()

    runtime = serializer_runtime(tmp_path, monkeypatch, change, action_on_save)
    original_board = runtime.board
    original_sha = runtime.source_sha256
    with pytest.raises(BoardError) as error:
        runtime.save_board("loaded.kicad_pcb", overwrite=True, expected_revision=7)
    assert error.value.code == "stale_file"
    if external_action == "edit":
        assert runtime.path.read_bytes() == b"external edit"
    else:
        assert not runtime.path.exists()
    assert runtime.board is original_board
    assert runtime.dirty and runtime.revision == 7 and runtime.source_sha256 == original_sha
    assert not list(tmp_path.glob(".kicad-save-*"))


def test_loaded_save_uses_real_file_sync_and_native_readback_contract(tmp_path, monkeypatch):
    runtime = serializer_runtime(tmp_path, monkeypatch)
    result = runtime.save_board("loaded.kicad_pcb", overwrite=True, expected_revision=7)
    assert runtime.path.read_bytes() == b"serialized board"
    assert result["sha256"] == hashlib.sha256(b"serialized board").hexdigest()
    assert result["reopened"] and result["revision"] == 8
    assert not runtime.dirty and runtime.revision == 8
    assert runtime.source_sha256 == result["sha256"]
    assert not list(tmp_path.glob(".kicad-save-*"))


def test_no_overwrite_keeps_existing_bytes_and_state(tmp_path, monkeypatch):
    runtime = serializer_runtime(tmp_path, monkeypatch)
    with pytest.raises(BoardError) as error:
        runtime.save_board("loaded.kicad_pcb")
    assert error.value.code == "output_exists"
    assert runtime.path.read_bytes() == b"original board"
    assert runtime.dirty and runtime.revision == 7
    assert not list(tmp_path.glob(".kicad-save-*"))


def test_cancellation_during_serialization_prevents_publication(tmp_path, monkeypatch):
    token = CancelToken()
    runtime = serializer_runtime(tmp_path, monkeypatch, lambda _path: token.cancel())
    context = set_cancel_token(token)
    try:
        with pytest.raises(DccMcpCancelledError):
            runtime.save_board("loaded.kicad_pcb", overwrite=True)
        assert runtime.path.read_bytes() == b"original board"
        assert runtime.dirty and runtime.revision == 7
        assert not list(tmp_path.glob(".kicad-save-*"))
    finally:
        reset_cancel_token(context)
