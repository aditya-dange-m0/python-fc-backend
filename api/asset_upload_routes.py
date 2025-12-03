"""
Asset Upload Routes Module - Processing Only

Python Backend handles:
- File processing (document/image analysis, RAG embeddings)
- Returns structured results for NestJS to store

NestJS Backend handles:
- File upload to S3
- Metadata storage in database
- Session management

Flow:
1. NestJS uploads file to S3 → gets S3 URL
2. NestJS calls Python backend with S3 URL
3. Python processes file → returns structured results
4. NestJS stores metadata in database
5. Chat endpoint retrieves asset context for LLM
"""

import os
import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from processors.document_processor import DocumentProcessor
from processors.image_processor import ImageProcessor

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/assets", tags=["assets"])

# Initialize processors (singleton instances)
_document_processor: Optional[DocumentProcessor] = None
_image_processor: Optional[ImageProcessor] = None


def get_document_processor() -> DocumentProcessor:
    """Get or create document processor singleton"""
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor


def get_image_processor() -> ImageProcessor:
    """Get or create image processor singleton"""
    global _image_processor
    if _image_processor is None:
        _image_processor = ImageProcessor()
    return _image_processor


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================


class DocumentProcessingResult(BaseModel):
    """
    Document processing result for NestJS to store.

    NestJS should store this in:
    - Project.metadata.documents[] array
    - RAG embeddings already stored in MongoDB by processor
    """

    success: bool
    asset_id: str
    filename: str
    s3_url: str
    file_type: str  # File extension (e.g., "py", "pdf")
    language: Optional[str] = None  # For code files (e.g., "python", "javascript")
    is_code_file: bool
    summary: str  # Document summary for LLM context
    total_chunks: int  # Number of chunks created
    token_count: int  # Total tokens in document
    rag_processed: bool = True  # RAG embeddings stored in MongoDB
    error: Optional[str] = None
    message: Optional[str] = None


class ImageProcessingResult(BaseModel):
    """
    Image processing result for NestJS to store.

    NestJS should store this in:
    - Project.metadata.images[] array
    - RAG embedding already stored in MongoDB by processor
    """

    success: bool
    asset_id: str
    filename: str
    s3_url: str
    image_url: str  # Same as s3_url, for LLM context
    analysis: str  # YAML-formatted visual analysis
    rag_processed: bool = True  # Image embedding stored in MongoDB
    error: Optional[str] = None
    message: Optional[str] = None


# ============================================================================
# METADATA STRUCTURE FOR NESTJS (Reference)
# ============================================================================
"""
Project.metadata structure (NestJS should store):

{
  "documents": [
    {
      "asset_id": "uuid",
      "filename": "script.py",
      "s3_url": "https://...",
      "file_type": "py",
      "language": "python",
      "is_code_file": true,
      "summary": "This Python script...",
      "total_chunks": 8,
      "token_count": 6709,
      "rag_processed": true,
      "added_at": "2025-01-03T10:30:00Z"
    }
  ],
  "images": [
    {
      "asset_id": "uuid",
      "filename": "dashboard.png",
      "s3_url": "https://...",
      "image_url": "https://...",
      "analysis": "Image Type: design\nPurpose: dashboard...",
      "rag_processed": true,
      "added_at": "2025-01-03T10:30:00Z"
    }
  ],
  "aiContext": {
    "documentSummaries": [
      "script.py (python): This Python script implements file operations..."
    ],
    "imageSummaries": [
      "dashboard.png (image): Modern dashboard design with navbar..."
    ]
  }
}
"""


# ============================================================================
# FILE TYPE DETECTION
# ============================================================================


def get_file_extension(filename: str) -> str:
    """Extract file extension from filename"""
    return Path(filename).suffix.lower()


def is_image_file(filename: str, content_type: Optional[str] = None) -> bool:
    """Check if file is an image based on extension and content type"""
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp"}
    image_mime_types = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/svg+xml",
        "image/webp",
        "image/bmp",
    }

    ext = get_file_extension(filename)
    if ext in image_extensions:
        return True

    if content_type and content_type.lower() in image_mime_types:
        return True

    return False


def is_document_file(filename: str, content_type: Optional[str] = None) -> bool:
    """Check if file is a document/code file"""
    # If it's an image, it's not a document
    if is_image_file(filename, content_type):
        return False

    # Document extensions (code files are also documents)
    document_extensions = {
        ".pdf",
        ".docx",
        ".doc",
        ".txt",
        ".md",
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".cpp",
        ".c",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".html",
        ".css",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".csv",
        ".xlsx",
        ".pptx",
    }

    ext = get_file_extension(filename)
    return ext in document_extensions


# ============================================================================
# ROUTES
# ============================================================================


