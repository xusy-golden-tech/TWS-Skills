"""MCP tool modules — each module exports a register_tools(registry, store_factory) function."""

from .search import register_tools as register_search_tools
from .code import register_tools as register_code_tools
from .analysis import register_tools as register_analysis_tools
from .advanced import register_tools as register_advanced_tools
from .query import register_tools as register_query_tools
from .dev_assist import register_tools as register_dev_assist_tools

__all__ = [
    "register_search_tools",
    "register_code_tools",
    "register_analysis_tools",
    "register_advanced_tools",
    "register_query_tools",
    "register_dev_assist_tools",
]
