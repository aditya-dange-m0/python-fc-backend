"""
Tool loading for the full-stack agent.

Loads all available tools from different modules.
"""

import logging
from typing import List
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


def load_all_tools() -> List[BaseTool]:
    """
    Load all available tools for the agent.

    Returns:
        List of LangChain tools ready for agent use
    """
    tools = []

    # File Tools (E2B Sandbox)
    try:
        from tools.file_tools_e2b import (
            read_file,
            write_file,
            file_exists,
            list_directory,
            create_directory,
            delete_file,
            batch_read_files,
            batch_write_files,
        )

        file_tools = [
            read_file,
            write_file,
            file_exists,
            list_directory,
            create_directory,
            delete_file,
            batch_read_files,
            batch_write_files,
        ]

        tools.extend(file_tools)
        logger.info(f"Loaded {len(file_tools)} file tools")
    except Exception as e:
        logger.error(f"Failed to load file tools: {e}", exc_info=True)

    # Edit Tools (E2B Sandbox)
    try:
        from tools.edit_tools_e2b import (
            edit_file,
            smart_edit_file,
        )

        edit_tools = [
            edit_file,
            smart_edit_file,
        ]

        tools.extend(edit_tools)
        logger.info(f"Loaded {len(edit_tools)} edit tools")
    except Exception as e:
        logger.error(f"Failed to load edit tools: {e}", exc_info=True)

    # Command Tools (E2B Sandbox)
    try:
        from tools.command_tools_e2b import (
            run_command,
            list_processes,
            kill_process,
            get_service_url,
        )

        command_tools = [
            run_command,
            list_processes,
            kill_process,
            get_service_url,
        ]

        tools.extend(command_tools)
        logger.info(f"Loaded {len(command_tools)} command tools")
    except Exception as e:
        logger.error(f"Failed to load command tools: {e}", exc_info=True)

    # Memory Tools
    try:
        from tools.memory_tools import (
            save_to_memory,
            retrieve_memory,
        )

        memory_tools = [
            save_to_memory,
            retrieve_memory,
        ]

        tools.extend(memory_tools)
        logger.info(f"Loaded {len(memory_tools)} memory tools")
    except Exception as e:
        logger.error(f"Failed to load memory tools: {e}", exc_info=True)

    # Web Search Tool
    try:
        from tools.web_search_tool import search_web

        tools.append(search_web)
        logger.info("Loaded search_web tool")
    except Exception as e:
        logger.error(f"Failed to load web search tool: {e}", exc_info=True)

    logger.info(f"Total tools loaded: {len(tools)}")

    if len(tools) == 0:
        logger.warning("No tools loaded! Agent will have limited functionality.")

    return tools

