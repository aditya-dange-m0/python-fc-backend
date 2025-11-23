# Production Audit Report: sandbox_manager.py (Redis-Enabled Version)

## Executive Summary
This audit focuses on the **Redis-enabled version** (commented code, lines 509-1149) which will be used in production. This version adds Redis caching for sandbox persistence across restarts.

**Production Readiness:** ⚠️ **NOT READY** - Critical issues must be addressed.

---

## 🔴 CRITICAL ISSUES

### 1. **Memory Leak: Unbounded User Locks Dictionary**
**Location:** Lines 623-624, 917-923
**Issue:** The `_user_locks` dictionary grows indefinitely and is never cleaned up. Each unique (user_id, project_id) combination creates a new lock that persists forever.

**Impact:** Memory exhaustion over time, especially with many unique users/projects.

**Fix Required:**
```python
async def _cleanup_user_locks(self):
    """Remove locks for users with no active sandboxes"""
    async with self._user_locks_lock:
        active_keys = set(self._sandbox_pool.keys())
        keys_to_remove = [
            key for key in self._user_locks.keys() 
            if key not in active_keys
        ]
        for key in keys_to_remove:
            del self._user_locks[key]
```

### 2. **Race Condition in Singleton Initialization**
**Location:** Lines 603-610, 612-614
**Issue:** The `__new__` method creates instance without proper locking. `_instance_lock` is `asyncio.Lock()` which cannot be used in `__new__` (synchronous method).

**Impact:** Multiple initializations possible, resource leaks, thread-safety issues.

**Fix Required:**
```python
import threading

class MultiTenantSandboxManager:
    _instance: Optional["MultiTenantSandboxManager"] = None
    _instance_lock = threading.Lock()  # Use threading.Lock for __new__
    _instance_async_lock = asyncio.Lock()  # Use asyncio.Lock for async methods

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:  # Now works in __new__
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
```

### 3. **API Key Exposure Risk**
**Location:** Lines 540, 662-664, 856, 875, 970
**Issue:** API key is stored in config object and passed around. Could be logged or exposed in error messages.

**Impact:** Security breach if API key leaks through logs or exceptions.

**Fix Required:**
- Never log API key values (mask them)
- Use environment variables directly when possible
- Consider using a secrets manager in production
- Add `__repr__` methods that mask sensitive data

### 4. **No Input Validation**
**Location:** Lines 759-765, 1137-1140
**Issue:** `user_id` and `project_id` parameters are not validated. Could be empty strings, None, or malicious values that could cause Redis key injection.

**Impact:** 
- Potential Redis key injection attacks
- Resource exhaustion
- Crashes from invalid input
- Redis key namespace pollution

**Fix Required:**
```python
def _validate_user_input(self, user_id: str, project_id: str):
    """Validate user input"""
    if not user_id or not isinstance(user_id, str):
        raise ValueError("user_id must be a non-empty string")
    if not project_id or not isinstance(project_id, str):
        raise ValueError("project_id must be a non-empty string")
    if len(user_id) > 255 or len(project_id) > 255:
        raise ValueError("user_id and project_id must be <= 255 characters")
    # Prevent Redis key injection
    if ':' in user_id or ':' in project_id:
        # Actually, ':' is used in key format, so validate format
        if not user_id.replace('_', '').replace('-', '').isalnum():
            raise ValueError("user_id contains invalid characters")
    if not project_id.replace('_', '').replace('-', '').isalnum():
        raise ValueError("project_id contains invalid characters")
```

### 5. **Redis Key Injection Vulnerability**
**Location:** Lines 700-702
**Issue:** Redis keys are constructed as `f"sandbox:{user_id}:{project_id}"` without sanitization. Malicious user_id/project_id could manipulate keys.

**Impact:** 
- Access other users' sandbox IDs
- Key namespace pollution
- Cache poisoning

