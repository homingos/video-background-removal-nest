/**
 * ChromaKey Settings Interface
 * Configuration for FFmpeg chromakey filter
 */
export interface ChromaKeySettings {
    /** Hex color to remove (e.g., '00FF00' for green) */
    color: string;
    /** 0.01 to 1.0 - higher = more colors matched */
    similarity: number;
    /** 0.0 to 1.0 - edge blending */
    blend: number;
}

/**
 * RGB color representation
 */
export interface RGB {
    r: number;
    g: number;
    b: number;
}

/**
 * Convert RGB to hex string (without #)
 */
export function rgbToHex(rgb: RGB): string {
    const toHex = (n: number) => Math.min(255, Math.max(0, n)).toString(16).padStart(2, '0');
    return `${toHex(rgb.r)}${toHex(rgb.g)}${toHex(rgb.b)}`.toUpperCase();
}

/**
 * Default settings for green screen
 * Lower similarity = more precise, avoids green spill on clothes
 */
export const GREEN_SCREEN_SETTINGS: ChromaKeySettings = {
    color: '00FF00',
    similarity: 0.01,
    blend: 0.08,
};

/**
 * Default settings for blue screen
 */
export const BLUE_SCREEN_SETTINGS: ChromaKeySettings = {
    color: '0000FF',
    similarity: 0.3,
    blend: 0.1,
};

/**
 * Processed video result
 */
export interface ProcessedVideoResult {
    /** Path to the mask video file */
    maskPath: string;
    /** Path to the result video with transparent background */
    resultPath: string;
}
