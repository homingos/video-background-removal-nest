import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { spawn } from 'child_process';
import * as fs from 'fs/promises';
import * as path from 'path';
import * as os from 'os';
import { v4 as uuidv4 } from 'uuid';
import * as sharp from 'sharp';
import {
    ChromaKeySettings,
    GREEN_SCREEN_SETTINGS,
    BLUE_SCREEN_SETTINGS,
    ProcessedVideoResult,
    RGB,
    rgbToHex,
} from '../video/interfaces/chroma-key-settings.interface';

export interface ProcessChromaKeyOptions {
    inputPath: string;
    outputDir: string;
    colorType: 'green' | 'blue';
    /** Settings for transparent result video */
    resultSettings?: Partial<ChromaKeySettings>;
    /** Settings for mask video (uses its own defaults if not provided) */
    maskSettings?: Partial<ChromaKeySettings>;
    sessionId?: string;
    onProgress?: (progress: number, phase: string) => void;
}

@Injectable()
export class FfmpegService implements OnModuleInit {
    private readonly logger = new Logger(FfmpegService.name);
    private readonly ffmpegPath: string;

    constructor(private readonly configService: ConfigService) {
        this.ffmpegPath = this.configService.get<string>('FFMPEG_PATH') ?? 'ffmpeg';
    }

    async onModuleInit(): Promise<void> {
        await this.checkFfmpegInstallation();
    }

    /**
     * Check if FFmpeg is available on the system
     */
    private async checkFfmpegInstallation(): Promise<void> {
        return new Promise((resolve, reject) => {
            const process = spawn(this.ffmpegPath, ['-version']);
            let output = '';

            process.stdout.on('data', (data: Buffer) => {
                output += data.toString();
            });

            process.on('close', (code) => {
                if (code === 0) {
                    const versionMatch = output.match(/ffmpeg version (\S+)/);
                    this.logger.log(`FFmpeg found: ${versionMatch?.[1] ?? 'unknown version'}`);
                    resolve();
                } else {
                    const error = new Error(
                        'FFmpeg not found. Please install FFmpeg on your system.',
                    );
                    this.logger.error(error.message);
                    reject(error);
                }
            });

            process.on('error', () => {
                const error = new Error(
                    'FFmpeg not found. Please install FFmpeg on your system.',
                );
                this.logger.error(error.message);
                reject(error);
            });
        });
    }

    /**
     * Extract a single frame from video at a specific percentage point
     * @param inputPath Path to input video
     * @param percentage Percentage into video (0-100)
     * @returns Path to extracted frame PNG
     */
    private async extractFrame(inputPath: string, percentage: number): Promise<string> {
        const tempDir = os.tmpdir();
        const framePath = path.join(tempDir, `frame_${uuidv4()}.png`);

        // Get video duration first
        const duration = await this.getVideoDuration(inputPath);
        const timestamp = (duration * percentage) / 100;

        await this.runFfmpegCommand([
            '-ss', timestamp.toFixed(2),
            '-i', inputPath,
            '-frames:v', '1',
            '-y',
            framePath,
        ]);

        return framePath;
    }

    /**
     * Get video duration in seconds
     */
    private async getVideoDuration(inputPath: string): Promise<number> {
        return new Promise((resolve, reject) => {
            const ffprobe = spawn('ffprobe', [
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'csv=p=0',
                inputPath,
            ]);

            let output = '';
            ffprobe.stdout.on('data', (data: Buffer) => {
                output += data.toString();
            });

            ffprobe.on('close', (code) => {
                if (code === 0) {
                    const duration = parseFloat(output.trim()) || 5;
                    resolve(duration);
                } else {
                    // Default to 5 seconds if we can't get duration
                    resolve(5);
                }
            });

            ffprobe.on('error', () => {
                resolve(5);
            });
        });
    }

    /**
     * Analyze a frame image to find the dominant green or blue color
     * @param framePath Path to the frame PNG
     * @param colorType Whether to look for green or blue (used for logging only)
     * @returns The most dominant color in the frame
     */
    private async analyzeFrameColors(framePath: string, colorType: 'green' | 'blue'): Promise<RGB | null> {
        const { data, info } = await sharp.default(framePath)
            .raw()
            .toBuffer({ resolveWithObject: true });

        const colorMap = new Map<string, { rgb: RGB; count: number }>();
        const quantizeFactor = 8; // Group similar colors

        for (let i = 0; i < data.length; i += info.channels) {
            const r = data[i];
            const g = data[i + 1];
            const b = data[i + 2];

            // Skip very dark colors (likely shadows or black elements)
            if (r < 30 && g < 30 && b < 30) continue;

            // Skip very light colors (likely white elements)
            if (r > 240 && g > 240 && b > 240) continue;

            // Quantize colors to group similar ones
            const qR = Math.floor(r / quantizeFactor) * quantizeFactor;
            const qG = Math.floor(g / quantizeFactor) * quantizeFactor;
            const qB = Math.floor(b / quantizeFactor) * quantizeFactor;
            const key = `${qR},${qG},${qB}`;

            if (colorMap.has(key)) {
                colorMap.get(key)!.count++;
            } else {
                colorMap.set(key, { rgb: { r: qR, g: qG, b: qB }, count: 1 });
            }
        }

        // Get the most frequent matching color
        const sorted = Array.from(colorMap.values()).sort((a, b) => b.count - a.count);
        return sorted[0]?.rgb ?? null;
    }

