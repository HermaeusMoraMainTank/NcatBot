"""测试 QQ 图片下载 - 使用 requests（与 ImageSender 相同的方式）"""
import requests
import html

# 测试 URL
test_url = "https://gchat.qpic.cn/download?appid=1407&fileid=EhRBtts54Q9AjBgvohHmafOOWbQTVxj_YiD_Cii82uG52qaWAzIEcHJvZFCAvaMBWhAk89CSqSnc1bl_7g2MhAVRegKua4IBAmd6&rkey=CAQSKAB6JWENi5LM_xp9vumLbuThJSaYf-yzMrbZsuq7Uz2qffcqm614gds&spec=0"

# 解码 HTML 实体
decoded_url = html.unescape(test_url)

print(f"原始 URL: {test_url[:80]}...")
print(f"解码后 URL: {decoded_url[:80]}...")
print()

# 测试 1: 不带任何头
print("=== 测试 1: 不带任何头 ===")
try:
    response = requests.get(test_url, timeout=10)
    print(f"状态码: {response.status_code}")
except Exception as e:
    print(f"失败: {e}")

# 测试 2: 带 User-Agent
print("\n=== 测试 2: 带 User-Agent ===")
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
}
try:
    response = requests.get(test_url, headers=headers, timeout=10)
    print(f"状态码: {response.status_code}")
except Exception as e:
    print(f"失败: {e}")

# 测试 3: 带完整头（与 ImageSender 相同）
print("\n=== 测试 3: 带完整头（与 ImageSender 相同） ===")
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://multimedia.nt.qq.com.cn/",
}
try:
    response = requests.get(decoded_url, headers=headers, timeout=10)
    print(f"状态码: {response.status_code}")
    print(f"内容类型: {response.headers.get('Content-Type')}")
    print(f"内容大小: {len(response.content)} bytes")
except Exception as e:
    print(f"失败: {e}")

# 测试 4: 使用原始 URL（不解码）
print("\n=== 测试 4: 使用原始 URL（不解码） ===")
try:
    response = requests.get(test_url, headers=headers, timeout=10)
    print(f"状态码: {response.status_code}")
    print(f"内容类型: {response.headers.get('Content-Type')}")
    print(f"内容大小: {len(response.content)} bytes")
except Exception as e:
    print(f"失败: {e}")