@router.post("/process-document")
async def process_document(
    s3_url: str,
    filename: str,
    session_id: str,
    user_id: str,
    content_type: Optional[str] = None,
):
    """
    Process a document file from S3 URL.

    Python backend processes the document:
    - Downloads from S3
    - Generates summary
    - Chunks document
    - Stores embeddings in MongoDB (RAG ready)

    Returns structured result for NestJS to store in Project.metadata.documents[]

    Args:
        s3_url: Full S3 URL of the uploaded file
        filename: Original filename
        session_id: Session/project ID
        user_id: User ID
        content_type: Optional MIME type

    Returns:
        DocumentProcessingResult with processing results
    """
    try:
        # Validate inputs
        if not s3_url:
            raise HTTPException(status_code=400, detail="s3_url is required")
        if not filename:
            raise HTTPException(status_code=400, detail="filename is required")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")

        logger.info(f"Processing document: {filename} (session: {session_id})")

        # Generate asset ID (NestJS will use this)
        asset_id = str(uuid.uuid4())

        # Process document
        processor = get_document_processor()
        file_ext = get_file_extension(filename)

        result = await processor.process_document(
            s3_url=s3_url,
            filename=filename,
            filetype=file_ext,
            session_id=session_id,
            user_id=user_id,
        )

        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            return JSONResponse(
                status_code=500,
                content=DocumentProcessingResult(
                    success=False,
                    asset_id=asset_id,
                    filename=filename,
                    s3_url=s3_url,
                    file_type=file_ext[1:] if file_ext.startswith(".") else file_ext,
                    is_code_file=False,
                    summary="",
                    total_chunks=0,
                    token_count=0,
                    rag_processed=False,
                    error=error_msg,
                    message=f"Document processing failed: {error_msg}",
                ).model_dump(),
            )

        # Return structured result for NestJS
        return JSONResponse(
            content=DocumentProcessingResult(
                success=True,
                asset_id=asset_id,
                filename=filename,
                s3_url=s3_url,
                file_type=result.get(
                    "file_type", file_ext[1:] if file_ext.startswith(".") else file_ext
                ),
                language=result.get("language"),
                is_code_file=result.get("is_code_file", False),
                summary=result.get("summary", ""),
                total_chunks=result.get("total_chunks", 0),
                token_count=result.get("token_count", 0),
                rag_processed=True,  # Embeddings stored by processor
                message=result.get(
                    "message", f"Document processed successfully: {filename}"
                ),
            ).model_dump(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Document processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process-image")
async def process_image(
    s3_url: str,
    filename: str,
    session_id: str,
    content_type: Optional[str] = None,
):
    """
    Process an image file from S3 URL.

    Python backend processes the image:
    - Downloads from S3
    - Analyzes with vision model
    - Stores embedding in MongoDB (RAG ready)

    Returns structured result for NestJS to store in Project.metadata.images[]

    Args:
        s3_url: Full S3 URL of the uploaded image
        filename: Original filename
        session_id: Session/project ID
        content_type: Optional MIME type

    Returns:
        ImageProcessingResult with processing results
    """
    try:
        # Validate inputs
        if not s3_url:
            raise HTTPException(status_code=400, detail="s3_url is required")
        if not filename:
            raise HTTPException(status_code=400, detail="filename is required")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")

        logger.info(f"Processing image: {filename} (session: {session_id})")

        # Generate asset ID (NestJS will use this)
        asset_id = str(uuid.uuid4())

        # Process image
        processor = get_image_processor()

        result = await processor.process_image(
            s3_url=s3_url,
            filename=filename,
            session_id=session_id,
        )

        if result.get("status") == "error":
            error_msg = result.get("error", "Unknown error")
            return JSONResponse(
                status_code=500,
                content=ImageProcessingResult(
                    success=False,
                    asset_id=asset_id,
                    filename=filename,
                    s3_url=s3_url,
                    image_url=s3_url,
                    analysis="",
                    rag_processed=False,
                    error=error_msg,
                    message=f"Image processing failed: {error_msg}",
                ).model_dump(),
            )

        # Return structured result for NestJS
        return JSONResponse(
            content=ImageProcessingResult(
                success=True,
                asset_id=asset_id,
                filename=filename,
                s3_url=s3_url,
                image_url=s3_url,  # For LLM context
                analysis=result.get("img_analysis", ""),
                rag_processed=True,  # Embedding stored by processor
                message=f"Image processed successfully: {filename}",
            ).model_dump(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Image processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/process-asset")
async def process_asset(
    s3_url: str,
    filename: str,
    session_id: str,
    user_id: str,
    content_type: Optional[str] = None,
):
    """
    Process an asset (document or image) - routes automatically based on file type.

    This is a convenience endpoint that routes to the appropriate processor.

    Args:
        s3_url: Full S3 URL of the uploaded file
        filename: Original filename
        session_id: Session/project ID
        user_id: User ID (required for documents)
        content_type: Optional MIME type

    Returns:
        Either DocumentProcessingResult or ImageProcessingResult
    """
    try:
        # Route based on file type
        if is_image_file(filename, content_type):
            return await process_image(
                s3_url=s3_url,
                filename=filename,
                session_id=session_id,
                content_type=content_type,
            )
        elif is_document_file(filename, content_type):
            return await process_document(
                s3_url=s3_url,
                filename=filename,
                session_id=session_id,
                user_id=user_id,
                content_type=content_type,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {filename}. Supported: documents (.pdf, .py, .txt, etc.) and images (.jpg, .png, etc.)",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Asset processing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# NOTES FOR NESTJS INTEGRATION
# ============================================================================
"""
ARCHITECTURE: Python Backend = Processing Only

Python Backend Responsibilities:
- Process documents/images and return structured results
- Store RAG embeddings in MongoDB (with session_id)
- NO database metadata operations

NestJS Backend Responsibilities:
- Upload files to S3
- Store metadata in Project.metadata (PostgreSQL)
- Extract asset context (image_urls, document_context) before chat requests
- Include asset context in chat request payload

Chat Request Flow:
1. NestJS retrieves Project.metadata for session
2. Extracts image_urls from Project.metadata.images[]
3. Extracts document_context from Project.metadata.documents[]
4. Sends chat request to Python with image_urls and document_context
5. Python agent uses provided context (no database query needed)

See api/asset_upload_architecture.md for full details.
"""
