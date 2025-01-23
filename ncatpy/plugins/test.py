import requests
import json

def search_player(name: str):
    url = "https://apiff14risingstones.web.sdo.com/api/common/search?type=6&keywords=%E8%93%9D%E6%99%B4&part_id=&orderBy=comment&page=1&limit=100&pageTime="
    payload = {}
    headers = {
        'Host': 'apiff14risingstones.web.sdo.com'
    }
    try:
        response = requests.request("GET", url, headers=headers, data=payload)
        response.raise_for_status()  # 检查请求是否成功
        return response.json()  # 返回 JSON 数据
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
    except requests.exceptions.RequestException as e:
        print(f"Request Error: {e}")
    return None

if __name__ == "__main__":
    # 固定玩家名称为 "蓝晴"
    player_name = "蓝晴"
    print(f"Searching for player: {player_name}")

    result = search_player(player_name)
    if result:
        print("Search result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))  # 格式化输出 JSON
    else:
        print("Failed to fetch data.")