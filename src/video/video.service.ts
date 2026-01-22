import { Injectable, Logger, BadRequestException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import * as fs from 'fs/promises';
import * as path from 'path';
import { v4 as uuidv4 } from 'uuid';
import axios from 'axios';
import { FfmpegService } from '../ffmpeg/ffmpeg.service';
import { ProcessVideoDto } from './dto/process-video.dto';
import { ProcessVideoResponseDto } from './dto/process-video-response.dto';
import { ChromaKeySettings } from './interfaces/chroma-key-settings.interface';

@Injectable()
export class VideoService {
    private readonly logger = new Logger(VideoService.name);
    private readonly outputDir: string;
    private readonly tempDir: string;

    constructor(
        private readonly ffmpegService: FfmpegService,
        private readonly configService: ConfigService,
    ) {
        // Get directories from environment or use defaults
        this.outputDir = path.join(
            process.cwd(),
            this.configService.get<string>('OUTPUT_DIR') ?? 'public/outputs'
        );
        this.tempDir = path.join(
            process.cwd(),
            this.configService.get<string>('TEMP_DIR') ?? 'temp'
        );

        // Ensure output directory exists on startup
        this.ensureOutputDir();
    }

    private async ensureOutputDir(): Promise<void> {
        try {
            await fs.mkdir(this.outputDir, { recursive: true });
            this.logger.log(`Output directory ready: ${this.outputDir}`);
        } catch (error) {
            this.logger.error(`Failed to create output directory: ${error}`);
        }
    }

    /**
     * Process video to remove background
     * Returns URLs to the processed video files
     */
    async processVideo(
        dto: ProcessVideoDto,
        uploadedFile?: Express.Multer.File,
        baseUrl?: string,
    ): Promise<ProcessVideoResponseDto> {
        const sessionId = uuidv4();
        let inputPath: string | undefined;
        let tempInputPath: string | undefined;

        try {
            // Get input file path (from upload or URL)
            if (uploadedFile) {
                inputPath = uploadedFile.path;
                this.logger.log(`Processing uploaded file: ${uploadedFile.originalname}`);
            } else if (dto.videoUrl) {
                tempInputPath = await this.downloadVideo(dto.videoUrl, sessionId);
                inputPath = tempInputPath;
                this.logger.log(`Processing video from URL: ${dto.videoUrl}`);
            } else {
                throw new BadRequestException(
                    'Either video file or videoUrl must be provided',
                );
            }

            // Auto-detect chroma key color if requested
            let detectedColor: string | undefined;
            if (dto.autoDetectColor) {
                const detected = await this.ffmpegService.findDominantChromaColor(
                    inputPath,
                    dto.colorType,
                );
                if (detected) {
                    detectedColor = detected;
                    this.logger.log(`Using auto-detected color: #${detectedColor}`);
                }
            }

            // Build settings for result (transparent) video - only override what's provided
            const resultSettings: Partial<ChromaKeySettings> = {};

            // Priority: auto-detected color > user-provided color > default
            if (detectedColor) {
                resultSettings.color = detectedColor;
            } else if (dto.color) {
                resultSettings.color = dto.color.toUpperCase();
            }

            if (dto.similarity !== undefined) {
                resultSettings.similarity = dto.similarity;
            }
            if (dto.blend !== undefined) {
                resultSettings.blend = dto.blend;
            }

            // Build settings for mask video - INDEPENDENT from result settings
            // Uses its own defaults unless mask_* params are explicitly provided
            const maskSettings: Partial<ChromaKeySettings> = {};

            // Priority: auto-detected color > user-provided color > default
            if (detectedColor) {
                maskSettings.color = detectedColor;
            } else if (dto.color) {
                maskSettings.color = dto.color.toUpperCase();
            }

            if (dto.mask_similarity !== undefined) {
                maskSettings.similarity = dto.mask_similarity;
            }
            // Note: if mask_similarity is NOT set, mask uses default (0.25 for green, 0.3 for blue)

            if (dto.mask_blend !== undefined) {
                maskSettings.blend = dto.mask_blend;
            }
            // Note: if mask_blend is NOT set, mask uses default (0.1)

            // Process video - ALWAYS pass both settings (they use their own defaults)
            const result = await this.ffmpegService.processChromaKey({
                inputPath,
                outputDir: this.outputDir,
                colorType: dto.colorType,
                resultSettings,
                maskSettings, // Always pass mask settings, ffmpegService will apply its own defaults
                sessionId,
            });

            // Build URLs for the output files
            const maskFilename = path.basename(result.maskPath);
            const resultFilename = path.basename(result.resultPath);

            const port = this.configService.get<string>('PORT') ?? '3000';
            const host = baseUrl ?? `http://localhost:${port}`;

            const response: ProcessVideoResponseDto = {
                success: true,
                data: {
                    mask: `${host}/outputs/${maskFilename}`,
                    result: `${host}/outputs/${resultFilename}`,
                },
            };

            return response;
        } finally {
            // Clean up temp input file if downloaded
            if (tempInputPath) {
                try {
                    await fs.unlink(tempInputPath);
                } catch {
                    // Ignore cleanup errors
                }
            }

            // Clean up uploaded file if exists
            if (uploadedFile?.path) {
                try {
                    await fs.unlink(uploadedFile.path);
                } catch {
                    // Ignore cleanup errors
                }
            }
        }
    }

    /**
     * Download video from URL to temp file
     */
    private async downloadVideo(url: string, sessionId: string): Promise<string> {
        this.logger.log(`Downloading video from: ${url}`);

        await fs.mkdir(this.tempDir, { recursive: true });

        const urlPath = new URL(url).pathname;
        const ext = path.extname(urlPath) || '.mp4';
        const outputPath = path.join(this.tempDir, `${sessionId}_input${ext}`);

        try {
            const response = await axios({
                method: 'GET',
                url: url,
                responseType: 'arraybuffer',
                timeout: 300000,
                maxContentLength: 500 * 1024 * 1024,
            });

            await fs.writeFile(outputPath, response.data);
            this.logger.log(`Downloaded video to: ${outputPath}`);
            return outputPath;
        } catch (error) {
            const message =
                error instanceof Error ? error.message : 'Unknown download error';
            this.logger.error(`Failed to download video: ${message}`);
            throw new BadRequestException(`Failed to download video from URL: ${message}`);
        }
    }
}
