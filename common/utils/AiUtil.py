import aiohttp
import asyncio
import json
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
        api_key = "sk-08b905b8cf4d4d27a9c04dba8f1ade70"

        # OpenAI 配置
        client = OpenAI(
            api_key=api_key,
            base_url=url,
        )

        retry_count = 3  # 最大重试次数
        delay = 2  # 重试延迟时间（秒）

        while retry_count > 0:
            try:
                completion = client.chat.completions.create(
                    # model="deepseek-v3",  # 你可以选择你需要的模型
                    model="deepseek-chat",  # 你可以选择你需要的模型
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": keyword},
                    ],
                    max_tokens=2048,
                    temperature=1.2,
                    stream=False,
                )

                # 获取返回的内容
                return_content = completion.choices[0].message.content
                _log.info(f"响应内容: {return_content}")
                return return_content

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


# 示例调用
async def main():
    keyword = "Python 异步编程"
    prompt = "你是一个编程助手，请帮助用户解决问题。"
    result = await AiUtil.search_deepseek(keyword, prompt)
    print(result)


# 运行示例
if __name__ == "__main__":
    asyncio.run(main())
