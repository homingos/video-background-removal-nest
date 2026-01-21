import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { spawn } from 'child_process';
import * as fs from 'fs/promises';
import * as path from 'path';
import { v4 as uuidv4 } from 'uuid';
import {
    ChromaKeySettings,
    GREEN_SCREEN_SETTINGS,
    BLUE_SCREEN_SETTINGS,
    ProcessedVideoResult,
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
            '-vf', `${resultChromakeyFilter},format=rgba`,
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
