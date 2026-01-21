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
 * Default settings for green screen
 */
export const GREEN_SCREEN_SETTINGS: ChromaKeySettings = {
    color: '00FF00',
    similarity: 0.25,
    blend: 0.1,
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
