# multi_tenant_sandbox_manager.py - WITH REDIS CACHING
# sandbox_manager.py - FIXED VERSION

import asyncio
import logging
import time
import threading
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

from e2b import AsyncSandbox
from e2b.exceptions import (
    SandboxException,
    AuthenticationException,
    RateLimitException,
    TimeoutException,
)

from redis_client import get_redis, close_redis


@dataclass
class SandboxConfig:
    """Configuration for sandbox creation"""

    template: Optional[str] = "next-fast-mongo-pre-v2"
    timeout: int = 500
    auto_pause: bool = True
    allow_internet_access: bool = True
    secure: bool = True
    api_key: Optional[str] = None

    # Pool limits
    max_sandboxes_per_user: int = 2
    max_total_sandboxes: int = 100

    # Cleanup settings (matches Redis TTL!)
    idle_timeout: int = 500  # 500 seconds
    max_sandbox_age: int = 900  # 900 seconds (15 min)

    # Retry configuration
    max_retries: int = 2
    retry_delay: float = 1.0

    # Redis caching
    enable_redis: bool = True


@dataclass
class SandboxInfo:
    """Information about a sandbox instance"""

    sandbox: AsyncSandbox
    sandbox_id: str
    user_id: str
    project_id: str
    created_at: float
    last_activity: float
    request_count: int = 0

    def is_idle(self, timeout: int) -> bool:
        """Check if sandbox has been idle too long"""
        return (time.time() - self.last_activity) > timeout

    def is_expired(self, max_age: int) -> bool:
        """Check if sandbox has exceeded max age"""
        return (time.time() - self.created_at) > max_age

    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = time.time()
        self.request_count += 1


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    )
    return logging.getLogger("MultiTenantSandboxManager")


def mask_api_key(api_key: Optional[str]) -> str:
    """Mask API key for safe logging (shows only first 4 and last 4 chars)"""
    if not api_key:
        return "None"
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


