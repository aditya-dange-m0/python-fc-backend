# Agent Singleton Issues Audit

## Issues Found in `agent/singleton_agent.py`

### 1. **CRITICAL: Duplicate `load_dotenv()` Calls**
   - **Location:** Lines 12, 26, and 66
   - **Issue:** `load_dotenv()` is called three times unnecessarily
   - **Impact:** Redundant initialization, no functional impact but poor practice
   - **Fix:** Keep only one call at the module level (line 26 or 66)

### 2. **Code Cleanup: Commented-Out Code Block**
   - **Location:** Lines 54-63
   - **Issue:** Large block of commented-out code for summary_model configuration
   - **Impact:** Code clutter, makes file harder to read
   - **Fix:** Remove commented code block entirely

### 3. **Emoji in Logger Message**
   - **Location:** Line 318
   - **Issue:** Logger message contains emoji: `"✅ Agent initialized (singleton)"`
   - **Impact:** Inconsistency with user's request to remove emojis
   - **Fix:** Remove emoji from logger message

### 4. **CRITICAL: Model Name Inconsistency**
   - **Location:** Lines 114 and 134
   - **Issue:** Hardcoded model name `"gpt-5-mini"` which may not exist or be correct
   - **Impact:** May cause model initialization failures
   - **Current Default in API:** `"x-ai/grok-4-fast"` (from `api/agent_api.py` line 128)
   - **Fix:** Use environment variable or match API default, or make configurable

### 5. **CRITICAL: Timeout Too Short (Causing ReadTimeout Errors)**
   - **Location:** Lines 118 and 138
   - **Issue:** `timeout=10` seconds is too short, causing `httpx.ReadTimeout` errors
   - **Evidence:** Terminal logs show timeout errors after 1 minute of processing
   - **Impact:** Agent requests fail due to timeout during long operations
   - **Fix:** Increase timeout to 120-300 seconds (2-5 minutes) for agent operations

### 6. **Code Cleanup: Empty EXCLUDED_TOOLS List**
   - **Location:** Lines 39-43
   - **Issue:** All tools in EXCLUDED_TOOLS are commented out, leaving an empty list
   - **Impact:** Minor - unused code, could be cleaner
   - **Fix:** Either remove the list entirely or uncomment tools if needed

### 7. **Missing base_url for Chat Model**
   - **Location:** Line 113-130
   - **Issue:** Chat model initialization doesn't include `base_url` and `api_key` like summary_model does
   - **Impact:** May not work correctly if using OpenRouter or custom endpoints
   - **Fix:** Add `base_url` and `api_key` configuration to match summary_model pattern

## Summary

**Critical Issues:** 3 (Model name, Timeout, Missing base_url)
**Code Quality Issues:** 3 (Duplicate load_dotenv, Commented code, Empty list)
**Minor Issues:** 1 (Emoji in logger)

## Recommended Fixes Priority

1. **HIGH PRIORITY:** Fix timeout values (causing actual errors)
2. **HIGH PRIORITY:** Fix model configuration (consistency and correctness)
3. **MEDIUM PRIORITY:** Remove duplicate load_dotenv calls
4. **LOW PRIORITY:** Clean up commented code and emoji

