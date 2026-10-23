import aiohttp
import asyncio
import base64
import json
import os
import time
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


# NVIDIA API 配置（保留兼容，仅旧链路使用）
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_API_KEY = _load_secret("nvidia_api_key", "NVIDIA_API_KEY")

# DeepSeek 官方 API
DEEPSEEK_API_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY = _load_secret("deepseek_api_key", "DEEPSEEK_API_KEY")

# 按能力拆分的 Key：平台侧可按 Key 单独查看用量
DEEPSEEK_CHAT_KEY = (
    _load_secret("deepseek_chat_key", "DEEPSEEK_CHAT_KEY") or DEEPSEEK_API_KEY
)
DEEPSEEK_VISION_KEY = (
    _load_secret("deepseek_vision_key", "DEEPSEEK_VISION_KEY") or DEEPSEEK_CHAT_KEY
)
DEEPSEEK_JUDGE_KEY = (
    _load_secret("deepseek_judge_key", "DEEPSEEK_JUDGE_KEY") or DEEPSEEK_CHAT_KEY
)
DEEPSEEK_SEARCH_KEY = (
    _load_secret("deepseek_search_key", "DEEPSEEK_SEARCH_KEY") or DEEPSEEK_CHAT_KEY
)
DEEPSEEK_MEMORY_KEY = (
    _load_secret("deepseek_memory_key", "DEEPSEEK_MEMORY_KEY") or DEEPSEEK_CHAT_KEY
)
DEEPSEEK_PRO_KEY = (
    _load_secret("deepseek_pro_key", "DEEPSEEK_PRO_KEY") or DEEPSEEK_CHAT_KEY
)

# MiMo Token Plan API 配置（OpenAI 兼容接口）
MIMO_API_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_API_KEY = _load_secret("mimo_api_key", "MIMO_API_KEY")
MIMO_CHAT_MODEL = "mimo-v2.5-pro"

# MiMo TTS（复用当前 MiMo Token Plan 凭据）
MIMO_TTS_API_URL = MIMO_API_URL
MIMO_TTS_API_KEY = MIMO_API_KEY
MIMO_TTS_MODEL = "mimo-v2.5-tts"

# DeepSeek 官方模型
DEEPSEEK_CHAT_MODEL = "deepseek-flash"
DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"
VISION_MODEL = "deepseek-flash"

# 文本对话（NVIDIA NIM OpenAI 兼容）：厂商前缀 + 模型名（旧链路保留）
CHAT_MODEL = "minimaxai/minimax-m2.7"

_CAPABILITY_KEYS = {
    "chat": DEEPSEEK_CHAT_KEY,
    "vision": DEEPSEEK_VISION_KEY,
    "judge": DEEPSEEK_JUDGE_KEY,
    "search": DEEPSEEK_SEARCH_KEY,
    "memory": DEEPSEEK_MEMORY_KEY,
    "pro": DEEPSEEK_PRO_KEY,
}
_CAPABILITY_MODELS = {
    "chat": DEEPSEEK_CHAT_MODEL,
    "vision": DEEPSEEK_CHAT_MODEL,
    "judge": DEEPSEEK_CHAT_MODEL,
    "search": DEEPSEEK_CHAT_MODEL,
    "memory": DEEPSEEK_CHAT_MODEL,
    "pro": DEEPSEEK_PRO_MODEL,
}


def _capability_key(capability: str) -> str:
    return _CAPABILITY_KEYS.get(capability, DEEPSEEK_CHAT_KEY)


def _capability_model(capability: str, override: Optional[str] = None) -> str:
    return override or _CAPABILITY_MODELS.get(capability, DEEPSEEK_CHAT_MODEL)


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
                result["model"] = VISION_MODEL
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
    ) -> Optional[dict]:
        """文本对话：按能力选择 Key/模型，默认关闭思考模式。"""
        api_key = _capability_key(capability)
        model = _capability_model(capability, model)
        if temperature is None:
            temperature = 0.9 if capability == "chat" else 0.3
        _log.info("[Chat] capability=%s model=%s", capability, model)

        provider = ("DeepSeek", DEEPSEEK_API_URL, api_key, model)
        providers = [provider] * max(1, retries)
        delay = 2

        for attempt, (name, url, api_key, model) in enumerate(providers, start=1):
            try:
                _log.info(f"[Chat] 第 {attempt} 次尝试，使用 {name} ({model})")
                client_kwargs = {
                    "api_key": api_key,
                    "base_url": url,
                    "max_retries": 0,
                }
                client_kwargs["timeout"] = request_timeout
                async with AsyncOpenAI(**client_kwargs) as client:
                    completion_kwargs = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": keyword},
                        ],
                        "stream": False,
                        "extra_body": {
                            "thinking": {
                                "type": "enabled" if thinking else "disabled"
                            }
                        },
                    }
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
                cause = e.__cause__ or e.__context__
                detail = f"{type(e).__name__}: {e}"
                if cause is not None and cause is not e:
                    detail += f"; cause={type(cause).__name__}: {cause}"
                _log.error(f"[Chat] {name} 请求失败（{url}）: {detail}")
                if attempt < len(providers):
                    _log.info(f"[Chat] {name} 将在 {delay} 秒后进行第 {attempt + 1} 次尝试...")
                    await asyncio.sleep(delay)

        _log.error(f"[Chat] {len(providers)} 次尝试均已失败，返回 None")
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
    ) -> Optional[dict]:
        """OpenAI 兼容 Chat Completions，支持 tools / tool_calls。

        返回 content、tool_calls、reasoning_content、usage、model。
        注意：思考模式 + tools 时，多轮请求必须把 reasoning_content 一并回传。
        """
        api_key = _capability_key(capability)
        model = _capability_model(capability, model)
        if temperature is None:
            temperature = 0.9 if capability == "chat" else 0.3
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "extra_body": {
                "thinking": {"type": "enabled" if thinking else "disabled"}
            },
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if not thinking:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            async with AsyncOpenAI(
                api_key=api_key,
                base_url=DEEPSEEK_API_URL,
                max_retries=0,
                timeout=request_timeout,
            ) as client:
                completion = await client.chat.completions.create(**payload)
        except Exception as e:
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
        api_key = _capability_key("chat")

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
    ) -> Optional[dict]:
        """DeepSeek 多模态调用（deepseek-flash 原生图像理解）。

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
        api_key = _capability_key(capability)
        model = _capability_model(capability, model)
        _log.info("[Vision] 调用 DeepSeek 多模态模型: %s", model)

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
            async with AsyncOpenAI(
                api_key=api_key,
                base_url=DEEPSEEK_API_URL,
                timeout=60.0,
            ) as client:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                    timeout=45.0,
                    extra_body={"thinking": {"type": "disabled"}},
                )

            result = _completion_to_result(completion, model)
            _log.info(f"[Vision] 响应内容: {result['content'][:100]}...")
            _log.info(f"[Vision] Token使用: {result['usage']}")
            return result

        except asyncio.TimeoutError as e:
            _log.error(f"[Vision] 请求超时: {e}")
            return None

        except Exception as e:
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
