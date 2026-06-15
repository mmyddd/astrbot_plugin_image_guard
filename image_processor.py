import base64
import io
import math
from datetime import datetime
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import blake2s
from pathlib import Path
from typing import Final

import httpx
from PIL import Image as PillowImage
from PIL import ImageOps


JPEG_DATA_URL_PREFIX: Final = "data:image/jpeg;base64,"
JPEG_QUALITIES: Final = (90, 80, 70, 60, 50, 40, 30, 20)

CompressionLogWriter = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class JpegEncodeResult:
    data: bytes
    quality: int


@dataclass(frozen=True, slots=True)
class JpegCompressionResult:
    data: bytes
    width: int
    height: int
    quality: int


@dataclass(frozen=True, slots=True)
class ImageCompressionResult:
    data_url: str
    original_bytes: int
    jpeg_bytes: int
    data_url_bytes: int
    original_width: int
    original_height: int
    compressed_width: int
    compressed_height: int
    quality: int
    max_data_url_bytes: int
    saved_path: str | None


def compress_image_to_data_url(image_bytes: bytes, max_bytes: int) -> str:
    return compress_image_with_result(image_bytes, max_bytes).data_url


def compress_image_with_result(image_bytes: bytes, max_bytes: int) -> ImageCompressionResult:
    image = ImageOps.exif_transpose(PillowImage.open(io.BytesIO(image_bytes))).convert("RGB")
    original_width, original_height = image.size
    image_max_bytes = max(1, ((max_bytes - len(JPEG_DATA_URL_PREFIX)) * 3) // 4)
    compressed = _compress_jpeg(image, image_max_bytes)
    encoded = base64.b64encode(compressed.data).decode("ascii")
    data_url = f"{JPEG_DATA_URL_PREFIX}{encoded}"
    return ImageCompressionResult(
        data_url=data_url,
        original_bytes=len(image_bytes),
        jpeg_bytes=len(compressed.data),
        data_url_bytes=len(data_url.encode("ascii")),
        original_width=original_width,
        original_height=original_height,
        compressed_width=compressed.width,
        compressed_height=compressed.height,
        quality=compressed.quality,
        max_data_url_bytes=max_bytes,
        saved_path=None,
    )


async def prepare_audit_images(
    image_urls: list[str],
    max_bytes: int,
    keep_compressed_image_in_temp: bool = False,
    compressed_image_temp_dir: Path | None = None,
    log_compression_result: CompressionLogWriter | None = None,
) -> list[str]:
    prepared_urls = []
    total_images = len(image_urls)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for image_index, image_url in enumerate(image_urls, start=1):
            if image_url.startswith("data:"):
                prepared_urls.append(image_url)
                if log_compression_result:
                    log_compression_result(
                        _format_existing_data_url_log(
                            image_index,
                            total_images,
                            len(image_url.encode("utf-8")),
                        )
                    )
                continue

            response = await client.get(image_url)
            response.raise_for_status()
            compression_result = compress_image_with_result(response.content, max_bytes)
            if keep_compressed_image_in_temp:
                compression_result = _save_compressed_image_to_temp(
                    compression_result,
                    image_url,
                    _resolve_compressed_image_temp_dir(compressed_image_temp_dir),
                )
            prepared_urls.append(compression_result.data_url)
            if log_compression_result:
                log_compression_result(
                    _format_compression_result(image_index, total_images, compression_result)
                )

    return prepared_urls


def _save_compressed_image_to_temp(
    compression_result: ImageCompressionResult,
    image_url: str,
    temp_dir: Path,
) -> ImageCompressionResult:
    temp_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    digest = blake2s(image_url.encode("utf-8"), digest_size=4).hexdigest()
    file_path = temp_dir / f"image_guard_{timestamp}_{digest}.jpg"
    file_path.write_bytes(base64.b64decode(compression_result.data_url.split(",", 1)[1]))

    return ImageCompressionResult(
        data_url=compression_result.data_url,
        original_bytes=compression_result.original_bytes,
        jpeg_bytes=compression_result.jpeg_bytes,
        data_url_bytes=compression_result.data_url_bytes,
        original_width=compression_result.original_width,
        original_height=compression_result.original_height,
        compressed_width=compression_result.compressed_width,
        compressed_height=compression_result.compressed_height,
        quality=compression_result.quality,
        max_data_url_bytes=compression_result.max_data_url_bytes,
        saved_path=str(file_path),
    )


def _resolve_compressed_image_temp_dir(compressed_image_temp_dir: Path | None) -> Path:
    if compressed_image_temp_dir:
        return compressed_image_temp_dir
    return Path("data") / "temp" / "astrbot_plugin_image_guard"


def _compress_jpeg(image: PillowImage.Image, max_bytes: int) -> JpegCompressionResult:
    current_image = image
    while True:
        compressed = _smallest_quality_jpeg(current_image, max_bytes)
        if len(compressed.data) <= max_bytes:
            width, height = current_image.size
            return JpegCompressionResult(
                data=compressed.data,
                width=width,
                height=height,
                quality=compressed.quality,
            )

        width, height = current_image.size
        if width <= 1 and height <= 1:
            return JpegCompressionResult(
                data=compressed.data,
                width=width,
                height=height,
                quality=compressed.quality,
            )

        shrink_ratio = math.sqrt(max_bytes / len(compressed.data)) * 0.9
        next_width = max(1, int(width * shrink_ratio))
        next_height = max(1, int(height * shrink_ratio))
        current_image = current_image.resize(
            (next_width, next_height),
            resample=PillowImage.Resampling.LANCZOS,
        )


def _smallest_quality_jpeg(image: PillowImage.Image, max_bytes: int) -> JpegEncodeResult:
    smallest = _encode_jpeg(image, JPEG_QUALITIES[0])
    if len(smallest.data) <= max_bytes:
        return smallest

    for quality in JPEG_QUALITIES[1:]:
        encoded = _encode_jpeg(image, quality)
        if len(encoded.data) <= max_bytes:
            return encoded
        if len(encoded.data) < len(smallest.data):
            smallest = encoded

    return smallest


def _encode_jpeg(image: PillowImage.Image, quality: int) -> JpegEncodeResult:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return JpegEncodeResult(data=buffer.getvalue(), quality=quality)


def _format_compression_result(
    image_index: int,
    total_images: int,
    result: ImageCompressionResult,
) -> str:
    status = "达标" if result.data_url_bytes <= result.max_data_url_bytes else "超限"
    saved_path_text = f"，保留文件={result.saved_path}" if result.saved_path else ""
    return (
        "[ImageGuard] 图片压缩结果: "
        f"第 {image_index}/{total_images} 张，"
        f"原图={result.original_width}x{result.original_height}/{_format_bytes(result.original_bytes)}，"
        f"送审={result.compressed_width}x{result.compressed_height}/"
        f"JPEG {_format_bytes(result.jpeg_bytes)}/"
        f"data URL {_format_bytes(result.data_url_bytes)}，"
        f"质量={result.quality}，"
        f"上限={_format_bytes(result.max_data_url_bytes)}，"
        f"处理后占原图={_format_percent(result.data_url_bytes, result.original_bytes)}，"
        f"状态={status}"
        f"{saved_path_text}"
    )


def _format_existing_data_url_log(
    image_index: int,
    total_images: int,
    data_url_bytes: int,
) -> str:
    return (
        "[ImageGuard] 图片压缩结果: "
        f"第 {image_index}/{total_images} 张，"
        "来源已是 data URL，"
        f"送审大小={_format_bytes(data_url_bytes)}，"
        "状态=跳过压缩"
    )


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.2f} MiB"


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "未知"
    return f"{(numerator / denominator) * 100:.1f}%"
