# LangChain Bug Summary - UnboundLocalError

## Quick Summary

**Location:** `langchain/agents/factory.py:1509`  
**Function:** `_fetch_last_ai_and_tool_messages()`  
**Issue:** Variable `last_ai_index` is type-annotated but not initialized, causing UnboundLocalError when no AIMessage exists

## The Bug Code

```python
# langchain/agents/factory.py:1497-1510
def _fetch_last_ai_and_tool_messages(messages: list[AnyMessage]) -> tuple[AIMessage, list[ToolMessage]]:
    last_ai_index: int          # ❌ Type annotation only - NOT initialized!
    last_ai_message: AIMessage  # ❌ Type annotation only - NOT initialized!
    
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            last_ai_index = i   # Only assigned if AIMessage found
            last_ai_message = cast("AIMessage", messages[i])
            break
    
    # ❌ BUG: Uses last_ai_index which may never have been assigned!
    tool_messages = [m for m in messages[last_ai_index + 1 :] if isinstance(m, ToolMessage)]
    return last_ai_message, tool_messages
```

## Why It Happens

1. **Context Editing Middleware** clears old messages to save tokens
2. **AIMessage gets cleared** but ToolMessages remain
3. **LangChain tries to find AIMessage** to match with ToolMessages
4. **No AIMessage found** → loop doesn't assign `last_ai_index`
5. **Line 1509 uses unassigned variable** → `UnboundLocalError`

## Current Handling

Your code already catches this error gracefully in `api/agent_api.py:402-417` ✅

## Recommended Fix

Adjust middleware to be less aggressive about clearing AIMessages:

```python
# In agent/singleton_agent.py
CONTEXT_TRIGGER_TOKENS = 80000  # Increase threshold
CLEAR_AT_LEAST_TOKENS = 30000   # Less aggressive clearing
KEEP_RECENT_TOOLS = 5           # Keep more tool contexts
```

This reduces the chance of clearing AIMessages while ToolMessages still exist.

