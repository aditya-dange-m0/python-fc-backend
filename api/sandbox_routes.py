"""
E2B Sandbox Session Management API Routes

This module provides API endpoints for managing E2B sandbox sessions:
- Create/Get sandbox session for user_id and project_id
- Pause sandbox session and update expiration time
- Resume sandbox session
- Get public URLs for ports (3000, 8000)
"""

import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sandbox_manager import get_multi_tenant_manager, get_user_sandbox
from e2b import AsyncSandbox
from e2b.exceptions import (
    SandboxException,
    AuthenticationException,
    NotFoundException,
    TimeoutException,
)

logger = logging.getLogger("api.sandbox")

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class CreateSandboxSessionRequest(BaseModel):
    """Request model for creating a sandbox session"""

    user_id: str = Field(..., description="User ID")
    project_id: str = Field(..., description="Project ID (session_id == project_id)")


class CreateSandboxSessionResponse(BaseModel):
    """Response model for creating a sandbox session"""

    success: bool
    sandbox_id: str
    user_id: str
    project_id: str
    frontend_url: Optional[str] = None  # Port 3000
    backend_url: Optional[str] = None  # Port 8000
    message: str


class PauseSandboxSessionRequest(BaseModel):
    """Request model for pausing a sandbox session"""

    user_id: Optional[str] = Field(None, description="User ID")
    project_id: Optional[str] = Field(None, description="Project ID")
    sandbox_id: Optional[str] = Field(
        None, description="Sandbox ID (if provided, takes priority)"
    )
    timeout: Optional[int] = Field(
        None, description="New timeout in seconds (updates expiration)"
    )


class PauseSandboxSessionResponse(BaseModel):
    """Response model for pausing a sandbox session"""

    success: bool
    sandbox_id: str
    paused: bool
    timeout: Optional[int] = None
    message: str


class ResumeSandboxSessionRequest(BaseModel):
    """Request model for resuming a sandbox session"""

    user_id: Optional[str] = Field(None, description="User ID")
    project_id: Optional[str] = Field(None, description="Project ID")
    sandbox_id: Optional[str] = Field(
        None, description="Sandbox ID (if provided, takes priority)"
    )


class ResumeSandboxSessionResponse(BaseModel):
    """Response model for resuming a sandbox session"""

    success: bool
    sandbox_id: str
    resumed: bool
    message: str


class PublicURLRequest(BaseModel):
    """Request model for getting public URL"""

    user_id: str = Field(..., description="User ID")
    project_id: str = Field(..., description="Project ID")
    port: int = Field(..., description="Port number (3000 or 8000)")


class PublicURLResponse(BaseModel):
    """Response model for public URL"""

    success: bool
    sandbox_id: str
    port: int
    public_url: str
    message: str


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


async def _get_sandbox_id_from_redis(user_id: str, project_id: str) -> Optional[str]:
    """Get sandbox ID from Redis cache"""
    try:
        manager = await get_multi_tenant_manager()
        # Access the private method via the manager instance
        sandbox_id = await manager._get_cached_sandbox_id(user_id, project_id)
        if sandbox_id:
            # Handle bytes decoding if needed
            if isinstance(sandbox_id, bytes):
                return sandbox_id.decode("utf-8")
            return str(sandbox_id)
        return None
    except Exception as e:
        logger.warning(f"Failed to get sandbox_id from Redis: {e}")
        return None


