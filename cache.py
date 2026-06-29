"""
图片审核缓存模块——基于内容指纹的审核次数记录器。

对每张送审图片计算 SHA-256 内容指纹，
当同一张图片的累计审核次数达到阈值（默认 3 次）后，
后续遇到将直接跳过 LLM 审核调用，减少不必要的 API 开销。

设计参考：
  https://github.com/FloranceYeh/astrbot_plugin_image_caption_cache
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

DEFAULT_REVIEW_THRESHOLD = 3
"""默认阈值——单张图片累计审核多少次后跳过后续审核。"""

DEFAULT_MAX_ENTRIES = 10000
"""内存中最多缓存的图片指纹条目数，防止无界增长。超出时淘汰计数最低的条目。"""


class ImageAuditCache:
    """图片审核缓存——基于内容指纹的审核次数记录。

    数据格式：{fingerprint_hex: audit_count}

    使用 ``to_dict`` / ``from_dict`` 实现序列化，可持久化到
    AstrBot KV 存储或 JSON 文件。

    Parameters
    ----------
    threshold:
        累计审核次数阈值。达到该次数后 ``should_skip`` 返回 True。
    max_entries:
        内存中最多缓存的条目数。超出时自动淘汰计数最低的条目。
        设为 0 表示不限（不推荐，可能无界增长）。
    """

    def __init__(
        self,
        threshold: int = DEFAULT_REVIEW_THRESHOLD,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        # fingerprint_hex -> audit_count
        self._data: dict[str, int] = {}
        self._threshold = self._resolve_threshold(threshold)
        self._max_entries = max(int(max_entries), 0)
        # 脏标记：记录 audit 后置 True，持久化后重置
        self._dirty = False

    # ── 阈值解析 ──────────────────────────────────────────────

    @staticmethod
    def _resolve_threshold(raw: object) -> int:
        try:
            return max(int(raw), 1)  # type: ignore
        except (TypeError, ValueError):
            return DEFAULT_REVIEW_THRESHOLD

    @property
    def threshold(self) -> int:
        """当前审核次数阈值。"""
        return self._threshold

    @threshold.setter
    def threshold(self, value: int) -> None:
        """更新阈值（不影响已有计数）。"""
        self._threshold = self._resolve_threshold(value)

    @property
    def dirty(self) -> bool:
        """自上次持久化后是否有新数据。"""
        return self._dirty

    def mark_clean(self) -> None:
        """标记已持久化。"""
        self._dirty = False

    # ── 指纹计算 ──────────────────────────────────────────────

    @staticmethod
    def compute_fingerprint(data_url: str) -> str:
        """从 data URL 计算图片内容指纹（SHA-256）。

        支持形如 ``data:image/jpeg;base64,/9j/4AAQ...`` 的 data URL。
        解析失败时回退到对完整 URL 字符串做哈希，确保不抛异常。
        """
        try:
            _, encoded = data_url.split(",", 1)
            image_bytes = base64.b64decode(encoded)
            return hashlib.sha256(image_bytes).hexdigest()
        except Exception:
            return hashlib.sha256(data_url.encode("utf-8")).hexdigest()

    # ── 核心查询 ──────────────────────────────────────────────

    def should_skip(self, fingerprint: str) -> bool:
        """若该图片已审核 ≥ threshold 次，返回 True。"""
        return self._data.get(fingerprint, 0) >= self._threshold

    def record_audit(self, fingerprint: str) -> None:
        """记录一次审核（计数器 +1）。
        若条目数超过 ``max_entries`` 上限，自动淘汰计数最低的条目。
        """
        is_new = fingerprint not in self._data
        self._data[fingerprint] = self._data.get(fingerprint, 0) + 1
        self._dirty = True
        if is_new and self._max_entries > 0:
            self._evict_lowest_count_entries()

    def get_count(self, fingerprint: str) -> int:
        """获取某张图片的累计审核次数。"""
        return self._data.get(fingerprint, 0)

    # ── 淘汰策略 ──────────────────────────────────────────────

    def _evict_lowest_count_entries(self) -> None:
        """超出上限时淘汰计数最低的条目（LFU 近似），一次最多淘汰 5%。"""
        while len(self._data) > self._max_entries > 0:
            # 按计数升序排列，取前 max(1, 5%) 条淘汰
            to_remove = max(1, len(self._data) // 20)
            sorted_keys = sorted(self._data, key=lambda k: self._data[k])
            for key in sorted_keys[:to_remove]:
                self._data.pop(key, None)

    # ── 序列化 ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, int]:
        """导出为纯 dict，用于持久化。"""
        return dict(self._data)

    def from_dict(self, raw: object) -> None:
        """从之前 ``to_dict`` 导出的数据恢复状态（合并模式，保留已有计数）。"""
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, (int, float)):
                    key = str(k)
                    existing = self._data.get(key, 0)
                    self._data[key] = max(existing, int(v))
            # 加载后同步到当前 max_entries 上限
            self._evict_lowest_count_entries()

    # ── 管理 ──────────────────────────────────────────────────

    def clear(self) -> int:
        """清空所有缓存记录，返回被清除的条目数。"""
        n = len(self._data)
        self._data.clear()
        self._dirty = True
        return n

    @property
    def stats(self) -> dict[str, Any]:
        """返回缓存统计信息。"""
        total = len(self._data)
        skipped = sum(1 for v in self._data.values() if v >= self._threshold)
        return {
            "total_unique_images": total,
            "images_skip_audit": skipped,
            "threshold": self._threshold,
            "max_entries": self._max_entries,
        }
