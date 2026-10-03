"""Bounded board operations; called only on the Core-owned serialized host lane."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from xml.etree import ElementTree

from dcc_mcp_core.cancellation import check_cancelled


class BoardError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class BoardRuntime:
    MAX_FILE_BYTES = 10 * 1024 * 1024
    MAX_ITEMS = 10000

    def __init__(self, workspace, cli=None):
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.workspace_identity = (self.workspace.stat().st_dev, self.workspace.stat().st_ino)
        self.cli = cli or shutil.which("kicad-cli")
        self.cli_version = None
        self.cli_compatible = False
        self.revision = 0
        self.source_sha256 = None
        self.board = None
        self.path = None
        self.dirty = False
        self.thread_id = None
        self.pcb = None

    def bind(self):
        if self.thread_id is not None and self.thread_id != threading.get_ident():
            raise BoardError("wrong_thread", "Runtime already belongs to a different host lane")
        self.thread_id = threading.get_ident()
        import pcbnew

        self.pcb = pcbnew
        host = self._version(pcbnew.GetBuildVersion())
        if host[0] != 9:
            raise BoardError("unsupported_host", "This adapter supports the KiCad 9 pcbnew API only")
        if self.cli:
            try:
                probe = subprocess.run(
                    [self.cli, "version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise BoardError("cli_unavailable", "Cannot run the configured KiCad CLI version probe") from exc
            self.cli_version = probe.stdout.strip()[:256]
            if probe.returncode != 0 or self._version(self.cli_version)[:2] != host[:2]:
                raise BoardError(
                    "incompatible_cli", "pcbnew and kicad-cli must have matching KiCad 9 major/minor versions"
                )
            self.cli_compatible = True
        return self.status()

    @staticmethod
    def _version(value):
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:$|[+~.-])", str(value))
        if not match:
            raise BoardError("unsupported_host", "Unrecognized KiCad version")
        return tuple(int(part) for part in match.groups())

    def close(self):
        if self.thread_id is not None and self.thread_id != threading.get_ident():
            raise BoardError("wrong_thread", "Runtime cleanup requires the owning host lane")
        self.board = self.pcb = self.path = None
        self.thread_id = None
        self.dirty = False
        self.source_sha256 = None
        self.cli_compatible = False

    def _revision_guard(self, expected_revision):
        if expected_revision is not None:
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
                raise BoardError("invalid_input", "expected_revision must be a nonnegative integer")
            if expected_revision != self.revision:
                raise BoardError("stale_revision", "Board changed; inspect it and retry with the current revision")

    def _lane(self):
        if self.thread_id is None or self.thread_id != threading.get_ident():
            raise BoardError("wrong_thread", "pcbnew access requires the adapter's serialized host lane")
        check_cancelled()

    def _board(self):
        self._lane()
        if self.board is None:
            raise BoardError("no_board", "Create or open a board first")
        return self.board

    def _path(self, value, suffix, existing=False):
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\x00" in value:
            raise BoardError("invalid_path", "A nonempty workspace-relative or absolute path is required")
        path = Path(value).expanduser()
        if ".." in path.parts:
            raise BoardError("outside_workspace", "Parent traversal is not allowed")
        if not path.is_absolute():
            path = self.workspace / path
        try:
            root_stat = self.workspace.stat()
            if (root_stat.st_dev, root_stat.st_ino) != self.workspace_identity:
                raise BoardError("workspace_changed", "Workspace directory identity changed; restart the service")
            resolved = path.resolve()
        except (OSError, RuntimeError) as exc:
            raise BoardError("invalid_path", "Path cannot be resolved") from exc
        if not resolved.is_relative_to(self.workspace):
            raise BoardError("outside_workspace", "Path must stay inside the configured workspace")
        # Reject even in-root symlinks; explicit paths never silently redirect writes.
        try:
            relative = Path(os.path.abspath(path)).relative_to(self.workspace)
        except ValueError as exc:
            raise BoardError("outside_workspace", "Path must stay inside the configured workspace") from exc
        component = self.workspace
        for part in relative.parts:
            component = component / part
            if component.is_symlink():
                raise BoardError("symlink_path", "Symlink path components are not allowed")
        path = resolved
        if path.exists() and not stat.S_ISREG(path.stat().st_mode):
            raise BoardError("invalid_file", "Expected a regular file")
        if path.suffix.lower() != suffix:
            raise BoardError("invalid_extension", "Expected a " + suffix + " path")
        if not path.parent.is_dir():
            raise BoardError("missing_directory", "Output parent directory must already exist")
        if existing:
            if not path.is_file():
                raise BoardError("missing_file", "Board file does not exist")
            if path.stat().st_size > self.MAX_FILE_BYTES:
                raise BoardError("board_too_large", "Board exceeds the 10 MiB input limit")
        return path

    @staticmethod
    def _number(value, minimum, maximum, label):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise BoardError("invalid_input", label + " must be a number")
        if not minimum <= value <= maximum or not math.isfinite(value):
            raise BoardError("invalid_input", label + " is outside the supported range")
        return float(value)

    def status(self):
        self._lane()
        return {
            "host_version": self.pcb.GetBuildVersion(),
            "runtime": "headless-pcbnew-file",
            "workspace": str(self.workspace),
            "board_loaded": self.board is not None,
            "dirty": self.dirty,
            "cli_available": bool(self.cli),
            "cli_version": self.cli_version,
            "cli_compatible": self.cli_compatible,
            "python_version": sys.version.split()[0],
            "revision": self.revision,
            "host_thread_id": self.thread_id,
            "process_id": os.getpid(),
            "live_editor_connection": False,
        }

    def _discard_guard(self, discard_unsaved):
        if not isinstance(discard_unsaved, bool):
            raise BoardError("invalid_input", "discard_unsaved must be a boolean")
        if self.dirty and not discard_unsaved:
            raise BoardError("unsaved_changes", "Save first or explicitly set discard_unsaved=true")

    def create_board(
        self, width_mm=60, height_mm=40, track_width_mm=0.8, discard_unsaved=False, expected_revision=None
    ):
        self._lane()
        self._discard_guard(discard_unsaved)
        self._revision_guard(expected_revision)
        width = self._number(width_mm, 20, 200, "width_mm")
        height = self._number(height_mm, 20, 200, "height_mm")
        track_width = self._number(track_width_mm, 0.1, 3, "track_width_mm")
        p = self.pcb
        v = lambda x, y: p.VECTOR2I(p.FromMM(x), p.FromMM(y))  # noqa: E731
        board = p.BOARD()
        board.SetCopperLayerCount(2)
        corners = [(0, 0), (width, 0), (width, height), (0, height)]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            edge = p.PCB_SHAPE(board)
            edge.SetShape(p.SHAPE_T_SEGMENT)
            edge.SetStart(v(*start))
            edge.SetEnd(v(*end))
            edge.SetLayer(p.Edge_Cuts)
            edge.SetWidth(p.FromMM(0.1))
            board.Add(edge)
        net = p.NETINFO_ITEM(board, "SIGNAL")
        board.Add(net)
        for reference, x in [("J1", 5), ("J2", width - 5)]:
            footprint = p.FOOTPRINT(board)
            footprint.SetReference(reference)
            footprint.SetValue("SYNTHETIC_TEST_POINT")
            footprint.SetPosition(v(x, height / 2))
            board.Add(footprint)
            pad = p.PAD(footprint)
            pad.SetNumber("1")
            pad.SetShape(p.PAD_SHAPE_CIRCLE)
            pad.SetAttribute(p.PAD_ATTRIB_PTH)
            pad.SetSize(v(4, 4))
            pad.SetDrillSize(v(1.2, 1.2))
            pad.SetLayerSet(p.LSET.AllCuMask())
            pad.SetPosition(v(x, height / 2))
            pad.SetNet(net)
            footprint.Add(pad)
            footprint.Reference().SetPosition(v(x, height / 2 - 5))
            footprint.Value().SetVisible(False)
        track = p.PCB_TRACK(board)
        track.SetStart(v(5, height / 2))
        track.SetEnd(v(width - 5, height / 2))
        track.SetWidth(p.FromMM(track_width))
        track.SetLayer(p.F_Cu)
        track.SetNet(net)
        board.Add(track)
        result = self._summary(board)
        if result["counts"] != {"footprints": 2, "tracks": 1, "drawings": 4}:
            raise BoardError("verification_failed", "Created board did not match expected geometry")
        check_cancelled()
        self.board, self.path, self.dirty = board, None, True
        self.source_sha256 = None
        self.revision += 1
        return dict(result, path=None, dirty=True, revision=self.revision)

    def create_sensor_carrier(
        self, width_mm=80, height_mm=50, label="AERIS SENSOR CARRIER", discard_unsaved=False, expected_revision=None
    ):
        """Fixed conceptual topology with native primitives, never library substitutions."""
        self._lane()
        self._discard_guard(discard_unsaved)
        self._revision_guard(expected_revision)
        w = self._number(width_mm, 70, 100, "width_mm")
        h = self._number(height_mm, 45, 70, "height_mm")
        if not isinstance(label, str) or not 1 <= len(label) <= 32 or not label.isascii() or not label.isprintable():
            raise BoardError("invalid_input", "label must contain 1–32 printable ASCII characters")
        p = self.pcb
        board = p.BOARD()
        board.SetCopperLayerCount(2)

        def vec(x, y):
            return p.VECTOR2I(p.FromMM(x), p.FromMM(y))

        def line(a, b, layer=p.F_SilkS, thickness=0.15):
            item = p.PCB_SHAPE(board)
            item.SetShape(p.SHAPE_T_SEGMENT)
            item.SetStart(vec(*a))
            item.SetEnd(vec(*b))
            item.SetLayer(layer)
            item.SetWidth(p.FromMM(thickness))
            board.Add(item)

        def text(value, x, y, size=1):
            item = p.PCB_TEXT(board)
            item.SetText(value)
            item.SetPosition(vec(x, y))
            item.SetTextSize(vec(size, size))
            item.SetTextThickness(p.FromMM(size / 6))
            item.SetLayer(p.F_SilkS)
            board.Add(item)

        for a, b in [((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))]:
            line(a, b, p.Edge_Cuts, 0.1)
        nets = {}
        for name in ["+3V3", "GND", "SDA", "SCL", "ALERT"]:
            net = p.NETINFO_ITEM(board, name)
            board.Add(net)
            nets[name] = net
        anchors = {}

        def footprint(reference, value, x, y, pads, through=False, hole=False):
            fp = p.FOOTPRINT(board)
            fp.SetReference(reference)
            fp.SetValue(value)
            fp.SetPosition(vec(x, y))
            board.Add(fp)
            for number, dx, dy, netname in pads:
                pad = p.PAD(fp)
                pad.SetNumber(str(number))
                pad.SetPosition(vec(x + dx, y + dy))
                pad.SetShape(p.PAD_SHAPE_CIRCLE if through else p.PAD_SHAPE_RECT)
                pad.SetAttribute(p.PAD_ATTRIB_NPTH if hole else p.PAD_ATTRIB_PTH if through else p.PAD_ATTRIB_SMD)
                pad.SetSize(vec(3.2, 3.2) if hole else vec(1.8, 1.8) if through else vec(1.2, 0.65))
                if through:
                    pad.SetDrillSize(vec(3.2, 3.2) if hole else vec(0.9, 0.9))
                    pad.SetLayerSet(p.LSET.AllCuMask())
                else:
                    layers = p.LSET()
                    layers.AddLayer(p.F_Cu)
                    layers.AddLayer(p.F_Mask)
                    layers.AddLayer(p.F_Paste)
                    pad.SetLayerSet(layers)
                if netname:
                    pad.SetNet(nets[netname])
                fp.Add(pad)
                anchors[(reference, str(number))] = (x + dx, y + dy, netname)
            fp.Reference().SetPosition(vec(x, y - 4 if through else y - 3))
            fp.Reference().SetTextSize(vec(0.8, 0.8))
            fp.Reference().SetTextThickness(p.FromMM(0.12))
            fp.Value().SetVisible(False)
            if hole:
                fp.Reference().SetVisible(False)
            return fp

        for index, (x, y) in enumerate([(4, 4), (w - 4, 4), (w - 4, h - 4), (4, h - 4)], 1):
            footprint("H" + str(index), "M3 MOUNT", x, y, [(1, 0, 0, None)], through=True, hole=True)
        cx, cy = w / 2, h / 2
        footprint(
            "U1",
            "SENSOR CONCEPT 8PAD",
            cx,
            cy,
            [
                (1, -2.3, -1.5, "+3V3"),
                (2, -2.3, -0.5, "GND"),
                (3, -2.3, 0.5, "SDA"),
                (4, -2.3, 1.5, "SCL"),
                (5, 2.3, 1.5, "ALERT"),
                (6, 2.3, 0.5, "GND"),
                (7, 2.3, -0.5, "GND"),
                (8, 2.3, -1.5, "+3V3"),
            ],
        )
        for a, b in [
            ((cx - 1.4, cy - 2.3), (cx + 1.4, cy - 2.3)),
            ((cx + 1.4, cy - 2.3), (cx + 1.4, cy + 2.3)),
            ((cx + 1.4, cy + 2.3), (cx - 1.4, cy + 2.3)),
            ((cx - 1.4, cy + 2.3), (cx - 1.4, cy - 2.3)),
        ]:
            line(a, b)
        footprint("J1", "POWER IN", 10, cy, [(1, 0, -1.27, "+3V3"), (2, 0, 1.27, "GND")], through=True)
        footprint(
            "J2",
            "I2C HOST",
            w - 10,
            cy,
            [(1, 0, -3.81, "+3V3"), (2, 0, -1.27, "GND"), (3, 0, 1.27, "SDA"), (4, 0, 3.81, "SCL")],
            through=True,
        )
        for ref, x, y, first, second, value in [
            ("C1", cx - 10, cy - 8, "+3V3", "GND", "100nF CONCEPT"),
            ("C2", cx - 10, cy + 8, "+3V3", "GND", "1uF CONCEPT"),
            ("R1", cx + 10, cy - 8, "+3V3", "SDA", "4k7 CONCEPT"),
            ("R2", cx + 10, cy + 8, "+3V3", "SCL", "4k7 CONCEPT"),
        ]:
            footprint(ref, value, x, y, [(1, -1, 0, first), (2, 1, 0, second)])
        # Only four explicit power routes are complete; signal nets remain unrouted.
        routes = [
            (("J1", "1"), ("C1", "1"), [(18, cy - 1.27), (18, cy - 8)]),
            (("C1", "1"), ("U1", "1"), [(cx - 11, cy - 4), (cx - 2.3, cy - 4)]),
            (("J1", "2"), ("C2", "2"), [(15, cy + 1.27), (15, cy + 12), (cx - 9, cy + 12)]),
            (("C2", "2"), ("U1", "2"), [(cx - 6, cy + 8), (cx - 6, cy - 0.5)]),
        ]
        for start, end, bends in routes:
            x1, y1, name = anchors[start]
            x2, y2, target_name = anchors[end]
            if name != target_name:
                raise BoardError("template_error", "Route net mismatch")
            points = [(x1, y1), *bends, (x2, y2)]
            for a, b in zip(points, points[1:], strict=False):
                if a == b:
                    continue
                track = p.PCB_TRACK(board)
                track.SetStart(vec(*a))
                track.SetEnd(vec(*b))
                track.SetWidth(p.FromMM(0.35))
                track.SetLayer(p.F_Cu)
                track.SetNet(nets[name])
                board.Add(track)
        text(label, cx, 6.5, 1.8)
        text("ENVIRONMENTAL SENSOR / I2C", cx, 10, 0.9)
        text("3V3 IN", 12, cy + 7, 1)
        text("I2C HOST", w - 12, cy + 9, 1)
        text("REV A  /  CONCEPT ONLY", cx, h - 7, 1)
        text("NOT ELECTRICALLY VALIDATED", cx, h - 4, 0.8)
        line((8, 13), (w - 8, 13), thickness=0.2)
        line((8, h - 11), (w - 8, h - 11), thickness=0.2)
        result = self._summary(board)
        if result["counts"]["footprints"] != 11 or result["counts"]["tracks"] < 12:
            raise BoardError("verification_failed", "Sensor carrier native readback mismatch")
        check_cancelled()
        self.board, self.path, self.dirty = board, None, True
        self.source_sha256 = None
        self.revision += 1
        return dict(
            result,
            path=None,
            dirty=True,
            revision=self.revision,
            template="sensor-carrier-concept",
            dimensions_mm=[w, h],
            net_names=sorted(nets),
            electrically_validated=False,
            limitations=["synthetic footprints", "partial conceptual routing", "no schematic or ERC"],
        )

    def _summary(self, board):
        p = self.pcb
        tracks = sorted(board.GetTracks(), key=lambda item: item.m_Uuid.AsString())
        footprints = sorted(board.GetFootprints(), key=lambda item: item.GetReference())
        drawings = list(board.GetDrawings())
        if len(tracks) + len(footprints) + len(drawings) > self.MAX_ITEMS:
            raise BoardError("board_too_large", "Board exceeds the 10000 top-level item limit")
        return {
            "counts": {"footprints": len(footprints), "tracks": len(tracks), "drawings": len(drawings)},
            "tracks": [
                {"uuid": item.m_Uuid.AsString(), "width_mm": p.ToMM(item.GetWidth()), "net": item.GetNetname()}
                for item in tracks[:1000]
            ],
            "footprints": [item.GetReference() for item in footprints[:1000]],
            "truncated": len(tracks) > 1000 or len(footprints) > 1000,
        }

    def inspect_board(self):
        board = self._board()
        return dict(
            self._summary(board), path=str(self.path) if self.path else None, dirty=self.dirty, revision=self.revision
        )

    def set_track_width(self, track_uuid, width_mm, expected_revision=None):
        board = self._board()
        self._revision_guard(expected_revision)
        if not isinstance(track_uuid, str) or len(track_uuid) != 36:
            raise BoardError("invalid_input", "track_uuid must be a canonical UUID from inspect_board")
        try:
            if str(uuid.UUID(track_uuid)) != track_uuid:
                raise ValueError("noncanonical")
        except ValueError as exc:
            raise BoardError("invalid_input", "track_uuid must be a canonical UUID from inspect_board") from exc
        width = self._number(width_mm, 0.1, 3, "width_mm")
        matches = [item for item in board.GetTracks() if item.m_Uuid.AsString() == track_uuid]
        if len(matches) != 1 or type(matches[0]).__name__ != "PCB_TRACK":
            raise BoardError("track_not_found", "Expected the UUID of one straight PCB track, not a via")
        item = matches[0]
        old = self.pcb.ToMM(item.GetWidth())
        native_width = self.pcb.FromMM(width)
        check_cancelled()
        try:
            item.SetWidth(native_width)
        finally:
            # A native setter can mutate before raising; require reinspection either way.
            self.dirty = True
            self.revision += 1
        actual = self.pcb.ToMM(item.GetWidth())
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or not math.isfinite(actual)
            or abs(actual - width) > 1e-6
        ):
            raise BoardError("verification_failed", "Native track width readback did not match")
        return {
            "track_uuid": track_uuid,
            "previous_width_mm": old,
            "width_mm": actual,
            "dirty": True,
            "revision": self.revision,
        }

    def open_board(self, path, discard_unsaved=False, expected_revision=None):
        self._lane()
        self._discard_guard(discard_unsaved)
        self._revision_guard(expected_revision)
        source = self._path(path, ".kicad_pcb", existing=True)
        before = self._artifact(source)["sha256"]
        try:
            board = self.pcb.LoadBoard(str(source))
        except Exception as exc:
            raise BoardError("invalid_board", "KiCad could not parse the board") from exc
        if board is None:
            raise BoardError("invalid_board", "KiCad could not load the board")
        summary = self._summary(board)
        if self._artifact(source)["sha256"] != before:
            raise BoardError("stale_file", "Board file changed while being opened")
        check_cancelled()
        self.board, self.path, self.dirty = board, source, False
        self.source_sha256 = before
        self.revision += 1
        return dict(summary, path=str(source), dirty=False, revision=self.revision)

    @classmethod
    def _artifact(cls, path):
        with path.open("rb") as stream:
            data = stream.read(cls.MAX_FILE_BYTES + 1)
        if len(data) > cls.MAX_FILE_BYTES:
            raise BoardError("artifact_too_large", "Artifact exceeds the 10 MiB limit")
        return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    @classmethod
    def _source_guard(cls, target, expected_sha256):
        try:
            actual = cls._artifact(target)["sha256"]
        except FileNotFoundError as exc:
            raise BoardError("stale_file", "Loaded board was removed externally; reopen it before saving") from exc
        if actual != expected_sha256:
            raise BoardError("stale_file", "Saved board changed externally; reopen it before overwriting")

    def _publish(self, stage, target, overwrite, expected_source_sha256=None):
        self._path(str(target), target.suffix.lower())
        # Windows requires a writable file descriptor for fsync.
        with stage.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        if expected_source_sha256 is not None:
            self._source_guard(target, expected_source_sha256)
        # Hash checking and replacement are separate filesystem operations.
        # External writers must stay paused across this final publication window.
        check_cancelled()
        if overwrite:
            os.replace(stage, target)
        else:
            try:
                os.link(stage, target)
            except FileExistsError as exc:
                raise BoardError("output_exists", "Output exists; explicitly set overwrite=true to replace it") from exc

    def _target(self, path, suffix, overwrite):
        if not isinstance(overwrite, bool):
            raise BoardError("invalid_input", "overwrite must be a boolean")
        target = self._path(path, suffix)
        if target.exists() and not overwrite:
            raise BoardError("output_exists", "Output exists; explicitly set overwrite=true to replace it")
        return target

    def save_board(self, path, overwrite=False, expected_revision=None):
        board = self._board()
        self._revision_guard(expected_revision)
        target = self._target(path, ".kicad_pcb", overwrite)
        self._summary(board)
        expected_source_sha256 = self.source_sha256 if target == self.path else None
        if expected_source_sha256 is not None:
            self._source_guard(target, expected_source_sha256)
        with tempfile.TemporaryDirectory(prefix=".kicad-save-", dir=target.parent) as directory:
            stage = Path(directory) / "board.kicad_pcb"
            if not self.pcb.SaveBoard(str(stage), board):
                raise BoardError("save_failed", "KiCad SaveBoard failed")
            expected = self._artifact(stage)["sha256"]
            loaded = self.pcb.LoadBoard(str(stage))
            summary = self._summary(loaded)
            verify = Path(directory) / "verify.kicad_pcb"
            if not self.pcb.SaveBoard(str(verify), loaded) or self._artifact(verify)["sha256"] != expected:
                raise BoardError("verification_failed", "Native reopen/resave did not preserve the complete board")
            self._publish(stage, target, overwrite, expected_source_sha256)
            loaded = self.pcb.LoadBoard(str(target))
            if not self.pcb.SaveBoard(str(verify), loaded) or self._artifact(verify)["sha256"] != expected:
                raise BoardError("verification_failed", "Published complete-board readback mismatch")
        self.board, self.path, self.dirty = loaded, target, False
        self.source_sha256 = expected
        self.revision += 1
        return dict(
            self._artifact(target),
            board=dict(summary, path=str(target), dirty=False, revision=self.revision),
            reopened=True,
            revision=self.revision,
        )

    def _cli_report(self, kind, output_path, overwrite=False, timeout_secs=60, expected_revision=None):
        board = self._board()
        self._revision_guard(expected_revision)
        timeout = self._number(timeout_secs, 1, 120, "timeout_secs")
        if not self.cli or not self.cli_compatible:
            raise BoardError("cli_unavailable", "kicad-cli must be installed and available on PATH")
        suffix = ".svg" if kind == "svg" else ".json"
        target = self._target(output_path, suffix, overwrite)
        with tempfile.TemporaryDirectory(prefix=".kicad-cli-", dir=target.parent) as directory:
            root = Path(directory)
            source, output = root / "snapshot.kicad_pcb", root / ("result" + suffix)
            if not self.pcb.SaveBoard(str(source), board):
                raise BoardError("save_failed", "Could not snapshot the current board")
            self._artifact(source)
            if kind == "svg":
                args = [
                    "pcb",
                    "export",
                    "svg",
                    "--layers",
                    "F.Cu,F.Silkscreen,Edge.Cuts",
                    "--mode-single",
                    "--fit-page-to-board",
                    "--exclude-drawing-sheet",
                ]
            else:
                args = ["pcb", "drc", "--format", "json", "--severity-all"]
            env = dict(os.environ)
            for key, name in [("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")]:
                env[key] = str(root / name)
                (root / name).mkdir()
            check_cancelled()
            try:
                with (root / "stdout.log").open("wb") as stdout, (root / "stderr.log").open("wb") as stderr:
                    process = subprocess.run(
                        [self.cli, *args, "-o", str(output), str(source)],
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        timeout=timeout,
                        env=env,
                        cwd=root,
                        check=False,
                    )
            except subprocess.TimeoutExpired as exc:
                raise BoardError("native_timeout", "KiCad CLI was killed after the requested timeout") from exc
            check_cancelled()
            if process.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                raise BoardError("cli_failed", "KiCad CLI failed to produce an artifact (exit %s)" % process.returncode)
            if output.stat().st_size > self.MAX_FILE_BYTES:
                raise BoardError("report_too_large", "Report exceeds the 10 MiB limit")
            if kind == "svg":
                tree = ElementTree.parse(output)
                if not tree.getroot().tag.endswith("svg"):
                    raise BoardError("verification_failed", "Export is not SVG")
                details = {"format": "svg", "elements": sum(1 for _ in tree.iter())}
            else:
                report = json.loads(output.read_text())
                keys = ["violations", "unconnected_items", "schematic_parity"]
                if not isinstance(report, dict) or not all(isinstance(report.get(key), list) for key in keys):
                    raise BoardError("verification_failed", "Unexpected KiCad DRC report shape")
                details = {
                    "format": "json",
                    "counts": {key: len(report.get(key, [])) for key in keys},
                    "certification": False,
                    "scope": "current board snapshot; no schematic parity requested",
                }
            expected = self._artifact(output)["sha256"]
            self._publish(output, target, overwrite)
        artifact = self._artifact(target)
        if artifact["sha256"] != expected:
            raise BoardError("verification_failed", "Published report digest mismatch")
        return dict(artifact, **details, revision=self.revision)

    def export_svg(self, output_path, overwrite=False, timeout_secs=60, expected_revision=None):
        return self._cli_report("svg", output_path, overwrite, timeout_secs, expected_revision)

    def run_drc(self, output_path, overwrite=False, timeout_secs=60, expected_revision=None):
        return self._cli_report("drc", output_path, overwrite, timeout_secs, expected_revision)


_active_runtime = None
_runtime_lock = threading.Lock()


def set_runtime(runtime, owner=None):
    global _active_runtime
    with _runtime_lock:
        if runtime is not None and _active_runtime is not None and _active_runtime is not runtime:
            raise RuntimeError("Only one KiCad server runtime per process is supported")
        if runtime is None and owner is not None and _active_runtime is not owner:
            return
        _active_runtime = runtime


def get_runtime():
    if _active_runtime is None:
        raise BoardError("runtime_unavailable", "Start the KiCad adapter before calling board tools")
    return _active_runtime
