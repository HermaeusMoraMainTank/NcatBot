import aiohttp
import asyncio
import base64
import json
import os
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, List, Optional, Tuple
from openai import AsyncOpenAI

from ncatbot.utils.logger import get_log

# 日志配置
_log = get_log()

_COMMON_DIR = Path(__file__).resolve().parent.parent
_SECRETS_FILE = _COMMON_DIR / "secrets.json"


def _load_secret(json_key: str, env_key: str) -> str:
    """优先读环境变量，其次读 plugins/common/secrets.json（该文件已 gitignore）。"""
    value = os.environ.get(env_key, "").strip()
    if value:
        return value
    if not _SECRETS_FILE.is_file():
        return ""
    try:
        data = json.loads(_SECRETS_FILE.read_text(encoding="utf-8"))
        return str(data.get(json_key, "")).strip()
    except Exception as e:
        _log.warning(f"读取 {_SECRETS_FILE.name} 失败: {e}")
        return ""


# DeepSeek 官方 API（搜索/余额等工具接口固定走官方，不受对话供应商影响）
DEEPSEEK_API_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY = _load_secret("deepseek_api_key", "DEEPSEEK_API_KEY")

# MiMo Token Plan API 配置（OpenAI 兼容接口）
MIMO_API_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_API_KEY = _load_secret("mimo_api_key", "MIMO_API_KEY")
MIMO_CHAT_MODEL = "mimo-v2.5-pro"

# MiMo TTS（复用当前 MiMo Token Plan 凭据）
MIMO_TTS_API_URL = MIMO_API_URL
MIMO_TTS_API_KEY = MIMO_API_KEY
MIMO_TTS_MODEL = "mimo-v2.5-tts"

# DeepSeek 官方模型（保持兼容；实际模型由当前供应商决定）
DEEPSEEK_CHAT_MODEL = "deepseek-flash"
DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"
VISION_MODEL = "deepseek-flash"

# 对话供应商预设：FakeAi 的 ai_provider 配置在这里选一个。
# 所有 preset 都走 OpenAI 兼容接口；搜索/余额仍固定用 DeepSeek 官方。
PROVIDER_PRESETS: dict[str, dict] = {
    "ds_official": {
        "label": "DeepSeek 官方 · deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "secret": "deepseek_api_key",
        "env": "DEEPSEEK_API_KEY",
        "chat_model": "deepseek-flash",
        "pro_model": "deepseek-v4-pro",
        "vision_model": "deepseek-flash",
        "supports_thinking": True,
    },
    "ocgo_gemini": {
        "label": "OpenCode Go · Gemini 4 Argon",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "secret": "opencode_api_key",
        "env": "OPENCODE_API_KEY",
        "chat_model": "gemini-4-argon",
        "pro_model": "gemini-4-argon",
        "vision_model": "deepseek-flash",
        "vision_provider": "ds_official",
        "supports_thinking": False,
        "headers": {"User-Agent": "NcatBot/5.0"},
        "session_header": True,
    },
    "ocgo_ds_flash": {
        "label": "OpenCode Go · DeepSeek V4.1 Flash",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "secret": "opencode_api_key",
        "env": "OPENCODE_API_KEY",
        "chat_model": "deepseek-v4.1-flash",
        "pro_model": "deepseek-v4.1-flash",
        "vision_model": "deepseek-flash",
        "vision_provider": "ds_official",
        "supports_thinking": False,
        "headers": {"User-Agent": "NcatBot/5.0"},
        "session_header": True,
    },
    "ocgo_ds_pro": {
        "label": "OpenCode Go · DeepSeek V4 Pro",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "secret": "opencode_api_key",
        "env": "OPENCODE_API_KEY",
        "chat_model": "deepseek-v4-pro",
        "pro_model": "deepseek-v4-pro",
        "vision_model": "deepseek-flash",
        "vision_provider": "ds_official",
        "supports_thinking": False,
        "headers": {"User-Agent": "NcatBot/5.0"},
        "session_header": True,
    },
    "ocgo_glm": {
        "label": "OpenCode Go · GLM-5.3",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "secret": "opencode_api_key",
        "env": "OPENCODE_API_KEY",
        "chat_model": "glm-5.3",
        "pro_model": "glm-5.3",
        "vision_model": "deepseek-flash",
        "vision_provider": "ds_official",
        "supports_thinking": False,
        "headers": {"User-Agent": "NcatBot/5.0"},
        "session_header": True,
    },
    "ocgo_kimi": {
        "label": "OpenCode Go · Kimi K3",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "secret": "opencode_api_key",
        "env": "OPENCODE_API_KEY",
        "chat_model": "kimi-k3",
        "pro_model": "kimi-k3",
        "vision_model": "deepseek-flash",
        "vision_provider": "ds_official",
        "supports_thinking": False,
        "headers": {"User-Agent": "NcatBot/5.0"},
        "session_header": True,
    },
    # 按量付费（Console 余额）：比 Go 订阅里的 Gemini 4 Argon 便宜很多
    "ocpay_gemini_lite": {
        "label": "OpenCode 按量 · Gemini 3.5 Flash Lite",
        "base_url": "https://opencode.ai/inference/openai/v1",
        "secret": "opencode_api_key",
        "env": "OPENCODE_API_KEY",
        "chat_model": "gemini-3.5-flash-lite",
        "pro_model": "gemini-3.5-flash-lite",
        "vision_model": "gemini-3.5-flash-lite",
        "supports_thinking": False,
        "headers": {"User-Agent": "NcatBot/5.0"},
        "session_header": True,
    },
    "ocpay_gemini_flash": {
        "label": "OpenCode 按量 · Gemini 3.8 Flash",
        "base_url": "https://opencode.ai/inference/openai/v1",
        "secret": "opencode_api_key",
        "env": "OPENCODE_API_KEY",
        "chat_model": "gemini-3.8-flash",
        "pro_model": "gemini-3.8-flash",
        "vision_model": "gemini-3.8-flash",
        "supports_thinking": False,
        "headers": {"User-Agent": "NcatBot/5.0"},
        "session_header": True,
    },
}