**Fix Required:** 
- Validate and sanitize user_id/project_id (see issue #4)
- Use Redis key prefix with proper escaping
- Consider using hash-based keys

### 6. **Sync Redis Calls in Async Context**
**Location:** Lines 674, 711, 737, 751, 989, 1029, 1046, 1109
**Issue:** Redis operations are synchronous but called from async code. This blocks the event loop.

**Impact:** 
- Performance degradation
- Event loop blocking
- Poor scalability

**Fix Required:** 
- Use `aioredis` or `redis.asyncio` for async Redis operations
- OR use `asyncio.to_thread()` to run sync Redis calls in thread pool
- OR accept blocking but document it clearly

**Current Code:**
```python
# Line 711 - BLOCKS EVENT LOOP
sandbox_id = self._redis.get(key)  # ← SYNC!

# Should be:
sandbox_id = await asyncio.to_thread(self._redis.get, key)
# OR use async Redis client
```

### 7. **Redis Connection Not Checked Before Use**
**Location:** Lines 706, 732, 746
**Issue:** Code checks `if not self._redis` but doesn't verify connection is still alive. Redis connection could have died.

**Impact:** 
- Silent failures
- Cache misses when Redis is down
- No automatic reconnection

**Fix Required:**
```python
def _is_redis_available(self) -> bool:
    """Check if Redis is available and connected"""
    if not self._redis:
        return False
    try:
        self._redis.ping()
        return True
    except Exception:
        return False
```

### 8. **Stats Dictionary Not Thread-Safe**
**Location:** Lines 636-644, 772, 714, 720, 985-986, 1049-1050
**Issue:** Stats dictionary is modified without locks in multiple places (e.g., line 772, 714, 720).

**Impact:** Race conditions, incorrect statistics, potential data corruption.

**Fix Required:** All stats modifications should be under lock or use atomic operations.

---

## 🟠 HIGH PRIORITY ISSUES

### 9. **Redis Value Type Mismatch**
**Location:** Lines 711, 737
**Issue:** Redis `get()` returns bytes or string depending on configuration. Code assumes string but doesn't handle bytes.

**Impact:** Type errors, crashes when Redis returns bytes.

**Fix Required:**
```python
sandbox_id = self._redis.get(key)
if sandbox_id:
    # Handle both bytes and string
    if isinstance(sandbox_id, bytes):
        sandbox_id = sandbox_id.decode('utf-8')
```

**Note:** `redis_client.py` uses `decode_responses=True` (line 73), so this should be safe, but defensive coding is better.

### 10. **No Redis TTL Synchronization**
**Location:** Lines 547-548, 993
**Issue:** Redis TTL is set to `max_sandbox_age` (900s), but cleanup loop checks both `idle_timeout` (500s) and `max_sandbox_age` (900s). There's a mismatch - Redis TTL doesn't match cleanup logic.

**Impact:** 
- Sandboxes removed from memory but still in Redis
- Or Redis expires before cleanup removes from memory
- Inconsistent state

**Fix Required:**
```python
# Set Redis TTL to match the longer of the two timeouts
redis_ttl = max(self._config.idle_timeout, self._config.max_sandbox_age)
self._cache_sandbox_id(user_id, project_id, sandbox.sandbox_id, ttl=redis_ttl)
```

### 11. **Reconnection Logic Uses Private API**
**Location:** Lines 834-915
**Issue:** `_reconnect_to_sandbox` uses private/internal E2B API (`response._envd_access_token`, `Unset`, etc.). This is fragile and may break with library updates.

**Impact:** 
- Code breaks when E2B library updates
- Maintenance burden
- Unsupported API usage

**Fix Required:**
- Use public E2B API if available
- Add version pinning in requirements
- Document dependency on internal API
- Add tests to catch breaking changes

### 12. **No Error Recovery for Redis Failures**
**Location:** Lines 724-726, 741-742, 753-754
**Issue:** Redis errors are caught and logged, but there's no retry logic or circuit breaker. If Redis goes down temporarily, all operations fail silently.

**Impact:** 
- Poor user experience during Redis outages
- No automatic recovery
- No fallback strategy

**Fix Required:**
- Implement circuit breaker pattern
- Add retry logic with exponential backoff
- Consider fallback to memory-only mode

### 13. **Health Check Optimization Missing**
**Location:** Lines 783-808
**Issue:** Good optimization (skip health check if < 30s idle), but the 30-second threshold is hardcoded.

**Impact:** Not configurable for different use cases.

**Fix Required:** Make health check threshold configurable.

### 14. **Redis Cache Invalidation Race Condition**
**Location:** Lines 808, 824, 1029, 1046
**Issue:** When removing sandbox from memory pool, Redis cache is removed. But there's a race condition - another request could be reading from Redis while it's being deleted.

**Impact:** 
- Stale data in cache
- Inconsistent state between memory and Redis

**Fix Required:**
- Use Redis transactions (MULTI/EXEC) for atomic operations
- Or use distributed locks (Redis SETNX) for cache invalidation

### 15. **No Redis Connection Pool Monitoring**
**Location:** Throughout
**Issue:** No monitoring of Redis connection pool health, connection leaks, or pool exhaustion.

**Impact:** 
- Silent connection pool exhaustion
- No visibility into Redis health
- Difficult to diagnose issues

**Fix Required:**
- Add metrics for connection pool usage
- Monitor pool stats in `get_stats()`
- Alert on pool exhaustion

---

## 🟡 MEDIUM PRIORITY ISSUES

### 16. **Hardcoded Logging Configuration**
**Location:** Lines 584-589
**Issue:** Logging level and format are hardcoded. Should be configurable for production.

**Impact:** Cannot adjust logging verbosity in production without code changes.

### 17. **No Rate Limiting**
**Location:** Throughout
**Issue:** No rate limiting per user or globally. Users could spam sandbox creation requests.

**Impact:** Resource exhaustion, DoS vulnerability.

### 18. **Generic Exception Handling**
**Location:** Lines 804-808, 819-824, 1005-1014, 1075-1076
**Issue:** Catching generic `Exception` hides specific error types and makes debugging difficult.

**Impact:** Difficult to diagnose issues, potential silent failures.

### 19. **Magic Numbers**
**Location:** Lines 787 (30 seconds), 858 (5.0 timeout), 890 (5.0 timeout), 1021 (3.0 timeout), 1056 (30 seconds)
**Issue:** Hardcoded timeout values should be constants or configurable.

**Impact:** Harder to tune for production.

### 20. **Redis Key Namespace Not Isolated**
**Location:** Line 702
**Issue:** Redis keys use simple prefix `sandbox:`. In multi-tenant or shared Redis, could conflict with other services.

**Impact:** Key collisions, cache pollution.

**Fix Required:**
```python
def _get_redis_key(self, user_id: str, project_id: str) -> str:
    """Generate Redis key with namespace isolation"""
    namespace = os.getenv("REDIS_NAMESPACE", "sandbox_manager")
    return f"{namespace}:sandbox:{user_id}:{project_id}"
```

### 21. **No Redis Key Expiration Monitoring**
**Location:** Throughout
**Issue:** No monitoring of when Redis keys expire vs when they're manually deleted. Could lead to stale data.

**Impact:** Difficult to debug cache issues.

### 22. **Stats Race Condition in Cache Operations**
**Location:** Lines 714, 720
**Issue:** `redis_cache_hits` and `redis_cache_misses` are incremented without locks.

**Impact:** Incorrect cache hit rate statistics.

### 23. **No Distributed Locking for Sandbox Creation**
**Location:** Lines 776-832
**Issue:** User locks are per-process. In multi-instance deployment, multiple processes could create sandboxes for same user+project.

**Impact:** 
- Duplicate sandboxes
- Resource waste
- Inconsistent state

**Fix Required:**
- Use Redis distributed locks (SETNX with expiration)
- Or use Redis transactions

### 24. **Cleanup Loop Doesn't Clean Redis**
**Location:** Lines 1052-1076
**Issue:** Cleanup loop removes sandboxes from memory pool but doesn't explicitly clean Redis (relies on TTL). If TTL is wrong, stale data remains.

**Impact:** Stale sandbox IDs in Redis cache.

**Fix Required:**
```python
# In cleanup loop, also remove from Redis
for key in keys_to_remove:
    await self._remove_sandbox(key)
    # Explicitly remove from Redis (in addition to TTL)
    self._remove_cached_sandbox_id(key[0], key[1])
```

---

## 🟢 LOW PRIORITY ISSUES

### 25. **Missing Type Hints**
**Location:** Line 630 (`self._redis: Optional[any] = None`)
**Issue:** Type hint uses `any` instead of proper type.

**Fix:** Use `Optional[redis.Redis]` or `Optional['redis.Redis']`

### 26. **Inconsistent Error Messages**
**Location:** Throughout
**Issue:** Error messages vary in format and detail level.

### 27. **No Unit Tests**
**Issue:** No test file visible in codebase.

### 28. **Documentation Gaps**
**Location:** Some methods
**Issue:** Not all methods have comprehensive docstrings, especially Redis-related methods.

### 29. **Redis Client Import Path**
**Location:** Line 528
**Issue:** Import uses `from redis_client import get_redis, close_redis` - should verify this matches actual module structure.

---

## 🔍 REDIS CLIENT AUDIT (redis_client.py)

### Positive Aspects ✅
1. ✅ Connection pooling implemented
2. ✅ Thread-safe singleton pattern
3. ✅ Graceful degradation if Redis unavailable
4. ✅ Health check interval configured
5. ✅ Connection timeouts configured
6. ✅ Retry on timeout enabled

### Issues Found:

#### 1. **No Connection Retry Logic**
**Location:** Lines 97-107
**Issue:** If Redis connection fails during initialization, it never retries. Connection could be temporarily unavailable.

**Fix:** Add retry logic with exponential backoff.

#### 2. **No Connection Health Monitoring**
**Location:** Throughout
**Issue:** No periodic health checks or reconnection logic if connection dies after initialization.

**Fix:** Add background health check task.

#### 3. **Pool Statistics Access Private Attributes**
**Location:** Lines 131-132
**Issue:** Accesses `_available_connections` and `_in_use_connections` which are private attributes. Could break with library updates.

**Fix:** Use public API if available, or wrap in try/except.

#### 4. **No Configuration Validation**
**Location:** Line 67
**Issue:** `REDIS_URL` from environment is used without validation.

**Fix:** Validate URL format.

---

## 📋 PRIORITIZED RECOMMENDATIONS

### Immediate Actions (Before Production)
1. ✅ Fix memory leak in user locks (Issue #1)
2. ✅ Fix singleton initialization race condition (Issue #2)
3. ✅ Add input validation (Issue #4)
4. ✅ Fix Redis key injection vulnerability (Issue #5)
5. ✅ Fix stats thread-safety (Issue #8)
6. ✅ Add Redis connection health checks (Issue #7)

### Short-term (First Sprint)
1. Implement async Redis operations or thread pool (Issue #6)
2. Fix Redis TTL synchronization (Issue #10)
3. Add distributed locking for multi-instance (Issue #23)
4. Implement circuit breaker for Redis (Issue #12)
5. Add Redis namespace isolation (Issue #20)
6. Fix cleanup loop to clean Redis (Issue #24)

### Medium-term
1. Replace private API usage in reconnection (Issue #11)
2. Add rate limiting
3. Add structured logging
4. Add metrics/telemetry
5. Add unit tests

### Long-term
1. Add connection retry logic in Redis client
2. Add connection health monitoring
3. Implement proper distributed locking
4. Add comprehensive monitoring dashboard

---

## 🔒 Security Checklist

- [ ] API keys never logged
- [ ] Input validation on all user inputs
- [ ] Redis key injection prevented
- [ ] Redis namespace isolated
- [ ] Rate limiting implemented
- [ ] Resource limits enforced
- [ ] Error messages don't leak sensitive info
- [ ] Secrets management in place
- [ ] Audit logging for sensitive operations
- [ ] Distributed locking for multi-instance

---

## 📊 Code Quality Metrics

- **Lines of Code (Redis version):** ~640 (excluding comments)
- **Cyclomatic Complexity:** Medium-High (reconnection logic is complex)
- **Test Coverage:** 0% (needs tests)
- **Documentation:** Partial (needs improvement)
- **Redis Integration:** Good concept, needs production hardening

---

## ✅ Positive Aspects

1. ✅ Good use of async/await
2. ✅ Redis caching for persistence
3. ✅ Graceful fallback if Redis unavailable
4. ✅ Smart health check optimization (skip if < 30s)
5. ✅ Proper use of locks for concurrency
6. ✅ Resource limits implemented
7. ✅ Automatic cleanup mechanism
8. ✅ Retry logic with exponential backoff
9. ✅ Statistics tracking including cache metrics
10. ✅ Graceful shutdown support

---

## 🚨 CRITICAL PRODUCTION BLOCKERS

These **MUST** be fixed before production:

1. **Memory leak** (user locks)
2. **Race condition** (singleton init)
3. **Input validation** (security)
4. **Redis key injection** (security)
5. **Sync Redis in async** (performance)
6. **Stats thread-safety** (data integrity)

---

## Summary

**Total Issues Found:** 29
- Critical: 8
- High: 7
- Medium: 8
- Low: 6

**Production Readiness:** ⚠️ **NOT READY** - 8 critical issues must be addressed.

**Estimated Fix Time:** 
- Critical issues: 3-5 days
- High priority: 1 week
- Medium priority: 2 weeks
- Low priority: Ongoing

**Risk Level:** 🔴 **HIGH** - Multiple security and stability issues present.

