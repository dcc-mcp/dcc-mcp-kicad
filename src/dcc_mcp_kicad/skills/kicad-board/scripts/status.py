from dcc_mcp_core.skill import run_main

from dcc_mcp_kicad.skill_tools import call


def main(**kwargs):
    return call("status", **kwargs)


if __name__ == "__main__":
    run_main(main)