DEFAULT_PROVIDER = "ds_official"
_active_provider = DEFAULT_PROVIDER
# Go 要求客户端带稳定的会话头；进程内保持一个即可
_opencode_session = f"ncatbot-{uuid.uuid4().hex[:16]}"
_secret_cache: dict[tuple[str, str], str] = {}


def _provider_secret(spec: dict) -> str:
    key = (str(spec.get("secret") or ""), str(spec.get("env") or ""))
    if key not in _secret_cache:
        _secret_cache[key] = _load_secret(key[0], key[1])
    return _secret_cache[key]


def active_provider_name() -> str:
    return _active_provider


def active_provider() -> dict:
    return PROVIDER_PRESETS.get(_active_provider, PROVIDER_PRESETS[DEFAULT_PROVIDER])


def set_provider(name: str) -> str:
    """切换对话供应商；未知名字回退到 DeepSeek 官方。"""
    global _active_provider
    candidate = str(name or "").strip()
    if candidate not in PROVIDER_PRESETS:
        _log.warning("[AI] 未知供应商 %s，回退 %s", name, DEFAULT_PROVIDER)
        candidate = DEFAULT_PROVIDER
    _active_provider = candidate
    # 换了供应商，识图能力要重新探测
    try:
        AiUtil._VISION_CAPABILITY = None
        AiUtil._VISION_PROBED_AT = 0.0
    except Exception:
        pass
    spec = active_provider()
    if not _provider_secret(spec):
        _log.warning("[AI] 供应商 %s 缺少 key（%s）", candidate, spec.get("secret"))
    _log.info("[AI] 对话供应商：%s（%s）", spec.get("label"), candidate)
    return candidate


def provider_label(name: str = "") -> str:
    spec = PROVIDER_PRESETS.get(name or _active_provider)
    return str((spec or {}).get("label") or "")


def _provider_client_kwargs(spec: dict, timeout: float | None) -> dict:
    kwargs = {
        "api_key": _provider_secret(spec),
        "base_url": spec["base_url"],
        "max_retries": 0,
        "timeout": timeout,
    }
    headers = dict(spec.get("headers") or {})
    if spec.get("session_header"):
        headers["x-opencode-session"] = _opencode_session
    if headers:
        kwargs["default_headers"] = headers
    return kwargs


