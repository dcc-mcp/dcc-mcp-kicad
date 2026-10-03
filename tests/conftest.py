"""Keep native gate markers registered when tests run outside the source tree."""


def pytest_configure(config):
    config.addinivalue_line("markers", "native: requires pcbnew and kicad-cli")
    config.addinivalue_line("markers", "mcp: real Core HTTP MCP acceptance")
