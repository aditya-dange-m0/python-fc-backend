# Agent Prompt Updates - December 2024

## Summary
Updated the agent prompt in `agent/singleton_agent.py` to prevent common mistakes and improve workflow guidance.

---

## ✅ Changes Made

### 1. **Added "CHECK FIRST, THEN ACT" Workflow (MANDATORY)**
   - Agent must ALWAYS check if files/directories exist before trying to read them
   - Example CORRECT workflow:
     - Step 1: `list_directory("/home/user/code/")` - See what exists
     - Step 2: `list_directory("/home/user/code/backend/")` - Check directory contents
     - Step 3: `file_exists("/home/user/code/backend/server.py")` - Verify file
     - Step 4: Only THEN read_file() or write_file()
   - Example WRONG workflow (clearly marked as DON'T DO THIS):
     - ❌ `read_file("backend/server.py")` - FAILS if file doesn't exist!
     - ❌ `batch_read_files(["backend/server.py"])` - FAILS if doesn't exist!

### 2. **Supervisor Restart Commands - Background Execution**
   - **CRITICAL**: Supervisor restart commands return NO OUTPUT - must run in background
   - Always use: `run_command("sudo supervisorctl restart all", background=True, cwd="/home/user/code")`
   - Added explicit instruction: "Always run restart commands with background=True (they don't return output)"

### 3. **Correct File Path Patterns**
   - ✅ CORRECT: `/home/user/code/backend/server.py`
   - ✅ CORRECT: `backend/server.py` (if cwd is `/home/user/code` and backend/ exists)
   - ❌ WRONG: `/home/user/backend/server.py` (backend/ is NOT at /home/user/)
   - ❌ WRONG: `backend/server.py` (if backend/ directory doesn't exist yet)

### 4. **Enhanced Error Diagnosis**
   - MongoDB BACKOFF/EXITED: Check logs, exit code 48 = port conflict
   - Backend/Frontend errors: Check logs for import errors, syntax errors, missing dependencies
   - Always verify services show RUNNING status after restart

### 5. **Development Phases (FOLLOW THIS ORDER)**
   - Phase 1: EXPLORE - Always check structure first
   - Phase 2: CREATE/EXTEND - Create directories/files as needed
   - Phase 3: TEST & RESTART - Restart services and verify

---

## 🎯 Key Improvements

### Before:
- Agent would try to read files that don't exist → Errors
- Supervisor restart commands run in foreground → No output, confusion
- No clear workflow for checking files first

### After:
- ✅ Mandatory "CHECK FIRST" workflow prevents file-not-found errors
- ✅ Supervisor restart always uses background=True
- ✅ Clear examples of CORRECT vs WRONG workflows
- ✅ Better error diagnosis for MongoDB and service issues
- ✅ Explicit path pattern guidance

---

## 📝 Prompt Sections Added/Updated

1. **CRITICAL PATH RULES** - Correct paths, wrong paths clearly marked
2. **SERVICE MANAGEMENT** - Supervisor workflow with background execution
3. **WORKFLOW GUIDELINES - CRITICAL ORDER** - Step-by-step mandatory workflow
4. **ERROR DIAGNOSIS** - Specific guidance for MongoDB and service errors

---

## ✅ Expected Behavior After Update

The agent should now:
1. ✅ Always check directory structure first before reading files
2. ✅ Never try to read files that don't exist
3. ✅ Run supervisor restart commands in background (background=True)
4. ✅ Use correct file paths (/home/user/code/backend/ not /home/user/backend/)
5. ✅ Better diagnose MongoDB and service errors
6. ✅ Follow a clear exploration → creation → testing workflow

---

**Date:** 2024-12-30
**File Updated:** `agent/singleton_agent.py`

