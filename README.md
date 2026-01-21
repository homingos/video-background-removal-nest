# Video Background Removal API

A NestJS API for removing green/blue screen backgrounds from videos using FFmpeg.

## Prerequisites

- **Node.js** >= 18
- **FFmpeg** installed on your system
  ```bash
  # macOS
  brew install ffmpeg
  
  # Ubuntu/Debian
  sudo apt install ffmpeg
  
  # Windows
  choco install ffmpeg
  ```

## Installation

```bash
npm install
```

## Environment Configuration

Copy `.env.example` to `.env` and configure:

```env
PORT=5173
FFMPEG_PATH=ffmpeg
MAX_FILE_SIZE=524288000
OUTPUT_DIR=public/outputs
TEMP_DIR=temp
```

## Running the API

```bash
# Development
npm run start:dev

# Production
npm run build
npm run start:prod
```

Server starts at `http://localhost:5173`

---

## API Reference

### Remove Background

**Endpoint:** `POST /api/v1/video/remove-background`

**Content-Type:** `multipart/form-data` or `application/json`

---

### Request Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `video` | File | ✅* | - | Video file upload |
| `videoUrl` | string | ✅* | - | URL to video (S3, GCP, HTTP) |
| `colorType` | string | ✅ | - | `"green"` or `"blue"` |
| `color` | string | ❌ | auto | Custom hex color (e.g., `"00FF00"`) |
| `similarity` | number | ❌ | 0.25 | Result video: color match (0.01-1.0) |
| `blend` | number | ❌ | 0.1 | Result video: edge blend (0.0-1.0) |
| `mask_similarity` | number | ❌ | 0.25 | Mask video: color match (0.01-1.0) |
| `mask_blend` | number | ❌ | 0.1 | Mask video: edge blend (0.0-1.0) |

> **Note:** *Either `video` OR `videoUrl` must be provided (one is required).

---

### Response

**Success (200 OK):**

```json
{
  "success": true,
  "data": {
    "mask": "http://localhost:5173/outputs/uuid_mask.webm",
    "result": "http://localhost:5173/outputs/uuid_result.webm"
  }
}
```

**Error (400 Bad Request):**

```json
{
  "success": false,
  "error": "Either video file or videoUrl must be provided"
}
```

---

### Examples

#### File Upload (Form Data)

```bash
curl -X POST http://localhost:5173/api/v1/video/remove-background \
  -F "video=@video.mp4" \
  -F "colorType=green" \
  -F "similarity=0.3" \
  -F "blend=0.1"
```

#### Video URL (JSON)

```bash
curl -X POST http://localhost:5173/api/v1/video/remove-background \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://s3.amazonaws.com/bucket/video.mp4",
    "colorType": "green",
    "similarity": 0.3,
    "blend": 0.15
  }'
```

#### With Separate Mask Settings

```bash
curl -X POST http://localhost:5173/api/v1/video/remove-background \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://example.com/video.mp4",
    "similarity": 0.3,
    "blend": 0.2,
    "mask_similarity": 0.4,
    "mask_blend": 0.15
  }'
```

---

### Understanding Parameters

#### `similarity` (0.01 - 1.0)
Controls how strictly the color must match to be removed:
- **Low (0.1-0.2):** Only removes pixels very close to target color
- **Medium (0.25-0.4):** Balanced - good starting point
- **High (0.5+):** Removes wider range of colors (may affect subject)

#### `blend` (0.0 - 1.0)
Controls edge smoothness:
- **0.0:** Hard, sharp edges
- **0.1-0.2:** Slight feathering (recommended)
- **0.3+:** Softer edges for hair/fine details

---

## Project Structure

```
src/
├── main.ts                 # Application entry
├── app.module.ts           # Root module
├── ffmpeg/
│   ├── ffmpeg.module.ts
│   └── ffmpeg.service.ts   # FFmpeg execution
└── video/
    ├── video.module.ts
    ├── video.controller.ts # REST endpoints
    ├── video.service.ts    # Business logic
    ├── dto/                # Request/Response DTOs
    └── interfaces/         # Type definitions
```
