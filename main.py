import httpx
import re
import random
import json
from pathlib import Path
from .image_processor import prepare_audit_images
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Image, Plain

@register("image_guard", "YEZI", "图片内容审查卫士", "1.6.6") # 版本号升级
class ImageGuard(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

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

        # === 2. 表情包与GIF强过滤 (Sticker Filter) ===
        raw_chain = []
        try:
            if hasattr(event, "original_event") and hasattr(event.original_event, "message"):
                raw_chain = event.original_event.message
            elif hasattr(event.message_obj, "raw_message"):
                raw_chain = event.message_obj.raw_message
            
            if isinstance(raw_chain, list):
                for seg in raw_chain:
                    if isinstance(seg, dict) and seg.get("type") == "image":
                        data = seg.get("data", {})
                        sub_type = int(data.get("sub_type", 0))
                        if sub_type != 0: return # 忽略表情包
        except Exception:
            pass 

        # === 3. 提取图片 URL 并过滤 GIF ===
        message_obj = event.message_obj
        if not message_obj.message: return
            
        image_urls = []
        for component in message_obj.message:
            if isinstance(component, Image):
                if component.url:
                    clean_url = component.url.split('?')[0].lower()
                    if clean_url.endswith('.gif'):
                        continue
                    image_urls.append(component.url)
        
        if not image_urls: return

        # === 4. 概率抽查 ===
        if random.random() > self.config.get("check_probability", 1.0): return

        # === 5. 检查配置 ===
        forbidden_texts = self.config.get("sensitive_texts", [])
        forbidden_descs = self.config.get("forbidden_descriptions", [])
        
        if not forbidden_texts and not forbidden_descs: return

        # === 6. 审核逻辑 ===
        custom_instruction = self.config.get("custom_vision_prompt", "")
        prompt = (
            "你是一个严格但公正的内容审核员。请分析图片是否包含违规信息。\n"
            f"【自定义关注点】\n{custom_instruction}\n\n"
            "【违规标准】\n"
            f"1. 包含文字：{str(forbidden_texts)}\n"
            f"2. 包含画面：{str(forbidden_descs)}\n\n"
            "【输出格式要求】\n"
            "请严格按照以下两行格式输出，不要包含其他废话：\n"
            "REASON: [这里简要说明判断理由，不超过20字]\n"
            "RESULT: [SAFE 或 VIOLATION]\n"
        )

        try:
            # [Fix] 优先使用独立配置的 LLM
            max_image_bytes = int(self.config.get("compressed_image_max_bytes", 1048576))
            keep_compressed_image_in_temp = bool(
                self.config.get("keep_compressed_image_in_temp", False)
            )
            audit_image_urls = await prepare_audit_images(
                image_urls,
                max_image_bytes,
                keep_compressed_image_in_temp=keep_compressed_image_in_temp,
                compressed_image_temp_dir=Path("data") / "temp" / "astrbot_plugin_image_guard",
                log_compression_result=logger.info,
            )
            response_text = await self._call_audit_llm(prompt, audit_image_urls)
            if self.config.get("debug_log_llm_response", False):
                logger.info(f"[ImageGuard] LLM 返回内容: {response_text}")
            
            # === 7. 解析结果 ===
            result_match = re.search(r"RESULT:\s*(VIOLATION|SAFE)", response_text, re.IGNORECASE)
            reason_match = re.search(r"REASON:\s*(.+)", response_text, re.IGNORECASE)
            
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

            # === 8. 判罚 ===
            if is_violation:
                logger.info(f"[ImageGuard] 违规命中: {reason_str}")
                await self.enforce_penalty(event, image_urls[0], is_group, reason_str)
                
        except Exception as e:
            logger.error(f"[ImageGuard] Check failed: {e}")

    async def _call_single_api(self, prompt, image_urls, api_key, base_url, model_name, api_name="API"):
        """调用单个API进行审核"""
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
        # 添加图片
        for url in image_urls:
            messages[0]["content"].append({
                "type": "image_url",
                "image_url": {"url": url}
            })

        timeout_seconds = float(self.config.get("llm_timeout_seconds", 120))
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            payload = {
                "model": model_name or "gpt-4o",
                "messages": messages,
                "max_tokens": int(self.config.get("llm_max_tokens", 512))
            }
            reasoning_effort = self.config.get("reasoning_effort", "")
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
            resp = await client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"}
            )
            resp.raise_for_status()
            response_json = resp.json()
            if self.config.get("debug_log_llm_response", False):
                logger.info(f"[ImageGuard] {api_name} 原始响应: {json.dumps(response_json, ensure_ascii=False)}")

            choice = response_json["choices"][0]
            message = choice.get("message", {})
            content = message.get("content") or choice.get("text")
            if not content or not str(content).strip():
                finish_reason = choice.get("finish_reason", "unknown")
                raise ValueError(f"{api_name} 返回内容为空，finish_reason={finish_reason}")

            return str(content)

    async def _call_audit_llm(self, prompt, image_urls):
        """支持API切换：依次尝试API1→API2→...→API8→AstrBot Provider"""
        # 收集所有API配置
        api_configs = []
        for i in range(1, 9):
            suffix = "" if i == 1 else f"_{i}"
            api_key = self.config.get(f"llm_api_key{suffix}")
            api_base = self.config.get(f"llm_base_url{suffix}")
            api_model = self.config.get(f"llm_model{suffix}")
            if api_key and api_base:
                api_configs.append((f"API{i}", api_key, api_base, api_model))
        
        # 依次尝试每个API
        for api_name, api_key, api_base, api_model in api_configs:
            try:
                logger.info(f"[ImageGuard] 尝试使用 {api_name} 进行审核...")
                result = await self._call_single_api(prompt, image_urls, api_key, api_base, api_model, api_name)
                if not result or not result.strip():
                    raise ValueError(f"{api_name} 返回内容为空")
                logger.info(f"[ImageGuard] {api_name} 审核成功")
                return result
            except Exception as e:
                logger.warning(f"[ImageGuard] {api_name} 调用失败: {e}")
        
        # 所有API都失败，回退到 AstrBot Provider
        logger.info("[ImageGuard] 尝试使用 AstrBot Provider 回退模式...")
        provider = self.context.get_using_provider()
        if not provider:
            raise ValueError("No provider available")
        
        resp = await provider.text_chat(
            prompt=prompt,
            image_urls=image_urls,
            session_id=None
        )
        return resp.completion_text

    async def enforce_penalty(self, event: AstrMessageEvent, violation_img_url: str, is_group: bool, reason: str):
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

    def _get_report_target(self, group_id: str | None, is_group: bool):
        if is_group and group_id:
            group_targets = self.config.get("group_report_targets", [])
            for item in group_targets:
                group_text, separator, target_text = str(item).partition(":")
                if not separator or group_text.strip() != str(group_id):
                    continue

                target_kind_text, kind_separator, target_id_text = target_text.partition(":")
                if kind_separator:
                    target_kind = target_kind_text.strip().lower()
                    target_id = int(target_id_text.strip())
                else:
                    target_kind = "private"
                    target_id = int(target_text.strip())

                return target_kind, target_id

        report_target = self.config.get("report_target_id")
        if report_target:
            return "private", int(str(report_target).strip())

        return None
