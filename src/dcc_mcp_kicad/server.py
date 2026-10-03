"""Composition root. Core owns discovery, MCP, jobs, and serialized dispatch."""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import signal
import threading
from pathlib import Path

from dcc_mcp_core import DccServerBase, DccServerOptions, HostExecutionBridge, MinimalModeConfig
from dcc_mcp_core.host import QueueDispatcher, StandaloneHost
from dcc_mcp_core.host_errors import capture_bootstrap_errors

from . import __version__
from .runtime import BoardError, BoardRuntime, set_runtime


class NativeDispatcher:
    """Reentrant facade over the Core queue; never creates a second queue."""

    def __init__(self, queue, runtime):
        self.queue = queue
        self.runtime = runtime

    def is_host_thread(self):
        return threading.get_ident() == self.runtime.thread_id

    def dispatch_callable(self, func, *args, timeout_hint_secs=None, **metadata):
        if self.is_host_thread():
            return func(*args)
        context = contextvars.copy_context()
        expired = threading.Event()

        def run():
            if expired.is_set():
                raise BoardError("dispatch_expired", "Queued operation expired before native execution")
            return context.run(func, *args)

        handle = self.queue.post(run)
        try:
            return handle.wait(timeout=timeout_hint_secs or 150)
        except BaseException:
            # This prevents a not-yet-started queued callback running after the waiter left.
            # An already-started native call remains monolithic and cannot be preempted.
            expired.set()
            raise


class KicadMcpServer(DccServerBase):
    def __init__(self, workspace=None, port=None, **kwargs):
        workspace = workspace or os.environ.get("DCC_MCP_KICAD_WORKSPACE")
        if not workspace:
            raise ValueError("Set DCC_MCP_KICAD_WORKSPACE or pass an explicit workspace directory")
        if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535):
            raise ValueError("port must be an integer from 0 through 65535")
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self.board_runtime = BoardRuntime(workspace)
        self.host_version = "not-started"
        self.host_queue = QueueDispatcher()
        self.host_driver = StandaloneHost(self.host_queue, thread_name="kicad-native-host")
        self.bridge = HostExecutionBridge(
            dispatcher=NativeDispatcher(self.host_queue, self.board_runtime), host_dispatcher=self.host_queue
        )
        # Direct loopback-only is the default; gateway opt-in is an operator decision.
        kwargs.setdefault("gateway_port", 0)
        kwargs.setdefault("enable_gateway_failover", False)
        options = DccServerOptions.from_env(
            "kicad",
            Path(__file__).parent / "skills",
            port=port,
            server_name="dcc-mcp-kicad",
            server_version=__version__,
            adapter_version=__version__,
            instance_type="standalone",
            execution_bridge=self.bridge,
            **kwargs,
        )
        super().__init__(options=options)
        self.register_builtin_actions(minimal_mode=MinimalModeConfig(skills=()))

    def _version_string(self):
        return self.host_version

    def start(self, **kwargs):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Stopped KiCad server instances cannot restart; create a new KicadMcpServer")
            if self.host_driver.is_running:
                return super().start(**kwargs)
            set_runtime(self.board_runtime)
            try:
                self.host_driver.start()
                status = self.host_queue.post(self.board_runtime.bind).wait(timeout=30)
                self.host_version = status["host_version"]
                return super().start(**kwargs)
            except BaseException:
                self.stop()
                raise

    def stop(self):
        with self._lifecycle_lock:
            if self._closed:
                return
            try:
                super().stop()
            finally:
                try:
                    if self.host_driver.is_running:
                        self.host_queue.post(self.board_runtime.close).wait(timeout=130)
                        self.host_driver.stop(timeout=130)
                    else:
                        self.host_queue.shutdown()
                finally:
                    # Do not release ownership while native work is still alive.
                    if not self.host_driver.is_running:
                        set_runtime(None, owner=self.board_runtime)
                        self._closed = True


def main():
    parser = argparse.ArgumentParser(description="KiCad 9 headless board-file MCP adapter")
    parser.add_argument("--workspace", default=os.environ.get("DCC_MCP_KICAD_WORKSPACE"))
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    done = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: done.set())
    with capture_bootstrap_errors("kicad", adapter_version=__version__, min_core_version="0.20.41"):
        server = KicadMcpServer(args.workspace, args.port)
        server.start()
        print(json.dumps({"ready": True, "mcp_url": server.mcp_url}), flush=True)
    try:
        done.wait()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
