"""Canonical envelopes for the typed board operations."""

from dcc_mcp_core.cancellation import DccMcpCancelledError
from dcc_mcp_core.skill import skill_error, skill_success

from .runtime import BoardError, get_runtime

MUTATIONS = {
    "create_sensor_carrier",
    "create_board",
    "open_board",
    "set_track_width",
    "save_board",
    "export_svg",
    "run_drc",
}
OPERATIONS = MUTATIONS | {"status", "inspect_board"}


def call(operation, **kwargs):
    if operation not in OPERATIONS:
        return skill_error("Unknown operation", "unknown_operation")
    try:
        result = getattr(get_runtime(), operation)(**kwargs)
        if operation in MUTATIONS:
            return skill_success(
                "KiCad operation verified",
                verified=True,
                postcondition={"method": "native_readback_or_parsed_artifact", "operation": operation},
                **result,
            )
        return skill_success("KiCad state inspected", **result)
    except DccMcpCancelledError:
        raise
    except BoardError as exc:
        return skill_error(str(exc), exc.code)
    except (TypeError, ValueError) as exc:
        return skill_error(str(exc), "invalid_input")
    except Exception as exc:
        return skill_error(str(exc), type(exc).__name__)