    /**
     * Automatically detect the dominant chroma key color from video
     * Samples multiple frames for accuracy
     * @param inputPath Path to input video
     * @param colorType Whether to detect green or blue screen
     * @returns Detected hex color or null if not found
     */
    async findDominantChromaColor(inputPath: string, colorType: 'green' | 'blue'): Promise<string | null> {
        this.logger.log(`Auto-detecting ${colorType} screen color from video...`);

        const framePaths: string[] = [];
        const colorCounts = new Map<string, number>();

        try {
            // Sample frames at 5%, 25%, and 50% of video
            for (const percentage of [5, 25, 50]) {
                const framePath = await this.extractFrame(inputPath, percentage);
                framePaths.push(framePath);

                const color = await this.analyzeFrameColors(framePath, colorType);
                if (color) {
                    const hex = rgbToHex(color);
                    colorCounts.set(hex, (colorCounts.get(hex) ?? 0) + 1);
                    this.logger.debug(`Frame at ${percentage}%: detected ${hex}`);
                }
            }

            // Find the most consistent color across frames
            let bestColor: string | null = null;
            let bestCount = 0;
            for (const [hex, count] of colorCounts) {
                if (count > bestCount) {
                    bestCount = count;
                    bestColor = hex;
                }
            }

            if (bestColor) {
                this.logger.log(`Detected ${colorType} screen color: #${bestColor}`);
            } else {
                this.logger.warn(`Could not detect ${colorType} screen color, using default`);
            }

            return bestColor;
        } finally {
            // Cleanup temp frames
            for (const framePath of framePaths) {
                try {
                    await fs.unlink(framePath);
                } catch {
                    // Ignore cleanup errors
                }
            }
        }
    }

    /**
     * Get settings based on color type with optional overrides
     */
    getSettings(
        colorType: 'green' | 'blue',
        overrides?: Partial<ChromaKeySettings>,
    ): ChromaKeySettings {
        const baseSettings =
            colorType === 'green' ? GREEN_SCREEN_SETTINGS : BLUE_SCREEN_SETTINGS;
        return { ...baseSettings, ...overrides };
    }

    /**
     * Process video to remove chroma key color
     * Supports separate settings for mask and result videos
     * Both use their own default values independently
     */
    async processChromaKey(options: ProcessChromaKeyOptions): Promise<ProcessedVideoResult> {
        const {
            inputPath,
            outputDir,
            colorType,
            resultSettings,
            maskSettings,
            sessionId,
            onProgress,
        } = options;

        // Get settings for result video (applies defaults + overrides)
        const resultChromaSettings = this.getSettings(colorType, resultSettings);

        // Get settings for mask video - ALWAYS use its own defaults + overrides
        // This ensures mask uses default values (0.25, 0.1) unless mask_* params are explicitly set
        const maskChromaSettings = this.getSettings(colorType, maskSettings);

        const id = sessionId ?? uuidv4();
        const maskOutput = path.join(outputDir, `${id}_mask.webm`);
        const resultOutput = path.join(outputDir, `${id}_result.webm`);

        // Ensure output directory exists
        await fs.mkdir(outputDir, { recursive: true });

        const maskChromakeyFilter = `chromakey=0x${maskChromaSettings.color}:${maskChromaSettings.similarity}:${maskChromaSettings.blend}`;
        const resultChromakeyFilter = `chromakey=0x${resultChromaSettings.color}:${resultChromaSettings.similarity}:${resultChromaSettings.blend}`;

        this.logger.log(`Processing video - Mask settings: ${JSON.stringify(maskChromaSettings)}`);
        this.logger.log(`Processing video - Result settings: ${JSON.stringify(resultChromaSettings)}`);

        // Generate mask video
        onProgress?.(20, 'Generating mask...');
        await this.runFfmpegCommand([
            '-i', inputPath,
            '-vf', `${maskChromakeyFilter},format=yuva420p,alphaextract,geq=lum='if(gt(lum(X,Y),128),255,0)',format=yuv420p`,
            '-c:v', 'libvpx',
            '-crf', '30',
            '-b:v', '1M',
            '-an',
            '-y',
            maskOutput,
        ]);

        onProgress?.(50, 'Mask generated');

        // Generate result video with transparent background (with audio)
        onProgress?.(60, 'Removing background...');
        await this.runFfmpegCommand([
            '-i', inputPath,
            '-vf', `split[bg][fg];[bg]drawbox=c=black:t=fill[bg2];[fg]${resultChromakeyFilter}[fg2];[bg2][fg2]overlay=format=auto`,
            '-c:v', 'libvpx',
            '-pix_fmt', 'yuva420p',
            '-auto-alt-ref', '0',
            '-crf', '30',
            '-b:v', '2M',
            '-c:a', 'libvorbis',
            '-y',
            resultOutput,
        ]);

        onProgress?.(90, 'Background removed');

        this.logger.log(`Processing complete. Mask: ${maskOutput}, Result: ${resultOutput}`);

        return {
            maskPath: maskOutput,
            resultPath: resultOutput,
        };
    }

    /**
     * Execute FFmpeg command and return a promise
     */
    private runFfmpegCommand(args: string[]): Promise<void> {
        return new Promise((resolve, reject) => {
            this.logger.debug(`Running: ffmpeg ${args.join(' ')}`);

            const process = spawn(this.ffmpegPath, args);
            let stderr = '';

            process.stderr.on('data', (data: Buffer) => {
                stderr += data.toString();
            });

            process.on('close', (code) => {
                if (code === 0) {
                    resolve();
                } else {
                    this.logger.error(`FFmpeg error: ${stderr}`);
                    reject(new Error(`FFmpeg exited with code ${code}: ${stderr}`));
                }
            });

            process.on('error', (error) => {
                reject(error);
            });
        });
    }
}
