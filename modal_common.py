"""
Common utilities for Modal video processing deployment
Shared configurations, helpers, and constants
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

# ============================================================================
# Constants
# ============================================================================

class ColorType(str, Enum):
    """Supported chroma key color types"""
    GREEN = "green"
    BLUE = "blue"


class VideoFormat(str, Enum):
    """Supported video formats"""
    MP4 = "mp4"
    WEBM = "webm"
    MOV = "mov"
    AVI = "avi"
    MKV = "mkv"


# File size limits
MAX_FILE_SIZE_MB = 500
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Processing timeouts
DOWNLOAD_TIMEOUT_SECONDS = 300
PROCESSING_TIMEOUT_SECONDS = 3600

# FFmpeg quality settings
DEFAULT_CRF = 30  # Constant Rate Factor (0-51, lower = better quality)
MASK_VIDEO_BITRATE = "1M"
RESULT_VIDEO_BITRATE = "2M"

# Allowed MIME types
ALLOWED_MIME_TYPES = [
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
]


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class ChromaKeySettings:
    """Configuration for FFmpeg chromakey filter"""
    color: str  # Hex color to remove (e.g., '00FF00' for green)
    similarity: float  # 0.01 to 1.0 - higher = more colors matched
    blend: float  # 0.0 to 1.0 - edge blending
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "color": self.color,
            "similarity": self.similarity,
            "blend": self.blend
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChromaKeySettings":
        """Create from dictionary"""
        return cls(
            color=data.get("color", "00FF00"),
            similarity=data.get("similarity", 0.25),
            blend=data.get("blend", 0.1)
        )


@dataclass
class ProcessingConfig:
    """Video processing configuration"""
    color_type: ColorType
    result_settings: ChromaKeySettings
    mask_settings: ChromaKeySettings
    session_id: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "color_type": self.color_type.value,
            "result_settings": self.result_settings.to_dict(),
            "mask_settings": self.mask_settings.to_dict(),
            "session_id": self.session_id
        }


# ============================================================================
# Default Settings
# ============================================================================

GREEN_SCREEN_SETTINGS = ChromaKeySettings(
    color="00FF00",
    similarity=0.01,  # Lower similarity = more precise, avoids green spill
    blend=0.08
)

BLUE_SCREEN_SETTINGS = ChromaKeySettings(
    color="0000FF",
    similarity=0.3,
    blend=0.1
)


# ============================================================================
# Helper Functions
# ============================================================================

def get_default_settings(color_type: ColorType) -> ChromaKeySettings:
    """Get default settings for color type"""
    if color_type == ColorType.GREEN:
        return ChromaKeySettings(
            color=GREEN_SCREEN_SETTINGS.color,
            similarity=GREEN_SCREEN_SETTINGS.similarity,
            blend=GREEN_SCREEN_SETTINGS.blend
        )
    else:
        return ChromaKeySettings(
            color=BLUE_SCREEN_SETTINGS.color,
            similarity=BLUE_SCREEN_SETTINGS.similarity,
            blend=BLUE_SCREEN_SETTINGS.blend
        )


def merge_settings(
    color_type: ColorType,
    overrides: Optional[Dict[str, Any]] = None
) -> ChromaKeySettings:
    """Merge default settings with overrides"""
    base = get_default_settings(color_type)
    
    if not overrides:
        return base
    
    return ChromaKeySettings(
        color=overrides.get("color", base.color).upper(),
        similarity=overrides.get("similarity", base.similarity),
        blend=overrides.get("blend", base.blend)
    )


def validate_hex_color(color: str) -> bool:
    """Validate hex color format"""
    if not color:
        return False
    
    # Remove '#' if present
    color = color.lstrip("#")
    
    # Check length
    if len(color) != 6:
        return False
    
    # Check if all characters are valid hex
    try:
        int(color, 16)
        return True
    except ValueError:
        return False


def validate_range(value: float, min_val: float, max_val: float) -> bool:
    """Validate value is within range"""
    return min_val <= value <= max_val


def get_file_extension(filename: str) -> str:
    """Get file extension from filename"""
    return os.path.splitext(filename)[1].lower().lstrip(".")


def is_allowed_mime_type(mime_type: str) -> bool:
    """Check if MIME type is allowed"""
    return mime_type in ALLOWED_MIME_TYPES


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def build_ffmpeg_chromakey_filter(settings: ChromaKeySettings) -> str:
    """Build FFmpeg chromakey filter string"""
    return (
        f"chromakey=0x{settings.color}:"
        f"{settings.similarity}:"
        f"{settings.blend}"
    )


def build_mask_filter_chain(settings: ChromaKeySettings) -> str:
    """Build complete FFmpeg filter chain for mask generation"""
    chromakey = build_ffmpeg_chromakey_filter(settings)
    return (
        f"{chromakey},"
        "format=yuva420p,"
        "alphaextract,"
        "geq=lum='if(gt(lum(X,Y),128),255,0)',"
        "format=yuv420p"
    )


def build_result_filter_chain(settings: ChromaKeySettings) -> str:
    """Build complete FFmpeg filter chain for result video with enhanced quality"""
    chromakey = build_ffmpeg_chromakey_filter(settings)
    # Enhanced filter: black background layer + overlay for better transparency
    return f"split[bg][fg];[bg]drawbox=c=black:t=fill[bg2];[fg]{chromakey}[fg2];[bg2][fg2]overlay=format=auto"


# ============================================================================
# Error Messages
# ============================================================================

class ErrorMessages:
    """Standard error messages"""
    
    NO_VIDEO_PROVIDED = "Either video file or video_url must be provided"
    BOTH_VIDEO_PROVIDED = "Provide either video file or video_url, not both"
    INVALID_COLOR_TYPE = "color_type must be 'green' or 'blue'"
    INVALID_FILE_TYPE = f"Invalid file type. Allowed: {', '.join(ALLOWED_MIME_TYPES)}"
    FILE_TOO_LARGE = f"File size exceeds {MAX_FILE_SIZE_MB}MB limit"
    INVALID_HEX_COLOR = "color must be a valid 6-character hex color (e.g., 00FF00)"
    INVALID_SIMILARITY = "similarity must be between 0.01 and 1.0"
    INVALID_BLEND = "blend must be between 0.0 and 1.0"
    DOWNLOAD_FAILED = "Failed to download video from URL"
    PROCESSING_FAILED = "Video processing failed"
    FILE_NOT_FOUND = "File not found"
    FFMPEG_NOT_FOUND = "FFmpeg not found. Please ensure FFmpeg is installed"


# ============================================================================
# Response Builders
# ============================================================================

def build_success_response(
    mask_url: str,
    result_url: str,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """Build success response"""
    response = {
        "success": True,
        "data": {
            "mask": mask_url,
            "result": result_url
        }
    }
    
    if session_id:
        response["session_id"] = session_id
    
    return response


def build_error_response(
    error: str,
    message: Optional[str] = None,
    status_code: int = 400
) -> Dict[str, Any]:
    """Build error response"""
    response = {
        "success": False,
        "error": error,
        "status_code": status_code
    }
    
    if message:
        response["message"] = message
    
    return response


# ============================================================================
# Logging Helpers
# ============================================================================

def log_processing_start(
    session_id: str,
    color_type: ColorType,
    source: str
) -> None:
    """Log processing start"""
    print(f"[{session_id}] Starting video processing")
    print(f"[{session_id}] Color type: {color_type.value}")
    print(f"[{session_id}] Source: {source}")


def log_processing_progress(
    session_id: str,
    phase: str,
    progress: int
) -> None:
    """Log processing progress"""
    print(f"[{session_id}] {phase} - {progress}%")


def log_processing_complete(
    session_id: str,
    mask_path: str,
    result_path: str
) -> None:
    """Log processing completion"""
    print(f"[{session_id}] Processing complete")
    print(f"[{session_id}] Mask: {mask_path}")
    print(f"[{session_id}] Result: {result_path}")


def log_error(session_id: str, error: str) -> None:
    """Log error"""
    print(f"[{session_id}] ERROR: {error}")


# ============================================================================
# Environment Configuration
# ============================================================================

def get_env_config() -> Dict[str, Any]:
    """Get environment configuration"""
    return {
        "ffmpeg_path": os.getenv("FFMPEG_PATH", "ffmpeg"),
        "max_file_size": int(os.getenv("MAX_FILE_SIZE", MAX_FILE_SIZE_BYTES)),
        "download_timeout": int(os.getenv("DOWNLOAD_TIMEOUT", DOWNLOAD_TIMEOUT_SECONDS)),
        "processing_timeout": int(os.getenv("PROCESSING_TIMEOUT", PROCESSING_TIMEOUT_SECONDS)),
        "output_dir": os.getenv("OUTPUT_DIR", "/data/outputs"),
        "temp_dir": os.getenv("TEMP_DIR", "/tmp"),
    }


# ============================================================================
# Validation Functions
# ============================================================================

def validate_process_request(
    video_data: Optional[bytes],
    video_url: Optional[str],
    color_type: str,
    color: Optional[str] = None,
    similarity: Optional[float] = None,
    blend: Optional[float] = None,
    mask_similarity: Optional[float] = None,
    mask_blend: Optional[float] = None
) -> Optional[str]:
    """
    Validate process request parameters
    Returns error message if invalid, None if valid
    """
    # Check video input
    if not video_data and not video_url:
        return ErrorMessages.NO_VIDEO_PROVIDED
    
    if video_data and video_url:
        return ErrorMessages.BOTH_VIDEO_PROVIDED
    
    # Check color type
    if color_type not in [ColorType.GREEN.value, ColorType.BLUE.value]:
        return ErrorMessages.INVALID_COLOR_TYPE
    
    # Check custom color
    if color and not validate_hex_color(color):
        return ErrorMessages.INVALID_HEX_COLOR
    
    # Check similarity values
    if similarity is not None and not validate_range(similarity, 0.01, 1.0):
        return ErrorMessages.INVALID_SIMILARITY
    
    if mask_similarity is not None and not validate_range(mask_similarity, 0.01, 1.0):
        return ErrorMessages.INVALID_SIMILARITY
    
    # Check blend values
    if blend is not None and not validate_range(blend, 0.0, 1.0):
        return ErrorMessages.INVALID_BLEND
    
    if mask_blend is not None and not validate_range(mask_blend, 0.0, 1.0):
        return ErrorMessages.INVALID_BLEND
    
    return None
