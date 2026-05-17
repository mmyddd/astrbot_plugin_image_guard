import inspect
import random
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register


VERSION = "1.7.3"


@dataclass(slots=True)
class ImageCandidate:
    url: str
    raw: dict[str, Any] | None = None


@register("image_guard", "YEZI", "图片内容审查卫士", VERSION)
class ImageGuard(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_image_message(self, event: AstrMessageEvent):
        if not self._in_scope(event):
            return

        image_urls = await self._image_urls(event)
        if not image_urls:
            return

        if random.random() > self._float_config("check_probability", 1.0, 0.0, 1.0):
            return

        sensitive_texts = self._list_config("sensitive_texts")
        forbidden_descriptions = self._list_config("forbidden_descriptions")
        if not sensitive_texts and not forbidden_descriptions:
            return

        try:
            response_text = await self._audit_images(
                self._prompt(sensitive_texts, forbidden_descriptions),
                image_urls,
            )
            result, reason = self._audit_result(response_text)
            if result != "VIOLATION":
                return

            logger.info(f"[ImageGuard] 违规命中: {reason}")
            await self._enforce(event, image_urls[0], reason)
            self._stop_event(event)
        except Exception as error:
            logger.error(f"[ImageGuard] 审查失败: {error}")

    def _in_scope(self, event: AstrMessageEvent) -> bool:
        group_id = self._text(event.get_group_id())
        user_id = self._text(event.get_sender_id())
        target_id = group_id or user_id
        scope = self._list_config("group_scope", ["0"]) if group_id else self._list_config("private_scope")
        return bool(target_id and ("0" in scope or target_id in scope))

    async def _image_urls(self, event: AstrMessageEvent) -> list[str]:
        candidates = await self._image_candidates(event)
        ignored_raw_urls = {self._url_key(candidate.url) for candidate in candidates if candidate.raw and self._ignored_raw_image(candidate.raw)}
        limit = self._int_config("max_images_per_message", 4, 1, 50)
        urls: list[str] = []
        seen: set[str] = set()

        for candidate in candidates:
            key = self._url_key(candidate.url)
            if not key or key in seen:
                continue
            if candidate.raw and self._ignored_raw_image(candidate.raw):
                ignored_raw_urls.add(key)
                continue
            if not candidate.raw and key in ignored_raw_urls:
                continue
            if self._ignored_url(candidate.url):
                continue

            urls.append(candidate.url)
            seen.add(key)
            if len(urls) >= limit:
                break

        return urls

    async def _image_candidates(self, event: AstrMessageEvent) -> list[ImageCandidate]:
        message_obj = getattr(event, "message_obj", None)
        component_root = getattr(message_obj, "message", None)
        raw_root = self._raw_message(event)
        candidates: list[ImageCandidate] = []

        candidates.extend(self._raw_image_candidates(raw_root))
        candidates.extend(await self._component_image_candidates(component_root))
        return candidates

    def _raw_message(self, event: AstrMessageEvent) -> Any:
        original_event = getattr(event, "original_event", None)
        if hasattr(original_event, "message"):
            return original_event.message

        message_obj = getattr(event, "message_obj", None)
        return getattr(message_obj, "raw_message", None)

    def _raw_image_candidates(self, payload: Any) -> list[ImageCandidate]:
        if isinstance(payload, list | tuple):
            candidates: list[ImageCandidate] = []
            for item in payload:
                candidates.extend(self._raw_image_candidates(item))
            return candidates

        if not isinstance(payload, dict):
            return []

        candidates = []
        segment_type = self._text(payload.get("type")).lower()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

        if segment_type == "image":
            url = self._raw_image_url(data)
            if url:
                candidates.append(ImageCandidate(url=url, raw=payload))

        return candidates

    async def _component_image_candidates(self, payload: Any) -> list[ImageCandidate]:
        if payload is None:
            return []

        if isinstance(payload, Image):
            url = await self._component_image_url(payload)
            return [ImageCandidate(url=url)] if url else []

        if isinstance(payload, MessageChain):
            return await self._component_image_candidates(payload.chain)

        if isinstance(payload, list | tuple):
            candidates: list[ImageCandidate] = []
            for item in payload:
                candidates.extend(await self._component_image_candidates(item))
            return candidates

        return []

    async def _component_image_url(self, component: Image) -> str:
        for name in ("url", "file", "path"):
            url = self._normalize_image_value(getattr(component, name, ""))
            if url:
                return url

        convert_to_base64 = getattr(component, "convert_to_base64", None)
        if callable(convert_to_base64):
            try:
                base64_data = await convert_to_base64()
                return self._normalize_image_value(f"base64://{base64_data}")
            except Exception as error:
                logger.warning(f"[ImageGuard] 图片组件转 base64 失败: {error}")

        return ""

    def _raw_image_url(self, data: dict[str, Any]) -> str:
        for name in ("url", "file", "path"):
            url = self._normalize_image_value(data.get(name))
            if url:
                return url
        return ""

    def _normalize_image_value(self, value: Any) -> str:
        text = self._text(value)
        if not text:
            return ""
        if text.startswith(("http://", "https://", "data:image/")):
            return text
        if text.startswith("base64://"):
            return f"data:image/jpeg;base64,{text.removeprefix('base64://')}"
        return ""

    def _ignored_image(self, candidate: ImageCandidate) -> bool:
        return bool(candidate.raw and self._ignored_raw_image(candidate.raw)) or self._ignored_url(candidate.url)

    def _ignored_raw_image(self, raw: dict[str, Any]) -> bool:
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        sub_type = self._text(data.get("sub_type") or data.get("subType")).lower()
        summary = self._text(data.get("summary")).lower()
        file_name = self._text(data.get("file") or data.get("url")).lower()

        if self._bool_config("ignore_stickers", True) and self._raw_image_is_sticker(data, sub_type, summary):
            return True

        return self._ignored_url(file_name)

    def _raw_image_is_sticker(self, data: dict[str, Any], sub_type: str, summary: str) -> bool:
        if sub_type in {"1", "sticker", "emoji", "face", "marketface", "market_face"}:
            return True

        if any(data.get(name) for name in ("emoji_id", "emoji_package_id", "key")):
            return True

        return any(word in summary for word in ("表情", "贴图", "sticker", "emoji"))

    def _ignored_url(self, value: str) -> bool:
        if not self._bool_config("ignore_gif", True):
            return False

        if value.lower().startswith(("data:image/gif", "data:image/apng")):
            return True

        parsed = urlparse(value)
        path = (parsed.path or value).lower()
        return path.endswith((".gif", ".apng"))

    def _url_key(self, url: str) -> str:
        if url.startswith("data:image/"):
            return url[:80]
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".lower() if parsed.scheme and parsed.netloc else url.lower()

    def _prompt(self, sensitive_texts: list[str], forbidden_descriptions: list[str]) -> str:
        custom_prompt = self._text(self.config.get("custom_vision_prompt"))
        text_rules = "、".join(sensitive_texts) or "无"
        image_rules = "、".join(forbidden_descriptions) or "无"

        return (
            "你是图片内容审核器，只判断用户上传的静态图片是否命中指定规则。\n"
            "不要因为相似、联想、地区、风格或弱相关内容判违规；证据不足必须判 SAFE。\n"
            f"自定义要求：{custom_prompt or '无'}\n"
            f"禁止文字：{text_rules}\n"
            f"禁止画面：{image_rules}\n"
            "只输出两行：\n"
            "REASON: 不超过20字的理由\n"
            "RESULT: SAFE 或 VIOLATION"
        )

    async def _audit_images(self, prompt: str, image_urls: list[str]) -> str:
        provider_id = self._text(self.config.get("audit_provider_id"))
        provider = self.context.get_provider_by_id(provider_id) if provider_id else self.context.get_using_provider()
        if not provider:
            raise RuntimeError("未配置可用的大模型服务")

        kwargs = {"prompt": prompt, "image_urls": image_urls}
        if "session_id" in inspect.signature(provider.text_chat).parameters:
            kwargs["session_id"] = None

        response = await provider.text_chat(**kwargs)
        return self._text(getattr(response, "completion_text", response))

    def _audit_result(self, response_text: str) -> tuple[str, str]:
        result_match = re.search(r"^\s*RESULT\s*[:：]\s*(SAFE|VIOLATION)\b", response_text, re.IGNORECASE | re.MULTILINE)
        reason_match = re.search(r"^\s*REASON\s*[:：]\s*(.+)$", response_text, re.IGNORECASE | re.MULTILINE)

        result = result_match.group(1).upper() if result_match else "SAFE"
        reason = reason_match.group(1).strip() if reason_match else "未说明理由"
        return result, reason[:80]

    async def _enforce(self, event: AstrMessageEvent, image_url: str, reason: str) -> None:
        group_id = self._text(event.get_group_id())
        user_id = self._text(event.get_sender_id())
        user_name = self._text(event.get_sender_name()) or user_id
        call_action = self._call_action(event)

        recalled = await self._recall(event, call_action, bool(group_id))
        banned = await self._ban(call_action, group_id, user_id)
        await self._report(event, call_action, image_url, reason, group_id, user_id, user_name, recalled, banned)

    async def _recall(self, event: AstrMessageEvent, call_action, is_group: bool) -> bool:
        if not call_action or not is_group or not self._bool_config("enable_recall", True):
            return False

        message_id = self._text(getattr(getattr(event, "message_obj", None), "message_id", ""))
        if not message_id:
            return False

        try:
            await call_action("delete_msg", message_id=message_id)
            return True
        except Exception as error:
            logger.warning(f"[ImageGuard] 撤回失败: {error}")
            return False

    async def _ban(self, call_action, group_id: str, user_id: str) -> bool:
        duration = self._int_config("ban_duration", 86400, 0, 2592000)
        if not call_action or not group_id or not user_id or duration <= 0:
            return False

        try:
            await call_action("set_group_ban", group_id=group_id, user_id=user_id, duration=duration)
            return True
        except Exception as error:
            logger.warning(f"[ImageGuard] 禁言失败: {error}")
            return False

    async def _report(
        self,
        event: AstrMessageEvent,
        call_action,
        image_url: str,
        reason: str,
        group_id: str,
        user_id: str,
        user_name: str,
        recalled: bool,
        banned: bool,
    ) -> None:
        target = self._text(self.config.get("report_target_id"))
        if not target:
            return

        text = self._report_text(reason, group_id, user_id, user_name, recalled, banned)

        try:
            if target.isdigit() and call_action:
                await call_action(
                    "send_private_msg",
                    user_id=int(target),
                    message=[
                        {"type": "text", "data": {"text": text}},
                        {"type": "image", "data": {"file": image_url}},
                    ],
                )
                return

            chain = MessageChain().message(text).url_image(image_url)
            await self.context.send_message(target, chain)
        except Exception as error:
            logger.error(f"[ImageGuard] 上报失败: {error}")

    def _report_text(self, reason: str, group_id: str, user_id: str, user_name: str, recalled: bool, banned: bool) -> str:
        source = f"群 {group_id}" if group_id else "私聊"
        recall_status = "成功" if recalled else "未执行"
        ban_status = "成功" if banned else "未执行"
        return (
            "[图片审核报告]\n"
            f"来源: {source}\n"
            f"用户: {user_name} ({user_id})\n"
            f"理由: {reason}\n"
            f"撤回: {recall_status}\n"
            f"禁言: {ban_status}\n"
            "证据:"
        )

    def _call_action(self, event: AstrMessageEvent):
        for owner in (getattr(event, "bot", None), getattr(event, "client", None)):
            call_action = getattr(getattr(owner, "api", None), "call_action", None) or getattr(owner, "call_action", None)
            if callable(call_action):
                return call_action
        return None

    def _stop_event(self, event: AstrMessageEvent) -> None:
        stop_event = getattr(event, "stop_event", None)
        if callable(stop_event):
            stop_event()

    def _list_config(self, name: str, default: list[str] | None = None) -> list[str]:
        value = self.config.get(name, default or [])
        if isinstance(value, str):
            value = re.split(r"[,，\n]", value)
        if not isinstance(value, list):
            return []
        return [item for item in (self._text(item) for item in value) if item]

    def _bool_config(self, name: str, default: bool) -> bool:
        value = self.config.get(name, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "是", "开启"}
        return bool(value)

    def _int_config(self, name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self.config.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _float_config(self, name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(self.config.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _text(self, value: Any) -> str:
        return "" if value is None else str(value).strip()