async def _get_sandbox_by_id_or_redis(
    sandbox_id: Optional[str],
    user_id: Optional[str],
    project_id: Optional[str],
) -> tuple[AsyncSandbox, str]:
    """
    Get sandbox instance either by sandbox_id or by looking up from Redis using user_id/project_id.

    Returns:
        tuple: (sandbox_instance, sandbox_id)
    """
    manager = await get_multi_tenant_manager()

    if sandbox_id:
        # Use provided sandbox_id
        try:
            sandbox = await AsyncSandbox.connect(
                sandbox_id,
                api_key=manager._config.api_key,
            )
            return sandbox, sandbox_id
        except Exception as e:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to connect to sandbox {sandbox_id}: {str(e)}",
            )

    # Need user_id and project_id to look up from Redis
    if not user_id or not project_id:
        raise HTTPException(
            status_code=400,
            detail="Either sandbox_id or both user_id and project_id must be provided",
        )

    # Try to get from Redis
    cached_sandbox_id = await _get_sandbox_id_from_redis(user_id, project_id)

    if cached_sandbox_id:
        try:
            sandbox = await AsyncSandbox.connect(
                cached_sandbox_id,
                api_key=manager._config.api_key,
            )
            return sandbox, cached_sandbox_id
        except Exception as e:
            # If connection fails, try to get/create from manager
            logger.warning(
                f"Failed to connect to cached sandbox {cached_sandbox_id}: {e}, trying to get/create new one"
            )
            try:
                sandbox = await get_user_sandbox(user_id, project_id)
                return sandbox, sandbox.sandbox_id
            except Exception as e2:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to get sandbox for user {user_id}, project {project_id}: {str(e2)}",
                )
    else:
        # Try to get/create from manager
        try:
            sandbox = await get_user_sandbox(user_id, project_id)
            return sandbox, sandbox.sandbox_id
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get sandbox for user {user_id}, project {project_id}: {str(e)}",
            )


# =============================================================================
# API ENDPOINTS
# =============================================================================


@router.post("/create", response_model=CreateSandboxSessionResponse)
async def create_session(request: CreateSandboxSessionRequest):
    """
    Create or get existing sandbox session for user_id and project_id.

    This endpoint will:
    - Create a new sandbox if one doesn't exist
    - Return existing sandbox if already created
    - Cache sandbox_id in Redis for persistence
    - Return public URLs for ports 3000 (frontend) and 8000 (backend)
    """
    try:
        logger.info(
            f"Creating/getting sandbox session for user={request.user_id}, project={request.project_id}"
        )

        # Get or create sandbox (this handles Redis caching internally)
        sandbox = await get_user_sandbox(request.user_id, request.project_id)

        # Small delay to ensure sandbox is fully initialized
        await asyncio.sleep(0.5)

        # Get public URLs for ports 3000 and 8000
        # get_host() returns the preview URL format: {port}-{sandbox_id}.e2b.app
        try:
            frontend_url = f"https://{sandbox.get_host(3000)}"
            logger.info(f"Frontend URL (port 3000): {frontend_url}")
        except Exception as e:
            logger.warning(f"Failed to get frontend URL (port 3000): {e}")
            frontend_url = None

        try:
            backend_url = f"https://{sandbox.get_host(8000)}"
            logger.info(f"Backend URL (port 8000): {backend_url}")
        except Exception as e:
            logger.warning(f"Failed to get backend URL (port 8000): {e}")
            backend_url = None

        return JSONResponse(
            status_code=200,
            content=CreateSandboxSessionResponse(
                success=True,
                sandbox_id=sandbox.sandbox_id,
                user_id=request.user_id,
                project_id=request.project_id,
                frontend_url=frontend_url,
                backend_url=backend_url,
                message=f"Sandbox session created/retrieved successfully",
            ).model_dump(),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to create session: {str(e)}"
        )


