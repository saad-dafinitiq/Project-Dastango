"""One editing operation as a registry tool."""
from __future__ import annotations

from typing import Any

from mcp.base_tool import BaseTool, ToolResult

OP_NAMES = ("set_transition", "set_camera", "set_music", "set_volume", "set_ambience", "set_graphics",
            "set_effect", "regenerate_shot", "edit_line", "set_voice", "remove_scene", "move_scene")


class EditOpTool(BaseTool):
    category = "edit"

    def __init__(self, op: str):
        self.op = op
        self.name = f"edit.{op}"
        self.description = f"Editing operation {op}: see agents/edit_agent/ops.py."

    def run(self, state: Any = None, project_id: str | None = None, **params) -> ToolResult:
        """With a `state`, change it in place (what the video agent and tests do) and say what was
        done. With a `project_id`, apply it to the film as a new version, cutting it again if it
        has been rendered (what a click does). Parameters are validated either way."""
        from agents.edit_agent import EditAgent, ops

        try:
            if state is not None:
                return ToolResult(success=True, data=ops.apply(state, self.op, params),
                                  metadata={"op": self.op})
            if not project_id:
                return ToolResult(success=False, error="give a state or a project_id")
            result = EditAgent().apply_op(project_id, self.op, params)
            return ToolResult(success=result.success, data=result.message, error=result.error,
                              metadata={"op": self.op, "version": result.new_version})
        except ops.OpError as e:
            return ToolResult(success=False, error=str(e), metadata={"op": self.op})
