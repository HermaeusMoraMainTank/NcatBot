import aiohttp
import asyncio
import json

from ncatpy import logging

# 日志配置
_log = logging.get_logger()

class AiUtil:
    @staticmethod
    async def search_deepseek(keyword: str, prompt: str) -> str:
        _log.info(keyword)
        _log.info(prompt)

        # 请求 URL 和 API Key
        url = "https://api.deepseek.com/chat/completions"
        api_key = "sk-08b905b8cf4d4d27a9c04dba8f1ade70"

        # 构建请求体
        system_message = {"role": "system", "content": prompt}
        user_message = {"role": "user", "content": keyword}
        messages = [system_message, user_message]

        request_body = {
            "model": "deepseek-chat",
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.5,
            "stream": False,
        }

        # 设置请求头
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # 重试次数
        retry_count = 3

        async with aiohttp.ClientSession() as session:
            while retry_count > 0:
                try:
                    # 发送 POST 请求
                    async with session.post(
                            url, headers=headers, json=request_body, timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status == 200:
                            # 解析响应
                            response_body = await response.text()
                            _log.info(f"响应结果: {response_body}")

                            json_response = json.loads(response_body)
                            choices = json_response["choices"]
                            return_content = choices[0]["message"]["content"]

                            return return_content
                        else:
                            # 处理失败响应
                            error_body = await response.text()
                            _log.info(f"请求失败，状态码: {response.status}")
                            _log.info(f"错误信息: {error_body}")
                            retry_count -= 1
                except Exception as e:
                    # 处理异常
                    _log.error(f"请求异常: {e}")
                    retry_count -= 1

        # 重试次数用尽后返回 None
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
