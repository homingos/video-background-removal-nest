import {
    IsString,
    IsOptional,
    IsNumber,
    IsBoolean,
    Min,
    Max,
    IsIn,
    IsUrl,
    Matches,
    IsNotEmpty,
} from 'class-validator';
import { Type } from 'class-transformer';

export class ProcessVideoDto {
    @IsOptional()
    @IsUrl({}, { message: 'videoUrl must be a valid URL' })
    videoUrl?: string;

    @IsNotEmpty({ message: 'colorType is required' })
    @IsIn(['green', 'blue'], { message: 'colorType must be "green" or "blue"' })
    colorType: 'green' | 'blue';

    /** If true, automatically detect the chroma key color from video frames */
    @IsOptional()
    @IsBoolean()
    @Type(() => Boolean)
    autoDetectColor?: boolean;

    @IsOptional()
    @IsString()
    @Matches(/^[0-9A-Fa-f]{6}$/, {
        message: 'color must be a valid 6-character hex color (e.g., 00FF00)',
    })
    color?: string;

    // --- Transparent video settings (default) ---
    @IsOptional()
    @IsNumber()
    @Min(0.01)
    @Max(1.0)
    @Type(() => Number)
    similarity?: number;

    @IsOptional()
    @IsNumber()
    @Min(0.0)
    @Max(1.0)
    @Type(() => Number)
    blend?: number;

    // --- Mask video settings (optional, separate from transparent) ---
    @IsOptional()
    @IsNumber()
    @Min(0.01)
    @Max(1.0)
    @Type(() => Number)
    mask_similarity?: number;

    @IsOptional()
    @IsNumber()
    @Min(0.0)
    @Max(1.0)
    @Type(() => Number)
    mask_blend?: number;
}
