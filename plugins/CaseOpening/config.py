DEFAULT_CONFIG = {
    # 1 元 = 200 金币（按全服金币量级定的；调这一个数即可整体缩放）
    "coin_per_cny": 200,
    # 回收手续费
    "sell_fee": 0.15,
    # CS2 钥匙按 Valve 定价折算
    "key_price_usd": 2.49,
    "usd_cny": 7.2,
    # 背包/饰品每页（3 列网格）
    "page_size": 15,
    # CS2
    "cs2_max_batch": 10,
    # Dota
    "dota_max_batch": 10,
}

#: 模块通用键 -> 本插件配置键
CS2_KEYS = {"max_batch": "cs2_max_batch"}
DOTA_KEYS = {"max_batch": "dota_max_batch"}
