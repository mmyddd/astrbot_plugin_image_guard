"""SystemOne 门控：视觉大模型之前的低成本前置筛选。

门控只回答一个问题——这张图值不值得花钱送 LLM 复核。它拿到的概率
仅用于本地分流，不进入任何提示词、不进入历史记录：LLM 侧看到的规则、
问题与上下文和门控关闭时完全一致，避免先入为主。
"""

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from .image_processor import (
    compress_image_with_result,
    detect_image_mime,
    encode_image_to_data_url,
    is_gate_accepted,
    to_gate_accepted_data_url,
)

GATE_DEFAULT_BASE_URL = "https://openrouter.ai/api"
"""未配置 base_url 时的默认网关。"""

GATE_DEFAULT_MODEL = "cloudflare/clef-flash"
"""默认门控模型：便宜、低延迟、支持图文联合判定。"""

GATE_DEFAULT_QUESTION = "Does the image contain any item from the violation list above?"
"""默认 noul 问题。门控只回答命中与否，不生成任何文字。"""

GATE_DEFAULT_THRESHOLD = 0.30
"""违规概率阈值：noul 低于它直接放行，高于它才送 LLM 复核。"""

GATE_DEFAULT_MAX_IMAGE_BYTES = 300000
"""原始图超过此字节数才走压缩；文档建议每张控制在 300KB 内。"""

GATE_DEFAULT_TIMEOUT = 15.0
"""单次门控请求超时。门控必须比视觉 LLM 快得多才有降本意义。"""

GATE_TEXT_TOKEN_BUDGET = 1800
"""state 文本预算。Workers AI 只读前约 2000 token，超出静默丢弃。"""

def _as_text_list(raw: object) -> list[str]:
    """把任意来源的规则列表规整成去空白的字符串列表。"""
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    elif isinstance(raw, str):
        values = [part for part in re.split(r"[,，\n]", raw)]
    else:
        values = []
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            result.append(text)
    return result


def resolve_gate_rules(config: dict) -> tuple[list[str], list[str], str]:
    """合并门控判据：覆盖项留空即继承共享规则。

    规则同源是门控降本成立的前提——只有 LLM 与门控按同一份清单判断，
    门控筛掉的图才等价于「LLM 也会放行」。所以做成「留空继承」而非
    「留空清空」：用户在页面清空输入框时拿到的是共享规则，而不是空清单。
    """
    texts = _as_text_list(config.get("systemone_sensitive_texts", [])) or _as_text_list(
        config.get("sensitive_texts", [])
    )
    descriptions = _as_text_list(config.get("systemone_forbidden_descriptions", [])) or _as_text_list(
        config.get("forbidden_descriptions", [])
    )
    prompt = str(config.get("systemone_vision_prompt", "") or "").strip() or str(
        config.get("custom_vision_prompt", "") or ""
    ).strip()
    return texts, descriptions, prompt


def resolve_gate_endpoint(base_url: str, endpoint_path: str) -> str:
    """按 base_url 形态推断 Decisions 端点，endpoint_path 非空时优先。"""
    explicit = str(endpoint_path or "").strip()
    if explicit:
        return explicit if explicit.startswith("/") else f"/{explicit}"

    normalized = str(base_url or "").rstrip("/")
    if not normalized:
        return "/alpha/decisions"
    # /api/v1 这类同时命中两段的情况，v1 段更具体，优先按 /decisions 处理
    if normalized.endswith("/v1"):
        return "/decisions"
    return "/alpha/decisions"


def build_gate_url(base_url: str, endpoint_path: str) -> str:
    """拼出完整请求地址。base_url 为空时回退默认网关。"""
    root = str(base_url or "").strip() or GATE_DEFAULT_BASE_URL
    return f"{root.rstrip(chr(47))}{resolve_gate_endpoint(root, endpoint_path)}"


