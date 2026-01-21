import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { ServeStaticModule } from '@nestjs/serve-static';
import { join } from 'path';
import { FfmpegModule } from './ffmpeg/ffmpeg.module';
import { VideoModule } from './video/video.module';

@Module({
  imports: [
    // Load environment variables
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: '.env',
    }),
    // Serve static files from /public directory
    ServeStaticModule.forRoot({
      rootPath: join(process.cwd(), process.env.OUTPUT_DIR?.split('/')[0] ?? 'public'),
      serveRoot: '/',
      serveStaticOptions: {
        index: false,
      },
    }),
    FfmpegModule,
    VideoModule,
  ],
  controllers: [],
  providers: [],
})
export class AppModule { }
