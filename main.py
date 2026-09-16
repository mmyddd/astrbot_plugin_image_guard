import asyncio
import base64
import httpx
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from .cache import ImageAuditCache
from .image_processor import (
    compress_image_with_result,
    _format_compression_result,
    _save_compressed_image_to_temp,
    _resolve_compressed_image_temp_dir,
)
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Image

AUDIT_IMAGE_DIR = Path("data") / "plugin_data" / "image_guard" / "audit_images"

# ── 预编译正则（避免每次消息重新编译） ──
_RESULT_RE = re.compile(r"RESULT:\s*(VIOLATION|SAFE)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE)
_TAGS_RE = re.compile(r"^\s*TAGS:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_MAX_AUDIT_TAGS = 8
_MAX_AUDIT_TAG_LENGTH = 24

# ── 插件页 API 元数据 ──
PAGE_LIST_LIMIT = 1000
"""审核历史 API 单次最多返回的记录条数，避免大历史拖垮页面。"""
CONNECTIVITY_TEST_TIMEOUT = 20.0
"""供应商连通性测试的超时时间（秒）。"""
SUPPORTED_PROVIDER_TEMPLATES = ("openai_compatible", "modelscope", "astrbot_provider")
"""新版供应商条目支持的模板 key。"""
SECRET_MASK = "********"
"""配置接口中敏感字段的掩码值，避免 API Key 明文回传页面。"""
SECRET_FIELDS = ("api_key",)
"""llm_providers 条目中需要掩码的字段。"""

PENDING_TTL_SECONDS = 900
"""单条“处理中”任务的最大存活时间，超时视为异常残留并被清理。"""
PENDING_MAX_ITEMS = 32
"""页面同时展示的“处理中”任务上限，超出时丢弃最旧的一条。"""
PENDING_PREVIEW_MAX_BYTES = 512 * 1024
"""为处理中的任务预生成的预览 data URL 上限（字节），仅用于页面预览。"""

AUDIT_STAT_KEYS = (
    "audit_total",
    "audit_skipped_cache",
    "audit_skipped_probability",
    "audit_skipped_no_rules",
    "audit_failed",
    "safe_total",
    "safe_discarded",
)
"""KV 中的审核运行统计字段。"""

_EDITABLE_LIST_FIELDS = ("group_scope", "private_scope", "sensitive_texts", "forbidden_descriptions")
_EDITABLE_TEXT_FIELDS = ("custom_vision_prompt", "reasoning_effort", "report_target_id")
_EDITABLE_DICT_LIST_FIELDS = ("group_report_targets",)
_EDITABLE_BOOL_FIELDS = ("enable_recall", "audit_cache_enabled", "keep_compressed_image_in_temp", "debug_log_llm_response")
_EDITABLE_INT_FIELDS = (
    "llm_max_tokens",
    "compressed_image_max_bytes",
    "ban_duration",
    "audit_history_max_records",
    "audit_cache_threshold",
    "audit_cache_max_entries",
)
_EDITABLE_FLOAT_FIELDS = ("llm_timeout_seconds", "check_probability")


def _as_list(raw: object) -> list:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        return [raw] if raw else []
    return []


def _as_text_list(raw: object) -> list[str]:
    return [str(item).strip() for item in _as_list(raw) if str(item).strip()]


def _as_int(raw: object, default: int) -> int:
    try:
        return int(float(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(raw: object, default: float) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def _sanitize_providers(raw: object, keep_index: bool = False) -> list[dict]:
    """把任意来源的供应商列表规整成持久化结构，非法模板退回 OpenAI 兼容。

    ``keep_index`` 为 True 时保留页面回传的 ``__index`` 提示（用于把掩码 Key
    还原成已保存的明文）；该字段只是临时提示，写回配置前会被清掉。"""
    providers: list[dict] = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        template = str(item.get("__template_key") or "").strip()
        if template not in SUPPORTED_PROVIDER_TEMPLATES:
            template = "openai_compatible"
        entry = {
            "__template_key": template,
            "name": str(item.get("name") or "").strip(),
        }
        if keep_index:
            index_hint = _as_int(item.get("__index", -1), -1)
            if index_hint >= 0:
                entry["__index"] = index_hint
        if template != "astrbot_provider":
            entry["api_key"] = str(item.get("api_key") or "").strip()
            entry["base_url"] = str(item.get("base_url") or "").strip()
            entry["model"] = str(item.get("model") or "").strip()
        if not entry["name"]:
            entry["name"] = "AstrBot Provider" if template == "astrbot_provider" else "OpenAI API"
        providers.append(entry)
    return providers


def _mask_secret(value: object) -> str:
    """保留首尾字符的掩码，便于用户确认填的是哪一把 Key。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 10:
        return SECRET_MASK
    return f"{text[:4]}{SECRET_MASK}{text[-4:]}"


def _mask_providers(providers: list[dict]) -> list[dict]:
    masked: list[dict] = []
    for entry in providers:
        item = dict(entry)
        for field in SECRET_FIELDS:
            if item.get(field):
                item[field] = _mask_secret(item[field])
                item[f"{field}_masked"] = True
        masked.append(item)
    return masked


def _normalize_audit_tags(raw_tags: object) -> list[str]:
    """清洗模型返回的标签，去重并限制数量，避免脏数据进入历史记录。"""
    if isinstance(raw_tags, str):
        values = re.split(r"[,，、|]\s*", raw_tags)
    elif isinstance(raw_tags, (list, tuple, set)):
        values = list(raw_tags)
    else:
        values = []

    normalized = []
    seen = set()
    for value in values:
        tag = str(value).strip().strip("[]\"'`")
        if not tag:
            continue
        tag = tag[:_MAX_AUDIT_TAG_LENGTH]
        if tag not in seen:
            seen.add(tag)
            normalized.append(tag)
        if len(normalized) >= _MAX_AUDIT_TAGS:
            break
    return normalized


def _parse_audit_tags(response_text: str) -> list[str]:
    """解析 TAGS 行，优先读取 JSON 数组，兼容普通逗号分隔文本。"""
    match = _TAGS_RE.search(response_text or "")
    if not match:
        return []

    raw_value = match.group(1).strip()
    if not raw_value:
        return []

    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = raw_value
    if isinstance(parsed, dict):
        parsed = parsed.get("tags", [])
    return _normalize_audit_tags(parsed)


@register("image_guard", "YEZI", "图片内容审查卫士", "1.9.0")
class ImageGuard(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        # 共享 HTTP 客户端（复用连接池）
        self._http_client = httpx.AsyncClient(timeout=120.0)

        # ── 审核缓存（同一图片重复多次后跳过） ──
        cache_threshold = config.get("audit_cache_threshold", 3) if config else 3
        cache_max_entries = config.get("audit_cache_max_entries", 10000) if config else 10000
        self._audit_cache = ImageAuditCache(
            threshold=cache_threshold, max_entries=cache_max_entries
        )
        self._audit_cache_loaded = False

        # ── 处理中的审核任务（仅内存，不持久化；命中违规才写入历史）──
        self._pending_audits: dict[str, dict] = {}
        self._pending_seq = 0

        # ── 审核历史 API ──
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/list",
            self._api_audit_list,
            ["GET"],
            "获取审核记录列表",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/clear",
            self._api_audit_clear,
            ["POST", "DELETE"],
            "清空审核记录",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/delete",
            self._api_audit_delete,
            ["POST", "DELETE"],
            "删除单条审核记录",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/config",
            self._api_audit_config_get,
            ["GET"],
            "获取插件配置",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/config/update",
            self._api_audit_config_update,
            ["POST"],
            "更新插件配置并重载",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/cache/clear",
            self._api_audit_cache_clear,
            ["POST", "DELETE"],
            "清空审核缓存指纹",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/providers/test",
            self._api_audit_provider_test,
            ["POST"],
            "测试 LLM 供应商连通性",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/providers/save",
            self._api_audit_providers_save,
            ["POST"],
            "保存 LLM 供应商列表并重载",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/pending",
            self._api_audit_pending,
            ["GET"],
            "获取正在处理的审核任务",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/storage/prune",
            self._api_audit_storage_prune,
            ["POST", "DELETE"],
            "清理没有对应记录的本地图片",
        )

    # ── 运行统计（KV）──────────────────────────────────────

    async def _load_audit_stats(self) -> dict:
        stats = await self.get_kv_data("audit_stats", {})
        if not isinstance(stats, dict):
            stats = {}
        return stats

    async def _bump_audit_stats(self, **deltas: int) -> None:
        """累加运行统计，用于页面区分“审核了多少张”与“保存了多少条”。"""
        if not deltas:
            return
        stats = await self._load_audit_stats()
        for key, value in deltas.items():
            if key not in AUDIT_STAT_KEYS:
                continue
            current = stats.get(key, 0)
            stats[key] = int(current) + int(value) if isinstance(current, (int, float)) else int(value)
        await self.put_kv_data("audit_stats", stats)

    # ── 处理中的任务───────────────────────────────────────

    def _cleanup_pending(self) -> None:
        """清理超时或超量的“处理中”任务。"""
        now = time.time()
        expired = [
            key
            for key, item in self._pending_audits.items()
            if now - float(item.get("started_at", now)) > PENDING_TTL_SECONDS
        ]
        for key in expired:
            self._pending_audits.pop(key, None)

        if len(self._pending_audits) <= PENDING_MAX_ITEMS:
            return
        ordered = sorted(
            self._pending_audits.items(), key=lambda kv: float(kv[1].get("started_at", 0))
        )
        for key, _ in ordered[: len(self._pending_audits) - PENDING_MAX_ITEMS]:
            self._pending_audits.pop(key, None)

    def _pending_start(
        self,
        image_count: int,
        group_id: str,
        user_id: str,
        user_name: str,
        preview: str = "",
    ) -> str:
        """登记一条“送审中”任务，返回任务 id。"""
        self._cleanup_pending()
        self._pending_seq += 1
        task_id = f"{int(time.time() * 1000)}-{self._pending_seq}"
        self._pending_audits[task_id] = {
            "id": task_id,
            "status": "pending",
            "started_at": time.time(),
            "image_count": int(image_count),
            "group_id": group_id,
            "user_id": user_id,
            "user_name": user_name,
            "preview": preview,
        }
        return task_id

    def _pending_finish(self, task_id: str | None) -> None:
        """任务结束（命中违规已写入历史，否则丢弃）后移除该条记录。"""
        if task_id:
            self._pending_audits.pop(task_id, None)

    def _pending_snapshot(self) -> list[dict]:
        """返回当前处理中的任务（不含图片内容）。"""
        self._cleanup_pending()
        now = time.time()
        items = []
        for item in self._pending_audits.values():
            started = float(item.get("started_at", now))
            items.append(
                {
                    "id": item.get("id"),
                    "status": item.get("status", "pending"),
                    "started_at": started,
                    "elapsed_seconds": max(0.0, round(now - started, 1)),
                    "image_count": item.get("image_count", 0),
                    "group_id": item.get("group_id", ""),
                    "user_id": item.get("user_id", ""),
                    "user_name": item.get("user_name", ""),
                }
            )
        items.sort(key=lambda entry: entry["started_at"])
        return items

    def _pending_preview(self, image_url: str) -> str:
        """为处理中的任务准备一张小预览图（仅在内存中，不落盘）。"""
        try:
            if not image_url or len(image_url) > PENDING_PREVIEW_MAX_BYTES:
                return ""
            if not image_url.startswith("data:image/"):
                return ""
            header, encoded = image_url.split(",", 1)
            raw = base64.b64decode(encoded)
            result = compress_image_with_result(raw, PENDING_PREVIEW_MAX_BYTES)
            return result.data_url or ""
        except Exception as e:
            logger.debug(f"[ImageGuard] 预览图生成失败: {e}")
            return ""

    async def _api_audit_pending(self) -> dict:
        """供页面轮询：哪些图片正在审核。"""
        return {
            "pending": self._pending_snapshot(),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ── 审核缓存持久化 ─────────────────────────────────────────

    async def _ensure_audit_cache_loaded(self) -> None:
        """从 KV 存储加载审核缓存（首次调用时）。"""
        if not self._audit_cache_loaded:
            data = await self.get_kv_data("image_audit_cache", {})
            self._audit_cache.from_dict(data)
            self._audit_cache_loaded = True

    async def _save_audit_cache(self) -> None:
        """将审核缓存持久化到 KV 存储（仅当有变动时）。"""
        if not self._audit_cache.dirty:
            return
        await self.put_kv_data("image_audit_cache", self._audit_cache.to_dict())
        self._audit_cache.mark_clean()

    # ── 消息处理入口 ────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_image_message(self, event: AstrMessageEvent):
        # === 1. 范围控制逻辑 ===
        group_id = event.get_group_id() or ""
        user_id = event.get_sender_id() or ""
        is_group = bool(group_id)

        group_scope = [str(x) for x in self.config.get("group_scope", ["0"])]
        private_scope = [str(x) for x in self.config.get("private_scope", [])]

        if is_group:
            if "0" not in group_scope and group_id not in group_scope: return
        else:
            if "0" not in private_scope and user_id not in private_scope: return

        # === 2. 过滤商城大表情（mface→image 转换）并提取图片 ===
        # QQ 商城大表情收到时会转为 image 段并带 key/emoji_id 等额外字段
        if hasattr(event, "original_event") and hasattr(event.original_event, "message"):
            raw_chain = event.original_event.message
            if isinstance(raw_chain, list):
                for seg in raw_chain:
                    if isinstance(seg, dict) and seg.get("type") == "image":
                        data = seg.get("data", {})
                        if data.get("key") or data.get("emoji_id"):
                            logger.info("[ImageGuard] 商城大表情，跳过审核")
                            return

        # === 3. 提取图片路径 ===
        message_obj = event.message_obj
        if not message_obj.message: return

        # 先收集所有本地图片路径
        image_paths = []
        for component in message_obj.message:
            if isinstance(component, Image):
                img_url = component.url or component.file or component.path or ""
                if not img_url:
                    continue
                path = Path(img_url.replace("file:///", ""))
                if path.exists():
                    image_paths.append(path)

        if not image_paths: return

        # === 4. 压缩所有图片为 data URL ===
        max_image_bytes = int(self.config.get("compressed_image_max_bytes", 1048576))
        keep_temp = self.config.get("keep_compressed_image_in_temp", False)
        image_urls = []
        for index, path in enumerate(image_paths, start=1):
            try:
                image_bytes = path.read_bytes()
                result = compress_image_with_result(image_bytes, max_image_bytes)
                if keep_temp:
                    result = _save_compressed_image_to_temp(
                        result,
                        str(path),
                        _resolve_compressed_image_temp_dir(None),
                    )
                image_urls.append(result.data_url)
                logger.info(_format_compression_result(index, len(image_paths), result))
            except Exception as e:
                logger.warning(f"[ImageGuard] 处理图片失败 {path}: {e}")

        if not image_urls: return

        # === 5. 审核缓存检查（重复多次的图片跳过审核） ===
        cache_enabled = self.config.get("audit_cache_enabled", True)
        if cache_enabled:
            await self._ensure_audit_cache_loaded()
            fingerprints = [ImageAuditCache.compute_fingerprint(u) for u in image_urls]
            sending_urls: list[str] = []
            sending_fingerprints: list[str] = []
            for url, fp in zip(image_urls, fingerprints):
                if self._audit_cache.should_skip(fp):
                    logger.info(
                        f"[ImageGuard] 图片指纹 {fp[:12]} "
                        f"已审核 {self._audit_cache.get_count(fp)} 次，跳过审核"
                    )
                else:
                    sending_urls.append(url)
                    sending_fingerprints.append(fp)
            if not sending_urls:
                logger.info("[ImageGuard] 所有图片均已多次审核，跳过本次审核")
                await self._bump_audit_stats(audit_skipped_cache=1)
                return

            # 去重：同一消息中相同的图片只送审一次，避免浪费 LLM 调用和计数膨胀
            dedup = dict.fromkeys(sending_fingerprints)  # key 按首次出现顺序排列
            if len(dedup) != len(sending_fingerprints):
                before = len(sending_fingerprints)
                url_by_fp = dict(zip(sending_fingerprints, sending_urls))
                sending_fingerprints = list(dedup)
                sending_urls = [url_by_fp[fp] for fp in sending_fingerprints]
                logger.info(
                    f"[ImageGuard] 去重后剩余 {len(sending_urls)}/{before} 张不重复图片"
                )

            image_urls = sending_urls
        else:
            sending_fingerprints = []

        # === 6. 概率抽查 ===
        if random.random() > self.config.get("check_probability", 1.0):
            logger.info("[ImageGuard] 概率抽查跳过")
            await self._bump_audit_stats(audit_skipped_probability=1)
            return

        # === 7. 检查配置 ===
        forbidden_texts = self.config.get("sensitive_texts", [])
        forbidden_descs = self.config.get("forbidden_descriptions", [])

        if not forbidden_texts and not forbidden_descs:
            logger.info("[ImageGuard] 未配置审查规则，跳过")
            await self._bump_audit_stats(audit_skipped_no_rules=1)
            return

        # === 8. 审核逻辑 ===
        custom_instruction = self.config.get("custom_vision_prompt", "")
        prompt = (
            "你是一个严格但公正的内容审核员。请分析图片是否包含违规信息。\n"
            f"【自定义关注点】\n{custom_instruction}\n\n"
            "【违规标准】\n"
            f"1. 包含文字：{str(forbidden_texts)}\n"
            f"2. 包含画面：{str(forbidden_descs)}\n\n"
            "【输出格式要求】\n"
            "请严格按照以下三行格式输出，不要包含其他废话：\n"
            "REASON: [这里简要说明判断理由，不超过20字]\n"
            "RESULT: [SAFE 或 VIOLATION]\n"
            "TAGS: [JSON数组，例如 [\"肌肤裸露\", \"白丝\"]；SAFE 时必须为 []]\n"
            "标签要求：仅在 VIOLATION 时提供，最多8个具体、简短、互不重复的画面标签。\n"
        )

        # ── 处理中状态：先在 WebUI 上登记，命中违规才会转为历史记录，否则丢弃 ──
        pending_id = self._pending_start(
            image_count=len(image_urls),
            group_id=group_id,
            user_id=user_id,
            user_name=event.get_sender_name() or "",
            preview=self._pending_preview(image_urls[0]) if image_urls else "",
        )
        await self._bump_audit_stats(audit_total=1)

        try:
            # v4.26+ 图片在提取时已压缩为 data URL，无需 prepare_audit_images 再次处理
            logger.info(f"[ImageGuard] 开始审核，共 {len(image_urls)} 张图片")
            response_text = await self._call_audit_llm(prompt, image_urls)
            if self.config.get("debug_log_llm_response", False):
                logger.info(f"[ImageGuard] LLM 返回内容: {response_text}")

            # 记录本批图片的审核次数（无论是否违规）
            if cache_enabled and sending_fingerprints:
                for fp in sending_fingerprints:
                    self._audit_cache.record_audit(fp)
                await self._save_audit_cache()
                await self._bump_audit_stats(cache_records=len(sending_fingerprints))

            # === 9. 解析结果 ===
            result_match = _RESULT_RE.search(response_text)
            reason_match = _REASON_RE.search(response_text)
            parsed_tags = _parse_audit_tags(response_text)

            is_violation = False
            reason_str = "未说明理由"

            if result_match and "VIOLATION" in result_match.group(1).upper():
                is_violation = True
            # 兜底检测
            if not result_match and "VIOLATION" in response_text.upper():
                is_violation = True

            if reason_match:
                reason_str = reason_match.group(1).strip()
            elif is_violation:
                reason_str = response_text.split('\n')[0][:50]

            # === 10. 判罚 ===
            if is_violation:
                audit_tags = parsed_tags
                logger.info(f"[ImageGuard] 违规命中: {reason_str}")
                # image_paths[0] 是原始本地文件，用于上报和持久化（非压缩 data URL）
                await self.enforce_penalty(
                    event,
                    str(image_paths[0]),
                    is_group,
                    reason_str,
                    audit_tags,
                )
            else:
                # SAFE：不写入历史、不保留图片，直接丢弃
                logger.info(f"[ImageGuard] 审核通过（SAFE），丢弃该次结果: {reason_str}")
                await self._bump_audit_stats(safe_total=1, safe_discarded=1)

        except Exception as e:
            logger.error(f"[ImageGuard] Check failed: {e}")
            await self._bump_audit_stats(audit_failed=1)
        finally:
            self._pending_finish(pending_id)

    # ── 多供应商调用核心 ──────────────────────────────────────

    async def _call_audit_llm(self, prompt: str, image_urls: list[str]) -> str:
        """按 llm_providers 列表顺序依次尝试，第一个成功的即返回。

        列表中每个条目是一个"供应商"，由其 ``__template_key`` 字段区分类型：
        - ``openai_compatible``：通过 OpenAI 兼容 API 调用（需填写 api_key / base_url）
        - ``astrbot_provider``：复用 AstrBot 当前会话配置的 LLM Provider
        """
        providers = self.config.get("llm_providers", [])
        if not providers:
            logger.info("[ImageGuard] 未配置任何供应商，直接使用 AstrBot Provider")
            result = await self._call_astrbot_provider(prompt, image_urls)
            await self._track_provider_usage("AstrBot Provider (fallback)")
            return result

        last_exception = None
        for prov in providers:
            template = prov.get("__template_key", "")
            prov_name = prov.get("name", "Unknown")
            try:
                if template == "astrbot_provider":
                    logger.info(f"[ImageGuard] 尝试 AstrBot Provider「{prov_name}」...")
                    result = await self._call_astrbot_provider(prompt, image_urls)
                    await self._track_provider_usage(prov_name)
                    return result

                else:
                    # 所有非 astrbot_provider 的模板（openai_compatible / modelscope 等）
                    # 均视为 OpenAI 兼容接口处理
                    logger.info(f"[ImageGuard] 尝试 OpenAI 兼容供应商「{prov_name}」...")
                    result = await self._call_single_api(prompt, image_urls, prov)
                    if not result or not result.strip():
                        raise ValueError(f"「{prov_name}」返回内容为空")
                    logger.info(f"[ImageGuard] 供应商「{prov_name}」审核成功")
                    await self._track_provider_usage(prov_name)
                    return result

            except Exception as e:
                last_exception = e
                logger.warning(f"[ImageGuard] 供应商「{prov_name}」调用失败: {e}")
                continue

        # 全部失败
        if last_exception:
            raise RuntimeError(
                f"所有供应商均不可用（共 {len(providers)} 个）"
            ) from last_exception
        raise RuntimeError("没有可用的供应商配置")

    async def _call_single_api(
        self,
        prompt: str,
        image_urls: list[str],
        provider: dict,
        timeout_seconds: float | None = None,
    ) -> str | None:
        """调用单个 OpenAI 兼容 API 进行审核。

        Args:
            provider: 供应商配置字典，包含 api_key / base_url / model / name 等字段。
            timeout_seconds: 覆盖全局超时（连通性测试等短请求使用）。
        """
        api_key = provider.get("api_key", "")
        base_url = provider.get("base_url", "")
        model_name = provider.get("model", "")
        api_name = provider.get("name", "OpenAI API")

        if not api_key or not base_url:
            return None

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt}
                ]
            }
        ]
        for url in image_urls:
            messages[0]["content"].append({
                "type": "image_url",
                "image_url": {"url": url}
            })

        if timeout_seconds is None:
            timeout_seconds = float(self.config.get("llm_timeout_seconds", 120))
        payload = {
            "model": model_name or "gpt-4o",
            "messages": messages,
            "max_tokens": int(self.config.get("llm_max_tokens", 512)),
        }
        reasoning_effort = self.config.get("reasoning_effort", "")
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        resp = await self._http_client.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        response_json = resp.json()
        if self.config.get("debug_log_llm_response", False):
            logger.info(
                f"[ImageGuard] {api_name} 原始响应: "
                f"{json.dumps(response_json, ensure_ascii=False)}"
            )

        choice = response_json["choices"][0]
        message = choice.get("message", {})
        content = message.get("content") or choice.get("text")
        if not content or not str(content).strip():
            finish_reason = choice.get("finish_reason", "unknown")
            raise ValueError(
                f"{api_name} 返回内容为空，finish_reason={finish_reason}"
            )

        return str(content)

    async def _call_astrbot_provider(self, prompt: str, image_urls: list[str]) -> str:
        """回退到 AstrBot 当前会话配置的 LLM Provider。"""
        provider = self.context.get_using_provider()
        if not provider:
            raise ValueError("No provider available")

        resp = await provider.text_chat(
            prompt=prompt,
            image_urls=image_urls,
            session_id=None,
        )
        return resp.completion_text

    # ── 判罚执行 ────────────────────────────────────────────────

    async def enforce_penalty(
        self,
        event: AstrMessageEvent,
        violation_img_url: str,
        is_group: bool,
        reason: str,
        tags: list[str] | None = None,
    ):
        """执行判罚 (依赖 OneBot 协议)"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        user_name = event.get_sender_name()

        recalled = False
        banned = False
        duration = int(self.config.get("ban_duration", 86400))

        client = None
        if hasattr(event, "bot"): client = event.bot
        elif hasattr(event, "client"): client = event.client

        if not client: return
        if not hasattr(client, "api") or not hasattr(client.api, "call_action"):
            return

        # A. 撤回消息
        if self.config.get("enable_recall", True) and is_group:
            try:
                msg_id = None
                if hasattr(event.message_obj, "message_id"):
                    msg_id = event.message_obj.message_id

                if msg_id:
                    await client.api.call_action('delete_msg', message_id=msg_id)
                    recalled = True
            except Exception as e:
                logger.warning(f"[ImageGuard] Silent Recall failed: {e}")

        # B. 禁言用户
        if duration > 0 and is_group:
            try:
                await client.api.call_action(
                    "set_group_ban",
                    group_id=group_id,
                    user_id=user_id,
                    duration=duration
                )
                banned = True
            except Exception as e:
                logger.warning(f"[ImageGuard] Silent Ban failed: {e}")

        # C. 上报证据 (私聊)
        report_target = self._get_report_target(group_id, is_group)
        if report_target:
            try:
                target_type, target_id = report_target
                source_str = f"群 {group_id}" if is_group else "私聊"
                status_str = f"撤回:{'✅' if recalled else '❌'} 禁言:{'✅' if banned else '❌'}"

                text_content = (
                    f"🕵️ [静默执法报告]\n"
                    f"来源: {source_str}\n"
                    f"用户: {user_name} ({user_id})\n"
                    f"理由: {reason}\n"
                    f"状态: {status_str}\n"
                    f"证据:"
                )

                message_payload = [
                    {"type": "text", "data": {"text": text_content}},
                    {"type": "image", "data": {"file": violation_img_url}}
                ]

                if target_type == "group":
                    await client.api.call_action(
                        "send_group_msg",
                        group_id=target_id,
                        message=message_payload
                    )
                else:
                    await client.api.call_action(
                        "send_private_msg",
                        user_id=target_id,
                        message=message_payload
                    )

            except Exception as e:
                logger.error(f"[ImageGuard] Report failed: {e}")

        # D. 保存审核记录
        try:
            await self._save_audit_record(
                event, violation_img_url, reason,
                recalled, banned, duration, is_group, tags,
            )
        except Exception as e:
            logger.error(f"[ImageGuard] 保存审核记录失败: {e}")

    def _get_report_target(self, group_id: str | None, is_group: bool):
        if is_group and group_id:
            for entry in self.config.get("group_report_targets", []):
                # 兼容旧版字符串格式 "来源群号:type:id"
                if isinstance(entry, str):
                    group_text, sep, target_text = entry.partition(":")
                    if not sep or group_text.strip() != str(group_id):
                        continue
                    target_kind, type_sep, target_id_text = target_text.partition(":")
                    try:
                        if type_sep:
                            target_kind = target_kind.strip().lower()
                            target_id = int(target_id_text.strip())
                        else:
                            target_kind = "private"
                            target_id = int(target_text.strip())
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            f"[ImageGuard] 战报目标解析失败 (旧格式): {entry}, {exc}"
                        )
                        continue
                    return target_kind, target_id

                # 新版 template_list 格式
                src_id = entry.get("source_group_id", "").strip()
                if src_id == str(group_id):
                    target_kind = entry.get("target_type", "private").strip().lower()
                    raw_id = entry.get("target_id", "").strip()
                    if not raw_id:
                        continue
                    try:
                        return target_kind, int(raw_id)
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            f"[ImageGuard] 战报目标解析失败 (新模板): {entry}, {exc}"
                        )
                        continue

        report_target = self.config.get("report_target_id")
        if report_target:
            try:
                return "private", int(str(report_target).strip())
            except (ValueError, TypeError) as exc:
                logger.warning(
                    f"[ImageGuard] report_target_id 解析失败: {report_target}, {exc}"
                )

        return None

    # ── 审核历史 ────────────────────────────────────────────────

    async def _save_audit_record(
        self,
        event: AstrMessageEvent,
        image_url: str,
        reason: str,
        recalled: bool,
        banned: bool,
        duration: int,
        is_group: bool,
        tags: list[str] | None = None,
    ) -> None:
        record = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": event.get_sender_id() or "",
            "user_name": event.get_sender_name() or "",
            "group_id": event.get_group_id() or "",
            "image_url": image_url,
            "reason": reason,
            "tags": _normalize_audit_tags(tags),
            "recalled": recalled,
            "banned": banned,
            "ban_duration": duration,
            "is_group": is_group,
        }
        # 持久化原始图片（HTTP URL、本地路径、data URL 均支持）
        if image_url:
            local_path = await self._download_and_save_image(image_url, record["id"])
            if local_path:
                record["local_image"] = local_path
        records = await self.get_kv_data("audit_history", [])
        if not isinstance(records, list):
            records = []
        records.append(record)
        # 最多保留 N 条
        max_records = int(self.config.get("audit_history_max_records", 500))
        if max_records > 0 and len(records) > max_records:
            records = records[-max_records:]
        await self.put_kv_data("audit_history", records)

    async def _track_provider_usage(self, provider_name: str) -> None:
        """记录供应商调用次数"""
        stats = await self.get_kv_data("provider_stats", {})
        if not isinstance(stats, dict):
            stats = {}
        stats[provider_name] = stats.get(provider_name, 0) + 1
        await self.put_kv_data("provider_stats", stats)

    async def _download_and_save_image(self, image_url: str, record_id: str) -> str | None:
        """保存原始图片到持久化目录，返回本地路径或 None"""
        try:
            AUDIT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            ext = ".jpg"
            data = None

            # data: URL — 解码
            if image_url.startswith("data:"):
                header, b64 = image_url.split(",", 1)
                if "image/png" in header:
                    ext = ".png"
                elif "image/gif" in header:
                    ext = ".gif"
                elif "image/webp" in header:
                    ext = ".webp"
                data = base64.b64decode(b64)
            # HTTP/HTTPS 远程 URL — 下载
            elif image_url.startswith(("http://", "https://")):
                resp = await self._http_client.get(image_url)
                resp.raise_for_status()
                data = resp.content
                # 根据 Content-Type 推断扩展名
                content_type = resp.headers.get("content-type", "")
                if "image/png" in content_type:
                    ext = ".png"
                elif "image/gif" in content_type:
                    ext = ".gif"
                elif "image/webp" in content_type:
                    ext = ".webp"
                elif "image/jpeg" in content_type or "image/jpg" in content_type:
                    ext = ".jpg"
            # 本地文件 — 复制
            else:
                src = Path(image_url.replace("file:///", ""))
                if src.exists():
                    ext = src.suffix.lower() or ".jpg"
                    data = src.read_bytes()
                else:
                    logger.warning(f"[ImageGuard] 持久化源文件不存在: {src}")
                    return None

            if data:
                file_path = (AUDIT_IMAGE_DIR / f"{record_id}{ext}").resolve()
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(data)
                return str(file_path)
        except Exception as e:
            logger.warning(f"[ImageGuard] 图片持久化失败: {e}")
            return None

    async def _api_audit_list(self) -> dict:
        records = await self.get_kv_data("audit_history", [])
        if not isinstance(records, list):
            records = []
        for record in records:
            if isinstance(record, dict):
                record["tags"] = _normalize_audit_tags(record.get("tags"))
        total_records = len(records)
        records = records[-PAGE_LIST_LIMIT:]
        stats = await self.get_kv_data("provider_stats", {})
        if not isinstance(stats, dict):
            stats = {}
        # 审核缓存统计（仅在启用时加载）
        cache_enabled = self.config.get("audit_cache_enabled", True)
        if cache_enabled:
            await self._ensure_audit_cache_loaded()
            cache_stats = self._audit_cache.stats
            cache_dict = self._audit_cache.to_dict()
            cache_data_size = len(json.dumps(cache_dict).encode("utf-8"))
        else:
            cache_stats = {
                "total_unique_images": 0,
                "images_skip_audit": 0,
                "threshold": 0,
                "max_entries": 0,
            }
            cache_data_size = 0
        # 为有本地缓存的记录生成 file_token 下载链接（绕过 /api/plug 的 JWT 认证）
        try:
            from astrbot.core import file_token_service
        except ImportError:
            file_token_service = None
            logger.warning("[ImageGuard] file_token_service 不可用，图片将回退到原始 URL")
        for r in records:
            if r.get("local_image"):
                path = Path(r["local_image"])
                if not path.exists():
                    path = Path(r["local_image"]).resolve()
                if path.exists():
                    r["local_image_size"] = path.stat().st_size
                    if file_token_service:
                        try:
                            token = await file_token_service.register_file(str(path), timeout=86400)
                            r["local_image_url"] = f"/api/file/{token}"
                        except Exception as e:
                            logger.warning(f"[ImageGuard] file_token 注册失败: {e}")
        runtime_stats = await self._load_audit_stats()
        records_json = json.dumps(records, ensure_ascii=False).encode("utf-8")
        return {
            "records": records,
            "runtime_stats": runtime_stats,
            "pending": self._pending_snapshot(),
            "provider_stats": stats,
            "cache_stats": cache_stats,
            "storage_size": {
                "audit_records": len(records_json),
                "cache_data": cache_data_size,
            },
            "audit_config": self._audit_config_summary(),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_records": total_records,
            "record_limit": PAGE_LIST_LIMIT,
            "truncated": total_records > len(records),
        }

    async def _api_audit_clear(self):
        await self.put_kv_data("audit_history", [])
        return {"message": "ok"}

    async def _api_audit_delete(self):
        from quart import request
        data = await request.get_json(silent=True) or {}
        record_id = request.args.get("id") or data.get("id")
        if not record_id:
            return {"message": "missing id"}, 400
        records = await self.get_kv_data("audit_history", [])
        if not isinstance(records, list):
            records = []
        records = [r for r in records if r.get("id") != record_id]
        await self.put_kv_data("audit_history", records)
        return {"message": "ok"}

    def _audit_config_summary(self) -> dict:
        """页面头部用的配置摘要，不含任何敏感字段。"""
        providers = _sanitize_providers(self.config.get("llm_providers", []))
        return {
            "group_scope": _as_text_list(self.config.get("group_scope", ["0"])) or ["0"],
            "private_scope": _as_text_list(self.config.get("private_scope", [])),
            "rule_count": len(_as_text_list(self.config.get("sensitive_texts", [])))
            + len(_as_text_list(self.config.get("forbidden_descriptions", []))),
            "provider_count": len(providers),
            "provider_names": [p.get("name", "") for p in providers][:5],
            "check_probability": _as_float(self.config.get("check_probability", 1.0), 1.0),
            "enable_recall": _as_bool(self.config.get("enable_recall", True)),
            "ban_duration": _as_int(self.config.get("ban_duration", 86400), 86400),
            "cache_enabled": _as_bool(self.config.get("audit_cache_enabled", True)),
            "report_enabled": bool(str(self.config.get("report_target_id", "")).strip())
            or bool(_as_list(self.config.get("group_report_targets", []))),
        }

    def _plugin_config_object(self):
        """拿到 AstrBot 持有的 AstrBotConfig（非副本），拿不到时退回实例配置。"""
        from astrbot.core.star.star import star_registry

        for plugin_md in star_registry:
            if plugin_md.name == "astrbot_plugin_image_guard":
                if plugin_md.config:
                    return plugin_md.config
                break
        return self.config

    async def _save_and_reload_plugin(self) -> bool:
        """持久化配置并尝试热重载插件，返回是否重载成功。"""
        config_obj = self._plugin_config_object()
        try:
            config_obj.save_config()
        except Exception as e:
            logger.warning(f"[ImageGuard] 配置保存失败: {e}")
            return False

        try:
            if hasattr(self.context, "reload_plugin"):
                await self.context.reload_plugin("astrbot_plugin_image_guard")
                return True
            if hasattr(self.context, "_star_manager"):
                await self.context._star_manager.reload("astrbot_plugin_image_guard")
                return True
        except Exception as e:
            logger.warning(f"[ImageGuard] 插件重载失败: {e}")
            return False

        logger.warning("[ImageGuard] 找不到 reload 方法，配置已保存但需手动重载插件")
        return False

    async def _api_audit_config_get(self) -> dict:
        """返回当前插件配置（API Key 已掩码，页面不需要明文）。"""
        config_obj = self._plugin_config_object()
        try:
            raw = dict(config_obj)
        except Exception:
            raw = dict(self.config)
        raw["llm_providers"] = _mask_providers(_sanitize_providers(raw.get("llm_providers", [])))
        return raw

    async def _api_audit_config_update(self):
        """按白名单合并配置：页面没提交的字段（如供应商列表）保持原值。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not payload:
            return {"message": "empty config"}, 400

        config_obj = self._plugin_config_object()
        updated: list[str] = []

        for field in _EDITABLE_LIST_FIELDS:
            if field in payload:
                config_obj[field] = _as_text_list(payload[field])
                updated.append(field)

        for field in _EDITABLE_TEXT_FIELDS:
            if field in payload:
                config_obj[field] = str(payload[field] or "").strip()
                updated.append(field)

        for field in _EDITABLE_DICT_LIST_FIELDS:
            if field in payload:
                entries = [item for item in _as_list(payload[field]) if isinstance(item, dict)]
                config_obj[field] = entries
                updated.append(field)

        for field in _EDITABLE_BOOL_FIELDS:
            if field in payload:
                config_obj[field] = _as_bool(payload[field])
                updated.append(field)

        for field in _EDITABLE_INT_FIELDS:
            if field in payload:
                config_obj[field] = _as_int(payload[field], _as_int(self.config.get(field, 0), 0))
                updated.append(field)

        for field in _EDITABLE_FLOAT_FIELDS:
            if field in payload:
                config_obj[field] = _as_float(payload[field], _as_float(self.config.get(field, 0), 0))
                updated.append(field)

        if not updated:
            return {"message": "no editable field in payload"}, 400

        reloaded = await self._save_and_reload_plugin()
        return {
            "message": "ok",
            "updated": updated,
            "reloaded": reloaded,
            "config": self._audit_config_summary(),
        }

    async def _api_audit_cache_clear(self):
        """清空审核缓存指纹（计数从零开始）。"""
        await self._ensure_audit_cache_loaded()
        removed = self._audit_cache.clear()
        await self._save_audit_cache()
        return {"message": "ok", "removed": removed}

    async def _api_audit_provider_test(self):
        """按供应商条目做一次最小请求，验证地址、Key 与模型是否可用。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        provider = payload.get("provider") if isinstance(payload, dict) else None
        if not isinstance(provider, dict):
            return {"message": "missing provider"}, 400

        entries = _sanitize_providers([provider])
        if not entries:
            return {"message": "invalid provider"}, 400
        entry = entries[0]
        template = entry.get("__template_key", "")
        name = entry.get("name", "Unknown")

        if template == "astrbot_provider":
            try:
                result = await asyncio.wait_for(
                    self._call_astrbot_provider(
                        "连通性测试：请只回复 OK 两个字母。", []
                    ),
                    timeout=CONNECTIVITY_TEST_TIMEOUT,
                )
                return {
                    "ok": True,
                    "provider": name,
                    "message": f"AstrBot Provider 可用，返回：{str(result).strip()[:60]}",
                }
            except Exception as e:
                return {"ok": False, "provider": name, "message": f"调用失败：{e}"}

        # 掩码值意味着用户没有改动 Key，回退到磁盘上已保存的那一把
        api_key = str(entry.get("api_key") or "")
        if SECRET_MASK in api_key:
            saved = self._find_saved_provider(name)
            api_key = str(saved.get("api_key") or "") if saved else ""

        if not api_key or not entry.get("base_url"):
            return {"ok": False, "provider": name, "message": "缺少 API Key 或 API 地址"}

        probe = dict(entry)
        probe["api_key"] = api_key
        timeout_seconds = min(
            max(_as_float(self.config.get("llm_timeout_seconds", 120), 120), 5.0),
            CONNECTIVITY_TEST_TIMEOUT,
        )
        try:
            result = await self._call_single_api(
                "连通性测试：请只回复 OK 两个字母。", [], probe, timeout_seconds
            )
            return {
                "ok": True,
                "provider": name,
                "message": f"接口可用，返回：{str(result).strip()[:60]}",
            }
        except Exception as e:
            return {"ok": False, "provider": name, "message": f"调用失败：{e}"}

    def _find_saved_provider(self, name: str) -> dict | None:
        """按名称回查磁盘上已保存的供应商（用于掩码 Key 的回填）。"""
        for entry in _sanitize_providers(self.config.get("llm_providers", [])):
            if entry.get("name") == name:
                return entry
        return None

    async def _api_audit_providers_save(self):
        """整体替换供应商列表（页面弹窗里增删改排序后的结果）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        raw_providers = payload.get("providers") if isinstance(payload, dict) else None
        if not isinstance(raw_providers, list):
            return {"message": "providers must be a list"}, 400

        providers = _sanitize_providers(raw_providers, keep_index=True)
        saved_list = _sanitize_providers(self.config.get("llm_providers", []))
        for index, entry in enumerate(providers):
            for field in SECRET_FIELDS:
                value = str(entry.get(field) or "")
                if SECRET_MASK not in value:
                    continue
                # 页面回传的是掩码，说明这项没改：沿用已保存的明文
                saved = self._find_saved_provider(entry.get("name", ""))
                origin = _as_int(entry.get("__index", index), index)
                if not saved and 0 <= origin < len(saved_list):
                    candidate = saved_list[origin]
                    if candidate.get("__template_key") == entry.get("__template_key"):
                        saved = candidate
                if not saved and 0 <= origin < len(saved_list):
                    saved = saved_list[origin]
                entry[field] = str(saved.get(field) or "") if saved else ""

        for entry in providers:
            entry.pop("__index", None)

        config_obj = self._plugin_config_object()
        config_obj["llm_providers"] = providers
        reloaded = await self._save_and_reload_plugin()
        return {
            "message": "ok",
            "count": len(providers),
            "reloaded": reloaded,
            "providers": _mask_providers(providers),
            "config": self._audit_config_summary(),
        }

    async def _api_audit_storage_prune(self):
        """删除本地已不存在记录的审核图片，释放磁盘空间。"""
        try:
            records = await self.get_kv_data("audit_history", [])
            if not isinstance(records, list):
                records = []
            known = {
                str(Path(r.get("local_image")).resolve())
                for r in records
                if isinstance(r, dict) and r.get("local_image")
            }

            if not AUDIT_IMAGE_DIR.exists():
                return {"message": "ok", "removed": 0, "freed_bytes": 0}

            removed = 0
            freed = 0
            for path in AUDIT_IMAGE_DIR.iterdir():
                if not path.is_file():
                    continue
                if str(path.resolve()) in known:
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    removed += 1
                    freed += size
                except OSError as e:
                    logger.warning(f"[ImageGuard] 清理图片失败 {path}: {e}")
            return {"message": "ok", "removed": removed, "freed_bytes": freed}
        except Exception as e:
            logger.error(f"[ImageGuard] 清理本地图片失败: {e}")
            return {"message": f"prune failed: {e}"}, 500

    # ── 生命周期 ────────────────────────────────────────────────

    async def terminate(self):
        """插件卸载时清理资源。"""
        await self._http_client.aclose()