@router.post("/pause", response_model=PauseSandboxSessionResponse)
async def pause_session(request: PauseSandboxSessionRequest):
    """
    Pause sandbox session and optionally update expiration time.

    This endpoint will:
    - Pause the sandbox (saves state)
    - Optionally update the timeout/expiration time
    - Works with sandbox_id or user_id/project_id lookup
    """
    try:
        # Get sandbox instance
        sandbox, sandbox_id = await _get_sandbox_by_id_or_redis(
            request.sandbox_id, request.user_id, request.project_id
        )

        logger.info(f"Pausing sandbox {sandbox_id}")

        # Update timeout if provided
        if request.timeout is not None:
            if request.timeout <= 0:
                raise HTTPException(
                    status_code=400, detail="Timeout must be a positive number"
                )

            try:
                await sandbox.set_timeout(request.timeout)
                logger.info(
                    f"Updated timeout to {request.timeout}s for sandbox {sandbox_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to update timeout: {e}")
                # Continue with pause even if timeout update fails

        # Pause the sandbox
        try:
            # Use beta_pause() method (async)
            await sandbox.beta_pause()
            logger.info(f"Sandbox {sandbox_id} paused successfully")
        except AttributeError:
            # Fallback if beta_pause doesn't exist
            logger.warning(
                "beta_pause() method not available, attempting alternative pause method"
            )
            # Some E2B SDK versions might have different method names
            if hasattr(sandbox, "pause"):
                await sandbox.pause()
            else:
                raise HTTPException(
                    status_code=501,
                    detail="Pause functionality not available in this E2B SDK version",
                )

        return JSONResponse(
            status_code=200,
            content=PauseSandboxSessionResponse(
                success=True,
                sandbox_id=sandbox_id,
                paused=True,
                timeout=request.timeout,
                message=f"Sandbox {sandbox_id} paused successfully",
            ).model_dump(),
        )

    except HTTPException:
        raise
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=f"Sandbox not found: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to pause session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to pause session: {str(e)}"
        )


