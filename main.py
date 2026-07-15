import httpx
import re
import random
import json
import base64
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


@register("image_guard", "YEZI", "图片内容审查卫士", "1.7.3")
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

        # === 3. 提取图片路径并过滤 GIF ===
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
                if path.exists() and path.suffix.lower() != ".gif":
                    image_paths.append(path)

        if not image_paths: return

        # === 4. 压缩所有图片为 data URL ===
        max_image_bytes = int(self.config.get("compressed_image_max_bytes", 1048576))
        keep_temp = self.config.get("keep_compressed_image_in_temp", False)
        image_urls = []
        for index, path in enumerate(image_paths, start=1):
            try:
                result = compress_image_with_result(path.read_bytes(), max_image_bytes)
                if keep_temp:
                    result = _save_compressed_image_to_temp(
                        result,
                        str(path),
                        _resolve_compressed_image_temp_dir(None),
                    )
                image_urls.append(result.data_url)
                logger.info(_format_compression_result(index, len(image_paths), result))
            except Exception as e:
                logger.warning(f"[ImageGuard] 压缩图片失败 {path}: {e}")

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
            return

        # === 7. 检查配置 ===
        forbidden_texts = self.config.get("sensitive_texts", [])
        forbidden_descs = self.config.get("forbidden_descriptions", [])

        if not forbidden_texts and not forbidden_descs:
            logger.info("[ImageGuard] 未配置审查规则，跳过")
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

        except Exception as e:
            logger.error(f"[ImageGuard] Check failed: {e}")

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

    async def _call_single_api(self, prompt: str, image_urls: list[str], provider: dict) -> str | None:
        """调用单个 OpenAI 兼容 API 进行审核。

        Args:
            provider: 供应商配置字典，包含 api_key / base_url / model / name 等字段。
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
        records_json = json.dumps(records, ensure_ascii=False).encode("utf-8")
        return {
            "records": records,
            "provider_stats": stats,
            "cache_stats": cache_stats,
            "storage_size": {
                "audit_records": len(records_json),
                "cache_data": cache_data_size,
            },
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

    async def _api_audit_config_get(self) -> dict:
        """返回当前插件配置（排除 KV 类数据）"""
        from astrbot.core.star.star import star_registry
        for plugin_md in star_registry:
            if plugin_md.name == "astrbot_plugin_image_guard":
                if plugin_md.config:
                    return dict(plugin_md.config)
                break
        return dict(self.config)

    async def _api_audit_config_update(self):
        """更新插件配置并重载插件"""
        from quart import request
        from astrbot.core.star.star import star_registry

        new_config = await request.get_json()
        if not new_config:
            return {"message": "empty config"}, 400

        for plugin_md in star_registry:
            if plugin_md.name == "astrbot_plugin_image_guard":
                if plugin_md.config:
                    plugin_md.config.save_config(new_config)
                    # 重载插件使新配置生效
                    try:
                        reloaded = False
                        # 优先使用公开 API
                        if hasattr(self.context, "reload_plugin"):
                            await self.context.reload_plugin("astrbot_plugin_image_guard")
                            reloaded = True
                        elif hasattr(self.context, "_star_manager"):
                            await self.context._star_manager.reload("astrbot_plugin_image_guard")
                            reloaded = True
                        if not reloaded:
                            logger.warning("[ImageGuard] 找不到 reload 方法，配置已保存但需手动重载插件")
                    except Exception as e:
                        logger.warning(f"[ImageGuard] 插件重载失败: {e}")
                    return {"message": "ok"}
                break

        return {"message": "plugin config not found"}, 404

    # ── 生命周期 ────────────────────────────────────────────────

    async def terminate(self):
        """插件卸载时清理资源。"""
        await self._http_client.aclose()
