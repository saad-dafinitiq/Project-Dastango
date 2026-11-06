"""Editing operations as tools (DST-39): `edit.<name>` for each operation in
agents/edit_agent/ops.py, so the video agent, the edit agent and the API call them the same way.

The names are listed here so that registering them imports nothing heavy (the operations
themselves import the video agent); a test keeps this list equal to the real table.
"""
from mcp.tool_registry import registry

from .edit_op_tool import OP_NAMES, EditOpTool

for _name in OP_NAMES:
    registry.register(EditOpTool(_name))

__all__ = ["EditOpTool", "OP_NAMES"]
