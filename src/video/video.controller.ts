import {
    Controller,
    Post,
    Body,
    UploadedFile,
    UseInterceptors,
    HttpCode,
    HttpStatus,
    BadRequestException,
    Req,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { diskStorage } from 'multer';
import * as path from 'path';
import * as os from 'os';
import { v4 as uuidv4 } from 'uuid';
import { VideoService } from './video.service';
import { ProcessVideoDto } from './dto/process-video.dto';
import { ProcessVideoResponseDto } from './dto/process-video-response.dto';

@Controller('video')
export class VideoController {
    constructor(private readonly videoService: VideoService) { }

    @Post('remove-background')
    @HttpCode(HttpStatus.OK)
    @UseInterceptors(
        FileInterceptor('video', {
            storage: diskStorage({
                destination: path.join(os.tmpdir(), 'video-uploads'),
                filename: (_req, file, cb) => {
                    const ext = path.extname(file.originalname);
                    cb(null, `${uuidv4()}${ext}`);
                },
            }),
            limits: {
                fileSize: 500 * 1024 * 1024, // 500MB
            },
            fileFilter: (_req, file, cb) => {
                const allowedMimes = [
                    'video/mp4',
                    'video/webm',
                    'video/quicktime',
                    'video/x-msvideo',
                    'video/x-matroska',
                ];
                if (allowedMimes.includes(file.mimetype)) {
                    cb(null, true);
                } else {
                    cb(
                        new BadRequestException(
                            `Invalid file type. Allowed: ${allowedMimes.join(', ')}`,
                        ),
                        false,
                    );
                }
            },
        }),
    )
    async removeBackground(
        @Body() dto: ProcessVideoDto,
        @UploadedFile() file: Express.Multer.File | undefined,
        @Req() req: { protocol: string; get: (name: string) => string | undefined },
    ): Promise<ProcessVideoResponseDto> {
        const host = req.get('host') ?? 'localhost:5173';
        const baseUrl = `${req.protocol}://${host}`;
        return this.videoService.processVideo(dto, file, baseUrl);
    }
}
