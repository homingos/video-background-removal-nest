"""
Video Background Removal API - Modal Deployment
Converts NestJS video background removal service to Modal serverless deployment
"""

import json
import os
import subprocess
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import modal

# ============================================================================
# Modal Configuration
# ============================================================================

# Define the container image with FFmpeg and required Python packages
video_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install(
        "fastapi[standard]",
        "pydantic",
        "python-multipart",
        "httpx",
        "requests",  # For signed URL uploads and API calls

    )
)

# Create persistent volume for temporary storage during processing
video_volume = modal.Volume.from_name("video-temp", create_if_missing=True)

# Create Modal app
app = modal.App("video-background-removal", image=video_image)

# ============================================================================
# Configuration Constants
# ============================================================================

# Resource allocation
GPU = None  # No GPU needed for FFmpeg processing
CPU = 4.0
MEMORY = 4096  # 4GB
TIMEOUT = 3600  # 1 hour
MIN_CONTAINERS = 0
MAX_CONTAINERS = 10
SCALEDOWN_WINDOW = 300  # 5 minutes

# ============================================================================
# Data Models
# ============================================================================

@dataclass
class ChromaKeySettings:
    """Configuration for FFmpeg chromakey filter"""
    color: str  # Hex color to remove (e.g., '00FF00' for green)
    similarity: float  # 0.01 to 1.0 - higher = more colors matched
    blend: float  # 0.0 to 1.0 - edge blending


@dataclass
class ProcessedVideoResult:
    """Result of video processing"""
    mask_path: str
    result_path: str


# Default settings (updated for better precision)
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
# FFmpeg Processing Functions
# ============================================================================

def get_settings(
    color_type: str,
    overrides: Optional[Dict[str, Any]] = None
) -> ChromaKeySettings:
    """Get settings based on color type with optional overrides"""
    base_settings = (
        GREEN_SCREEN_SETTINGS if color_type == "green" 
        else BLUE_SCREEN_SETTINGS
    )
    
    if overrides:
        return ChromaKeySettings(
            color=overrides.get("color", base_settings.color),
            similarity=overrides.get("similarity", base_settings.similarity),
            blend=overrides.get("blend", base_settings.blend)
        )
    
    return base_settings


def run_ffmpeg_command(args: list[str]) -> None:
    """Execute FFmpeg command and raise exception on failure"""
    print(f"Running: ffmpeg {' '.join(args)}")
    
    result = subprocess.run(
        ["ffmpeg"] + args,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"FFmpeg exited with code {result.returncode}: {result.stderr}")


def get_video_duration(input_path: str) -> float:
    """Get video duration in seconds using ffprobe"""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            input_path,
        ],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 5.0  # Default fallback
    return 5.0  # Default fallback


def extract_raw_frame_data(input_path: str, percentage: float) -> bytes:
    """
    Extract a single frame as raw RGB bytes from video at a specific percentage point
    
    Args:
        input_path: Path to input video
        percentage: Percentage into video (0-100)
    
    Returns:
        Bytes containing raw RGB data
    """
    # Get video duration
    duration = get_video_duration(input_path)
    timestamp = (duration * percentage) / 100
    
    args = [
        "-ss", f"{timestamp:.2f}",
        "-i", input_path,
        "-frames:v", "1",
        "-vf", "scale=320:-1",  # Downscale for faster processing
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "pipe:1"
    ]
    
    # Run ffmpeg specifically for this extraction to capture stdout bytes
    result = subprocess.run(
        ["ffmpeg"] + args,
        capture_output=True,
        check=False  # We handle errors below
    )
    
    if result.returncode != 0:
        error_msg = result.stderr.decode('utf-8', errors='ignore')
        print(f"FFmpeg extract error: {error_msg}")
        raise RuntimeError(f"FFmpeg exited with code {result.returncode}")
        
    return result.stdout


