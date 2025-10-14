import aiohttp
import asyncio
from openai import OpenAI

from ncatbot.utils.logger import get_log

# 日志配置
_log = get_log()


class AiUtil:
    @staticmethod
    async def search_deepseek(keyword: str, prompt: str) -> str:
        _log.info(keyword)

        # 请求 URL 和 API Key
        url = "https://api.lkeap.cloud.tencent.com/v1"
        api_key = "sk-AWmHgm8yzHqY8OhEMA35lC9MZ3ueNn6KndFil9fPbON865zx"

        url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key = "sk-869c983ef74c4678b63c934478339b25"

        url = "https://api.deepseek.com"
        api_key = "sk-b13e5ef0d21942b0819728c345f1295a"

        # OpenAI 配置
        client = OpenAI(
            api_key=api_key,
            base_url=url,
            timeout=30.0,  # 设置超时时间为30秒
        )

        retry_count = 3  # 最大重试次数
        delay = 2  # 重试延迟时间（秒）

        while retry_count > 0:
            try:
                completion = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": keyword},
                    ],
                    max_tokens=2048,
                    temperature=1.2,
                    stream=False,
                    timeout=20.0,  # 为单个请求设置超时时间
                )

                # 获取返回的内容和token使用信息
                return_content = completion.choices[0].message.content
                usage_info = completion.usage
                _log.info(f"响应内容: {return_content}")
                _log.info(f"Token使用: {usage_info}")

                # 返回包含内容和token信息的字典
                return {
                    "content": return_content,
                    "usage": {
                        "prompt_tokens": usage_info.prompt_tokens,
                        "completion_tokens": usage_info.completion_tokens,
                        "total_tokens": usage_info.total_tokens,
                    },
                }

            except asyncio.TimeoutError as e:
                _log.error(f"请求超时: {e}")
                retry_count -= 1
                if retry_count > 0:
                    _log.info(f"重试 {3 - retry_count} 次...")
                    await asyncio.sleep(delay)

            except aiohttp.ClientError as e:  # 捕捉网络请求的异常
                _log.error(f"请求异常: {e}")
                retry_count -= 1
                if retry_count > 0:
                    _log.info(f"重试 {3 - retry_count} 次...")
                    await asyncio.sleep(delay)  # 延迟重试

            except Exception as e:  # 捕捉其他异常
                _log.error(f"未知异常: {e}")
                break  # 若是未知异常，直接中止重试

        # 重试次数用尽后返回 None
        _log.error("重试次数已用尽，返回 None")
        return None

    @staticmethod
    async def get_deepseek_balance() -> dict:
        """查询 DeepSeek API 余额"""
        url = "https://api.deepseek.com/user/balance"
        api_key = "sk-b13e5ef0d21942b0819728c345f1295a"

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
