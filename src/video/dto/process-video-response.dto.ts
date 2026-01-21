export class ProcessedVideoData {
    mask: string;
    result: string;
}

export class ProcessVideoResponseDto {
    success: boolean;
    data: ProcessedVideoData;
}

export class ErrorResponseDto {
    success: boolean;
    error: string;
    message?: string;
}