@router.post("/resume", response_model=ResumeSandboxSessionResponse)
async def resume_session(request: ResumeSandboxSessionRequest):
    """
    Resume sandbox session.

    This endpoint will:
    - Resume a paused sandbox
    - Automatically reconnect if needed
    - Works with sandbox_id or user_id/project_id lookup from Redis

    If sandbox_id is provided, it will be used directly.
    If not provided, it will be looked up from Redis using user_id/project_id.
    """
    try:
        manager = await get_multi_tenant_manager()

        # Determine sandbox_id
        sandbox_id = request.sandbox_id

        if not sandbox_id:
            # Look up from Redis using user_id/project_id
            if not request.user_id or not request.project_id:
                raise HTTPException(
                    status_code=400,
                    detail="Either sandbox_id or both user_id and project_id must be provided",
                )

            cached_sandbox_id = await _get_sandbox_id_from_redis(
                request.user_id, request.project_id
            )
            if not cached_sandbox_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"Sandbox not found in cache for user {request.user_id}, project {request.project_id}",
                )
            sandbox_id = cached_sandbox_id

        logger.info(f"Resuming sandbox {sandbox_id}")

        # Connect/resume sandbox (connect automatically resumes paused sandboxes)
        try:
            sandbox = await AsyncSandbox.connect(
                sandbox_id,
                api_key=manager._config.api_key,
            )
            logger.info(f"Sandbox {sandbox_id} resumed successfully")

        except NotFoundException as e:
            raise HTTPException(
                status_code=404, detail=f"Sandbox {sandbox_id} not found: {str(e)}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to resume sandbox {sandbox_id}: {str(e)}",
            )

        return JSONResponse(
            status_code=200,
            content=ResumeSandboxSessionResponse(
                success=True,
                sandbox_id=sandbox_id,
                resumed=True,
                message=f"Sandbox {sandbox_id} resumed successfully",
            ).model_dump(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resume session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to resume session: {str(e)}"
        )


@router.get("/public-url", response_model=PublicURLResponse)
async def get_public_url(
    user_id: str = Query(..., description="User ID"),
    project_id: str = Query(..., description="Project ID"),
    port: int = Query(..., description="Port number (3000 or 8000)", ge=1, le=65535),
):
    """
    Get public URL for a specific port (3000 or 8000) on the sandbox.

    This endpoint will:
    - Get or reconnect to sandbox for user_id/project_id
    - Use sandbox.connect() to reconnect if necessary
    - Return public URL for the specified port

    Supported ports: 3000 (frontend), 8000 (backend)
    """
    try:
        # Validate port
        if port not in [3000, 8000]:
            raise HTTPException(
                status_code=400,
                detail=f"Port {port} not supported. Only ports 3000 and 8000 are supported.",
            )

        logger.info(
            f"Getting public URL for port {port}, user={user_id}, project={project_id}"
        )

        # Get sandbox (this will reconnect if needed)
        sandbox = await get_user_sandbox(user_id, project_id)

        # Get public host for the port
        try:
            host = sandbox.get_host(port)
            public_url = f"https://{host}"

            logger.info(f"Public URL for port {port}: {public_url}")

            return JSONResponse(
                status_code=200,
                content=PublicURLResponse(
                    success=True,
                    sandbox_id=sandbox.sandbox_id,
                    port=port,
                    public_url=public_url,
                    message=f"Public URL retrieved successfully for port {port}",
                ).model_dump(),
            )

        except Exception as e:
            logger.error(f"Failed to get public URL for port {port}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get public URL for port {port}: {str(e)}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get public URL: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get public URL: {str(e)}"
        )


@router.get("/public-urls")
async def get_all_public_urls(
    user_id: str = Query(..., description="User ID"),
    project_id: str = Query(..., description="Project ID"),
):
    """
    Get public URLs for both ports 3000 and 8000.

    Returns a combined response with URLs for both frontend (3000) and backend (8000) ports.
    """
    try:
        logger.info(f"Getting all public URLs for user={user_id}, project={project_id}")

        # Get sandbox
        sandbox = await get_user_sandbox(user_id, project_id)

        urls = {}
        errors = {}

        # Get URL for port 3000
        try:
            host_3000 = sandbox.get_host(3000)
            urls["port_3000"] = f"https://{host_3000}"
        except Exception as e:
            errors["port_3000"] = str(e)

        # Get URL for port 8000
        try:
            host_8000 = sandbox.get_host(8000)
            urls["port_8000"] = f"https://{host_8000}"
        except Exception as e:
            errors["port_8000"] = str(e)

        return JSONResponse(
            status_code=200,
            content={
                "success": len(errors) == 0,
                "sandbox_id": sandbox.sandbox_id,
                "urls": urls,
                "errors": errors if errors else None,
                "message": (
                    "Public URLs retrieved successfully"
                    if not errors
                    else "Some URLs could not be retrieved"
                ),
            },
        )

    except Exception as e:
        logger.error(f"Failed to get public URLs: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get public URLs: {str(e)}"
        )


@router.get("/status")
async def get_sandbox_status(
    user_id: str = Query(..., description="User ID"),
    project_id: str = Query(..., description="Project ID"),
    sandbox_id: Optional[str] = Query(None, description="Sandbox ID (optional)"),
):
    """
    Get status of sandbox session.

    Returns information about the sandbox including:
    - Sandbox ID
    - Connection status
    - Redis cache status
    """
    try:
        manager = await get_multi_tenant_manager()

        # Determine sandbox_id
        cached_sandbox_id = None
        if not sandbox_id:
            cached_sandbox_id = await _get_sandbox_id_from_redis(user_id, project_id)
            sandbox_id = cached_sandbox_id

        if not sandbox_id:
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "exists": False,
                    "message": "No sandbox found for this user/project",
                },
            )

        # Try to connect to verify status
        try:
            sandbox = await AsyncSandbox.connect(
                sandbox_id,
                api_key=manager._config.api_key,
            )

            # Try a simple operation to verify it's alive
            await sandbox.files.list(".")

            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "exists": True,
                    "sandbox_id": sandbox_id,
                    "status": "active",
                    "in_redis": cached_sandbox_id is not None,
                    "message": "Sandbox is active and responsive",
                },
            )
        except NotFoundException:
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "exists": False,
                    "sandbox_id": sandbox_id,
                    "status": "not_found",
                    "message": "Sandbox ID found but sandbox does not exist",
                },
            )
        except Exception as e:
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "exists": True,
                    "sandbox_id": sandbox_id,
                    "status": "error",
                    "error": str(e),
                    "message": "Sandbox exists but is not responsive",
                },
            )

    except Exception as e:
        logger.error(f"Failed to get sandbox status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get sandbox status: {str(e)}"
        )
