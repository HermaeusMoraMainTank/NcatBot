import aiohttp
import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any, List, Optional
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


# NVIDIA API 配置
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_API_KEY = _load_secret("nvidia_api_key", "NVIDIA_API_KEY")

# DeepSeek API 配置（NVIDIA 宕机时降级）
DEEPSEEK_API_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY = _load_secret("deepseek_api_key", "DEEPSEEK_API_KEY")
DEEPSEEK_CHAT_MODEL = "deepseek-v4-flash"

# 多模态模型
VISION_MODEL = "meta/llama-4-maverick-17b-128e-instruct"

# 文本对话（NVIDIA NIM OpenAI 兼容）：厂商前缀 + 模型名
# CHAT_MODEL = "minimaxai/minimax-m2.7"  # 暂禁用：响应过慢
CHAT_MODEL = "minimaxai/minimax-m2.7"


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


class AiUtil:
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
                        # 根据 Content-Type 确定图片类型
                        content_type = response.headers.get(
                            "Content-Type", "image/jpeg"
                        )
                        if "png" in content_type:
                            mime_type = "image/png"
                        elif "gif" in content_type:
                            mime_type = "image/gif"
                        elif "webp" in content_type:
                            mime_type = "image/webp"
                        else:
                            mime_type = "image/jpeg"

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
        max_tokens: int = 2048,
        temperature: float = 1.2,
    ) -> Optional[dict]:
        """文本对话：暂时全走 DeepSeek（minimax 响应过慢），失败重试 3 次。"""
        _log.info(keyword)

        provider = (
            "DeepSeek",
            DEEPSEEK_API_URL,
            DEEPSEEK_API_KEY,
            DEEPSEEK_CHAT_MODEL,
        )
        providers = [provider] * 3
        delay = 2

        for attempt, (name, url, api_key, model) in enumerate(providers, start=1):
            try:
                _log.info(f"[Chat] 第 {attempt} 次尝试，使用 {name} ({model})")
                async with AsyncOpenAI(
                    api_key=api_key,
                    base_url=url,
                    timeout=30.0,
                ) as client:
                    completion = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": keyword},
                        ],
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=False,
                        timeout=20.0,
                    )

                result = _completion_to_result(completion, model)
                _log.info(f"[Chat] {name} 响应成功: {result['content']}")
                _log.info(f"[Chat] Token使用: {result['usage']}")
                return result

            except Exception as e:
                _log.error(f"[Chat] {name} 请求失败: {e}")
                if attempt < len(providers):
                    _log.info("[Chat] 降级到下一提供商...")
                    await asyncio.sleep(delay)

        _log.error("[Chat] 三次尝试均已失败，返回 None")
        return None

    @staticmethod
    async def get_deepseek_balance() -> dict:
        """查询 DeepSeek API 余额"""
        url = "https://api.deepseek.com/user/balance"
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
        temperature: float = 0.7,
    ) -> Optional[dict]:
        """多模态模型调用 - 使用 Llama 3.2 90B Vision

        Args:
            prompt: 用户提问文本
            image_urls: 图片URL列表
            image_base64_list: 图片base64编码列表（纯base64或data:image/xxx;base64,xxx格式）
            system_prompt: 系统提示词
            max_tokens: 最大生成token数
            temperature: 温度参数

        Returns:
            包含 content 和 usage 的字典，或 None
        """
        _log.info(f"[Vision] 调用多模态模型: {VISION_MODEL}")

        # 构建消息内容
        content_parts = []

        # 添加文本
        content_parts.append({"type": "text", "text": prompt})

        # 添加图片URL
        if image_urls:
            for url in image_urls:
                content_parts.append({"type": "image_url", "image_url": {"url": url}})

        # 添加base64图片
        if image_base64_list:
            for img_b64 in image_base64_list:
                # 如果不是完整的 data URI，补全
                if not img_b64.startswith("data:"):
                    img_b64 = f"data:image/jpeg;base64,{img_b64}"
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": img_b64}}
                )

        # 构建消息
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        retry_count = 3
        delay = 2

        while retry_count > 0:
            try:
                async with AsyncOpenAI(
                    api_key=NVIDIA_API_KEY,
                    base_url=NVIDIA_API_URL,
                    timeout=60.0,
                ) as client:
                    completion = await client.chat.completions.create(
                        model=VISION_MODEL,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=False,
                        timeout=45.0,
                    )

                result = _completion_to_result(completion, VISION_MODEL)
                _log.info(f"[Vision] 响应内容: {result['content'][:100]}...")
                _log.info(f"[Vision] Token使用: {result['usage']}")
                return result

            except asyncio.TimeoutError as e:
                _log.error(f"[Vision] 请求超时: {e}")
                retry_count -= 1
                if retry_count > 0:
                    _log.info(f"[Vision] 重试 {3 - retry_count} 次...")
                    await asyncio.sleep(delay)

            except Exception as e:
                _log.error(f"[Vision] 异常: {e}")
                retry_count -= 1
                if retry_count > 0:
                    _log.info(f"[Vision] 重试 {3 - retry_count} 次...")
                    await asyncio.sleep(delay)
                else:
                    break

        _log.error("[Vision] 重试次数已用尽")
        return None
