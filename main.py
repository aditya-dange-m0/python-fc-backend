from fastapi import FastAPI
import json
import os
import time
import logging
from datetime import datetime
from enum import Enum
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from agent.singleton_agent import get_agent
from checkpoint import get_checkpointer_service
from tools.memory_tools import MemoryContext

logger = logging.getLogger("api")

# Global agent reference
_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan handler for application lifecycle management.
    Initializes checkpointer on startup and closes on shutdown.
    """
    global _agent

    # ==================== STARTUP ====================
    logger.info("[APP] 🚀 Starting application...")

    try:
        # Initialize checkpointer service (creates MongoDB connection)
        checkpointer_service = await get_checkpointer_service()
        await checkpointer_service.initialize()
        logger.info("✅ Checkpointer initialized")

        # Create singleton agent (lazy initialization on first call is also fine)
        _agent = await get_agent()
        logger.info("✅ Agent created with checkpointer")

        logger.info("[APP] ✅ All services ready!")

    except Exception as e:
        logger.error(f"[APP] ❌ Startup failed: {e}", exc_info=True)
        raise

    yield

    # ==================== SHUTDOWN ====================
    logger.info("[APP] 🛑 Shutting down...")

    # Close checkpointer service
    checkpointer_service = await get_checkpointer_service()
    await checkpointer_service.close()
    logger.info("✅ Checkpointer connections closed")


app = FastAPI(
    title="FS_main API",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "FS_main API is running"}


def format_sse_event(event_type: str, data: dict) -> str:
    """Format Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# ========== REQUEST MODELS ==========


class ModelProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE_GENAI = "google_genai"
    OPENROUTER = "openrouter"


class MessageRequest(BaseModel):
    message: str
    session_id: str  # Required for conversation memory and context
    user_id: str = "default-user"  # User ID for memory namespace
    model: str = "x-ai/grok-4-fast"
    model_provider: ModelProvider = ModelProvider.OPENROUTER
    streaming: bool = True
    temperature: float = 0.5
    timeout: int = 10
    max_tokens: int = 1000


# ========== AGENT ENDPOINT ==========


@app.post("/invoke")
async def invoke_agent(request: MessageRequest):
    """
    Invoke agent with streaming response.
    Supports multiple model providers (OpenAI, Anthropic, Google, OpenRouter).
    """
    start = time.monotonic()

    try:
        start_time = datetime.now()
        first_response_time = None

        config = {
            "configurable": {
                "thread_id": request.session_id,  # For conversation persistence (checkpointer)
                "user_id": request.user_id,  # For memory namespace
                "session_id": request.session_id,  # For memory context
                "model": request.model,
                "model_provider": request.model_provider.value,
                "streaming": request.streaming,
                "temperature": request.temperature,
                "timeout": request.timeout,
                "max_tokens": request.max_tokens,
            }
        }

        # Handle OpenRouter specially
        if request.model_provider.value == "openrouter":
            config["configurable"]["model_provider"] = "openai"
            config["configurable"]["base_url"] = "https://openrouter.ai/api/v1"
            config["configurable"]["api_key"] = os.getenv("OPENROUTER_API_KEY")

        async def generate():
            nonlocal first_response_time
            try:
                usage_metadata = {}
                model_provider = None
                model_name = None

                # Get singleton agent
                agent = await get_agent()

                # Create memory context
                memory_context = MemoryContext(
                    user_id=request.user_id, session_id=request.session_id
                )

                async for stream_mode, chunk in agent.astream(
                    {
                        "messages": [HumanMessage(content=request.message)],
                        "memory_keys": [],  # Initialize memory_keys in state
                    },
                    config=config,
                    stream_mode=["updates", "messages"],
                    context=memory_context,  # Pass memory context to tools
                ):
                    # Track first response time
                    if first_response_time is None:
                        first_response_time = datetime.now()

                    # Handle state updates (node transitions)
                    if stream_mode == "updates":
                        for node_name, node_data in chunk.items():
                            if node_data is None:
                                continue

                            messages = node_data.get("messages", [])
                            if messages:
                                last_message = messages[-1]

                                # Check for tool calls
                                if (
                                    hasattr(last_message, "tool_calls")
                                    and last_message.tool_calls
                                ):
                                    for tool_call in last_message.tool_calls:
                                        yield format_sse_event(
                                            "tool_start",
                                            {
                                                "tool_name": tool_call.get("name"),
                                                "tool_id": tool_call.get("id"),
                                                "tool_args": tool_call.get("args", {}),
                                                "node": node_name,
                                            },
                                        )

                                # Check for tool results
                                if hasattr(last_message, "name") and last_message.name:
                                    output_preview = (
                                        str(last_message.content)[:200] + "..."
                                        if len(str(last_message.content)) > 200
                                        else str(last_message.content)
                                    )
                                    yield format_sse_event(
                                        "tool_complete",
                                        {
                                            "tool_name": last_message.name,
                                            "output_preview": output_preview,
                                            "node": node_name,
                                        },
                                    )

                    # Handle LLM token streaming
                    elif stream_mode == "messages":
                        message_chunk, metadata = chunk

                        if metadata is None:
                            continue
                        if model_provider is None and "ls_provider" in metadata:
                            model_provider = metadata["ls_provider"]
                        if model_name is None and "ls_model_name" in metadata:
                            model_name = metadata["ls_model_name"]

                        if (
                            hasattr(message_chunk, "usage_metadata")
                            and message_chunk.usage_metadata
                        ):
                            usage_metadata.update(message_chunk.usage_metadata)

                        node_name = metadata.get("langgraph_node", "unknown")

                        if hasattr(message_chunk, "content"):
                            content = message_chunk.content

                            if isinstance(content, str) and content:
                                yield format_sse_event(
                                    "agent_thinking",
                                    {
                                        "token": content,
                                        "node": node_name,
                                    },
                                )

                            elif isinstance(content, list) and content:
                                for block in content:
                                    if isinstance(block, dict):
                                        block_type = block.get("type")

                                        if block_type == "text":
                                            text = block.get("text", "")
                                            if text:
                                                yield format_sse_event(
                                                    "agent_thinking",
                                                    {
                                                        "token": text,
                                                        "node": node_name,
                                                    },
                                                )

                                        elif block_type == "tool_use":
                                            yield format_sse_event(
                                                "tool_calling",
                                                {
                                                    "tool_name": block.get("name"),
                                                    "tool_id": block.get("id"),
                                                    "node": node_name,
                                                },
                                            )

                # Calculate timing
                end_time = datetime.now()
                total_duration = (
                    end_time - start_time
                ).total_seconds() * 1000  # in milliseconds
                first_response_duration = (
                    (first_response_time - start_time).total_seconds() * 1000
                    if first_response_time
                    else None
                )

                # Send completion event
                yield format_sse_event(
                    "agent_complete",
                    {
                        "timestamp": end_time.isoformat(),
                        "usage_metadata": usage_metadata,
                        "model_provider": model_provider,
                        "model_name": model_name,
                        "timing": {
                            "total_duration_ms": round(total_duration, 2),
                            "first_response_ms": (
                                round(first_response_duration, 2)
                                if first_response_duration
                                else None
                            ),
                            "start_time": start_time.isoformat(),
                            "end_time": end_time.isoformat(),
                        },
                    },
                )
            except Exception as e:
                yield format_sse_event("error", {"message": str(e)})

        return StreamingResponse(generate(), media_type="text/event-stream")

    finally:
        duration = time.monotonic() - start
        logger.info(f"/invoke took {duration:.2f} seconds")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
