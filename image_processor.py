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
GIF_DATA_URL_PREFIX: Final = "data:image/gif;base64,"
JPEG_QUALITIES: Final = (90, 80, 70, 60, 50, 40, 30, 20)
GIF_COLOR_COUNTS: Final = (256, 128, 64, 32)

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
    output_format: str = "JPEG"
    original_frames: int = 1
    compressed_frames: int = 1


def encode_image_to_data_url(image_bytes: bytes, mime_type: str) -> str:
    """将原始图片字节编码为指定 MIME 类型的 data URL。"""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def compress_image_to_data_url(image_bytes: bytes, max_bytes: int) -> str:
    return compress_image_with_result(image_bytes, max_bytes).data_url


def compress_image_with_result(image_bytes: bytes, max_bytes: int) -> ImageCompressionResult:
    raw = PillowImage.open(io.BytesIO(image_bytes))
    if raw.format == "GIF":
        return _compress_gif_with_result(raw, image_bytes, max_bytes)

    image = (ImageOps.exif_transpose(raw) or raw).convert("RGB")
    original_width, original_height = image.size
    image_max_bytes = _max_raw_bytes_for_data_url(max_bytes, JPEG_DATA_URL_PREFIX)
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


def _compress_gif_with_result(
    raw: PillowImage.Image,
    image_bytes: bytes,
    max_bytes: int,
) -> ImageCompressionResult:
    original_width, original_height = raw.size
    original_frames = getattr(raw, "n_frames", 1)
    loop_value = raw.info.get("loop")
    loop = int(loop_value) if loop_value is not None else None
    frames: list[PillowImage.Image] = []
    durations: list[int] = []
    for frame_index in range(original_frames):
        raw.seek(frame_index)
        frames.append(raw.convert("RGBA").copy())
        durations.append(int(raw.info.get("duration", 100)))

    image_max_bytes = _max_raw_bytes_for_data_url(max_bytes, GIF_DATA_URL_PREFIX)
    compressed, width, height, colors, compressed_durations = _compress_gif(
        frames,
        durations,
        loop,
        image_max_bytes,
    )
    encoded = base64.b64encode(compressed).decode("ascii")
    data_url = f"{GIF_DATA_URL_PREFIX}{encoded}"
    return ImageCompressionResult(
        data_url=data_url,
        original_bytes=len(image_bytes),
        jpeg_bytes=len(compressed),
        data_url_bytes=len(data_url.encode("ascii")),
        original_width=original_width,
        original_height=original_height,
        compressed_width=width,
        compressed_height=height,
        quality=colors,
        max_data_url_bytes=max_bytes,
        saved_path=None,
        output_format="GIF",
        original_frames=original_frames,
        compressed_frames=len(compressed_durations),
    )


async def prepare_audit_images(
    image_urls: list[str],
    max_bytes: int,
    keep_compressed_image_in_temp: bool = False,
    compressed_image_temp_dir: Path | None = None,
    log_compression_result: CompressionLogWriter | None = None,
) -> list[str]:
    """（旧版异步路径）从 URL 列表下载并压缩图片为 data URL。

    .. note::
       main.py 当前不使用此函数——它直接通过 ``Path.read_bytes()`` 同步处理本地文件，
       静态图片和 GIF 均调用 ``compress_image_with_result``；GIF 会保留动画。
       此函数保留为 legacy/alternate 入口，用于远程 URL 批量处理场景。
    """
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
    suffix = ".gif" if compression_result.output_format == "GIF" else ".jpg"
    file_path = temp_dir / f"image_guard_{timestamp}_{digest}{suffix}"
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
        output_format=compression_result.output_format,
        original_frames=compression_result.original_frames,
        compressed_frames=compression_result.compressed_frames,
    )


def _resolve_compressed_image_temp_dir(compressed_image_temp_dir: Path | None) -> Path:
    if compressed_image_temp_dir:
        return compressed_image_temp_dir
    return Path("data") / "temp" / "astrbot_plugin_image_guard"


