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
from tools.memory_tools import (
    MemoryAgentState,
    MemoryContext,
    MEMORY_TOOLS,
)

load_dotenv()


class MiddlewareConfig:
    """Production middleware configuration"""

    CACHE_TTL = "5m"  # 5 minutes cache
    MIN_MESSAGES_TO_CACHE = 3  # Start caching after 3 messages

    # Context Editing (CRITICAL for performance)
    CONTEXT_TRIGGER_TOKENS = 200  # Anthropic's limit is 200k
    CLEAR_AT_LEAST_TOKENS = 100  # Reclaim at least 20k tokens
    KEEP_RECENT_TOOLS = 1  # Keep last 3 tool results
    EXCLUDED_TOOLS = [
        # "read_file",
        # "list_directory",
        # "get_file_info",
    ]

    # Summarization (MEDIUM priority)
    MAX_TOKENS_BEFORE_SUMMARY = 10000  # Trigger at 150k tokens
    MESSAGES_TO_KEEP = 1  # Keep last 3 messages

    # Metadata Extraction (Always enabled)
    REQUIRED_FIELDS = ["phase", "thinking", "next_steps"]

    # Dynamic Prompts (State-aware)
    ENABLE_PHASE_PROMPTS = True
    # summary_model = chat_model = init_chat_model(
    #     model="gpt-5-mini",
    #     model_provider="openai",
    #     streaming=True,
    #     temperature=0.5,
    #     timeout=10,
    #     max_tokens=2000,
    #     base_url="https://openrouter.ai/api/v1",
    #     api_key=os.getenv("OPENROUTER_API_KEY"),
    # )


load_dotenv()

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

        # Get checkpointer service (singleton pattern)
        checkpointer_service = await get_checkpointer_service()
        
        # Ensure checkpointer initialized
        if not checkpointer_service._initialized:
            await checkpointer_service.initialize()

        # Get checkpointer instance (shared across all requests)
        checkpointer = checkpointer_service.get_checkpointer()

        # Get store instance (shares same MongoDB client)
        store = checkpointer_service.get_store()
        logger.info("✅ Retrieved checkpointer + store from service")

        # Create model
        chat_model = init_chat_model(
            model="gpt-5-mini",
            model_provider="openai",
            streaming=True,
            temperature=0.5,
            timeout=10,
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
            timeout=10,
            max_tokens=2000,
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )

        # Create agent ONCE
        _agent = create_agent(
            model=chat_model,
            debug=True,
            system_prompt="""You are an intelligent assistant with long-term memory capabilities.

MEMORY SYSTEM:
You have access to a persistent memory store that saves information across sessions.

AVAILABLE TOOLS:
1. save_to_memory(key, content)
   - Save important information to long-term memory
   - Use descriptive keys like "user_preference", "project_details", "tech_stack"
   - Always save facts, preferences, and context the user wants remembered

2. retrieve_memory(query, retrieval_method, limit)
   - Retrieve saved information from memory store
   - Methods:
     • "direct" - Get specific memory by exact key
     • "semantic" - Search by meaning/similarity
   - Special: Use retrieval_method="direct" with limit=0 to list ALL memories

WHEN TO USE MEMORY:
- User explicitly asks you to remember something
- User shares important facts, preferences, or personal details
- User mentions projects, goals, or ongoing work
- User provides technical specifications or requirements

MEMORY WORKFLOW EXAMPLES:

Example 1 - Saving:
User: "Remember that I prefer Python and TypeScript"
You: save_to_memory(key="programming_languages", content="User prefers Python and TypeScript")

Example 2 - Direct retrieval:
User: "What languages do I prefer?"
You: retrieve_memory(query="programming_languages", retrieval_method="direct")

Example 3 - Semantic search:
User: "What do you know about my coding preferences?"
You: retrieve_memory(query="coding preferences", retrieval_method="semantic", limit=5)

Example 4 - List all:
User: "What have I told you so far?"
You: retrieve_memory(query="", retrieval_method="direct", limit=0)

BEST PRACTICES:
- Use clear, descriptive keys (snake_case preferred)
- Save complete, self-contained information
- Search semantically when you don't know the exact key
- List all memories periodically to stay aware of what's saved
""",
            checkpointer=checkpointer,  # ← Shared checkpointer!
            name="production-agent",
            tools=MEMORY_TOOLS,  # ← Add memory tools
            state_schema=MemoryAgentState,  # ← Include memory_keys in state
            store=store,  # ← Add memory store
            context_schema=MemoryContext,  # ← For tools
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

        logger.info("✅ Agent initialized (singleton)")

        return _agent