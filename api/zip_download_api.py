"""
Code ZIP Download API
==========================

Single unified endpoint for all ZIP download scenarios.
Based on optimized ZipDownloadService with universal create_zip() method.

Features:
- One endpoint for all use cases (full project, folders, custom paths)
- Direct download URL generation (no streaming)
- Support for E2B signed URLs with expiration
- Smart parameter handling (relative/absolute paths)
- Production-ready error handling

Endpoint:
- POST /api/projects/{project_id}/download - Universal download endpoint
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Body, Query
from pydantic import BaseModel, Field, field_validator

from services.zip_download_service import get_zip_service

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/projects", tags=["downloads"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class DownloadRequest(BaseModel):
    """
    Universal request model for all download scenarios.

    Examples:
    - Full project: source_path=None
    - Frontend folder: source_path="frontend"
    - Backend absolute: source_path="/home/user/code/backend"
    - Nested path: source_path="frontend/src/components"
    """

    user_id: str = Field(..., description="User identifier", example="user_123")

    source_path: Optional[str] = Field(
        None,
        description=(
            "Path to ZIP. "
            "None = full project (/home/user/code), "
            "Relative path (e.g., 'frontend', 'backend') = relative to /home/user/code, "
            "Absolute path (e.g., '/home/user/code/backend') = used as-is"
        ),
        example="frontend",
    )

    zip_name: Optional[str] = Field(
        None,
        description="Custom ZIP filename (auto-generated if None)",
        example="my_project_v1.zip",
    )

    exclude_patterns: Optional[List[str]] = Field(
        None,
        description="Custom exclusion patterns (merged with defaults if use_defaults=True)",
        example=["*.log", "*.tmp", "test/*"],
    )

    use_defaults: bool = Field(
        True,
        description="If True, merges custom patterns with default excludes (node_modules, .git, etc.)",
    )

    url_expiration: Optional[int] = Field(
        None,
        description="Download URL expiration in seconds (default: 10000 = 2.7 hours)",
        example=3600,
    )

    @field_validator("source_path", mode="before")
    @classmethod
    def normalize_source_path(cls, v):
        """Convert empty string to None for consistent handling."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


class DownloadResponse(BaseModel):
    """Universal response model for all download scenarios."""

    success: bool = Field(..., description="Operation success status")

    download_url: str = Field(
        ..., description="Signed download URL (E2B sandbox URL with signature)"
    )

    filename: str = Field(..., description="ZIP filename")

    source_path: str = Field(..., description="Path that was zipped")

    is_full_project: bool = Field(..., description="True if entire project was zipped")

    size_bytes: int = Field(..., description="File size in bytes")

    size_mb: float = Field(..., description="File size in MB")

    created_at: str = Field(..., description="ISO timestamp of creation")

    expires_at: str = Field(..., description="ISO timestamp of URL expiration")

    sandbox_path: str = Field(
        ..., description="Path to ZIP file in sandbox (for debugging)"
    )

    user_id: str
    project_id: str


# =============================================================================
# UNIVERSAL DOWNLOAD ENDPOINT
# =============================================================================


@router.post(
    "/{project_id}/download",
    response_model=DownloadResponse,
    summary="Code ZIP Download",
    description=(
        "Create and download ZIP archive with flexible path options. "
        "Handles full project, specific folders, or custom paths with single endpoint."
    ),
)
async def download_project_zip(
    project_id: str,
    request: DownloadRequest = Body(...),
) -> DownloadResponse:
    """
    Code ZIP download endpoint - handles all use cases.

    Use Cases:
    1. Full project: source_path=None
    2. Specific folder: source_path="frontend" or "backend"
    3. Nested path: source_path="frontend/src/components"
    4. Absolute path: source_path="/home/user/code/backend"

    Args:
        project_id: Project identifier (from URL path)
        request: DownloadRequest with download parameters

    Returns:
        DownloadResponse with download_url and metadata

    Raises:
        HTTPException 400: Invalid path or parameters
        HTTPException 404: Path not found in sandbox
        HTTPException 500: ZIP creation or sandbox error

    Example Requests:

    ```
    # Full project
    {
        "user_id": "user_123"
    }

    # Frontend folder
    {
        "user_id": "user_123",
        "source_path": "frontend"
    }

    # Backend with custom excludes
    {
        "user_id": "user_123",
        "source_path": "backend",
        "exclude_patterns": ["*.pyc", "venv/*"],
        "use_defaults": true
    }

    # Custom expiration (1 hour)
    {
        "user_id": "user_123",
        "source_path": "frontend",
        "url_expiration": 3600
    }
    ```
    """

    try:
        # Log request
        source_desc = request.source_path or "full project"
        logger.info(f"[{request.user_id}/{project_id}] Download request: {source_desc}")

        # Get service instance
        service = get_zip_service()

        # Call universal create_zip method
        result = await service.create_zip(
            user_id=request.user_id,
            project_id=project_id,
            source_path=request.source_path,
            zip_name=request.zip_name,
            exclude_patterns=request.exclude_patterns,
            use_defaults=request.use_defaults,
            url_expiration=request.url_expiration,
        )

        # Log success
        logger.info(
            f"[{request.user_id}/{project_id}] ✓ ZIP created: "
            f"{result['filename']} ({result['size_mb']} MB)"
        )

        # Return response
        return DownloadResponse(**result)

    except ValueError as e:
        # Invalid parameters
        logger.warning(f"Invalid download request: {e}")
        raise HTTPException(
            status_code=400, detail=f"Invalid request parameters: {str(e)}"
        )

    except FileNotFoundError as e:
        # Path not found
        logger.warning(f"Path not found: {e}")
        raise HTTPException(
            status_code=404, detail=f"Path not found in sandbox: {str(e)}"
        )

    except Exception as e:
        # General error (sandbox issues, ZIP creation failed, etc.)
        error_msg = str(e)
        logger.error(
            f"[{request.user_id}/{project_id}] Download error: {error_msg}",
            exc_info=True,
        )

        # Check for specific error types
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=404, detail=f"Resource not found: {error_msg}"
            )
        elif "permission" in error_msg.lower():
            raise HTTPException(
                status_code=403, detail=f"Permission denied: {error_msg}"
            )
        else:
            raise HTTPException(
                status_code=500, detail=f"Failed to create ZIP: {error_msg}"
            )