def _max_raw_bytes_for_data_url(max_bytes: int, prefix: str) -> int:
    available_base64_bytes = max(0, max_bytes - len(prefix))
    return max(1, (available_base64_bytes // 4) * 3)


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


def _compress_gif(
    frames: list[PillowImage.Image],
    durations: list[int],
    loop: int | None,
    max_bytes: int,
) -> tuple[bytes, int, int, int, list[int]]:
    current_frames = frames
    current_durations = durations

    while True:
        smallest = b""
        selected_colors = GIF_COLOR_COUNTS[-1]
        for colors in GIF_COLOR_COUNTS:
            encoded = _encode_gif(current_frames, current_durations, loop, colors)
            if not smallest or len(encoded) < len(smallest):
                smallest = encoded
                selected_colors = colors
            if len(encoded) <= max_bytes:
                width, height = current_frames[0].size
                return encoded, width, height, colors, current_durations

        width, height = current_frames[0].size
        if len(current_frames) > 1 and (
            width <= 1 or height <= 1 or max_bytes / len(smallest) < 0.25
        ):
            current_frames, current_durations = _halve_gif_frames(
                current_frames,
                current_durations,
            )
            continue

        if width <= 1 and height <= 1:
            return smallest, width, height, selected_colors, current_durations

        shrink_ratio = min(0.9, math.sqrt(max_bytes / len(smallest)) * 0.9)
        next_width = max(1, int(width * shrink_ratio))
        next_height = max(1, int(height * shrink_ratio))
        current_frames = [
            frame.resize(
                (next_width, next_height),
                resample=PillowImage.Resampling.LANCZOS,
            )
            for frame in current_frames
        ]


def _encode_gif(
    frames: list[PillowImage.Image],
    durations: list[int],
    loop: int | None,
    colors: int,
) -> bytes:
    palette_frames = [_quantize_gif_frame(frame, colors) for frame in frames]
    buffer = io.BytesIO()
    save_options = {
        "format": "GIF",
        "save_all": True,
        "append_images": palette_frames[1:],
        "duration": durations,
        "disposal": 2,
        "optimize": True,
        "transparency": 255,
    }
    if loop is not None:
        save_options["loop"] = loop
    palette_frames[0].save(buffer, **save_options)
    return buffer.getvalue()


def _quantize_gif_frame(image: PillowImage.Image, colors: int) -> PillowImage.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    quantized = rgba.convert("RGB").quantize(
        colors=min(colors, 255),
        method=PillowImage.Quantize.MEDIANCUT,
    )
    transparent = alpha.point(lambda value: 255 if value <= 127 else 0)
    quantized.paste(255, mask=transparent)
    quantized.info["transparency"] = 255
    return quantized


def _halve_gif_frames(
    frames: list[PillowImage.Image],
    durations: list[int],
) -> tuple[list[PillowImage.Image], list[int]]:
    reduced_frames: list[PillowImage.Image] = []
    reduced_durations: list[int] = []
    for index in range(0, len(frames), 2):
        reduced_frames.append(frames[index])
        reduced_durations.append(sum(durations[index : index + 2]))
    return reduced_frames, reduced_durations


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
    animation_text = ""
    quality_text = f"质量={result.quality}"
    if result.output_format == "GIF":
        animation_text = (
            f"，帧数={result.original_frames}->{result.compressed_frames}"
        )
        quality_text = f"颜色数<={result.quality}"
    return (
        "[ImageGuard] 图片压缩结果: "
        f"第 {image_index}/{total_images} 张，"
        f"原图={result.original_width}x{result.original_height}/{_format_bytes(result.original_bytes)}，"
        f"送审={result.compressed_width}x{result.compressed_height}/"
        f"{result.output_format} {_format_bytes(result.jpeg_bytes)}/"
        f"data URL {_format_bytes(result.data_url_bytes)}，"
        f"{quality_text}{animation_text}，"
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
