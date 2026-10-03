from pathlib import Path

import yaml

import dcc_mcp_kicad

SKILL = Path(dcc_mcp_kicad.__file__).resolve().parent / "skills/kicad-board"


def test_skill_contract():
    skill = SKILL
    tools = yaml.safe_load((skill / "tools.yaml").read_text())["tools"]
    assert len(tools) == 9
    for tool in tools:
        assert (skill / tool["source_file"]).is_file()
        assert tool["affinity"] == "main"
        assert tool["requires_in_process"]
        assert tool["input_schema"]["additionalProperties"] is False
        assert all(
            isinstance(tool["annotations"][key], bool)
            for key in ["read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"]
        )
        if tool["name"] in {"save_board", "open_board", "export_svg", "run_drc"}:
            assert tool["execution"] == "async"


def test_native_import_is_lazy():
    from dcc_mcp_kicad import KicadMcpServer

    assert KicadMcpServer.__name__ == "KicadMcpServer"


def test_input_output_schemas_are_valid_and_bounded():
    from jsonschema import Draft202012Validator

    tools = yaml.safe_load((SKILL / "tools.yaml").read_text())["tools"]
    for tool in tools:
        Draft202012Validator.check_schema(tool["input_schema"])
        Draft202012Validator.check_schema(tool["output_schema"])
        schema = tool["output_schema"]
        variants = schema.get("anyOf", [schema])
        assert all(variant["additionalProperties"] is False for variant in variants)
        for name in ("path", "output_path"):
            if name in tool["input_schema"]["properties"]:
                assert tool["input_schema"]["properties"][name]["maxLength"] == 4096
        if tool["name"] not in {"status", "inspect_board"}:
            assert tool["input_schema"]["properties"]["expected_revision"]["type"] == "integer"