# =============================================================================
# UTILITY ENDPOINTS
# =============================================================================


@router.get(
    "/{project_id}/download/list-zips",
    summary="List ZIP Files",
    description="List all existing ZIP files in the sandbox",
)
async def list_project_zips(
    project_id: str,
    user_id: str = Query(..., description="User identifier"),
):
    """
    List all ZIP files in /home/user/code directory.

    Args:
        project_id: Project identifier
        user_id: User identifier (query parameter)

    Returns:
        List of ZIP files with metadata
    """
    try:
        logger.info(f"[{user_id}/{project_id}] Listing ZIP files")

        service = get_zip_service()
        zip_files = await service.list_zip_files(user_id, project_id)

        return {
            "success": True,
            "project_id": project_id,
            "zip_count": len(zip_files),
            "zip_files": zip_files,
        }

    except Exception as e:
        logger.error(f"Error listing ZIPs: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to list ZIP files: {str(e)}"
        )


@router.delete(
    "/{project_id}/download/cleanup",
    summary="Cleanup ZIP Files",
    description="Delete specific or all ZIP files to free sandbox space",
)
async def cleanup_project_zips(
    project_id: str,
    user_id: str = Query(..., description="User identifier"),
    sandbox_path: Optional[str] = Query(None, description="Specific ZIP path to delete (None = delete all)"),
):
    """
    Cleanup ZIP files in sandbox.

    Args:
        project_id: Project identifier
        user_id: User identifier (query parameter)
        sandbox_path: Specific ZIP path to delete (None = delete all)

    Returns:
        Cleanup status
    """
    try:
        service = get_zip_service()

        if sandbox_path:
            # Delete specific ZIP
            logger.info(f"[{user_id}/{project_id}] Deleting: {sandbox_path}")
            success = await service.cleanup_zip(user_id, project_id, sandbox_path)

            return {
                "success": success,
                "message": "ZIP file deleted" if success else "Delete failed",
                "deleted_path": sandbox_path,
            }
        else:
            # Delete all ZIPs
            logger.info(f"[{user_id}/{project_id}] Cleaning up all ZIPs")
            zip_files = await service.list_zip_files(user_id, project_id)

            deleted_count = 0
            for zf in zip_files:
                success = await service.cleanup_zip(user_id, project_id, zf["path"])
                if success:
                    deleted_count += 1

            return {
                "success": True,
                "message": f"Deleted {deleted_count} ZIP files",
                "deleted_count": deleted_count,
                "total_count": len(zip_files),
            }

    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to cleanup ZIPs: {str(e)}")


# @router.get(
#     "/{project_id}/download/info",
#     summary="Get ZIP Info",
#     description="Get information about a specific ZIP file",
# )
# async def get_zip_file_info(
#     project_id: str,
#     user_id: str,
#     sandbox_path: str,
# ):
#     """
#     Get metadata about a specific ZIP file.

#     Args:
#         project_id: Project identifier
#         user_id: User identifier (query parameter)
#         sandbox_path: Full path to ZIP file in sandbox

#     Returns:
#         ZIP file metadata or 404 if not found
#     """
#     try:
#         logger.info(f"[{user_id}/{project_id}] Getting info: {sandbox_path}")

#         service = get_zip_service()
#         info = await service.get_zip_info(user_id, project_id, sandbox_path)

#         if not info:
#             raise HTTPException(
#                 status_code=404, detail=f"ZIP file not found: {sandbox_path}"
#             )

#         return {
#             "success": True,
#             "project_id": project_id,
#             "zip_info": info,
#         }

#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Error getting ZIP info: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=f"Failed to get ZIP info: {str(e)}")


# =============================================================================
# INTEGRATION HELPER
# =============================================================================


def include_download_routes(app):
    """
    Include download routes in the main FastAPI app.

    Usage:
        from api.zip_download_api import include_download_routes

        app = FastAPI()
        include_download_routes(app)
    """
    app.include_router(router)
    logger.info("✓ Universal ZIP download routes registered")
