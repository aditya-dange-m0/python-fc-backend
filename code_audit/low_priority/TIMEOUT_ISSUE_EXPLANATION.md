# Timeout Issue Explanation and Fix

## The Problem

The timeout error is still occurring because there are **TWO** timeout configurations that need to be aligned:

### 1. Model Initialization Timeout (in `agent/singleton_agent.py`)
- **Location:** Lines 96-115
- **Current:** `timeout=300` (5 minutes) ✅
- **Purpose:** Sets the timeout when the model is first initialized

### 2. Request Config Timeout (in `api/agent_api.py`)
- **Location:** Line 132
- **Previous:** `timeout: int = 10` ❌ **THIS WAS THE PROBLEM**
- **Fix Applied:** `timeout: int = 300` ✅
- **Purpose:** This timeout is passed to the agent via config and **OVERRIDES** the initialization timeout

## Why the Error Occurred

1. Model initialized with `timeout=300` in singleton_agent.py
2. Request comes in with default `timeout=10` from MessageRequest
3. This timeout (10 seconds) is passed to agent config: `"timeout": request.timeout`
4. The config timeout **overrides** the initialization timeout
5. Request times out after ~11 seconds → `httpx.ReadTimeout`

## The Fix

Changed the default timeout in `api/agent_api.py`:
```python
# Before:
timeout: int = 10

# After:
timeout: int = 300  # 5 minutes timeout for long-running agent operations
```

## Timeline from Logs

- **23:03:20** - Request started, HTTP 200 OK
- **23:03:31** - Timeout error after ~11 seconds
- **Error:** `httpx.ReadTimeout` - exactly matching the 10-second timeout

## Why 300 Seconds (5 minutes)?

- Agent operations can take time (tool calls, LLM reasoning, file operations)
- Streaming responses need time to process
- 5 minutes provides enough buffer for complex tasks
- Still reasonable to prevent hanging requests

## Verification

After this fix:
- Default timeout is now 300 seconds (5 minutes)
- Matches the model initialization timeout
- Long-running agent operations should complete successfully

