"""
Production Singleton Agent
===========================

Creates agent ONCE on startup, reuses for all requests.
This is the PRODUCTION approach for FastAPI/web servers.
"""

import os
import asyncio
import logging
from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from checkpoint import get_checkpointer_service
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    SummarizationMiddleware,
)
from context.runtime_context import RuntimeContext
from agent_state import FullStackAgentState
from tools.tool_loader import load_all_tools

load_dotenv()


class MiddlewareConfig:
    """Production middleware configuration"""

    CACHE_TTL = "5m"  # 5 minutes cache
    MIN_MESSAGES_TO_CACHE = 3  # Start caching after 3 messages

    # Context Editing (CRITICAL for performance)
    CONTEXT_TRIGGER_TOKENS = 50000  # Anthropic's limit is 200k
    CLEAR_AT_LEAST_TOKENS = 20000  # Reclaim at least 20k tokens
    KEEP_RECENT_TOOLS = 3  # Keep last 3 tool results
    EXCLUDED_TOOLS = []  # Tools to exclude from context clearing

    # Summarization (MEDIUM priority)
    MAX_TOKENS_BEFORE_SUMMARY = 40000  # Trigger at 150k tokens
    MESSAGES_TO_KEEP = 7  # Keep last 3 messages

    # Metadata Extraction (Always enabled)
    REQUIRED_FIELDS = ["phase", "thinking", "next_steps"]

    # Dynamic Prompts (State-aware)
    ENABLE_PHASE_PROMPTS = True

logger = logging.getLogger(__name__)

# Global agent (singleton)
_agent = None
_agent_lock = asyncio.Lock()


