"""测试 QQ 图片下载"""
import asyncio
import sys
sys.path.insert(0, '.')

async def test_download():
    from plugins.common.utils.AiUtil import AiUtil
    from plugins.common.utils.async_io import http_get_bytes
    
    # 测试 URL
    test_url = "https://gchat.qpic.cn/download?appid=1407&fileid=EhQfqeLZ6k8YRAQTx9o-00OrcgI3Dxin0wUg_woo58S6itSmlgMyBHByb2RQgL2jAVoQuXYQaxgeoqWeVfP426qAH3oCktOCAQJneg&rkey=CAQSKAB6JWENi5LM_xp9vumLbuThJSaYf-yzMrbZsuq7Uz2qffcqm614gds&spec=0"
    
    print("=== 测试 1: http_get_bytes ===")
    try:
        status, content = await http_get_bytes(test_url, timeout=30, verify_ssl=False)
        print(f"状态码: {status}")
        if status == 200:
            print(f"内容大小: {len(content)} bytes")
        else:
            print(f"下载失败")
    except Exception as e:
        print(f"异常: {e}")
    
    print("\n=== 测试 2: AiUtil._download_with_aiohttp ===")
    try:
        result = await AiUtil._download_with_aiohttp(test_url)
        if result:
            print(f"成功: {len(result)} chars")
        else:
            print("失败")
    except Exception as e:
        print(f"异常: {e}")

if __name__ == "__main__":
    asyncio.run(test_download())