def _provider_thinking_body(spec: dict, thinking: bool) -> dict:
    if not spec.get("supports_thinking"):
        return {}
    return {"thinking": {"type": "enabled" if thinking else "disabled"}}


def _capability_key(capability: str) -> str:
    """兼容旧调用：搜索/工具固定官方 key，其余用当前供应商。"""
    if capability in ("search", "tools"):
        return DEEPSEEK_API_KEY
    return _provider_secret(active_provider())


def _resolve_spec(provider: str | None = None) -> dict:
    if provider:
        return PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS[DEFAULT_PROVIDER])
    return active_provider()


def _is_auth_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    return (
        status == 401
        or "api_key_expired" in text
        or "authentication" in text
    )


def _capability_model(
    capability: str,
    override: Optional[str] = None,
    spec: Optional[dict] = None,
) -> str:
    if override:
        return override
    spec = spec or active_provider()
    if capability == "pro":
        return str(spec.get("pro_model") or spec["chat_model"])
    if capability == "vision":
        return str(spec.get("vision_model") or spec["chat_model"])
    return str(spec["chat_model"])


# 搜索/抓取按次计费，重复查询走 TTL 缓存，避免重复花钱
_TOOLS_CACHE: dict = {}
_TOOLS_CACHE_TTL = 15 * 60
_TOOLS_CACHE_MAX = 128


def _tools_cache_get(key: str) -> Optional[dict]:
    item = _TOOLS_CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > _TOOLS_CACHE_TTL:
        _TOOLS_CACHE.pop(key, None)
        return None
    return value


def _tools_cache_set(key: str, value: dict) -> None:
    if len(_TOOLS_CACHE) >= _TOOLS_CACHE_MAX:
        oldest = min(_TOOLS_CACHE, key=lambda k: _TOOLS_CACHE[k][0])
        _TOOLS_CACHE.pop(oldest, None)
    _TOOLS_CACHE[key] = (time.time(), value)


def _completion_to_result(completion: Any, model: str) -> dict:
    """将 OpenAI ChatCompletion 转为统一返回结构。"""
    return_content = completion.choices[0].message.content
    usage_info = completion.usage
    usage = {
        "prompt_tokens": usage_info.prompt_tokens,
        "completion_tokens": usage_info.completion_tokens,
        "total_tokens": usage_info.total_tokens,
    }
    if usage_info:
        for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            val = getattr(usage_info, key, None)
            if val is not None:
                usage[key] = val
    return {
        "content": return_content,
        "usage": usage,
        "model": model,
    }


