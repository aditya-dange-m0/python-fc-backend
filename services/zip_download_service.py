"""
Production ZIP Download Service for E2B Sandboxes
==================================================

Optimized production-ready service for creating ZIP archives and generating download URLs.
Uses get_user_sandbox() for simplified integration.

Features:
- Single universal create_zip() method for all use cases
- Support for both relative and absolute paths
- Auto-install zip utility if needed
- Smart exclude patterns with sensible defaults
- Signed download URLs with configurable expiration
- Comprehensive error handling and logging
- Resource cleanup utilities
- File listing and info retrieval

Integration:
- Simple function-based approach using get_user_sandbox()
- No need to manage sandbox manager instances
- Proper async/await patterns
- Production-grade security with sudo for permission handling
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import os

from e2b import AsyncSandbox
from sandbox_manager import get_user_sandbox

logger = logging.getLogger(__name__)


class ZipDownloadService:
    """
    Production service for creating ZIPs and generating download URLs.

    Single universal method handles all use cases - no redundant wrappers.
    """

    # Default exclusion patterns for cleaner ZIPs
    DEFAULT_EXCLUDES = [
        # Dependencies
        "*/node_modules/*",
        "*/.git/*",
        "*/.venv/*",
        "*/venv/*",
        "*/__pycache__/*",
        "*.pyc",
        # Config & secrets
        # "*/.env*",
        "*/.DS_Store",
        # Build artifacts
        "*/dist/*",
        "*/build/*",
        "*/.next/*",
        "*/coverage/*",
        # Logs & archives
        "*.log",
        "*.zip",
        # System directories (prevent infinite loops)
        "*/dev/*",
        "*/proc/*",
        "*/sys/*",
        "*/run/*",
        "*/tmp/*",
    ]

    # Default download URL expiration (27.7 hours)
    DEFAULT_URL_EXPIRATION = 10000

    def __init__(self):
        """Initialize ZIP download service with default settings."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def ensure_zip_installed(self, sandbox: AsyncSandbox) -> bool:
        """
        Ensure zip utility is installed in sandbox.

        Args:
            sandbox: AsyncSandbox instance

        Returns:
            True if zip is available, False otherwise
        """
        try:
            # Check if zip exists
            self.logger.debug("Checking for zip utility...")
            check = await sandbox.commands.run("which zip 2>/dev/null")

            if check.exit_code == 0:
                self.logger.debug("✓ zip utility already installed")
                return True

            # Install zip quietly
            self.logger.info("Installing zip utility...")
            result = await sandbox.commands.run(
                "sudo DEBIAN_FRONTEND=noninteractive apt-get install -qq -y zip"
            )

            if result.exit_code == 0:
                self.logger.info("✓ zip utility installed successfully")
                return True

            error_msg = result.stderr.strip() if result.stderr else "Unknown error"
            self.logger.error(f"Failed to install zip: {error_msg}")
            return False

        except Exception as e:
            self.logger.error(f"Error installing zip utility: {e}", exc_info=True)
            return False

    async def create_zip(
        self,
        user_id: str,
        project_id: str,
        source_path: Optional[str] = None,
        zip_name: Optional[str] = None,
        exclude_patterns: Optional[List[str]] = None,
        use_defaults: bool = True,
        url_expiration: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Universal ZIP creation method - handles all use cases.

        Args:
            user_id: User identifier
            project_id: Project identifier
            source_path: Path to zip. Can be:
                        - None: zips entire /home/user/code (full project)
                        - Relative path (e.g., "frontend", "backend"): relative to /home/user/code
                        - Absolute path (e.g., "/home/user/code/backend"): used as-is
            zip_name: Custom ZIP filename (auto-generated if None)
            exclude_patterns: Custom exclusion patterns (merged with defaults if use_defaults=True)
            use_defaults: If True, merges custom patterns with DEFAULT_EXCLUDES
            url_expiration: Download URL expiration in seconds (default: 10000)

        Returns:
            Dict containing:
                - success: bool
                - sandbox_path: Path to ZIP in sandbox
                - download_url: Signed download URL
                - filename: ZIP filename
                - source_path: What was zipped
                - size_bytes: File size in bytes
                - size_mb: File size in MB
                - created_at: ISO timestamp
                - expires_at: URL expiration timestamp
                - user_id, project_id: Identifiers

        Raises:
            Exception: If sandbox access fails, folder doesn't exist, or ZIP creation fails

        Examples:
            # Full project (default excludes)
            result = await service.create_zip("user1", "proj1")

            # Specific folder (relative path)
            result = await service.create_zip("user1", "proj1", source_path="frontend")

            # Absolute path
            result = await service.create_zip("user1", "proj1", source_path="/home/user/code/backend")

            # Custom excludes only (no defaults)
            result = await service.create_zip("user1", "proj1",
                                             exclude_patterns=["*.tmp"],
                                             use_defaults=False)

            # Get download URL directly
            url = result["download_url"]
        """
        try:
            # Normalize source_path: treat empty string as None (full project)
            if source_path is not None and source_path.strip() == "":
                source_path = None

            # Determine what we're zipping
            is_full_project = source_path is None
            log_target = "full project" if is_full_project else f"'{source_path}'"

            self.logger.info(f"[{user_id}/{project_id}] Creating ZIP of {log_target}")

            # Get sandbox
            sandbox = await get_user_sandbox(user_id, project_id)

            # Ensure zip utility is available
            if not await self.ensure_zip_installed(sandbox):
                raise Exception("Could not install zip utility in sandbox")

            # Determine paths and validate
            if is_full_project:
                # Full project: zip entire /home/user/code
                work_dir = "/home/user/code"
                zip_target = "."
                display_name = "project"
            else:
                # Specific path: determine if absolute or relative
                is_absolute = source_path.startswith("/")

                if is_absolute:
                    full_path = source_path
                    display_name = os.path.basename(source_path.rstrip("/"))
                else:
                    full_path = f"/home/user/code/{source_path.lstrip('/')}"
                    display_name = source_path.replace("/", "_")

                # Verify path exists
                self.logger.debug(f"Checking if path exists: {full_path}")
                check = await sandbox.commands.run(
                    f'test -e "{full_path}" && echo "exists"'
                )

                if check.stdout.strip() != "exists":
                    raise Exception(f"Path not found: {source_path}")

                # Set up paths for zipping
                if is_absolute:
                    work_dir = os.path.dirname(full_path)
                    zip_target = os.path.basename(full_path)
                else:
                    work_dir = "/home/user/code"
                    zip_target = f"{source_path.lstrip('/')}/"

            # Generate filename with timestamp for uniqueness
            if not zip_name:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                zip_name = f"{display_name}_{project_id}_{timestamp}.zip"

            if not zip_name.endswith(".zip"):
                zip_name += ".zip"

            # Build exclusion patterns
            final_excludes = self._build_exclude_patterns(
                custom_patterns=exclude_patterns, use_defaults=use_defaults
            )

            exclude_args = " ".join([f"-x '{pattern}'" for pattern in final_excludes])

            # Create ZIP (always in /home/user/code for consistency)
            zip_path = f"/home/user/code/{zip_name}"

            # Build ZIP command with safety flags:
            # -r: recursive
            # -q: quiet mode (suppress verbose file listing - CRITICAL for large projects)
            # -y: store symbolic links (don't follow them - prevents /dev/fd/3 loops)
            # --exclude: exclude system directories that cause infinite loops
            # Use sudo to avoid permission issues
            cmd = (
                f'cd "{work_dir}" && '
                f'sudo zip -r -q -y "{zip_path}" {zip_target} '
                f'{exclude_args} '
                f'--exclude "*/dev/*" --exclude "*/proc/*" --exclude "*/sys/*"'
            )

            self.logger.debug(f"Executing ZIP command (quiet mode)")
            self.logger.debug(f"Command: {cmd}")
            
            # Run with reasonable timeout (5 minutes for large projects)
            # No output callbacks needed with -q flag
            result = await sandbox.commands.run(cmd, timeout=300)

            # Exit codes: 0 = success, 12 = success with warnings (some files skipped)
            if result.exit_code not in [0, 12]:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                raise Exception(
                    f"ZIP creation failed (exit code {result.exit_code}): {error_msg}"
                )

            # Get file size
            file_size = await self._get_file_size(sandbox, zip_path)

            # Generate signed download URL
            expiration = (
                url_expiration
                if url_expiration is not None
                else self.DEFAULT_URL_EXPIRATION
            )
            download_url = sandbox.download_url(
                path=zip_path, user="user", use_signature_expiration=expiration
            )

            # Calculate timestamps
            created_at = datetime.now()
            expires_at = created_at.timestamp() + expiration

            result_info = {
                "success": True,
                "sandbox_path": zip_path,
                "download_url": download_url,
                "filename": zip_name,
                "source_path": source_path if source_path else "/home/user/code",
                "is_full_project": is_full_project,
                "size_bytes": file_size,
                "size_mb": round(file_size / (1024 * 1024), 2),
                "created_at": created_at.isoformat(),
                "expires_at": datetime.fromtimestamp(expires_at).isoformat(),
                "user_id": user_id,
                "project_id": project_id,
            }

            self.logger.info(
                f"[{user_id}/{project_id}] ✓ ZIP created: {zip_name} "
                f"({result_info['size_mb']} MB, expires in {expiration}s)"
            )

            return result_info

        except Exception as e:
            self.logger.error(
                f"[{user_id}/{project_id}] Error creating ZIP: {e}", exc_info=True
            )
            raise

    async def _get_file_size(self, sandbox: AsyncSandbox, file_path: str) -> int:
        """Get file size in bytes."""
        try:
            stat_result = await sandbox.commands.run(f'stat -c "%s" "{file_path}"')
            if stat_result.exit_code == 0:
                return int(stat_result.stdout.strip())
        except (ValueError, AttributeError):
            self.logger.warning(f"Could not parse file size for {file_path}")
        return 0

    def _build_exclude_patterns(
        self, custom_patterns: Optional[List[str]], use_defaults: bool
    ) -> List[str]:
        """
        Build final list of exclusion patterns.

        Args:
            custom_patterns: User-provided patterns (None = no custom patterns)
            use_defaults: Whether to include DEFAULT_EXCLUDES

        Returns:
            Final list of patterns to exclude
        """
        if custom_patterns is None:
            # No custom patterns provided
            return self.DEFAULT_EXCLUDES if use_defaults else []

        if not use_defaults:
            # Only custom patterns, no defaults
            return custom_patterns

        # Merge custom with defaults (remove duplicates)
        combined = list(self.DEFAULT_EXCLUDES)
        for pattern in custom_patterns:
            if pattern not in combined:
                combined.append(pattern)

        return combined

    async def cleanup_zip(
        self, user_id: str, project_id: str, sandbox_path: str
    ) -> bool:
        """
        Delete ZIP file from sandbox to free up space.

        Args:
            user_id: User identifier
            project_id: Project identifier
            sandbox_path: Full path to ZIP file in sandbox

        Returns:
            True if deleted or doesn't exist, False on error
        """
        try:
            self.logger.debug(f"[{user_id}/{project_id}] Cleaning up: {sandbox_path}")
            sandbox = await get_user_sandbox(user_id, project_id)
            result = await sandbox.commands.run(f'sudo rm -f "{sandbox_path}"')

            if result.exit_code == 0:
                self.logger.info(
                    f"[{user_id}/{project_id}] ✓ Cleaned up: {sandbox_path}"
                )
                return True

            error_msg = result.stderr.strip() if result.stderr else "Unknown error"
            self.logger.warning(
                f"[{user_id}/{project_id}] Failed to cleanup {sandbox_path}: {error_msg}"
            )
            return False

        except Exception as e:
            self.logger.error(
                f"[{user_id}/{project_id}] Error cleaning up ZIP: {e}", exc_info=True
            )
            return False

    async def list_zip_files(
        self, user_id: str, project_id: str
    ) -> List[Dict[str, Any]]:
        """
        List all ZIP files in /home/user/code directory.

        Args:
            user_id: User identifier
            project_id: Project identifier

        Returns:
            List of dicts with filename, path, size_bytes, size_mb, modified_at
        """
        try:
            self.logger.debug(f"[{user_id}/{project_id}] Listing ZIP files...")
            sandbox = await get_user_sandbox(user_id, project_id)

            # Use E2B files.list() API to list directory contents (more reliable than find command)
            code_dir = "/home/user/code"
            
            try:
                entries = await sandbox.files.list(code_dir)
            except Exception as e:
                self.logger.warning(f"Failed to list directory {code_dir} using files.list(): {e}")
                # Fallback: use ls command
                ls_result = await sandbox.commands.run(f'ls -1 {code_dir}/*.zip 2>/dev/null || true')
                if not ls_result.stdout.strip():
                    self.logger.info(f"[{user_id}/{project_id}] No ZIP files found in {code_dir}")
                    return []
                
                # Parse ls output and get file details
                zip_files = []
                for zip_path in ls_result.stdout.strip().split("\n"):
                    if not zip_path.strip():
                        continue
                    zip_path = zip_path.strip()
                    
                    try:
                        # Get file stats
                        stat_result = await sandbox.commands.run(f'stat -c "%s|%Y" "{zip_path}"')
                        if stat_result.exit_code == 0:
                            size_str, mtime_str = stat_result.stdout.strip().split("|")
                            size_bytes = int(size_str)
                            mtime = int(mtime_str)
                            
                            zip_files.append({
                                "filename": os.path.basename(zip_path),
                                "path": zip_path,
                                "size_bytes": size_bytes,
                                "size_mb": round(size_bytes / (1024 * 1024), 2),
                                "modified_at": datetime.fromtimestamp(mtime).isoformat(),
                            })
                    except Exception as e:
                        self.logger.warning(f"Failed to get stats for {zip_path}: {e}")
                        continue
                
                self.logger.info(f"[{user_id}/{project_id}] Found {len(zip_files)} ZIP files (via ls fallback)")
                return zip_files

            zip_files = []
            self.logger.info(f"[{user_id}/{project_id}] Processing {len(entries)} entries from files.list()")
            
            # Log all entries for debugging
            all_entry_names = [e.name for e in entries]
            self.logger.debug(f"All entries in {code_dir}: {all_entry_names}")
            
            for entry in entries:
                # Log entry details for debugging
                self.logger.debug(f"Checking entry: name={entry.name}, type={getattr(entry, 'type', 'unknown')}, size={getattr(entry, 'size', 'unknown')}")
                
                # Filter for ZIP files only
                if not entry.name.endswith(".zip"):
                    continue
                
                self.logger.debug(f"Found ZIP file candidate: {entry.name}")
                
                # Check if it's a file (not a directory)
                # Be more lenient with type checking
                is_file = True  # Default to True
                if hasattr(entry, "type"):
                    try:
                        type_value = entry.type.value if hasattr(entry.type, "value") else str(entry.type)
                        if type_value == "dir" or type_value == "directory":
                            self.logger.debug(f"Skipping {entry.name} - it's a directory")
                            continue
                    except Exception as e:
                        self.logger.debug(f"Could not check type for {entry.name}: {e}, assuming it's a file")

                # Get file modification time using stat command
                try:
                    stat_cmd = f'stat -c "%Y" "{entry.path}"'
                    stat_result = await sandbox.commands.run(stat_cmd)
                    
                    if stat_result.exit_code == 0 and stat_result.stdout and stat_result.stdout.strip():
                        mtime = int(stat_result.stdout.strip())
                        modified_at = datetime.fromtimestamp(mtime).isoformat()
                    else:
                        # Fallback to current time if stat fails
                        modified_at = datetime.now().isoformat()
                        self.logger.debug(f"Could not get mtime for {entry.path}, using current time")
                except Exception as e:
                    self.logger.warning(f"Error getting mtime for {entry.path}: {e}")
                    modified_at = datetime.now().isoformat()

                entry_size = entry.size if hasattr(entry, "size") and entry.size else 0
                zip_files.append(
                    {
                        "filename": entry.name,
                        "path": entry.path,
                        "size_bytes": entry_size,
                        "size_mb": round(entry_size / (1024 * 1024), 2),
                        "modified_at": modified_at,
                    }
                )
                self.logger.info(f"✓ Added ZIP file: {entry.name} ({entry_size} bytes)")

            self.logger.info(
                f"[{user_id}/{project_id}] Found {len(zip_files)} ZIP files"
            )
            return zip_files

        except Exception as e:
            self.logger.error(
                f"[{user_id}/{project_id}] Error listing ZIP files: {e}", exc_info=True
            )
            return []

    async def get_zip_info(
        self, user_id: str, project_id: str, sandbox_path: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific ZIP file.

        Args:
            user_id: User identifier
            project_id: Project identifier
            sandbox_path: Full path to ZIP file

        Returns:
            Dict with file info or None if file doesn't exist
        """
        try:
            sandbox = await get_user_sandbox(user_id, project_id)

            cmd = f'test -f "{sandbox_path}" && stat -c "%s|%Y" "{sandbox_path}"'
            result = await sandbox.commands.run(cmd)

            if result.exit_code != 0:
                return None

            size, mtime = result.stdout.strip().split("|")
            return {
                "filename": os.path.basename(sandbox_path),
                "path": sandbox_path,
                "size_bytes": int(size),
                "size_mb": round(int(size) / (1024 * 1024), 2),
                "modified_at": datetime.fromtimestamp(int(mtime)).isoformat(),
            }

        except Exception as e:
            self.logger.error(f"Error getting ZIP info: {e}", exc_info=True)
            return None


# ============================================================================
# Singleton Service Instance
# ============================================================================

_zip_service_instance: Optional[ZipDownloadService] = None


def get_zip_service() -> ZipDownloadService:
    """
    Get or create singleton ZIP download service instance.

    Returns:
        Shared ZipDownloadService instance

    Example:
        service = get_zip_service()
        result = await service.create_zip("user123", "proj456")
        print(result["download_url"])
    """
    global _zip_service_instance

    if _zip_service_instance is None:
        _zip_service_instance = ZipDownloadService()

    return _zip_service_instance


# ============================================================================
# Example Usage
# ============================================================================


async def example_usage():
    """
    Comprehensive example showing all service features.

    Now using only create_zip() for everything - no redundant wrappers!
    """

    service = get_zip_service()
    user_id = "user_123"
    project_id = "project_456"

    print("=" * 80)
    print("ZIP Download Service - Streamlined API")
    print("=" * 80)

    try:
        # ====================================================================
        # USE CASE 1: Full Project Downloads
        # ====================================================================

        print("\n📦 FULL PROJECT DOWNLOADS")
        print("-" * 80)

        # 1a. Full project with default excludes
        print("\n1️⃣  Full project (default excludes)...")
        result = await service.create_zip(user_id, project_id)
        print(f"   ✓ {result['filename']} ({result['size_mb']} MB)")
        print(f"   🔗 {result['download_url']}")

        # 1b. Full project with custom excludes merged with defaults
        print("\n2️⃣  Full project (defaults + custom excludes)...")
        result = await service.create_zip(
            user_id,
            project_id,
            exclude_patterns=["*.bak", "*.swp"],  # Added to defaults
            use_defaults=True,
        )
        print(
            f"   ✓ {result['filename']} ({result['size_mb']} MB) ({result['download_url']})"
        )

        # 1c. Full project with ONLY custom excludes (no defaults)
        print("\n3️⃣  Full project (custom excludes only)...")
        result = await service.create_zip(
            user_id,
            project_id,
            exclude_patterns=["*.tmp", "*.cache"],
            use_defaults=False,  # Only use custom patterns
        )
        print(
            f"   ✓ {result['filename']} ({result['size_mb']} MB) ({result['download_url']})"
        )

        # 1d. Full project with NO excludes at all
        print("\n4️⃣  Full project (no excludes)...")
        result = await service.create_zip(
            user_id, project_id, exclude_patterns=[], use_defaults=False
        )
        print(
            f"   ✓ {result['filename']} ({result['size_mb']} MB) ({result['download_url']})"
        )

        # ====================================================================
        # USE CASE 2: Specific Folder Downloads (Relative Paths)
        # ====================================================================

        print("\n\n📁 SPECIFIC FOLDER DOWNLOADS (Relative Paths)")
        print("-" * 80)

        # 2a. Frontend folder
        print("\n5️⃣  Frontend folder...")
        result = await service.create_zip(user_id, project_id, source_path="frontend")
        print(
            f"   ✓ {result['filename']} ({result['size_mb']} MB) ({result['download_url']})"
        )
        print(f"   📂 Source: {result['source_path']}")

        # 2b. Backend folder
        print("\n6️⃣  Backend folder...")
        result = await service.create_zip(user_id, project_id, source_path="backend")
        print(
            f"   ✓ {result['filename']} ({result['size_mb']} MB) ({result['download_url']})"
        )

        # 2c. Nested folder
        print("\n7️⃣  Nested folder (src/components)...")
        result = await service.create_zip(
            user_id, project_id, source_path="frontend/src/components"
        )
        print(
            f"   ✓ {result['filename']} ({result['size_mb']} MB) ({result['download_url']})"
        )

        # ====================================================================
        # USE CASE 3: Absolute Path Downloads
        # ====================================================================

        print("\n\n🗂️  ABSOLUTE PATH DOWNLOADS")
        print("-" * 80)

        # 3a. Backend using absolute path
        print("\n8️⃣  Backend (absolute path)...")
        result = await service.create_zip(
            user_id, project_id, source_path="/home/user/code/backend"
        )
        print(
            f"   ✓ {result['filename']} ({result['size_mb']} MB) ({result['download_url']})"
        )

        # ====================================================================
        # USE CASE 4: Custom Options
        # ====================================================================

        print("\n\n⚙️  CUSTOM OPTIONS")
        print("-" * 80)

        # 4a. Custom filename
        print("\n9️⃣  Custom filename...")
        result = await service.create_zip(
            user_id,
            project_id,
            source_path="frontend",
            zip_name="my_frontend_build",  # .zip added automatically
        )
        print(f"   ✓ {result['filename']}")

        # 4b. Custom URL expiration
        print("\n🔟 Custom URL expiration (1 hour)...")
        result = await service.create_zip(
            user_id,
            project_id,
            source_path="backend",
            url_expiration=3600,  # 1 hour instead of default
        )
        print(f"   ✓ {result['filename']}")
        print(f"   ⏰ Expires: {result['expires_at']}")

        # ====================================================================
        # USE CASE 5: Quick One-Liner Pattern
        # ====================================================================

        print("\n\n⚡ QUICK ONE-LINER PATTERN")
        print("-" * 80)

        # Get URL directly in one line
        print("\n1️⃣1️⃣  Get download URL in one line...")
        url = (await service.create_zip(user_id, project_id))["download_url"]
        print(f"   🔗 {url[:80]}...")

        # Multiple one-liners for different folders
        print("\n1️⃣2️⃣  Multiple folder URLs...")
        frontend_url = (await service.create_zip(user_id, project_id, "frontend"))[
            "download_url"
        ]
        backend_url = (await service.create_zip(user_id, project_id, "backend"))[
            "download_url"
        ]
        print(f"   🔗 Frontend: {frontend_url[:60]}...")
        print(f"   🔗 Backend: {backend_url[:60]}...")

        # ====================================================================
        # USE CASE 6: Management Operations
        # ====================================================================

        print("\n\n🛠️  MANAGEMENT OPERATIONS")
        print("-" * 80)

        # 6a. List all ZIPs
        print("\n1️⃣3️⃣  Listing all ZIP files...")
        zip_files = await service.list_zip_files(user_id, project_id)
        print(f"   Found {len(zip_files)} ZIP files:")
        for zf in zip_files[:5]:  # Show first 5
            print(
                f"   - {zf['filename']}: {zf['size_mb']} MB (modified: {zf['modified_at']})"
            )

        # 6b. Get info about specific ZIP
        if zip_files:
            print("\n1️⃣4️⃣  Getting info about specific ZIP...")
            info = await service.get_zip_info(user_id, project_id, zip_files[0]["path"])
            if info:
                print(f"   📦 {info['filename']}")
                print(f"   💾 {info['size_mb']} MB")
                print(f"   📅 {info['modified_at']}")

        # 6c. Cleanup old ZIPs
        print("\n1️⃣5️⃣  Cleaning up old ZIP files...")
        cleanup_count = 0
        for zf in zip_files[:3]:  # Clean first 3
            success = await service.cleanup_zip(user_id, project_id, zf["path"])
            if success:
                cleanup_count += 1
                print(f"   ✓ Cleaned: {zf['filename']}")
        print(f"   Total cleaned: {cleanup_count} files")

        # ====================================================================
        # USE CASE 7: Real-World API Endpoint Pattern
        # ====================================================================

        print("\n\n🌐 REAL-WORLD API ENDPOINT PATTERN")
        print("-" * 80)

        print("\n1️⃣6️⃣  Simulating API endpoint...")

        async def api_download_endpoint(
            user_id: str, project_id: str, folder: Optional[str] = None
        ):
            """Example API endpoint logic"""
            try:
                result = await service.create_zip(
                    user_id=user_id,
                    project_id=project_id,
                    source_path=folder,  # None = full project, "frontend" = specific folder
                )
                return {
                    "status": "success",
                    "download_url": result["download_url"],
                    "filename": result["filename"],
                    "size_mb": result["size_mb"],
                    "expires_at": result["expires_at"],
                }
            except Exception as e:
                return {"status": "error", "message": str(e)}

        # Test different scenarios
        response1 = await api_download_endpoint(user_id, project_id)
        print(f"   API Response (full project): {response1['status']}")
        print(f"   📦 {response1['filename']} ({response1['size_mb']} MB)")

        response2 = await api_download_endpoint(user_id, project_id, "frontend")
        print(f"\n   API Response (frontend): {response2['status']}")
        print(f"   📦 {response2['filename']} ({response2['size_mb']} MB)")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()

    print("\n" + "=" * 80)
    print("✅ Example complete - Single create_zip() method for everything!")
    print("=" * 80)


async def quick_examples():
    """
    Quick reference examples showing common patterns.
    """
    service = get_zip_service()

    # Full project
    result = await service.create_zip("user123", "proj456")

    # Frontend folder
    result = await service.create_zip("user123", "proj456", source_path="frontend")

    # Backend with absolute path
    result = await service.create_zip(
        "user123", "proj456", source_path="/home/user/code/backend"
    )

    # Get URL directly
    url = (await service.create_zip("user123", "proj456"))["download_url"]

    # Custom excludes
    result = await service.create_zip(
        "user123", "proj456", exclude_patterns=["*.log"], use_defaults=True
    )

    # List and cleanup
    zips = await service.list_zip_files("user123", "proj456")
    for z in zips:
        await service.cleanup_zip("user123", "proj456", z["path"])


if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    # Add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    print("\n🚀 Running Streamlined ZIP Service Examples...\n")
    asyncio.run(example_usage())