class MultiTenantSandboxManager:
    """
    Production-grade multi-tenant sandbox manager with Redis caching.

    Features:
    - Redis caching of sandbox IDs (TTL = sandbox uptime)
    - Each (user_id, project_id) gets isolated sandbox
    - Automatic cleanup and resource limits
    - Graceful fallback if Redis unavailable
    """

    _instance: Optional["MultiTenantSandboxManager"] = None
    _instance_lock = threading.Lock()  # Use threading.Lock for __new__ (synchronous)
    _instance_async_lock = asyncio.Lock()  # Use asyncio.Lock for async methods

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:  # Thread-safe singleton initialization
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.logger = setup_logging()

        # Sandbox pool: key = (user_id, project_id), value = SandboxInfo
        self._sandbox_pool: Dict[Tuple[str, str], SandboxInfo] = {}
        self._pool_lock = asyncio.Lock()

        # Per-user sandbox creation locks
        self._user_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        self._user_locks_lock = asyncio.Lock()

        # Configuration
        self._config: Optional[SandboxConfig] = None

        # Redis client
        self._redis: Optional[Any] = None

        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None

        # Statistics (protected by stats_lock for thread-safety)
        self._stats = {
            "total_sandboxes_created": 0,
            "total_requests": 0,
            "active_sandboxes": 0,
            "cleaned_up_sandboxes": 0,
            "rejected_requests": 0,
            "redis_cache_hits": 0,
            "redis_cache_misses": 0,
        }
        self._stats_lock = asyncio.Lock()

        self._initialized = True
        self.logger.info("=" * 80)
        self.logger.info("MultiTenantSandboxManager initialized")
        self.logger.info("Each user+project gets isolated sandbox")
        self.logger.info("Redis caching enabled for persistence")
        self.logger.info("=" * 80)

    async def initialize(
        self, config: Optional[SandboxConfig] = None
    ) -> "MultiTenantSandboxManager":
        """Initialize the manager with configuration"""
        async with self._instance_async_lock:
            if config is None:
                config = SandboxConfig()

            if config.api_key is None:
                config.api_key = os.getenv("E2B_API_KEY")
                if not config.api_key:
                    raise ValueError("E2B_API_KEY not set")

            if config.template is None:
                config.template = os.getenv("E2B_TEMPLATE_ID")

            self._config = config

            # Initialize Redis (SYNC CALL - NO AWAIT!)
            if config.enable_redis:
                try:
                    self._redis = get_redis()  # ← SYNC!
                    if self._redis:
                        self.logger.info("✅ Redis caching enabled")
                    else:
                        self.logger.warning("⚠️ Redis unavailable - caching disabled")
                except Exception as e:
                    self.logger.warning(f"⚠️ Redis init failed: {e} - caching disabled")
                    self._redis = None

            self.logger.info(
                f"Configuration: template={config.template or 'default'}, "
                f"max_per_user={config.max_sandboxes_per_user}, "
                f"max_total={config.max_total_sandboxes}, "
                f"redis_enabled={self._redis is not None}, "
                f"api_key={mask_api_key(config.api_key)}"
            )

            # Start cleanup task
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())

            return self

    def _validate_userid_projectid(self, user_id: str, project_id: str):
        """
        Validate user_id and project_id with simple checks.

        Args:
            user_id: User identifier
            project_id: Project identifier

        Raises:
            ValueError: If validation fails
        """
        # Check if inputs are provided
        if not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not project_id:
            raise ValueError("project_id must be a non-empty string")

        # Check types
        if not isinstance(user_id, str):
            raise ValueError("user_id must be a string")
        if not isinstance(project_id, str):
            raise ValueError("project_id must be a string")

    # =========================================================================
    # REDIS CACHE OPERATIONS (ALL SYNC - NO ASYNC!)
    # =========================================================================

    def _is_redis_available(self) -> bool:
        """Check if Redis is available and connected"""
        if not self._redis:
            return False
        try:
            # Ping Redis to verify connection is alive
            self._redis.ping()
            return True
        except Exception:
            return False

    def _get_redis_key(self, user_id: str, project_id: str) -> str:
        """Generate Redis key for user+project"""
        return f"sandbox:{user_id}:{project_id}"

    async def _get_cached_sandbox_id(
        self, user_id: str, project_id: str
    ) -> Optional[str]:
        """Get sandbox ID from Redis cache (async wrapper with single retry)"""
        if not self._is_redis_available():
            return None

        key = self._get_redis_key(user_id, project_id)

        # Try with single retry on failure
        for attempt in range(2):
            try:
                # Run sync Redis call in thread pool to avoid blocking event loop
                sandbox_id = await asyncio.to_thread(self._redis.get, key)

                if sandbox_id:
                    # Thread-safe stats update
                    async with self._stats_lock:
                        self._stats["redis_cache_hits"] += 1
                    self.logger.debug(
                        f"[{user_id}/{project_id}] Redis cache HIT: {sandbox_id}"
                    )
                    return sandbox_id
                else:
                    # Thread-safe stats update
                    async with self._stats_lock:
                        self._stats["redis_cache_misses"] += 1
                    self.logger.debug(f"[{user_id}/{project_id}] Redis cache MISS")
                    return None

            except (ConnectionError, TimeoutError, OSError) as e:
                if attempt == 0:
                    # Retry once
                    await asyncio.sleep(0.1)
                    continue
                else:
                    self.logger.warning(f"Redis get error after retry: {e}")
                    return None
            except Exception as e:
                self.logger.error(f"Unexpected Redis get error: {e}")
                return None

        return None

    async def _cache_sandbox_id(
        self, user_id: str, project_id: str, sandbox_id: str, ttl: int
    ):
        """Cache sandbox ID in Redis with TTL (async wrapper with single retry)"""
        if not self._is_redis_available():
            return

        key = self._get_redis_key(user_id, project_id)

        # Try with single retry on failure
        for attempt in range(2):
            try:
                # Run sync Redis call in thread pool to avoid blocking event loop
                await asyncio.to_thread(self._redis.setex, key, ttl, sandbox_id)
                self.logger.debug(
                    f"[{user_id}/{project_id}] Cached in Redis: {sandbox_id} (TTL={ttl}s)"
                )
                return
            except (ConnectionError, TimeoutError, OSError) as e:
                if attempt == 0:
                    # Retry once
                    await asyncio.sleep(0.1)
                    continue
                else:
                    self.logger.warning(f"Redis set error after retry: {e}")
                    return
            except Exception as e:
                self.logger.error(f"Unexpected Redis set error: {e}")
                return

    async def _remove_cached_sandbox_id(self, user_id: str, project_id: str):
        """Remove sandbox ID from Redis cache (async wrapper with single retry)"""
        if not self._is_redis_available():
            return

        key = self._get_redis_key(user_id, project_id)

        # Try with single retry on failure
        for attempt in range(2):
            try:
                # Run sync Redis call in thread pool to avoid blocking event loop
                await asyncio.to_thread(self._redis.delete, key)
                self.logger.debug(f"[{user_id}/{project_id}] Removed from Redis cache")
                return
            except (ConnectionError, TimeoutError, OSError) as e:
                if attempt == 0:
                    # Retry once
                    await asyncio.sleep(0.1)
                    continue
                else:
                    self.logger.warning(f"Redis delete error after retry: {e}")
                    return
            except Exception as e:
                self.logger.error(f"Unexpected Redis delete error: {e}")
                return

    # =========================================================================
    # SANDBOX RETRIEVAL WITH REDIS
    # =========================================================================
    async def get_sandbox(
        self,
        user_id: str,
        project_id: str,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
    ) -> AsyncSandbox:
        """Get or create sandbox for specific user and project."""

        if not self._config:
            raise ValueError("Manager not initialized. Call initialize() first.")

        # Validate input
        self._validate_userid_projectid(user_id, project_id)

        key = (user_id, project_id)
        async with self._stats_lock:
            self._stats["total_requests"] += 1

        self.logger.info(f"[{user_id}/{project_id}] Sandbox request")

        user_lock = await self._get_user_lock(user_id, project_id)

        async with user_lock:
            # Step 1: Check memory pool
            if key in self._sandbox_pool:
                sandbox_info = self._sandbox_pool[key]

                # Smart health check: Only verify if sandbox has been idle
                idle_seconds = time.time() - sandbox_info.last_activity

                # Skip health check if recently used (< 30s)
                if idle_seconds < 30:
                    sandbox_info.update_activity()
                    self.logger.info(
                        f"[{user_id}/{project_id}] Memory pool HIT (fresh): "
                        f"{sandbox_info.sandbox_id}"
                    )
                    return sandbox_info.sandbox

                # Health check for idle sandboxes only
                try:
                    await self._verify_sandbox_health(sandbox_info.sandbox)
                    sandbox_info.update_activity()
                    self.logger.info(
                        f"[{user_id}/{project_id}] Memory pool HIT (verified): "
                        f"{sandbox_info.sandbox_id}"
                    )
                    return sandbox_info.sandbox
                except (TimeoutError, ConnectionError, SandboxException) as e:
                    self.logger.warning(
                        f"[{user_id}/{project_id}] Health check failed: {e}"
                    )
                    await self._remove_sandbox(key)
                except Exception as e:
                    self.logger.error(
                        f"[{user_id}/{project_id}] Unexpected health check error: {e}"
                    )
                    await self._remove_sandbox(key)

            # Step 2: Check Redis cache
            cached_sandbox_id = await self._get_cached_sandbox_id(user_id, project_id)

            if cached_sandbox_id:
                try:
                    sandbox = await self._reconnect_to_sandbox(
                        cached_sandbox_id, user_id, project_id
                    )
                    return sandbox
                except (
                    TimeoutError,
                    ConnectionError,
                    SandboxException,
                    AuthenticationException,
                ) as e:
                    self.logger.warning(
                        f"[{user_id}/{project_id}] Reconnect failed for "
                        f"{cached_sandbox_id}: {e}"
                    )
                    await self._remove_cached_sandbox_id(user_id, project_id)
                except Exception as e:
                    self.logger.error(
                        f"[{user_id}/{project_id}] Unexpected reconnect error for "
                        f"{cached_sandbox_id}: {e}"
                    )
                    await self._remove_cached_sandbox_id(user_id, project_id)

            # Step 3: Create new sandbox
            await self._enforce_resource_limits(user_id, project_id)
            sandbox = await self._create_sandbox_for_user(
                user_id, project_id, metadata, envs
            )

            return sandbox

    async def _reconnect_to_sandbox(
        self, sandbox_id: str, user_id: str, project_id: str
    ) -> AsyncSandbox:
        """
        Reconnect to an existing sandbox using the public AsyncSandbox.connect() API.
        This method automatically resumes paused sandboxes and is the recommended approach.
        """
        key = (user_id, project_id)

        self.logger.info(f"[{user_id}/{project_id}] Reconnecting: {sandbox_id}")

        try:
            # Use public API to connect to existing sandbox
            # This automatically resumes paused sandboxes and handles connection setup
            sandbox = await asyncio.wait_for(
                AsyncSandbox.connect(
                    sandbox_id,
                    api_key=self._config.api_key,
                ),
                timeout=5.0,
            )

            # Verify it's alive
            try:
                await asyncio.wait_for(
                    self._verify_sandbox_health(sandbox), timeout=5.0
                )
            except asyncio.TimeoutError:
                raise Exception("Sandbox not responding")

            # Add to pool
            sandbox_info = SandboxInfo(
                sandbox=sandbox,
                sandbox_id=sandbox_id,
                user_id=user_id,
                project_id=project_id,
                created_at=time.time(),
                last_activity=time.time(),
            )

            async with self._pool_lock:
                self._sandbox_pool[key] = sandbox_info
                async with self._stats_lock:
                    self._stats["active_sandboxes"] = len(self._sandbox_pool)

            self.logger.info(
                f"[{user_id}/{project_id}] ✅ Reconnected using public API"
            )

            return sandbox

        except (
            TimeoutError,
            ConnectionError,
            SandboxException,
            AuthenticationException,
        ) as e:
            self.logger.warning(f"[{user_id}/{project_id}] Reconnect failed: {e}")
            raise
        except Exception as e:
            self.logger.error(
                f"[{user_id}/{project_id}] Unexpected reconnect error: {e}"
            )
            raise

    async def _get_user_lock(self, user_id: str, project_id: str) -> asyncio.Lock:
        """Get or create lock for specific user+project"""
        key = (user_id, project_id)
        async with self._user_locks_lock:
            if key not in self._user_locks:
                self._user_locks[key] = asyncio.Lock()
            return self._user_locks[key]

    async def _cleanup_user_locks(self):
        """Remove locks for users with no active sandboxes (prevents memory leak)"""
        async with self._user_locks_lock:
            active_keys = set(self._sandbox_pool.keys())
            keys_to_remove = [
                key for key in self._user_locks.keys() if key not in active_keys
            ]
            for key in keys_to_remove:
                del self._user_locks[key]
            if keys_to_remove:
                self.logger.debug(f"Cleaned up {len(keys_to_remove)} unused user locks")

    async def _enforce_resource_limits(self, user_id: str, project_id: str):
        """Enforce resource limits before creating new sandbox"""
        if len(self._sandbox_pool) >= self._config.max_total_sandboxes:
            async with self._stats_lock:
                self._stats["rejected_requests"] += 1
            raise RuntimeError(
                f"Maximum total sandboxes ({self._config.max_total_sandboxes}) reached"
            )

        user_sandboxes = [key for key in self._sandbox_pool.keys() if key[0] == user_id]

        if len(user_sandboxes) >= self._config.max_sandboxes_per_user:
            async with self._stats_lock:
                self._stats["rejected_requests"] += 1
            raise RuntimeError(
                f"User {user_id} reached max sandboxes "
                f"({self._config.max_sandboxes_per_user})"
            )

    async def _create_sandbox_for_user(
        self,
        user_id: str,
        project_id: str,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
    ) -> AsyncSandbox:
        """Create new sandbox and cache in Redis"""
        key = (user_id, project_id)

        self.logger.info(f"[{user_id}/{project_id}] Creating NEW sandbox...")

        full_metadata = {
            "user_id": user_id,
            "project_id": project_id,
            "created_at": datetime.now().isoformat(),
            **(metadata or {}),
        }

        for attempt in range(1, self._config.max_retries + 1):
            try:
                sandbox = await AsyncSandbox.create(
                    template=self._config.template,
                    timeout=self._config.timeout,
                    allow_internet_access=self._config.allow_internet_access,
                    metadata=full_metadata,
                    envs=envs or {},
                    secure=self._config.secure,
                    api_key=self._config.api_key,
                )

                # Store in memory pool
                sandbox_info = SandboxInfo(
                    sandbox=sandbox,
                    sandbox_id=sandbox.sandbox_id,
                    user_id=user_id,
                    project_id=project_id,
                    created_at=time.time(),
                    last_activity=time.time(),
                )

                async with self._pool_lock:
                    self._sandbox_pool[key] = sandbox_info
                    async with self._stats_lock:
                        self._stats["total_sandboxes_created"] += 1
                        self._stats["active_sandboxes"] = len(self._sandbox_pool)

                # Cache in Redis (async)
                # Set TTL to max of both timeouts to ensure Redis doesn't expire before cleanup
                redis_ttl = max(self._config.idle_timeout, self._config.max_sandbox_age)
                await self._cache_sandbox_id(
                    user_id,
                    project_id,
                    sandbox.sandbox_id,
                    ttl=redis_ttl,
                )

                self.logger.info("=" * 80)
                self.logger.info(f"[{user_id}/{project_id}] ✅ Sandbox created!")
                self.logger.info(f"   Sandbox ID: {sandbox.sandbox_id}")
                self.logger.info(f"   Redis TTL: {redis_ttl}s")
                self.logger.info(f"   Active: {len(self._sandbox_pool)}")
                self.logger.info("=" * 80)

                return sandbox

            except (
                SandboxException,
                AuthenticationException,
                RateLimitException,
                TimeoutException,
            ) as e:
                if attempt < self._config.max_retries:
                    delay = self._config.retry_delay * (2 ** (attempt - 1))
                    self.logger.warning(
                        f"[{user_id}/{project_id}] Attempt {attempt} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
            except Exception as e:
                self.logger.error(
                    f"[{user_id}/{project_id}] Unexpected error creating sandbox: {e}"
                )
                raise

        raise RuntimeError("Failed to create sandbox after retries")

    async def _verify_sandbox_health(self, sandbox: AsyncSandbox):
        """Quick health check"""
        try:
            await asyncio.wait_for(sandbox.files.list("."), timeout=3.0)
        except (TimeoutError, ConnectionError, SandboxException) as e:
            raise Exception(f"Health check failed: {e}")
        except Exception as e:
            raise Exception(f"Unexpected health check error: {e}")

    async def close_sandbox(self, user_id: str, project_id: str):
        """Close sandbox and remove from Redis"""
        # Validate input
        self._validate_userid_projectid(user_id, project_id)
        key = (user_id, project_id)
        await self._remove_sandbox(key)
        await self._remove_cached_sandbox_id(user_id, project_id)

    async def _remove_sandbox(self, key: Tuple[str, str]):
        """Remove and close sandbox from pool"""
        async with self._pool_lock:
            if key in self._sandbox_pool:
                sandbox_info = self._sandbox_pool[key]

                try:
                    await sandbox_info.sandbox.kill()
                    self.logger.info(
                        f"[{key[0]}/{key[1]}] Closed: {sandbox_info.sandbox_id}"
                    )
                except Exception as e:
                    self.logger.warning(f"Error closing sandbox: {e}")

                del self._sandbox_pool[key]
                async with self._stats_lock:
                    self._stats["active_sandboxes"] = len(self._sandbox_pool)
                    self._stats["cleaned_up_sandboxes"] += 1

        # Remove from Redis cache (async, outside pool_lock to avoid deadlock)
        await self._remove_cached_sandbox_id(key[0], key[1])

        # Cleanup unused user locks to prevent memory leak (outside pool_lock to avoid deadlock)
        # await self._cleanup_user_locks()

    async def _cleanup_loop(self):
        """Background cleanup task"""
        while True:
            try:
                await asyncio.sleep(30)

                if not self._config:
                    continue

                keys_to_remove = []

                async with self._pool_lock:
                    for key, sandbox_info in self._sandbox_pool.items():
                        if sandbox_info.is_idle(self._config.idle_timeout):
                            self.logger.info(f"[{key[0]}/{key[1]}] Cleanup: Idle")
                            keys_to_remove.append(key)
                        elif sandbox_info.is_expired(self._config.max_sandbox_age):
                            self.logger.info(f"[{key[0]}/{key[1]}] Cleanup: Expired")
                            keys_to_remove.append(key)

                for key in keys_to_remove:
                    await self._remove_sandbox(key)

                # Cleanup unused user locks periodically
                # await self._cleanup_user_locks()

            except Exception as e:
                self.logger.error(f"Cleanup error: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics"""
        return {
            **self._stats,
            "active_sandboxes": len(self._sandbox_pool),
            "redis_enabled": self._redis is not None,
            "cache_hit_rate": (
                self._stats["redis_cache_hits"]
                / (self._stats["redis_cache_hits"] + self._stats["redis_cache_misses"])
                if (self._stats["redis_cache_hits"] + self._stats["redis_cache_misses"])
                > 0
                else 0
            ),
        }

    async def shutdown(self):
        """Shutdown manager"""
        self.logger.info("Shutting down...")

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        keys = list(self._sandbox_pool.keys())
        for key in keys:
            await self._remove_sandbox(key)

        # Close Redis (SYNC CALL!)
        close_redis()  # ← NO AWAIT!

        stats = self.get_stats()
        self.logger.info("=" * 80)
        self.logger.info("FINAL STATS:")
        self.logger.info(f"  Total created: {stats['total_sandboxes_created']}")
        self.logger.info(f"  Redis cache hits: {stats['redis_cache_hits']}")
        self.logger.info(f"  Cache hit rate: {stats['cache_hit_rate']:.1%}")
        self.logger.info("=" * 80)


# Global instance
_multi_tenant_manager: Optional[MultiTenantSandboxManager] = None
_manager_lock = asyncio.Lock()


async def get_multi_tenant_manager() -> MultiTenantSandboxManager:
    """Get the global multi-tenant manager"""
    global _multi_tenant_manager

    async with _manager_lock:
        if _multi_tenant_manager is None:
            _multi_tenant_manager = MultiTenantSandboxManager()
            await _multi_tenant_manager.initialize()

        return _multi_tenant_manager


async def get_user_sandbox(user_id: str, project_id: str, **kwargs) -> AsyncSandbox:
    """Convenience function to get sandbox for user+project"""
    # Validation is done in manager.get_sandbox() method
    manager = await get_multi_tenant_manager()
    return await manager.get_sandbox(user_id, project_id, **kwargs)


async def cleanup_multi_tenant_manager():
    """Cleanup on shutdown"""
    global _multi_tenant_manager
    if _multi_tenant_manager:
        await _multi_tenant_manager.shutdown()
        _multi_tenant_manager = None