def build_gate_state(text: str, data_url: str) -> list:
    """组装 Decisions 的 state。

    文本项必须是纯字符串而不是 text part——门控不解析 content part，只认
    裸字符串；写成 part 会被当成无效项静默忽略，整条 state 只剩图片。
    图片 part 放在顶层数组，嵌在其他对象里的图片不会被读取。
    """
    return [text, {"type": "image_url", "image_url": {"url": data_url}}]


def parse_gate_probability(payload: object, question_name: str) -> float:
    """从响应里取出 noul 概率，兼容多种服务商方言。

    依次尝试 answers.*.noul、answers.*.probability、以及 data.result.answers
    （网关代理常见的再包一层）。取不到就抛错，由调用方按 fail-open 处理
    ——宁可多花一次 LLM 的钱，也不把门控故障误判成安全。
    """
    if not isinstance(payload, dict):
        raise ValueError("门控响应不是 JSON 对象")

    result = payload.get("result")
    data = payload.get("data")
    nested = data.get("result") if isinstance(data, dict) else None
    candidates = [
        payload.get("answers"),
        result.get("answers") if isinstance(result, dict) else None,
        nested.get("answers") if isinstance(nested, dict) else None,
    ]

    for container in candidates:
        if not isinstance(container, dict):
            continue
        answer = container.get(question_name)
        if not isinstance(answer, dict):
            continue
        for key in ("noul", "probability"):
            value = answer.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            number = float(value)
            if 0.0 <= number <= 1.0:
                return number
    raise ValueError(f"门控响应中找不到 {question_name} 的概率字段")


def _extract_usage(payload: dict, key: str) -> float:
    """从 usage 里取数值字段，兼容 data.result 嵌套形态。"""
    result = payload.get("result")
    data = payload.get("data")
    nested = data.get("result") if isinstance(data, dict) else None
    containers = [payload.get("usage")]
    if isinstance(result, dict):
        containers.append(result.get("usage"))
    if isinstance(nested, dict):
        containers.append(nested.get("usage"))
    for container in containers:
        if isinstance(container, dict):
            value = container.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return 0.0


def _extract_error_detail(resp, url: str) -> str:
    """从错误响应里取出可读原因，并附上排查建议。

    OpenRouter 的错误信封是 {"error": {"message": ..., "code": ...}}，
    但网关代理与官方错误格式不一，这里逐级兜底取 message，取不到就退回
    响应正文片段。状态码单独列出，避免用户只看到一串 URL 猜不出问题。
    """
    status = getattr(resp, "status_code", "?")
    hint = ""
    body_text = ""
    try:
        raw = resp.json()
    except Exception:
        raw = None

    if isinstance(raw, dict):
        err = raw.get("error")
        if isinstance(err, dict):
            hint = str(err.get("message") or err.get("code") or "").strip()
        elif isinstance(err, str):
            hint = err.strip()
        elif isinstance(raw.get("detail"), str):
            hint = raw["detail"].strip()
        if not hint:
            hint = str(raw.get("message") or raw.get("detail") or "").strip()
    if not hint:
        try:
            body_text = str(resp.text or "").strip()[:300]
        except Exception:
            body_text = ""
        hint = body_text or "响应正文为空"

    if status == 401:
        advice = "请检查门控 API Key 是否正确、是否已过期或已被撤销"
    elif status == 402:
        advice = "账户额度不足，请充值或更换 Key"
    elif status == 403:
        advice = "该 Key 无权访问此模型或端点，请检查模型名与网关地址"
    elif status == 404:
        advice = "端点路径不存在，请核对网关地址或端点路径"
    elif status == 413:
        advice = "图片超出模型 token 窗口，请调小门控单图字节上限"
    elif status == 429:
        advice = "触发限流或额度上限，请稍后重试"
    else:
        advice = ""

    parts = [hint]
    if advice:
        parts.append(advice)
    parts.append(url)
    return "；".join(parts)