def _detect_mime_type(image_data: bytes, content_type: str) -> str:
    """根据响应头与文件头推断 MIME。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return ct
    head = image_data[:12]
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _needs_vision_conversion(mime_type: str, image_data: bytes) -> bool:
    mime = mime_type.lower()
    if mime in ("image/gif", "image/webp"):
        return True
    return image_data[:6].startswith((b"GIF87a", b"GIF89a"))


def _shrink_for_vision(
    image_data: bytes, mime_type: str, max_side: int = 1568
) -> Tuple[bytes, str]:
    """大图先缩到 1568px 再送模型：视觉 token 基本按像素算，原图很贵。"""
    try:
        from PIL import Image as PILImage
    except ImportError:
        return image_data, mime_type
    try:
        img = PILImage.open(BytesIO(image_data))
    except Exception:
        return image_data, mime_type
    with img:
        width, height = img.size
        if max(width, height) <= max_side and len(image_data) <= 400_000:
            return image_data, mime_type
        try:
            img.thumbnail((max_side, max_side))
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=82)
            out = buf.getvalue()
        except Exception as e:
            _log.debug("[Vision] 缩放失败，用原图: %s", e)
            return image_data, mime_type
    _log.info(
        "[Vision] 图片已缩放 %sx%s → %sx%s，%s → %s bytes",
        width,
        height,
        img.size[0],
        img.size[1],
        len(image_data),
        len(out),
    )
    return out, "image/jpeg"


def _normalize_image_for_vision(image_data: bytes, mime_type: str) -> Tuple[bytes, str]:
    """Vision API 不支持 GIF/WebP，转为 JPEG（动图取首帧）。"""
    if not _needs_vision_conversion(mime_type, image_data):
        return image_data, mime_type

    try:
        from PIL import Image as PILImage
    except ImportError:
        _log.warning("[Vision] 未安装 Pillow，无法转换 GIF/WebP")
        return image_data, mime_type

    with PILImage.open(BytesIO(image_data)) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        frame = img
        if frame.mode in ("RGBA", "LA"):
            if frame.getchannel("A").getextrema() != (255, 255):
                background = PILImage.new("RGB", frame.size, (255, 255, 255))
                background.paste(frame, mask=frame.split()[-1])
                frame = background
            else:
                frame = frame.convert("RGB")
        elif frame.mode == "P":
            if "transparency" in frame.info:
                frame = frame.convert("RGBA")
                background = PILImage.new("RGB", frame.size, (255, 255, 255))
                background.paste(frame, mask=frame.split()[-1])
                frame = background
            else:
                frame = frame.convert("RGB")
        elif frame.mode != "RGB":
            frame = frame.convert("RGB")

        buf = BytesIO()
        frame.save(buf, format="JPEG", quality=85)
        out = buf.getvalue()

    _log.info(f"[Vision] 图片已转为 JPEG，大小: {len(out)} bytes (原 {mime_type})")
    return out, "image/jpeg"


class AiUtil:
    # 识图能力探测（进程内缓存 6 小时，避免每次对话都试）
    _VISION_CAPABILITY: Optional[bool] = None
    _VISION_PROBED_AT: float = 0.0
    _VISION_PROBE_TTL: float = 6 * 3600
    _PROBE_PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    @staticmethod
    def set_provider(name: str) -> str:
        """切换对话供应商（配置项 ai_provider）。"""
        return set_provider(name)

    @staticmethod
    def current_provider() -> str:
        return active_provider_name()

    @staticmethod
    def current_model(capability: str = "chat") -> str:
        return _capability_model(capability)

    @staticmethod
    def provider_options() -> list[dict]:
        return [
            {"value": key, "label": spec["label"]}
            for key, spec in PROVIDER_PRESETS.items()
        ]

    @staticmethod
    async def vision_available(force: bool = False) -> bool:
        """探测当前模型到底能不能看图，结果缓存 6 小时。

        不能看图时还硬塞图片，只会白烧 token 然后让模型编造内容。
        """
        now = time.time()
        if (
            not force
            and AiUtil._VISION_CAPABILITY is not None
            and now - AiUtil._VISION_PROBED_AT < AiUtil._VISION_PROBE_TTL
        ):
            return bool(AiUtil._VISION_CAPABILITY)
        AiUtil._VISION_PROBED_AT = now
        try:
            b64 = "data:image/png;base64," + base64.b64encode(
                AiUtil._PROBE_PNG
            ).decode()
            res = await AiUtil.chat_with_vision(
                "这张图是什么颜色？只回答颜色。",
                image_base64_list=[b64],
                max_tokens=12,
                temperature=0.0,
            )
            AiUtil._VISION_CAPABILITY = bool(res and res.get("content"))
        except Exception as e:
            _log.debug("[Vision] 能力探测失败: %s", e)
            AiUtil._VISION_CAPABILITY = False
        _log.info(
            "[Vision] 模型识图能力: %s",
            "可用" if AiUtil._VISION_CAPABILITY else "不可用",
        )
        return bool(AiUtil._VISION_CAPABILITY)

    @staticmethod
    def image_bytes_to_base64(data: bytes, mime_type: str = "") -> Optional[str]:
        """原始图片字节 → 规范化/缩放后的 data URI，避免重复下载。"""
        if not data:
            return None
        try:
            mime = mime_type or _detect_mime_type(data, "")
            data, mime = _normalize_image_for_vision(data, mime)
            data, mime = _shrink_for_vision(data, mime)
            return f"data:{mime};base64," + base64.b64encode(data).decode()
        except Exception as e:
            _log.debug("[Vision] 图片转 base64 失败: %s", e)
            return None

    @staticmethod
    async def synthesize_mimo_tts(
        text: str,
        voice: str = "mimo_default",
    ) -> Optional[bytes]:
        """使用 MiMo TTS 生成 WAV 音频。"""
        text = (text or "").strip()
        if not text:
            return None
        if not MIMO_TTS_API_KEY:
            _log.error("[TTS] 未配置 mimo_api_key")
            return None

        try:
            async with AsyncOpenAI(
                api_key=MIMO_TTS_API_KEY,
                base_url=MIMO_TTS_API_URL,
                timeout=60.0,
            ) as client:
                completion = await client.chat.completions.create(
                    model=MIMO_TTS_MODEL,
                    messages=[{"role": "assistant", "content": text}],
                    audio={"format": "wav", "voice": voice},
                    stream=False,
                    timeout=50.0,
                )

            audio = getattr(completion.choices[0].message, "audio", None)
            audio_data = (
                audio.get("data")
                if isinstance(audio, dict)
                else getattr(audio, "data", None)
            )
            if not audio_data:
                _log.error("[TTS] MiMo 响应中没有音频数据")
                return None
            return base64.b64decode(audio_data)
        except Exception as e:
            _log.error(f"[TTS] MiMo 请求失败: {e}")
            return None

    @staticmethod
    async def download_image_bytes(
        url: str, max_bytes: int = 8 * 1024 * 1024
    ) -> Optional[bytes]:
        """下载图片原始字节（不转码），用于收藏表情等场景。"""
        if not str(url or "").startswith(("http://", "https://")):
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=20) as response:
                    if response.status != 200:
                        _log.warning("[Image] 下载失败，状态码: %s", response.status)
                        return None
                    data = await response.read()
                    if not data or len(data) > max_bytes:
                        return None
                    return data
        except Exception as e:
            _log.warning("[Image] 下载异常: %s", e)
            return None

    @staticmethod
    async def download_image_as_base64(url: str) -> Optional[str]:
        """下载图片并转换为 base64 编码

        Args:
            url: 图片 URL

        Returns:
            base64 编码的图片字符串，失败返回 None
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        content_type = response.headers.get("Content-Type", "")
                        mime_type = _detect_mime_type(image_data, content_type)
                        try:
                            image_data, mime_type = await asyncio.to_thread(
                                _normalize_image_for_vision, image_data, mime_type
                            )
                            image_data, mime_type = await asyncio.to_thread(
                                _shrink_for_vision, image_data, mime_type
                            )
                        except Exception as e:
                            _log.error(f"[Vision] 图片格式转换失败: {e}")
                            return None

                        b64_str = base64.b64encode(image_data).decode("utf-8")
                        _log.info(
                            f"[Vision] 图片下载成功，大小: {len(image_data)} bytes"
                        )
                        return f"data:{mime_type};base64,{b64_str}"
                    else:
                        _log.error(f"[Vision] 图片下载失败，状态码: {response.status}")
                        return None
        except Exception as e:
            _log.error(f"[Vision] 图片下载异常: {e}")
            return None

    @staticmethod
    async def download_images_as_base64(urls: List[str]) -> List[str]:
        """批量下载图片并转换为 base64

        Args:
            urls: 图片 URL 列表

        Returns:
            base64 编码的图片列表（跳过下载失败的）
        """
        tasks = [AiUtil.download_image_as_base64(url) for url in urls]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    @staticmethod
    async def describe_image_briefly(image_url: str) -> Optional[dict]:
        """生成图片的详细描述（用于聊天上下文）

        Returns:
            含 content、usage、model 的字典，失败返回 None
        """
        try:
            image_base64 = await AiUtil.download_image_as_base64(image_url)
            if not image_base64:
                _log.warning(f"[Vision] 图片下载失败: {image_url[:50]}...")
                return None

            result = await AiUtil.chat_with_vision(
                prompt="""请详细描述这张图片的内容，包括：
1. 图片类型（照片/截图/漫画/表情包/插画等）
2. 画面中的主要内容、人物、场景
3. 如果有文字，请完整转录
4. 如果是漫画/对话，描述剧情和对话内容
5. 图片传达的情绪或氛围

直接输出描述，不要加"这张图片"等前缀。控制在150字以内。""",
                image_base64_list=[image_base64],
                system_prompt="你是一个图像分析专家，用准确详细的中文描述图片内容，特别注意转录图中的文字。",
                max_tokens=300,
                temperature=0.3,
            )

            if result and result.get("content"):
                description = result["content"].strip()
                _log.info(f"[Vision] 图片描述: {description}")
                result["content"] = description
                result["model"] = _capability_model("vision")
                return result
            return None

        except Exception as e:
            _log.error(f"[Vision] 图片描述生成失败: {e}")
            return None

    @staticmethod
    async def search_deepseek(
        keyword: str,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        *,
        request_timeout: float | None = None,
        retries: int = 3,
        capability: str = "chat",
        model: str | None = None,
        thinking: bool = False,
        provider: str | None = None,
        _fallback: bool = False,
    ) -> Optional[dict]:
        """文本对话：按当前供应商选择模型，默认关闭思考模式。"""
        spec = _resolve_spec(provider)
        name = str(spec.get("label") or active_provider_name())
        url = str(spec["base_url"])
        requested_model = model
        model = _capability_model(capability, model, spec)
        if temperature is None:
            temperature = 0.9 if capability == "chat" else 0.3
        _log.info("[Chat] provider=%s capability=%s model=%s", active_provider_name(), capability, model)

        attempts = max(1, retries)
        delay = 2

        for attempt in range(1, attempts + 1):
            try:
                _log.info(f"[Chat] 第 {attempt} 次尝试，使用 {name} ({model})")
                client_kwargs = _provider_client_kwargs(spec, request_timeout)
                async with AsyncOpenAI(**client_kwargs) as client:
                    completion_kwargs = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": keyword},
                        ],
                        "stream": False,
                    }
                    thinking_body = _provider_thinking_body(spec, thinking)
                    if thinking_body:
                        completion_kwargs["extra_body"] = thinking_body
                    if not thinking:
                        completion_kwargs["temperature"] = temperature
                    if max_tokens is not None:
                        completion_kwargs["max_tokens"] = max_tokens
                    completion_kwargs["timeout"] = request_timeout
                    completion = await client.chat.completions.create(
                        **completion_kwargs,
                    )

                result = _completion_to_result(completion, model)
                _log.info(f"[Chat] {name} 响应成功: {result['content']}")
                _log.info(f"[Chat] Token使用: {result['usage']}")
                return result

            except Exception as e:
                if (
                    not _fallback
                    and provider is None
                    and active_provider_name() != "ds_official"
                    and _is_auth_error(e)
                ):
                    _log.warning(
                        "[Chat] %s 鉴权失败，临时回退 DeepSeek 官方",
                        active_provider_name(),
                    )
                    return await AiUtil.search_deepseek(
                        keyword,
                        prompt,
                        max_tokens,
                        temperature,
                        request_timeout=request_timeout,
                        retries=retries,
                        capability=capability,
                        model=requested_model,
                        thinking=thinking,
                        provider="ds_official",
                        _fallback=True,
                    )
                cause = e.__cause__ or e.__context__
                detail = f"{type(e).__name__}: {e}"
                if cause is not None and cause is not e:
                    detail += f"; cause={type(cause).__name__}: {cause}"
                _log.error(f"[Chat] {name} 请求失败（{url}）: {detail}")
                if attempt < attempts:
                    _log.info(f"[Chat] {name} 将在 {delay} 秒后进行第 {attempt + 1} 次尝试...")
                    await asyncio.sleep(delay)

        _log.error(f"[Chat] {attempts} 次尝试均已失败，返回 None")
        return None

    @staticmethod
    async def chat_completion(
        messages: List[dict],
        *,
        tools: Optional[List[dict]] = None,
        capability: str = "chat",
        model: str | None = None,
        thinking: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        request_timeout: float | None = 90,
        provider: str | None = None,
        _fallback: bool = False,
    ) -> Optional[dict]:
        """OpenAI 兼容 Chat Completions，支持 tools / tool_calls。

        返回 content、tool_calls、reasoning_content、usage、model。
        注意：思考模式 + tools 时，多轮请求必须把 reasoning_content 一并回传。
        供应商鉴权失败时，自动用 DeepSeek 官方兜底一次。
        """
        spec = _resolve_spec(provider)
        requested_model = model
        model = _capability_model(capability, model, spec)
        if temperature is None:
            temperature = 0.9 if capability == "chat" else 0.3
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        thinking_body = _provider_thinking_body(spec, thinking)
        if thinking_body:
            payload["extra_body"] = thinking_body
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if not thinking:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            async with AsyncOpenAI(
                **_provider_client_kwargs(spec, request_timeout)
            ) as client:
                completion = await client.chat.completions.create(**payload)
        except Exception as e:
            if (
                not _fallback
                and provider is None
                and active_provider_name() != "ds_official"
                and _is_auth_error(e)
            ):
                _log.warning(
                    "[Chat] %s 鉴权失败，临时回退 DeepSeek 官方",
                    active_provider_name(),
                )
                return await AiUtil.chat_completion(
                    messages,
                    tools=tools,
                    capability=capability,
                    model=requested_model,
                    thinking=thinking,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    request_timeout=request_timeout,
                    provider="ds_official",
                    _fallback=True,
                )
            _log.error("[Chat] chat_completion 失败: %s: %s", type(e).__name__, e)
            return None

        msg = completion.choices[0].message
        usage_info = completion.usage
        usage = {
            "prompt_tokens": getattr(usage_info, "prompt_tokens", 0),
            "completion_tokens": getattr(usage_info, "completion_tokens", 0),
            "total_tokens": getattr(usage_info, "total_tokens", 0),
        }
        for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            val = getattr(usage_info, key, None)
            if val is not None:
                usage[key] = val

        tool_calls = []
        for tc in msg.tool_calls or []:
            tool_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )
        return {
            "content": msg.content or "",
            "tool_calls": tool_calls,
            "reasoning_content": getattr(msg, "reasoning_content", None),
            "usage": usage,
            "model": model,
        }

    @staticmethod
    async def get_deepseek_balance() -> dict:
        """查询 DeepSeek API 余额"""
        url = f"{DEEPSEEK_API_URL}/user/balance"
        api_key = DEEPSEEK_API_KEY

        headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}

        retry_count = 3  # 最大重试次数
        delay = 2  # 重试延迟时间（秒）

        while retry_count > 0:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, headers=headers, timeout=30
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            _log.info(f"余额查询成功: {data}")
                            return data
                        else:
                            _log.error(f"余额查询失败，状态码: {response.status}")
                            error_text = await response.text()
                            _log.error(f"错误信息: {error_text}")
                            return {"error": f"API请求失败，状态码: {response.status}"}

            except asyncio.TimeoutError as e:
                _log.error(f"余额查询超时: {e}")
                retry_count -= 1
                if retry_count > 0:
                    _log.info(f"重试 {3 - retry_count} 次...")
                    await asyncio.sleep(delay)

            except aiohttp.ClientError as e:
                _log.error(f"余额查询网络异常: {e}")
                retry_count -= 1
                if retry_count > 0:
                    _log.info(f"重试 {3 - retry_count} 次...")
                    await asyncio.sleep(delay)

            except Exception as e:
                _log.error(f"余额查询未知异常: {e}")
                break

        # 重试次数用尽后返回错误信息
        _log.error("余额查询重试次数已用尽")
        return {"error": "查询失败，请稍后重试"}

    @staticmethod
    async def chat_with_vision(
        prompt: str,
        image_urls: List[str] = None,
        image_base64_list: List[str] = None,
        system_prompt: str = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        *,
        capability: str = "vision",
        model: str | None = None,
        provider: str | None = None,
        _fallback: bool = False,
    ) -> Optional[dict]:
        """OpenAI 兼容多模态调用（当前供应商的 vision 模型）。

        Args:
            prompt: 用户提问文本
            image_urls: 图片 URL 列表
            image_base64_list: 图片 base64 列表（纯 base64 或 data:image/... 前缀）
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数
            temperature: 温度参数
            capability: 使用的 Key 能力（默认 vision）
            model: 覆盖模型（默认 deepseek-flash）

        Returns:
            包含 content 和 usage 的字典，或 None
        """
        spec = _resolve_spec(provider)
        # Go 订阅里的 DeepSeek/GLM/Kimi 不支持识图，Gemini 在当前网络下会超时；
        # 识图统一回落到 DeepSeek 官方，避免每次看图都等 60 秒。
        if capability == "vision" and spec.get("vision_provider"):
            spec = _resolve_spec(str(spec["vision_provider"]))
        requested_model = model
        model = _capability_model(capability, model, spec)
        _log.info(
            "[Vision] 调用多模态模型: %s（provider=%s）",
            model,
            spec.get("label") or active_provider_name(),
        )

        content_parts = [{"type": "text", "text": prompt}]

        if image_urls:
            for url in image_urls:
                content_parts.append({"type": "image_url", "image_url": {"url": url}})

        if image_base64_list:
            for img_b64 in image_base64_list:
                if not img_b64.startswith("data:"):
                    img_b64 = f"data:image/jpeg;base64,{img_b64}"
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": img_b64}}
                )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        try:
            async with AsyncOpenAI(**_provider_client_kwargs(spec, 60.0)) as client:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": False,
                    "timeout": 45.0,
                }
                thinking_body = _provider_thinking_body(spec, False)
                if thinking_body:
                    payload["extra_body"] = thinking_body
                completion = await client.chat.completions.create(**payload)

            result = _completion_to_result(completion, model)
            _log.info(f"[Vision] 响应内容: {result['content'][:100]}...")
            _log.info(f"[Vision] Token使用: {result['usage']}")
            return result

        except asyncio.TimeoutError as e:
            _log.error(f"[Vision] 请求超时: {e}")
            return None

        except Exception as e:
            if (
                not _fallback
                and provider is None
                and active_provider_name() != "ds_official"
                and _is_auth_error(e)
            ):
                _log.warning(
                    "[Vision] %s 鉴权失败，临时回退 DeepSeek 官方",
                    active_provider_name(),
                )
                return await AiUtil.chat_with_vision(
                    prompt,
                    image_urls,
                    image_base64_list,
                    system_prompt,
                    max_tokens,
                    temperature,
                    capability=capability,
                    model=requested_model,
                    provider="ds_official",
                    _fallback=True,
                )
            _log.error(f"[Vision] 异常: {e}")
            return None

    @staticmethod
    async def _post_tools_api(path: str, payload: dict, *, timeout: int = 60) -> dict:
        """调用 DeepSeek Search/Fetch 工具 API（按次计费）。"""
        url = f"{DEEPSEEK_API_URL}{path}"
        headers = {
            "Authorization": f"Bearer {_capability_key('search')}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=timeout
            ) as response:
                text = await response.text()
                if response.status != 200:
                    raise RuntimeError(
                        f"DeepSeek tools API {path} HTTP {response.status}: {text[:300]}"
                    )
                return json.loads(text)

    @staticmethod
    async def search_web(
        query: str, *, count: int = 10, need_content: bool = False
    ) -> dict:
        """DeepSeek 自研搜索：返回标题/URL/摘要/正文（15 分钟缓存）。"""
        key = f"search:{query}|{count}|{int(need_content)}"
        cached = _tools_cache_get(key)
        if cached is not None:
            _log.info("[ToolsCache] search hit: %s", query[:40])
            return cached
        data = await AiUtil._post_tools_api(
            "/v1/tools/search",
            {"query": query, "count": count, "need_content": need_content},
        )
        _tools_cache_set(key, data)
        return data

    @staticmethod
    async def fetch_web(url: str) -> dict:
        """DeepSeek 网页抓取：返回 Markdown 正文（15 分钟缓存）。"""
        key = f"fetch:{url}"
        cached = _tools_cache_get(key)
        if cached is not None:
            _log.info("[ToolsCache] fetch hit: %s", url[:60])
            return cached
        data = await AiUtil._post_tools_api("/v1/tools/fetch", {"url": url})
        _tools_cache_set(key, data)
        return data
