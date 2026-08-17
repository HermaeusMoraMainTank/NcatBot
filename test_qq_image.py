import requests
import html

# 从日志中提取的 URL
url = "https://gchat.qpic.cn/download?appid=1407&fileid=EhQfqeLZ6k8YRAQTx9o-00OrcgI3Dxin0wUg_woo58S6itSmlgMyBHByb2RQgL2jAVoQuXYQaxgeoqWeVfP426qAH3oCktOCAQJneg&rkey=CAQSKAB6JWENi5LM_xp9vumLbuThJSaYf-yzMrbZsuq7Uz2qffcqm614gds&spec=0"

# 解码 HTML 实体
decoded_url = html.unescape(url)

print(f"原始 URL: {url[:80]}...")
print(f"解码后 URL: {decoded_url[:80]}...")
print(f"URL 是否相同: {url == decoded_url}")
print()

# 测试 1: 不带任何头
print("=== 测试 1: 不带任何头 ===")
try:
    response = requests.get(url, timeout=10)
    print(f"状态码: {response.status_code}")
except Exception as e:
    print(f"失败: {e}")

# 测试 2: 带 User-Agent
print("\n=== 测试 2: 带 User-Agent ===")
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}
try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"状态码: {response.status_code}")
except Exception as e:
    print(f"失败: {e}")

# 测试 3: 带完整头
print("\n=== 测试 3: 带完整头 ===")
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://multimedia.nt.qq.com.cn/",
}
try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"状态码: {response.status_code}")
    print(f"内容类型: {response.headers.get('Content-Type')}")
    print(f"内容大小: {len(response.content)} bytes")
except Exception as e:
    print(f"失败: {e}")

# 测试 4: 带完整头 + 解码 URL
print("\n=== 测试 4: 带完整头 + 解码 URL ===")
try:
    response = requests.get(decoded_url, headers=headers, timeout=10)
    print(f"状态码: {response.status_code}")
    print(f"内容类型: {response.headers.get('Content-Type')}")
    print(f"内容大小: {len(response.content)} bytes")
except Exception as e:
    print(f"失败: {e}")