def _extract_provider(payload: dict) -> str:
    """取出实际服务的上游模型名，仅用于日志排查。"""
    data = payload.get("data")
    sources = [payload, payload.get("result")]
    if isinstance(data, dict):
        sources.append(data.get("result"))
    for source in sources:
        if isinstance(source, dict):
            name = source.get("provider")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""

def build_gate_text(
    texts: list[str],
    descriptions: list[str],
    vision_prompt: str,
    question: str,
) -> str:
    """拼出门控侧 state 文本：规则清单 + 引导词 + 问题。

    超预算时按规则条目裁剪，但问题句永远保留——它是判准主体，丢了
    模型就没有明确的判定目标，只能凭规则文本自由发挥。
    """
    head: list[str] = []
    if texts:
        head.append("【文字类（OCR）】")
        head.extend(f"- {item}" for item in texts)
    if descriptions:
        head.append("【画面类（语义）】")
        head.extend(f"- {item}" for item in descriptions)
    if vision_prompt:
        head.append("【额外关注点】")
        head.append(vision_prompt)

    question_block = f"【问题】\n{question}" if question else ""
    body = "\n".join(head)
    budget_chars = GATE_TEXT_TOKEN_BUDGET * 4
    if len(body) + len(question_block) > budget_chars and body:
        body = body[: max(0, budget_chars - len(question_block))].rstrip()
    if question_block:
        return f"{body}\n{question_block}".strip()
    return body.strip()


@dataclass(frozen=True, slots=True)
class GateResult:
    """一次门控判定的结果。

    passed 是唯一被主流程消费的字段：True 表示这张图值得送 LLM 复核。
    probability 仅写日志，不外传给提示词或历史记录。
    """

    passed: bool
    probability: float
    provider: str
    cost: float
    error: str
    input_tokens: int


