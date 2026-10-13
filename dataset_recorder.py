"""
数据集留存模块——把「送审图片 + 提示词 + 模型返回 + 模型标签」落盘成可微调数据集。

开关 `dataset_capture_enabled`（插件页「留存prompt」）关闭时，本模块不会创建任何
目录、不写任何文件：所有写入口都先判断 `enabled`，连目录探测都不会发生。

落盘布局（`dataset_dir`，默认 data/plugin_data/image_guard/dataset）::

    samples.jsonl                  每条样本一行，含 base64 图片与完整 prompt
    images/<sample_id>/img_1.jpg   解码后的图片文件，训练框架可直接读取
    export/<格式>-<时间戳>/         openai.json / sharegpt.json / samples.jsonl

`samples.jsonl` 每行的字段结构::

    {
      "id": "20261013-224912-a1b2c3",   # 样本 id，同时是图片目录名
      "schema_version": 1,
      "created_at": "2026-10-13 22:49:12",
      "source": {"group_id": "", "user_id": "", "user_name": "", "is_group": false},
      "request": {
        "prompt": "完整提示词",
        "provider": "供应商别名", "model": "模型名",
        "image_count": 1,
        "images": [
          {"index": 1, "mime": "image/jpeg", "base64": "...", "bytes": 12345,
           "sha256": "...", "path": "images/<id>/img_1.jpg", "missing_reason": ""}
        ]
      },
      "response": {
        "status": "pending|ok|failed",
        "text": "模型返回原文", "tags": ["标签"], "label": "violation|safe|unknown",
        "reason": "判定理由", "error": "", "elapsed_ms": 1234,
        "recorded_at": "...", "raw": null
      }
    }

记录时机：`begin` 在调用模型前落盘 request（图片先解码成文件，base64 内联进
JSONL），`finish` 在调用返回后回填 response。进程在中途挂掉时，请求侧已经落盘，
不会整条丢失。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # 脱离 AstrBot 单独跑测试时回退到标准库日志
    from astrbot.api import logger
except ImportError:  # pragma: no cover - 仅在无 AstrBot 的环境触发
    import logging

    logger = logging.getLogger("astrbot_plugin_image_guard.dataset")

DEFAULT_DATASET_DIR = Path("data") / "plugin_data" / "image_guard" / "dataset"
"""默认存储根目录，与审核图片目录同级，便于一起备份或清理。"""

SAMPLES_FILE = "samples.jsonl"
"""样本主文件：一行一条 JSON，图片 base64 内联，便于整体搬运。"""

IMAGES_DIR_NAME = "images"
"""解码后的图片目录，供训练框架直接按路径读取。"""

EXPORT_DIR_NAME = "export"
"""导出产物根目录。"""

SCHEMA_VERSION = 1
"""样本字段结构版本号；字段变更时递增，导出侧据此做兼容。"""

EXPORT_FORMATS = ("openai", "sharegpt", "raw")
"""导出格式：标准 OpenAI messages / ShareGPT 会话 / 原样拷贝。"""

FORMAT_ALIASES = {"sft": "openai"}
"""历史格式名到现名的映射，避免旧页面缓存把导出请求打回 400。"""

DEFAULT_MAX_SAMPLES = 2000
"""默认样本条数上限，防止长期开启把磁盘写满。"""

DEFAULT_MIN_FREE_MB = 512
"""默认磁盘余量下限（MB），低于该值时停止留存，而不是把磁盘写满。"""

DEFAULT_MAX_IMAGE_MB = 8
"""单张送审图片的留存上限（MB），超过则只记元数据不落 base64。"""

_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}

_WHITESPACE_RE = re.compile(r"\s+")


def _as_bool(raw: object) -> bool:
    """与 main.py 同语义的宽松布尔解析（配置可能来自 JSON 或页面回传）。"""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def _as_int(raw: object, default: int) -> int:
    try:
        return int(float(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def parse_data_url(value: str) -> tuple[str, str]:
    """拆出 data URL 的 (mime, base64 负载)。

    只接受 `data:image/...;base64,...`。http(s) 地址、本地路径、空串一律返回空串，
    由调用方记入 `missing_reason`：这类输入没有 base64 可留存，但 prompt 与模型
    返回仍然有价值，不能因为图片取不到就整条丢掉。
    """
    text = str(value or "").strip()
    if not text.startswith("data:"):
        return "", ""
    header, separator, payload = text.partition(",")
    if not separator:
        return "", ""
    has_base64_flag = False
    mime = ""
    for part in (item.strip() for item in header[5:].split(";")):
        if not part:
            continue
        lowered = part.lower()
        if lowered == "base64":
            has_base64_flag = True
        elif "=" in part:
            continue  # charset=utf-8 之类的参数，与 mime 无关
        elif not mime:
            mime = lowered
    if not has_base64_flag or not mime.startswith("image/"):
        return "", ""
    return mime, payload


def decode_base64_payload(payload: str) -> bytes | None:
    """严格解码 base64 负载；含非法字符或解不出内容时返回 None。"""
    text = _WHITESPACE_RE.sub("", payload or "")
    if not text:
        return None
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    return data or None


def normalize_image(value: str, index: int, max_bytes: int) -> dict:
    """把一条送审图片输入规整成可落盘结构。

    `max_bytes` 为 0 表示不限制；超过上限时保留元数据但丢弃 base64 与文件，
    避免单张超大图把样本文件撑爆。
    """
    mime, payload = parse_data_url(value)
    item: dict[str, Any] = {
        "index": index,
        "mime": mime,
        "base64": "",
        "bytes": 0,
        "sha256": "",
        "path": "",
        "missing_reason": "",
    }
    if not mime:
        item["missing_reason"] = "非 base64 data URL，无图片内容可留存"
        return item

    data = decode_base64_payload(payload)
    if data is None:
        item["missing_reason"] = "base64 解码失败，图片内容不可用"
        return item

    if max_bytes > 0 and len(data) > max_bytes:
        item["bytes"] = len(data)
        item["missing_reason"] = f"图片 {len(data)} 字节超过留存上限 {max_bytes} 字节"
        return item

    item["base64"] = _WHITESPACE_RE.sub("", payload)
    item["bytes"] = len(data)
    item["sha256"] = hashlib.sha256(data).hexdigest()
    item["_data"] = data  # 仅供本次落盘使用，写盘前会剔除
    return item


def image_extension(mime: str, index: int) -> str:
    """按 mime 推断扩展名，未知类型退回 .bin 而不是猜成 .jpg。"""
    return _MIME_EXT.get(str(mime or "").lower(), ".bin")


def _iso_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DatasetRecorder:
    """数据集留存器：开关关闭时是一个彻底的 no-op。

    所有写入口（`begin` / `finish`）第一步就检查 `enabled`，关闭时既不建目录、
    也不读盘，插件其余行为与没有本功能时逐字节相同。
    """

    def __init__(self, config: dict | None, root: Path | None = None) -> None:
        self._config = config or {}
        self._explicit_root = Path(root) if root is not None else None
        self._root = self._resolve_root()
        self._enabled = _as_bool(self._config.get("dataset_capture_enabled", False))
        self._disk_full = False
        self._limit_hit = False

    def _resolve_root(self) -> Path:
        """显式传入的根目录优先，其次读配置里的 dataset_dir，最后用默认值。"""
        if self._explicit_root is not None:
            return self._explicit_root
        raw = str(self._config.get("dataset_dir") or "").strip()
        return Path(raw) if raw else DEFAULT_DATASET_DIR

    # ── 开关与参数 ────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def root(self) -> Path:
        return self._root

    @property
    def samples_path(self) -> Path:
        return self._root / SAMPLES_FILE

    @property
    def responses_path(self) -> Path:
        return self._root / "responses.jsonl"

    @property
    def images_dir(self) -> Path:
        return self._root / IMAGES_DIR_NAME

    def refresh(self, config: dict | None = None) -> None:
        """配置热重载后同步开关与路径；磁盘告警状态一并复位。"""
        if config is not None:
            self._config = config
        self._root = self._resolve_root()
        self._enabled = _as_bool(self._config.get("dataset_capture_enabled", False))
        self._disk_full = False
        self._limit_hit = False

    def _max_samples(self) -> int:
        raw = self._config.get("dataset_max_samples", DEFAULT_MAX_SAMPLES)
        return max(0, _as_int(raw, DEFAULT_MAX_SAMPLES))

    def _max_image_bytes(self) -> int:
        mb = _as_int(self._config.get("dataset_max_image_mb", DEFAULT_MAX_IMAGE_MB), DEFAULT_MAX_IMAGE_MB)
        return max(0, mb) * 1024 * 1024

    def _min_free_bytes(self) -> int:
        mb = _as_int(self._config.get("dataset_min_free_mb", DEFAULT_MIN_FREE_MB), DEFAULT_MIN_FREE_MB)
        return max(0, mb) * 1024 * 1024

    def _keep_raw_response(self) -> bool:
        return _as_bool(self._config.get("dataset_capture_raw_response", False))

    # ── 磁盘保护 ──────────────────────────────────────────────

    def _nearest_existing(self, path: Path) -> Path:
        current = path
        while not current.exists() and current.parent != current:
            current = current.parent
        return current

    def free_bytes(self) -> int:
        """返回数据目录所在磁盘的剩余字节数（目录尚未创建时向上找）。"""
        try:
            return int(shutil.disk_usage(str(self._nearest_existing(self._root))).free)
        except Exception:
            return -1  # 取不到就当作不限制，避免误伤留存

    def _has_room(self) -> bool:
        """磁盘余量不足时停止留存：写满磁盘会让 AstrBot 一起挂掉。"""
        if self._disk_full:
            return False
        minimum = self._min_free_bytes()
        free = self.free_bytes()
        if minimum > 0 and 0 <= free < minimum:
            self._disk_full = True
            logger.warning(
                f"[ImageGuard] 数据集留存已暂停：磁盘剩余 {free} 字节低于下限 {minimum} 字节"
            )
            return False
        return True

    def _at_limit(self) -> bool:
        """条数达到上限后停止留存而不是淘汰旧样本——旧样本可能还没导出。"""
        if self._limit_hit:
            return True
        limit = self._max_samples()
        if limit <= 0:
            return False
        if self.count_samples() >= limit:
            self._limit_hit = True
            logger.warning(f"[ImageGuard] 数据集留存已达上限 {limit} 条，停止留存，请先导出再清空")
            return True
        return False

    # ── 写入 ──────────────────────────────────────────────────

    @staticmethod
    def _new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    def _append_jsonl(self, path: Path, payload: dict) -> None:
        """追加一行 JSON；单行写入在本地文件系统上是原子的，无需锁。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _write_images(self, sample_id: str, images: list[dict]) -> None:
        """把 base64 解码成真实图片文件，路径写回 images[].path。"""
        for item in images:
            data = item.pop("_data", None)
            if not data:
                continue
            extension = image_extension(item["mime"], item["index"])
            relative = f"{IMAGES_DIR_NAME}/{sample_id}/img_{item['index']}{extension}"
            target = self._root / relative
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                item["path"] = relative
            except OSError as error:
                logger.warning(f"[ImageGuard] 留存图片写入失败 {target}: {error}")
                item["path"] = ""

    def begin(
        self,
        prompt: str,
        image_urls: list[str],
        group_id: str = "",
        user_id: str = "",
        user_name: str = "",
        is_group: bool = False,
        provider: str = "",
        model: str = "",
    ) -> str | None:
        """调用模型前落盘请求侧内容，返回样本 id；未留存时返回 None。

        图片先解码成文件再写 JSONL，解码失败的图只记 `missing_reason`：
        图片坏掉不该连带丢掉 prompt 与模型返回这两份更有价值的监督信号。
        """
        if not self._enabled:
            return None
        if not self._has_room() or self._at_limit():
            return None

        images = [
            normalize_image(url, index, self._max_image_bytes())
            for index, url in enumerate(image_urls, start=1)
        ]
        if not images:
            images = [normalize_image("", 1, self._max_image_bytes())]

        sample_id = self._new_id()
        self._write_images(sample_id, images)
        record = {
            "id": sample_id,
            "schema_version": SCHEMA_VERSION,
            "created_at": _iso_now(),
            "source": {
                "group_id": str(group_id or ""),
                "user_id": str(user_id or ""),
                "user_name": str(user_name or ""),
                "is_group": bool(is_group),
            },
            "request": {
                "prompt": str(prompt or ""),
                "provider": str(provider or ""),
                "model": str(model or ""),
                "image_count": len(images),
                "images": images,
            },
        }
        try:
            self._append_jsonl(self.samples_path, record)
        except OSError as error:
            logger.error(f"[ImageGuard] 数据集留存失败（请求侧）: {error}")
            return None
        return sample_id

    def finish(
        self,
        sample_id: str | None,
        text: str = "",
        tags: list[str] | None = None,
        label: str = "unknown",
        reason: str = "",
        error: str = "",
        elapsed_ms: int | None = None,
        provider: str = "",
        model: str = "",
        raw: object = None,
    ) -> None:
        """调用返回后落盘响应侧内容；失败与空返回同样记录。"""
        if not self._enabled or not sample_id:
            return
        response = {
            "id": sample_id,
            "status": "ok" if not error else "failed",
            "text": str(text or ""),
            "tags": list(tags or []),
            "label": str(label or "unknown"),
            "reason": str(reason or ""),
            "error": str(error or ""),
            "elapsed_ms": int(elapsed_ms) if elapsed_ms is not None else None,
            "provider": str(provider or ""),
            "model": str(model or ""),
            "recorded_at": _iso_now(),
            "raw": raw if self._keep_raw_response() else None,
        }
        try:
            self._append_jsonl(self.responses_path, response)
        except OSError as error_obj:
            logger.error(f"[ImageGuard] 数据集留存失败（响应侧）: {error_obj}")

    # ── 读取 ──────────────────────────────────────────────────

    def _read_jsonl(self, path: Path, limit: int = 0) -> list[dict]:
        """逐行解析 JSONL，跳过损坏行（写入中断可能留下半行）。

        `limit` > 0 时只解析末尾若干行，用于页面预览。
        """
        if not path.exists():
            return []
        items: list[dict] = []
        try:
            if limit > 0:
                source = self._tail_lines(path, limit)
            else:
                with path.open("r", encoding="utf-8") as handle:
                    source = [line.strip() for line in handle if line.strip()]
            for line in source:
                try:
                    parsed = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(parsed, dict):
                    items.append(parsed)
        except OSError as error:
            logger.warning(f"[ImageGuard] 读取数据集失败 {path}: {error}")
        return items

    def _tail_lines(self, path: Path, limit: int) -> list[str]:
        """只读文件末尾的 limit 行。

        预览没必要把整份数据集读进内存：样本里内联着 base64，几百条就是几百 MB，
        每次打开面板都全量解析会把页面接口拖成秒级响应。
        """
        if limit <= 0 or not path.exists():
            return []
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                chunk = 64 * 1024
                buffer = b""
                position = size
                while position > 0 and buffer.count(b"\n") <= limit:
                    step = min(chunk, position)
                    position -= step
                    handle.seek(position)
                    buffer = handle.read(step) + buffer
        except OSError as error:
            logger.warning(f"[ImageGuard] 读取数据集尾部失败 {path}: {error}")
            return []
        lines = [line for line in buffer.decode("utf-8", errors="ignore").splitlines() if line.strip()]
        return lines[-limit:]

    def count_samples(self) -> int:
        if not self.samples_path.exists():
            return 0
        try:
            with self.samples_path.open("r", encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0

    def load_samples(self, limit: int = 0) -> list[dict]:
        """把请求侧与响应侧按 id 合并成完整样本。

        `limit` > 0 时只取末尾若干条（供页面预览）；导出必须传 0，否则会漏样本。
        """
        responses = {str(item.get("id")): item for item in self._read_jsonl(self.responses_path)}
        merged: list[dict] = []
        for sample in self._read_jsonl(self.samples_path, limit):
            response = responses.get(str(sample.get("id")))
            if response is None:
                response = {"id": sample.get("id"), "status": "pending", "text": "", "tags": [], "label": "unknown", "reason": "", "error": "", "elapsed_ms": None, "recorded_at": ""}
            merged.append({"sample": sample, "response": response})
        return merged

    def stats(self) -> dict:
        """供插件页展示：条数、占用、磁盘余量、暂停原因。"""
        samples = self.count_samples()
        responses = len(self._read_jsonl(self.responses_path)) if self.responses_path.exists() else 0
        size = 0
        if self._root.exists():
            for path in self._root.rglob("*"):
                try:
                    if path.is_file():
                        size += path.stat().st_size
                except OSError:
                    continue
        limit = self._max_samples()
        return {
            "enabled": self._enabled,
            "dir": str(self._root),
            "samples": samples,
            "responses": responses,
            "pending": max(0, samples - responses),
            "size_bytes": size,
            "limit": limit,
            "at_limit": bool(limit > 0 and samples >= limit),
            "paused_no_space": self._disk_full,
            "free_bytes": self.free_bytes(),
        }

    # ── 导出 ──────────────────────────────────────────────────

    @staticmethod
    def _image_paths(sample: dict) -> list[str]:
        return [
            str(item.get("path") or "")
            for item in sample.get("request", {}).get("images", [])
            if item.get("path")
        ]

    @staticmethod
    def _data_urls(sample: dict) -> list[str]:
        """还原送审时的 data URL，供标准 OpenAI 消息内联图片。"""
        urls: list[str] = []
        for item in sample.get("request", {}).get("images", []):
            payload = str(item.get("base64") or "")
            if not payload:
                continue
            urls.append(f"data:{item.get('mime') or 'image/jpeg'};base64,{payload}")
        return urls

    @staticmethod
    def _target_text(response: dict) -> str:
        """监督目标：只保留三行结构，与插件解析器（RESULT/REASON/TAGS）完全一致。

        标签用 JSON 数组原样写回，微调后的模型输出可以直接被 _parse_audit_tags 解析。
        """
        result = "VIOLATION" if response.get("label") == "violation" else "SAFE"
        tags = response.get("tags") or []
        return (
            f"REASON: {response.get('reason') or ''}\n"
            f"RESULT: {result}\n"
            f"TAGS: {json.dumps(tags, ensure_ascii=False)}"
        )

    def _openai_record(self, sample: dict, response: dict) -> dict:
        """标准 OpenAI chat.completions 结构：messages + content 分块 + image_url。

        与插件真实发往接口的请求体同构（prompt 在前、图片随后），这样导出数据
        与线上请求一致，微调后的模型可以直接替换现有供应商而不需要改提示词。
        """
        request = sample.get("request", {})
        content: list[dict] = [{"type": "text", "text": request.get("prompt", "")}]
        for url in self._data_urls(sample):
            content.append({"type": "image_url", "image_url": {"url": url}})
        return {
            "messages": [
                {"role": "user", "content": content},
                {"role": "assistant", "content": self._target_text(response)},
            ],
            # 训练侧标注：不属于 OpenAI 字段，训练框架会忽略未知键
            "label": response.get("label", "unknown"),
            "tags": response.get("tags") or [],
            "id": sample.get("id", ""),
        }

    def _sharegpt_record(self, sample: dict, response: dict) -> dict:
        """ShareGPT 会话结构：图片走 LLaMA-Factory 的 images 路径列表。"""
        request = sample.get("request", {})
        return {
            "conversations": [
                {"from": "human", "value": request.get("prompt", "")},
                {"from": "gpt", "value": self._target_text(response)},
            ],
            "images": self._image_paths(sample),
            "system": "",
            "label": response.get("label", "unknown"),
            "tags": response.get("tags") or [],
            "id": sample.get("id", ""),
        }

    def export(self, fmt: str = "sft", limit: int = 0) -> dict:
        """导出数据集，返回产物路径与统计；未开启或没有样本时不写任何文件。"""
        if not self._enabled:
            return {"ok": False, "message": "留存prompt 未开启，没有可导出的数据集"}
        merged = self.load_samples()
        if limit > 0:
            merged = merged[-limit:]
        if not merged:
            return {"ok": False, "message": "数据集为空，先让插件跑几条审核再导出"}

        fmt = FORMAT_ALIASES.get(fmt, fmt)
        if fmt not in EXPORT_FORMATS:
            fmt = "openai"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self._root / EXPORT_DIR_NAME / f"{fmt}-{stamp}"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return {"ok": False, "message": f"创建导出目录失败：{error}"}

        files: list[str] = []
        try:
            if fmt == "raw":
                files.append(self._export_raw(target, merged))
            else:
                records = [
                    self._openai_record(item["sample"], item["response"])
                    if fmt == "openai"
                    else self._sharegpt_record(item["sample"], item["response"])
                    for item in merged
                ]
                name = "openai.json" if fmt == "openai" else "sharegpt.json"
                (target / name).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                files.append(name)
                info = {
                    "image-guard-" + fmt: {
                        "file_name": name,
                        "formatting": "sharegpt",
                        "columns": {"messages": "messages", "images": "images"},
                        "tags": {
                            "role_tag": "role",
                            "content_tag": "content",
                            "user_tag": "user",
                            "assistant_tag": "assistant",
                        },
                    }
                }
                (target / "dataset_info.json").write_text(
                    json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                files.append("dataset_info.json")
        except OSError as error:
            return {"ok": False, "message": f"写导出文件失败：{error}"}

        manifest = {
            "exported_at": _iso_now(),
            "format": fmt,
            "samples": len(merged),
            "with_image": sum(1 for item in merged if self._data_urls(item["sample"])),
            "with_response": sum(1 for item in merged if item["response"].get("status") == "ok"),
            "label_violation": sum(1 for item in merged if item["response"].get("label") == "violation"),
            "label_safe": sum(1 for item in merged if item["response"].get("label") == "safe"),
            "source_dir": str(self._root),
            "files": files,
        }
        try:
            (target / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
        return {"ok": True, "message": "导出完成", "dir": str(target), "manifest": manifest}

    def _export_raw(self, target: Path, merged: list[dict]) -> str:
        """raw 格式：原样拷出两份 JSONL，训练侧自行合并。"""
        name = "samples.jsonl"
        with (target / name).open("w", encoding="utf-8") as handle:
            for item in merged:
                handle.write(json.dumps(item["sample"], ensure_ascii=False) + "\n")
        with (target / "responses.jsonl").open("w", encoding="utf-8") as handle:
            for item in merged:
                handle.write(json.dumps(item["response"], ensure_ascii=False) + "\n")
        (target / "README.md").write_text(
            "# ImageGuard 数据集（raw）\n\n"
            "- samples.jsonl：一行一条请求侧记录，含 prompt 与图片 base64；\n"
            "- responses.jsonl：一行一条响应侧记录，按 id 与 samples.jsonl 关联；\n"
            "- 图片路径相对原数据集目录（<dataset_dir>/images/<样本id>/）。\n\n"
            "需要标准 OpenAI 训练格式时改用「导出数据集 → 标准 OpenAI」。\n",
            encoding="utf-8",
        )
        return name

    # ── 清理 ──────────────────────────────────────────────────

    def clear(self) -> dict:
        """清空样本与图片（保留 export 目录里已导出的产物）。"""
        removed = 0
        for path in (self.samples_path, self.responses_path):
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except OSError as error:
                logger.warning(f"[ImageGuard] 删除 {path} 失败: {error}")
        try:
            if self.images_dir.exists():
                shutil.rmtree(self.images_dir)
                removed += 1
        except OSError as error:
            logger.warning(f"[ImageGuard] 删除图片目录失败: {error}")
        self._limit_hit = False
        return {"removed": removed}