def analyze_frame_colors(frame_bytes: bytes, color_type: str) -> Optional[tuple[int, int, int]]:
    """
    Analyze raw RG bytes to find the dominant green or blue color
    
    Args:
        frame_bytes: Raw RGB bytes
        color_type: Whether to look for green or blue - used to filter relevant colors
    
    Returns:
        RGB tuple of the most dominant matching color or None
    """
    color_map = defaultdict(int)
    quantize_factor = 8  # Group similar colors
    
    # Iterate through bytes in groups of 3 (R, G, B)
    # Note: len(frame_bytes) should be divisible by 3
    for i in range(0, len(frame_bytes), 3):
        if i + 2 >= len(frame_bytes):
            break
            
        r = frame_bytes[i]
        g = frame_bytes[i + 1]
        b = frame_bytes[i + 2]
        
        # Skip very dark colors (likely shadows or black elements)
        if r < 30 and g < 30 and b < 30:
            continue
        
        # Skip very light colors (likely white elements)
        if r > 240 and g > 240 and b > 240:
            continue
        
        # Filter for expected color type to avoid detecting subject colors
        if color_type == "green":
            # Only count green-ish colors (green channel is dominant)
            if not (g > r and g > b and g > 50):
                continue
        elif color_type == "blue":
            # Only count blue-ish colors (blue channel is dominant)
            if not (b > r and b > g and b > 50):
                continue
        
        # Quantize colors to group similar ones
        qr = (r // quantize_factor) * quantize_factor
        qg = (g // quantize_factor) * quantize_factor
        qb = (b // quantize_factor) * quantize_factor
        
        color_map[(qr, qg, qb)] += 1
    
    if not color_map:
        return None
    
    # Get the most frequent matching color
    dominant_color = max(color_map.items(), key=lambda x: x[1])[0]
    return dominant_color


def find_dominant_chroma_color(input_path: str, color_type: str) -> Optional[str]:
    """
    Automatically detect the dominant chroma key color from video
    Samples multiple frames for accuracy
    
    Args:
        input_path: Path to input video
        color_type: Whether to detect green or blue screen
    
    Returns:
        Detected hex color (without #) or None if not found
    """
    print(f"Auto-detecting {color_type} screen color from video...")
    
    color_counts = defaultdict(int)
    
    try:
        # Sample frames at 5%, 25%, and 50% of video
        for percentage in [5, 25, 50]:
            try:
                frame_bytes = extract_raw_frame_data(input_path, percentage)
                
                rgb = analyze_frame_colors(frame_bytes, color_type)
                if rgb:
                    hex_color = f"{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
                    color_counts[hex_color] += 1
                    print(f"Frame at {percentage}%: detected #{hex_color}")
            except Exception as e:
                print(f"Error analyzing frame at {percentage}%: {e}")
        
        # Find the most consistent color across frames
        if color_counts:
            best_color = max(color_counts.items(), key=lambda x: x[1])[0]
            print(f"Detected {color_type} screen color: #{best_color}")
            return best_color
        else:
            print(f"Could not detect {color_type} screen color, using default")
            return None
            
    except Exception as e:
        print(f"Error extracting dominant color: {e}")
        return None


def process_chroma_key(
    input_path: str,
    output_dir: str,
    color_type: str,
    result_settings: Optional[Dict[str, Any]] = None,
    mask_settings: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    auto_detect_color: bool = False
) -> ProcessedVideoResult:
    """
    Process video to remove chroma key color
    Supports separate settings for mask and result videos
    Supports automatic color detection
    """
    detected_color = None
    
    # Auto-detect chroma key color if requested
    if auto_detect_color:
        detected_color = find_dominant_chroma_color(input_path, color_type)
        if detected_color:
            print(f"Using auto-detected color: #{detected_color}")
    
    # Priority: auto-detected color > user-provided color > default
    # Apply detected color to both result and mask settings if found
    if detected_color:
        if result_settings is None:
            result_settings = {}
        if mask_settings is None:
            mask_settings = {}
        
        # Only override if user didn't explicitly provide a color
        if "color" not in result_settings:
            result_settings["color"] = detected_color
        if "color" not in mask_settings:
            mask_settings["color"] = detected_color
    
    # Get settings for result video (applies defaults + overrides)
    result_chroma_settings = get_settings(color_type, result_settings)
    
    # Get settings for mask video - uses its own defaults + overrides
    mask_chroma_settings = get_settings(color_type, mask_settings)
    
    session_id = session_id or str(uuid.uuid4())
    mask_output = os.path.join(output_dir, f"{session_id}_mask.webm")
    result_output = os.path.join(output_dir, f"{session_id}_result.webm")
    
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    mask_chromakey_filter = (
        f"chromakey=0x{mask_chroma_settings.color}:"
        f"{mask_chroma_settings.similarity}:"
        f"{mask_chroma_settings.blend}"
    )
    result_chromakey_filter = (
        f"chromakey=0x{result_chroma_settings.color}:"
        f"{result_chroma_settings.similarity}:"
        f"{result_chroma_settings.blend}"
    )
    
    print(f"Processing video - Mask settings: {mask_chroma_settings}")
    print(f"Processing video - Result settings: {result_chroma_settings}")
    
    # Generate mask video
    print("Generating mask...")
    run_ffmpeg_command([
        "-i", input_path,
        "-vf", f"{mask_chromakey_filter},format=yuva420p,alphaextract,geq=lum='if(gt(lum(X,Y),128),255,0)',format=yuv420p",
        "-c:v", "libvpx",
        "-crf", "30",
        "-b:v", "1M",
        "-an",
        "-y",
        mask_output,
    ])
    print("Mask generated")
    
    # Generate result video with improved filter (black background + overlay)
    print("Removing background...")
    run_ffmpeg_command([
        "-i", input_path,
        "-vf", f"split[bg][fg];[bg]drawbox=c=black:t=fill[bg2];[fg]{result_chromakey_filter}[fg2];[bg2][fg2]overlay=format=auto",
        "-c:v", "libvpx",
        "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0",
        "-crf", "30",
        "-b:v", "2M",
        "-c:a", "libvorbis",
        "-y",
        result_output,
    ])
    print("Background removed")
    
    print(f"Processing complete. Mask: {mask_output}, Result: {result_output}")
    
    return ProcessedVideoResult(
        mask_path=mask_output,
        result_path=result_output
    )


async def download_video(url: str, output_path: str) -> None:
    """Download video from URL to specified path"""
    import httpx
    
    print(f"Downloading video from: {url}")
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        
        with open(output_path, "wb") as f:
            f.write(response.content)
    
    print(f"Downloaded video to: {output_path}")


# ============================================================================
# GCP Storage Functions (Using Internal API)
# ============================================================================

def get_signed_url_from_api(filename: str, content_type: str = "") -> tuple[str, str]:
    """
    Get signed URL from internal resource API
    
    Args:
        filename: Name of the file to upload
        content_type: MIME type of the file (optional)
    
    Returns:
        Tuple of (signed_url, resource_url)
    """
    import requests
    
    api_url = "https://fi.production.flamapis.com/resource-svc/api/v1/resources"
    
    print(f"Requesting signed URL for file: {filename}")
    print(f"Content type for API: {content_type if content_type else '(empty)'}")
    
    try:
        payload = {
            "file_name": filename,
            "type": content_type  # Pass content type to API
        }
        headers = {
            "Content-Type": "application/json"
        }
        
        response = requests.post(api_url, json=payload, headers=headers)
        response.raise_for_status()
        
        api_response = response.json()
        
        # Check for API errors
        if api_response.get("status") != 200 or api_response.get("error", False):
            error_msg = api_response.get("error", "Unknown error")
            raise ConnectionError(f"API returned error: {error_msg}")
        
        data = api_response.get("data", {})
        signed_url = data.get("signed_url")
        resource_url = data.get("resource_url")
        
        if not signed_url or not resource_url:
            raise ValueError("API response missing signed_url or resource_url")
        
        print(f"Received signed URL from API")
        print(f"Resource URL: {resource_url}")
        
        return signed_url, resource_url
        
    except requests.exceptions.RequestException as e:
        print(f"Failed to get signed URL from API: {e}")
        raise ConnectionError(f"Failed to get signed URL from API: {e}") from e


def upload_file_with_signed_url(
    local_path: str,
    signed_url: str,
    content_type: str = "video/mp4"
) -> None:
    """
    Upload file to storage using signed URL
    
    Args:
        local_path: Path to local file
        signed_url: Pre-signed URL for upload
        content_type: MIME type of the file (must match what was used to generate signed URL)
    """
    import requests
    
    print(f"Uploading file: {local_path}")
    print(f"Content-Type: {content_type}")
    
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"File not found at: {local_path}")
    
    try:
        # Read file content
        with open(local_path, 'rb') as f:
            file_content = f.read()
        
        file_size = len(file_content)
        print(f"File size: {file_size} bytes ({file_size / 1024 / 1024:.2f} MB)")
        
        # Upload with Content-Type header matching the signed URL
        upload_headers = {
            'Content-Type': content_type
        }
        
        print(f"Uploading to GCP with Content-Type: {content_type}")
        upload_response = requests.put(signed_url, data=file_content, headers=upload_headers)
        upload_response.raise_for_status()
        
        print(f"Upload successful (status: {upload_response.status_code})")
        
    except requests.exceptions.RequestException as e:
        print(f"Failed to upload file to signed URL: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response headers: {dict(e.response.headers)}")
            print(f"Response body: {e.response.text[:500]}")
        raise ConnectionError(f"Failed to upload file to signed URL: {e}") from e
    except Exception as e:
        print(f"Unexpected error during upload: {e}")
        raise ConnectionError(f"Unexpected error during upload: {e}") from e


def upload_to_gcp(
    local_path: str,
    filename: str
) -> str:
    """
    Upload file to GCP Storage using internal API for signed URLs
    
    Args:
        local_path: Path to local file
        filename: Name for the uploaded file
    
    Returns:
        Public resource URL to the uploaded file
    """
    print(f"Uploading {local_path} as {filename}")
    
    # Determine content type based on file extension
    ext = Path(local_path).suffix.lower()
    content_type_map = {
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
        '.mov': 'video/quicktime',
        '.avi': 'video/x-msvideo',
        '.mkv': 'video/x-matroska',
    }
    content_type = content_type_map.get(ext, 'video/mp4')
    
    print(f"Detected content type: {content_type}")
    
    # Step 1: Get signed URL from internal API (pass content type)
    signed_url, resource_url = get_signed_url_from_api(filename, content_type)
    
    # Step 2: Upload file using signed URL with matching content type
    upload_file_with_signed_url(
        local_path=local_path,
        signed_url=signed_url,
        content_type=content_type
    )
    
    print(f"Uploaded successfully: {resource_url}")
    
    return resource_url


# ============================================================================
# Modal Functions
# ============================================================================

@app.function(
    image=video_image,
    volumes={"/data": video_volume},
    timeout=TIMEOUT,
    memory=MEMORY,
    cpu=CPU,
)
async def process_video_background_removal(
    video_url: Optional[str] = None,
    video_data: Optional[bytes] = None,
    video_filename: Optional[str] = None,
    color_type: str = "green",
    color: Optional[str] = None,
    similarity: Optional[float] = None,
    blend: Optional[float] = None,
    mask_similarity: Optional[float] = None,
    mask_blend: Optional[float] = None,
    auto_detect_color: bool = False,
) -> Dict[str, Any]:
    """
    Process video to remove background
    
    Args:
        video_url: URL to download video from
        video_data: Raw video bytes (for file uploads)
        video_filename: Original filename (for file uploads)
        color_type: "green" or "blue"
        color: Custom hex color (e.g., "00FF00")
        similarity: Result video color match (0.01-1.0)
        blend: Result video edge blend (0.0-1.0)
        mask_similarity: Mask video color match (0.01-1.0)
        mask_blend: Mask video edge blend (0.0-1.0)
        auto_detect_color: If True, automatically detect chroma key color from video
    
    Returns:
        Dictionary with mask and result URLs from GCP
    """
    session_id = str(uuid.uuid4())
    
    # Create temp directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Get input file
        if video_data:
            # File upload case
            ext = Path(video_filename or "video.mp4").suffix or ".mp4"
            input_path = os.path.join(temp_dir, f"{session_id}_input{ext}")
            with open(input_path, "wb") as f:
                f.write(video_data)
            print(f"Processing uploaded file: {video_filename}")
        elif video_url:
            # URL download case
            ext = Path(video_url).suffix or ".mp4"
            input_path = os.path.join(temp_dir, f"{session_id}_input{ext}")
            await download_video(video_url, input_path)
            print(f"Processing video from URL: {video_url}")
        else:
            raise ValueError("Either video_url or video_data must be provided")
        
        # Build settings for result video
        result_settings = {}
        if color:
            result_settings["color"] = color.upper()
        if similarity is not None:
            result_settings["similarity"] = similarity
        if blend is not None:
            result_settings["blend"] = blend
        
        # Build settings for mask video
        mask_settings = {}
        if color:
            mask_settings["color"] = color.upper()
        if mask_similarity is not None:
            mask_settings["similarity"] = mask_similarity
        if mask_blend is not None:
            mask_settings["blend"] = mask_blend
        
        # Process video (output to temp directory)
        output_dir = os.path.join(temp_dir, "outputs")
        os.makedirs(output_dir, exist_ok=True)
        
        result = process_chroma_key(
            input_path=input_path,
            output_dir=output_dir,
            color_type=color_type,
            result_settings=result_settings if result_settings else None,
            mask_settings=mask_settings if mask_settings else None,
            session_id=session_id,
            auto_detect_color=auto_detect_color
        )
        
        # Upload processed videos to GCP using internal API
        mask_filename = os.path.basename(result.mask_path)
        result_filename = os.path.basename(result.result_path)
        
        print(f"Uploading mask: {mask_filename}")
        mask_url = upload_to_gcp(
            local_path=result.mask_path,
            filename=mask_filename
        )
        
        print(f"Uploading result: {result_filename}")
        result_url = upload_to_gcp(
            local_path=result.result_path,
            filename=result_filename
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "mask_url": mask_url,
            "result_url": result_url,
            "mask_filename": mask_filename,
            "result_filename": result_filename,
        }


# Note: Signed URL generation is now handled by the internal API
# The get_signed_url_from_api() function is used for uploads


# ============================================================================
# FastAPI Web Interface
# ============================================================================

@app.function(image=video_image)
@modal.asgi_app()
def fastapi_app():
    import io
    from typing import Optional

    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, StreamingResponse
    from pydantic import BaseModel, Field, validator
    
    web_app = FastAPI(
        title="Video Background Removal API",
        description="Remove green/blue screen backgrounds from videos",
        version="1.0.0"
    )
    
    # Enable CORS
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Request/Response models
    class ProcessVideoRequest(BaseModel):
        video_url: Optional[str] = None
        color_type: str = Field(..., pattern="^(green|blue)$")
        color: Optional[str] = Field(None, pattern="^[0-9A-Fa-f]{6}$")
        similarity: Optional[float] = Field(None, ge=0.01, le=1.0)
        blend: Optional[float] = Field(None, ge=0.0, le=1.0)
        mask_similarity: Optional[float] = Field(None, ge=0.01, le=1.0)
        mask_blend: Optional[float] = Field(None, ge=0.0, le=1.0)
        auto_detect_color: Optional[bool] = False
        
        @validator("video_url")
        def validate_video_url(cls, v):
            if v and not v.startswith(("http://", "https://", "s3://", "gs://")):
                raise ValueError("Invalid video URL")
            return v
    
    class ProcessVideoResponse(BaseModel):
        success: bool
        data: Dict[str, str]
    
    @web_app.get("/")
    async def root():
        return {
            "service": "Video Background Removal API",
            "version": "1.0.0",
            "status": "running"
        }
    
    @web_app.get("/health")
    async def health():
        return {"status": "healthy"}
    
    @web_app.post("/api/v1/video/remove-background", response_model=ProcessVideoResponse)
    async def remove_background_multipart(
        video: Optional[UploadFile] = File(None),
        color_type: str = Form(...),
        video_url: Optional[str] = Form(None),
        color: Optional[str] = Form(None),
        similarity: Optional[float] = Form(None),
        blend: Optional[float] = Form(None),
        mask_similarity: Optional[float] = Form(None),
        mask_blend: Optional[float] = Form(None),
        auto_detect_color: Optional[bool] = Form(False),
    ):
        """
        Remove background from video (multipart/form-data)
        
        Either 'video' file or 'video_url' must be provided
        """
        # Validate input
        if not video and not video_url:
            raise HTTPException(
                status_code=400,
                detail="Either video file or video_url must be provided"
            )
        
        if video and video_url:
            raise HTTPException(
                status_code=400,
                detail="Provide either video file or video_url, not both"
            )
        
        # Validate color_type
        if color_type not in ["green", "blue"]:
            raise HTTPException(
                status_code=400,
                detail="color_type must be 'green' or 'blue'"
            )
        
        # Validate file size and type
        if video:
            # Check file type
            allowed_types = [
                "video/mp4",
                "video/webm",
                "video/quicktime",
                "video/x-msvideo",
                "video/x-matroska",
            ]
            if video.content_type not in allowed_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
                )
            
            # Read video data
            video_data = await video.read()
            video_filename = video.filename
            
            # Check size (500MB limit)
            if len(video_data) > 500 * 1024 * 1024:
                raise HTTPException(
                    status_code=400,
                    detail="File size exceeds 500MB limit"
                )
        else:
            video_data = None
            video_filename = None
        
        try:
            # Process video using Modal function
            result = await process_video_background_removal.remote.aio(
                video_url=video_url,
                video_data=video_data,
                video_filename=video_filename,
                color_type=color_type,
                color=color,
                similarity=similarity,
                blend=blend,
                mask_similarity=mask_similarity,
                mask_blend=mask_blend,
                auto_detect_color=auto_detect_color,
            )
            
            # Return GCP public URLs directly
            return ProcessVideoResponse(
                success=True,
                data={
                    "mask": result["mask_url"],
                    "result": result["result_url"],
                    "session_id": result["session_id"],
                }
            )
        
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Video processing failed: {str(e)}"
            )
    
    @web_app.post("/api/v1/video/remove-background-json", response_model=ProcessVideoResponse)
    async def remove_background_json(request: ProcessVideoRequest):
        """
        Remove background from video (application/json)
        
        Requires video_url in request body
        """
        if not request.video_url:
            raise HTTPException(
                status_code=400,
                detail="video_url is required for JSON requests"
            )
        
        try:
            # Process video using Modal function
            result = await process_video_background_removal.remote.aio(
                video_url=request.video_url,
                color_type=request.color_type,
                color=request.color,
                similarity=request.similarity,
                blend=request.blend,
                mask_similarity=request.mask_similarity,
                mask_blend=request.mask_blend,
                auto_detect_color=request.auto_detect_color,
            )
            
            # Return GCP public URLs directly
            return ProcessVideoResponse(
                success=True,
                data={
                    "mask": result["mask_url"],
                    "result": result["result_url"],
                    "session_id": result["session_id"],
                }
            )
        
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Video processing failed: {str(e)}"
            )
    
    return web_app


# ============================================================================
# Local Testing
# ============================================================================

@app.local_entrypoint()
def main():
    """Local testing entrypoint"""
    print("Video Background Removal API - Modal Deployment")
    print("=" * 60)
    print("\nTo deploy this app, run:")
    print("  modal deploy modal_app.py")
    print("\nTo test locally:")
    print("  modal serve modal_app.py")
    print("\nExample API call:")
    print('  curl -X POST https://your-app.modal.run/api/v1/video/remove-background \\')
    print('    -F "video=@video.mp4" \\')
    print('    -F "color_type=green"')
