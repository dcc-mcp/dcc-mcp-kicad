"""Revision recovery after a native edit fails to return a verified result."""

import threading
from types import SimpleNamespace

import pytest
from dcc_mcp_core.cancellation import CancelToken, DccMcpCancelledError, reset_cancel_token, set_cancel_token

from dcc_mcp_kicad.runtime import BoardError, BoardRuntime

TRACK_UUID = "00000000-0000-0000-0000-000000000001"
NO_OVERRIDE = object()


class PCB_TRACK:
    def __init__(self):
        self.m_Uuid = SimpleNamespace(AsString=lambda: TRACK_UUID)
        self.width = 0.8
        self.set_calls = 0
        self.readback = NO_OVERRIDE
        self.readback_error = False
        self.setter_error = None

    def GetWidth(self):
        if self.set_calls:
            if self.readback_error:
                raise RuntimeError("Native readback failed")
            if self.readback is not NO_OVERRIDE:
                return self.readback
        return self.width

    def GetNetname(self):
        return "TEST"

    def SetWidth(self, value):
        self.set_calls += 1
        if self.setter_error == "before":
            raise RuntimeError("Native setter failed")
        self.width = value
        if self.setter_error == "after":
            raise RuntimeError("Native setter failed")


@pytest.fixture
def track_runtime(tmp_path):
    track = PCB_TRACK()
    runtime = BoardRuntime(tmp_path)
    runtime.thread_id = threading.get_ident()
    runtime.pcb = SimpleNamespace(FromMM=lambda value: value, ToMM=lambda value: value)
    runtime.board = SimpleNamespace(GetTracks=lambda: [track], GetFootprints=lambda: [], GetDrawings=lambda: [])
    runtime.revision = 7
    runtime.source_sha256 = "unchanged-loaded-source"
    return runtime, track


def assert_failed_edit_requires_reinspection(runtime, track):
    with pytest.raises(BoardError) as error:
        runtime.set_track_width(TRACK_UUID, 1.4, expected_revision=7)
    assert error.value.code == "stale_revision"
    assert track.set_calls == 1
    assert runtime.source_sha256 == "unchanged-loaded-source"
    with pytest.raises(BoardError) as error:
        runtime._discard_guard(False)
    assert error.value.code == "unsaved_changes"

    track.readback = NO_OVERRIDE
    track.readback_error = False
    track.setter_error = None
    inspected = runtime.inspect_board()
    assert inspected["dirty"] and inspected["revision"] == 8
    result = runtime.set_track_width(TRACK_UUID, 1.4, expected_revision=inspected["revision"])
    assert result["revision"] == 9 and result["width_mm"] == track.width == 1.4
    assert track.set_calls == 2


@pytest.mark.parametrize("readback", [0.9, float("nan"), float("inf"), None, "1.2"])
def test_failed_width_verification_rejects_stale_followup(track_runtime, readback):
    runtime, track = track_runtime
    track.readback = readback
    with pytest.raises(BoardError) as error:
        runtime.set_track_width(TRACK_UUID, 1.2, expected_revision=7)
    assert error.value.code == "verification_failed"
    assert track.width == 1.2
    assert_failed_edit_requires_reinspection(runtime, track)


def test_width_readback_exception_rejects_stale_followup(track_runtime):
    runtime, track = track_runtime
    track.readback_error = True
    with pytest.raises(RuntimeError, match="Native readback failed"):
        runtime.set_track_width(TRACK_UUID, 1.2, expected_revision=7)
    assert track.width == 1.2
    assert_failed_edit_requires_reinspection(runtime, track)


@pytest.mark.parametrize("failure_point", ["before", "after"])
def test_width_setter_exception_requires_reinspection(track_runtime, failure_point):
    runtime, track = track_runtime
    track.setter_error = failure_point
    with pytest.raises(RuntimeError, match="Native setter failed"):
        runtime.set_track_width(TRACK_UUID, 1.2, expected_revision=7)
    assert track.width == (0.8 if failure_point == "before" else 1.2)
    assert_failed_edit_requires_reinspection(runtime, track)


def test_verified_width_edit_advances_revision_once(track_runtime):
    runtime, track = track_runtime
    result = runtime.set_track_width(TRACK_UUID, 1.2, expected_revision=7)
    assert result["previous_width_mm"] == 0.8
    assert result["width_mm"] == track.width == 1.2
    assert result["dirty"] and runtime.dirty
    assert result["revision"] == runtime.revision == 8
    assert track.set_calls == 1


def test_width_conversion_failure_does_not_mark_a_mutation(track_runtime):
    runtime, track = track_runtime

    def fail_conversion(value):
        raise RuntimeError("Native conversion failed")

    runtime.pcb.FromMM = fail_conversion
    with pytest.raises(RuntimeError, match="Native conversion failed"):
        runtime.set_track_width(TRACK_UUID, 1.2, expected_revision=7)
    assert track.width == 0.8 and track.set_calls == 0
    assert runtime.revision == 7 and not runtime.dirty


def test_cancelled_width_edit_does_not_mark_a_mutation(track_runtime):
    runtime, track = track_runtime
    token = CancelToken()
    token.cancel()
    context = set_cancel_token(token)
    try:
        with pytest.raises(DccMcpCancelledError):
            runtime.set_track_width(TRACK_UUID, 1.2, expected_revision=7)
    finally:
        reset_cancel_token(context)
    assert track.width == 0.8 and track.set_calls == 0
    assert runtime.revision == 7 and not runtime.dirty
