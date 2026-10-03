"""Headless KiCad file adapter. Discovery never imports pcbnew."""

__version__ = "0.1.0"


def __getattr__(name):
    if name == "KicadMcpServer":
        from .server import KicadMcpServer

        return KicadMcpServer
    raise AttributeError(name)
