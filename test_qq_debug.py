import asyncio
import aiohttp
import html

# 测试 URL
test_url = "https://gchat.qpic.cn/download?appid=1407&fileid=EhQfqeLZ6k8YRAQTx9o-00OrcgI3Dxin0wUg_woo58S6itSmlgMyBHByb2RQgL2jAVoQuXYQaxgeoqWeVfP426qAH3oCktOCAQJneg&rkey=CAQSKAB6JWENi5LM_xp9vumLbuThJSaYf-yzMrbZsuq7Uz2qffcqm614gds&spec=0"

print(f"原始 URL: {test_url[:80]}...")
print(f"HTML 解码后: {html.unescape(test_url)[:80]}...")
print(f"URL 是否包含 &amp;: {'&amp;' in test_url}")
print()

async def test_download():
    # 完整的浏览器请求头
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://multimedia.nt.qq.com.cn/",
    }
    
    print("=== 测试 1: 使用 aiohttp (当前代码) ===")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, headers=headers, timeout=30) as response:
                print(f"状态码: {response.status}")
                if response.status == 200:
                    data = await response.read()
                    print(f"内容大小: {len(data)} bytes")
                else:
                    text = await response.text()
                    print(f"错误响应: {text[:200]}")
    except Exception as e:
        print(f"异常: {e}")
    
    print()
    print("=== 测试 2: 使用 aiohttp + 解码 URL ===")
    decoded_url = html.unescape(test_url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(decoded_url, headers=headers, timeout=30) as response:
                print(f"状态码: {response.status}")
                if response.status == 200:
                    data = await response.read()
                    print(f"内容大小: {len(data)} bytes")
                else:
                    text = await response.text()
                    print(f"错误响应: {text[:200]}")
    except Exception as e:
        print(f"异常: {e}")
    
    print()
    print("=== 测试 3: 使用 aiohttp + 无 Referer ===")
    headers_no_referer = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, headers=headers_no_referer, timeout=30) as response:
                print(f"状态码: {response.status}")
                if response.status == 200:
                    data = await response.read()
                    print(f"内容大小: {len(data)} bytes")
                else:
                    text = await response.text()
                    print(f"错误响应: {text[:200]}")
    except Exception as e:
        print(f"异常: {e}")

asyncio.run(test_download())