async def get_agent():
    """
    Get singleton agent instance.

    Creates agent ONCE on first call, reuses for all subsequent requests.
    This is the PRODUCTION approach for FastAPI/web servers.
    """
    global _agent

    if _agent is not None:
        return _agent

    async with _agent_lock:
        # Double-check after acquiring lock
        if _agent is not None:
            return _agent

        logger.info("[AGENT] Initializing singleton agent...")

        # Load all tools
        all_tools = load_all_tools()
        logger.info(f"Loaded {len(all_tools)} tools for agent")

        # Get checkpointer service (singleton pattern)
        checkpointer_service = await get_checkpointer_service()

        # Ensure checkpointer initialized
        if not checkpointer_service._initialized:
            await checkpointer_service.initialize()

        # Get checkpointer instance (shared across all requests)
        checkpointer = checkpointer_service.get_checkpointer()

        # Get store instance (shares same MongoDB client)
        store = checkpointer_service.get_store()
        logger.info("Retrieved checkpointer + store from service")

        # Create model
        chat_model = init_chat_model(
            model="gpt-5-mini",
            model_provider="openai",
            streaming=True,
            temperature=0.5,
            timeout=300,  # 5 minutes timeout for long-running operations
            max_tokens=1000,
            configurable_fields=(
                "model",
                "model_provider",
                "streaming",
                "temperature",
                "timeout",
                "max_tokens",
                "base_url",
                "api_key",
            ),
        )

        # Create summary model for middleware (initialized here to avoid module-level blocking)
        summary_model = init_chat_model(
            model="gpt-5-mini",
            model_provider="openai",
            streaming=True,
            temperature=0.5,
            timeout=120,  # 2 minutes timeout for summarization
            max_tokens=2000,
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )

        # Create agent ONCE
        _agent = create_agent(
            model=chat_model,
            debug=True,
            system_prompt="""You are an intelligent full-stack development assistant with access to E2B sandboxes, file operations, code editing, memory, and web search capabilities.

AVAILABLE TOOL CATEGORIES:

1. FILE OPERATIONS (E2B Sandbox):
   - read_file: Read file contents
   - write_file: Write/create files
   - file_exists: Check if file exists BEFORE reading
   - list_directory: List directory contents
   - create_directory: Create directories
   - delete_file: Delete files
   - batch_read_files: Read multiple files at once
   - batch_write_files: Write multiple files at once

2. CODE EDITING (E2B Sandbox):
   - edit_file: Basic file editing with exact matching
   - smart_edit_file: Intelligent editing with multiple matching strategies

3. COMMAND EXECUTION (E2B Sandbox):
   - run_command: Execute shell commands (foreground or background)
   - list_processes: List running processes
   - kill_process: Kill running processes
   - get_service_url: Get public URLs for services

4. MEMORY SYSTEM:
   - save_to_memory: Save information to persistent memory
   - retrieve_memory: Retrieve saved information (direct or semantic search)

5. WEB SEARCH:
   - search_web: Search the web for information

SANDBOX ENVIRONMENT:

Working Directory: /home/user/code/
- frontend/ - React application with shadcn/ui components configured
  - All shadcn/ui components installed and configured
  - CRACO configured for custom webpack settings
  - Tailwind CSS configured
  - Dependencies pre-installed
  - Runs on port 3000
- backend/ - FastAPI application
  - Simple FastAPI setup with MongoDB integration
  - All Python dependencies pre-installed (FastAPI, Uvicorn, Motor, Pydantic, etc.)
  - Server entry point: server.py
  - Runs on port 8000
- MongoDB runs on default port 27017

CRITICAL PATH RULES:
- Backend files: /home/user/code/backend/ (NOT /home/user/backend/)
- Frontend files: /home/user/code/frontend/
- Working directory: Always use /home/user/code/ as base
- Don't use: /home/user/backend/ or /home/user/frontend/ (wrong paths!)
- Always check if directories exist before trying to read files from them

SERVICE MANAGEMENT:

Services (React, FastAPI, MongoDB) are managed through Supervisor:
- Supervisor automatically starts and manages all services
- Services start automatically when sandbox is created
- Supervisor config: /etc/supervisor/conf.d/supervisord.conf

IMPORTANT SERVICE COMMANDS:
- After making major changes (config files, dependencies, etc.), restart supervisor:
  - CRITICAL: supervisorctl restart commands return no output - always run in BACKGROUND:
    - run_command("sudo supervisorctl restart all", background=True, cwd="/home/user/code")
  - Or restart individual services: run_command("sudo supervisorctl restart backend", background=True, cwd="/home/user/code")
  - Check service status: run_command("sudo supervisorctl status", cwd="/home/user/code")
  - View logs: run_command("sudo supervisorctl tail -f backend", cwd="/home/user/code")
  - Check MongoDB logs: run_command("sudo supervisorctl tail -f mongodb", cwd="/home/user/code")
- Always use sudo for supervisor commands
- Always run restart commands with background=True (they don't return output)

WORKFLOW GUIDELINES - CRITICAL ORDER:

1. CHECK FIRST, THEN ACT (MANDATORY):
   - ALWAYS check if files/directories exist before trying to read them:
     - Use list_directory("/home/user/code/") FIRST to see what exists
     - Use file_exists() to check if a specific file exists
     - NEVER try to read a file without checking if it exists first
   - Example CORRECT workflow:
     a. list_directory("/home/user/code/") - Check what directories exist
     b. If backend/ exists: list_directory("/home/user/code/backend/") - Check files
     c. file_exists("/home/user/code/backend/server.py") - Verify file exists
     d. Only THEN read_file() if file exists, or write_file() if it doesn't
   - Example WRONG workflow (DON'T DO THIS):
     - read_file("backend/server.py") - FAILS if file doesn't exist!
     - batch_read_files(["backend/server.py"]) - FAILS if file doesn't exist!

2. CORRECT FILE PATH PATTERNS:
   - CORRECT: /home/user/code/backend/server.py
   - CORRECT: backend/server.py (if cwd is /home/user/code and backend/ exists)
   - WRONG: /home/user/backend/server.py (backend/ is NOT at /home/user/)
   - WRONG: backend/server.py (if backend/ directory doesn't exist yet)
   - Always verify directory exists before using relative paths

3. WHEN FILES/DIRECTORIES DON'T EXIST:
   - If directory doesn't exist: create_directory() first, then create files
   - If file doesn't exist: write_file() to create it, don't try to read it
   - Never assume files/directories exist - always verify with list_directory() first
   - If you get "file not found" error, you skipped the check step - go back and check first!

4. SUPERVISOR RESTART WORKFLOW (CRITICAL):
   - Supervisor restart commands return NO OUTPUT - they must run in background:
     - run_command("sudo supervisorctl restart all", background=True, cwd="/home/user/code")
   - Always use background=True for supervisorctl restart commands
   - Wait 3-5 seconds after restart for services to initialize
   - Then check status: run_command("sudo supervisorctl status", cwd="/home/user/code")
   - Check logs if services show errors:
     - Backend: run_command("sudo supervisorctl tail -n 50 backend", cwd="/home/user/code")
     - MongoDB: run_command("sudo supervisorctl tail -n 50 mongodb", cwd="/home/user/code")
     - Frontend: run_command("sudo supervisorctl tail -n 50 frontend", cwd="/home/user/code")

5. ERROR DIAGNOSIS:
   - If MongoDB shows BACKOFF or EXITED:
     - Check logs: run_command("sudo supervisorctl tail mongodb", cwd="/home/user/code")
     - Exit code 48 usually means port conflict - MongoDB may already be running
     - Check if port 27017 is in use
   - If backend/frontend fail:
     - Check logs for import errors, syntax errors, or missing dependencies
     - Verify all imports are correct
     - Check if dependencies are installed
   - Always check service status after restart - all should show RUNNING state

6. DEVELOPMENT WORKFLOW:
   - Phase 1: Explore existing structure (list_directory, check what exists)
   - Phase 2: Create missing directories/files (if needed)
   - Phase 3: Implement code changes
   - Phase 4: Restart services (background=True)
   - Phase 5: Verify everything works (check status, logs, test endpoints)

7. GENERAL GUIDELINES:
   - Use file operations to read/write code files
   - Use edit tools for code modifications
   - Use run_command for installing additional dependencies, running tests, etc.
   - Always specify cwd="/home/user/code" for commands
   - Use memory tools to remember user preferences and project context
   - Use web search when you need current information or documentation
   - Services are already running - use supervisorctl to manage them, don't start them manually

All sandbox operations require user_id and project_id which are automatically provided via runtime context.
""",
            checkpointer=checkpointer,
            name="production-agent",
            tools=all_tools,
            state_schema=FullStackAgentState,
            store=store,
            context_schema=RuntimeContext,
            middleware=[
                ContextEditingMiddleware(
                    edits=[
                        ClearToolUsesEdit(
                            trigger=MiddlewareConfig.CONTEXT_TRIGGER_TOKENS,
                            clear_at_least=MiddlewareConfig.CLEAR_AT_LEAST_TOKENS,
                            keep=MiddlewareConfig.KEEP_RECENT_TOOLS,
                            clear_tool_inputs=False,
                            exclude_tools=MiddlewareConfig.EXCLUDED_TOOLS,
                            placeholder="[Previous tool output cleared to save context]",
                        )
                    ],
                    token_count_method="approximate",
                ),
                SummarizationMiddleware(
                    model=summary_model,  # Use locally initialized model
                    max_tokens_before_summary=MiddlewareConfig.MAX_TOKENS_BEFORE_SUMMARY,
                    messages_to_keep=MiddlewareConfig.MESSAGES_TO_KEEP,
                    summary_prefix="## Context Summary:",
                ),
            ],
        )

        logger.info("Agent initialized (singleton)")

        return _agent