class SystemOneGate:
    """Decisions API 客户端，负责把原始图片送去做一次低成本判定。"""

    def __init__(self, config: dict, client: httpx.AsyncClient) -> None:
        self.config = config or {}
        self._client = client

    # ── 配置读取 ──────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """门控是否已开启。未开启时不产生任何网络请求。"""
        value = self.config.get("systemone_enabled", False)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def api_key(self) -> str:
        """取明文 Key。掩码值视为未填，避免把展示串当成真 Key 发出去。"""
        key = str(self.config.get("systemone_api_key", "") or "").strip()
        return "" if "********" in key else key

    def threshold(self) -> float:
        """违规概率阈值，夹到合理区间避免配错导致门控永开或永关。"""
        raw = self.config.get("systemone_violation_threshold", GATE_DEFAULT_THRESHOLD)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = GATE_DEFAULT_THRESHOLD
        return min(0.95, max(0.05, value))

    def timeout_seconds(self) -> float:
        """单次请求超时。"""
        raw = self.config.get("systemone_timeout_seconds", GATE_DEFAULT_TIMEOUT)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = GATE_DEFAULT_TIMEOUT
        return max(3.0, value)

    def max_image_bytes(self) -> int:
        """原始图体积上限，超过才走压缩链路。"""
        raw = self.config.get("systemone_max_image_bytes", GATE_DEFAULT_MAX_IMAGE_BYTES)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = GATE_DEFAULT_MAX_IMAGE_BYTES
        return max(16384, value)

    def model(self) -> str:
        """门控模型名。"""
        return str(self.config.get("systemone_model", "") or "").strip() or GATE_DEFAULT_MODEL

    def question_name(self) -> str:
        """noul 问题的键名，请求与响应共用。"""
        return "image_violation"

    def question_instructions(self) -> str:
        """noul 的 instructions。为空会被门控拒绝，必须兜底默认值。"""
        return str(self.config.get("systemone_question", "") or "").strip() or GATE_DEFAULT_QUESTION

    def build_url(self) -> str:
        """完整请求地址。"""
        return build_gate_url(
            self.config.get("systemone_base_url", "") or GATE_DEFAULT_BASE_URL,
            self.config.get("systemone_endpoint_path", ""),
        )

    def build_state_text(self) -> str:
        """按当前配置生成 state 文本。"""
        texts, descriptions, prompt = resolve_gate_rules(self.config)
        return build_gate_text(texts, descriptions, prompt, self.question_instructions())

    def build_request(self, text: str, data_url: str) -> dict:
        """组装完整请求体，正式调用与连通性测试共用。"""
        return {
            "model": self.model(),
            "state": build_gate_state(text, data_url),
            "questions": {
                self.question_name(): {
                    "type": "noul",
                    "instructions": self.question_instructions(),
                }
            },
        }

    # ── 图片准备 ──────────────────────────────────────────

    def prepare_data_url(self, raw_bytes: bytes) -> str:
        """把原始字节编码成门控可接受的静态 data URL。

        默认直接送原始字节——压缩会抹掉小字水印、二维码、边角贴纸这些
        恰恰最关键的细节。只有原始图超出体积上限才走压缩，否则请求会
        被 token 窗口直接拒掉。
        """
        limit = self.max_image_bytes()

        # 已合规且未超限：直接送原始字节，最大化保留细节
        if is_gate_accepted(raw_bytes):
            data_url = encode_image_to_data_url(raw_bytes, detect_image_mime(raw_bytes))
            if len(data_url) <= limit:
                return data_url

        # 格式不合规（GIF 等）：先归一成静态帧。必须走这一步而不是交给
        # 压缩链路——它对 GIF 会保留动画并输出 gif，门控只吃静态图。
        if not is_gate_accepted(raw_bytes):
            return to_gate_accepted_data_url(raw_bytes)

        # 已合规但超限：交给现有压缩链路，它会降到 jpeg
        try:
            return compress_image_with_result(raw_bytes, limit).data_url
        except Exception:
            # 压缩失败时宁可送大图，也不要把图丢掉
            return encode_image_to_data_url(raw_bytes, detect_image_mime(raw_bytes))

    # ── 调用 ──────────────────────────────────────────────

    async def check_image(self, text: str, path: Path) -> GateResult:
        """对一张本地图片做门控判定，任何失败都按「放行」返回。"""
        try:
            raw_bytes = Path(path).read_bytes()
        except OSError as exc:
            return GateResult(True, 0.0, "", 0.0, f"读取图片失败: {exc}", 0)

        try:
            data_url = self.prepare_data_url(raw_bytes)
            payload = self.build_request(text, data_url)
        except Exception as exc:
            return GateResult(True, 0.0, "", 0.0, f"门控请求构建失败: {exc}", 0)

        url = self.build_url()
        try:
            resp = await self._client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key()}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds(),
            )
        except Exception as exc:
            # 不重试：门控的价值是省钱，为省一次调用加重试延迟得不偿失
            return GateResult(True, 0.0, "", 0.0, f"门控调用失败: {exc}", 0)

        # 先看错误正文再抛：raise_for_status 只带状态码，OpenRouter 会把
        # 「Key 无效 / 额度不足 / 账户未开通」写在 body 里，丢了就没法排查
        if resp.status_code >= 400:
            return GateResult(
                True, 0.0, "", 0.0,
                f"门控返回 {resp.status_code}: {_extract_error_detail(resp, url)}", 0,
            )

        try:
            body = resp.json()
        except Exception as exc:
            return GateResult(True, 0.0, "", 0.0, f"门控响应不是合法 JSON: {exc}", 0)

        try:
            probability = parse_gate_probability(body, self.question_name())
        except ValueError as exc:
            return GateResult(True, 0.0, "", 0.0, str(exc), 0)

        return GateResult(
            passed=probability >= self.threshold(),
            probability=probability,
            provider=_extract_provider(body),
            cost=_extract_usage(body, "cost"),
            error="",
            input_tokens=int(_extract_usage(body, "input_tokens")),
        )