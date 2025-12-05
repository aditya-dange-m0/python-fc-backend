"""
Tools Package for LangGraph Agent
==================================

This package provides all tools for the full-stack agent, organized by category:

- Command Tools: E2B sandbox command execution (run_command, list_processes, etc.)
- File Tools: File operations (read, write, list, create, delete)
- Edit Tools: Code editing with intelligent matching strategies
- Memory Tools: Persistent memory storage and retrieval
- Web Search Tool: Web search via Parallel AI

All tools require RuntimeContext with user_id and project_id.
"""

# =============================================================================
# COMMAND TOOLS
# =============================================================================

from .command_tools_e2b import (
    COMMAND_TOOLS,
    CORE_COMMAND_TOOLS,
)

# =============================================================================
# FILE TOOLS
# =============================================================================

from .file_tools_e2b import (
    FILE_TOOLS,
    create_file_tools,
)

# =============================================================================
# EDIT TOOLS
# =============================================================================

from .edit_tools_e2b import (
    EDIT_TOOLS,
)

# =============================================================================
# MEMORY TOOLS
# =============================================================================

from .memory_tools import (
    MEMORY_TOOLS,
    save_to_memory,
    retrieve_memory,
)
# DEPRECATED: MemoryAgentState and MemoryContext are now integrated into:
# - RuntimeContext (from context/runtime_context.py) - includes session_id property
# - FullStackAgentState (from agent_state/state.py) - includes messages and memory_keys fields

# =============================================================================
# WEB SEARCH TOOL
# =============================================================================

from .web_search_tool import (
    SEARCH_TOOL,
    search_web,
)

# =============================================================================
# AGGREGATED TOOL COLLECTIONS
# =============================================================================

# All tools combined
ALL_TOOLS = [
    *COMMAND_TOOLS,
    *FILE_TOOLS,
    *EDIT_TOOLS,
    *MEMORY_TOOLS,
    SEARCH_TOOL,
]

# Sandbox-specific tools (E2B operations)
SANDBOX_TOOLS = [
    *COMMAND_TOOLS,
    *FILE_TOOLS,
    *EDIT_TOOLS,
]

# Agent enhancement tools (memory, search)
AGENT_TOOLS = [
    *MEMORY_TOOLS,
    SEARCH_TOOL,
]

# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Command tools
    "COMMAND_TOOLS",
    "CORE_COMMAND_TOOLS",
    # File tools
    "FILE_TOOLS",
    "create_file_tools",
    # Edit tools
    "EDIT_TOOLS",
    # Memory tools
    "MEMORY_TOOLS",
    "save_to_memory",
    "retrieve_memory",
    # Web search
    "SEARCH_TOOL",
    "search_web",
    # Aggregated collections
    "ALL_TOOLS",
    "SANDBOX_TOOLS",
    "AGENT_TOOLS",
]

# =============================================================================
# METADATA
# =============================================================================

__version__ = "1.0.0"

# Tool counts for reference
TOOL_COUNTS = {
    "command": len(COMMAND_TOOLS),
    "file": len(FILE_TOOLS),
    "edit": len(EDIT_TOOLS),
    "memory": len(MEMORY_TOOLS),
    "search": 1,
    "total": len(ALL_TOOLS),
}
