"""商店供需定价。

旧实现每天给每件物品独立抽一个 50%–199% 的随机折扣（`random.randint`），
均价 124.5%，等于把基准价整体抬高约四分之一，而且与玩家行为完全无关：
卖 200 条鱼只会让第二天的折扣区间收窄到 90%–109%，既不会持续下跌，
也不会在停止抛售后回到基准价。

这里改成单一状态量的供需模型：

* 每件物品（每条鱼、每种鱼王、每种贵重品…）各自记录一个 ``pressure``
  （净过剩供给，单位＝件），因此任何一条鱼被卖出都只影响它自己的价格；
* 价格系数 ``factor = depth / (depth + pressure)``，卖出越多价格越低；
* ``depth`` 是「让价格跌到一半」的件数：按类别给基准值，鱼类再按自身基准价
  缩放——便宜的高产鱼池子深，稀有的高价鱼池子浅，同样的抛售量对稀有鱼
  的打击更大；
* ``pressure`` 每天残留 82%（半衰期约 3.5 天），并向略低于 0 的基线
  ``rest`` 收敛，因此停售后价格会回到基准价附近（约 100%–103%）；
* 价格下限 15%：集中抛售可以把一类物品砸到很低，但永远回不到基准价以上
  （除非压库存），也不会跌破 0。

买入不改变 pressure：避免「先低价买入压低系数再卖出」的套利环路，
出售税本身已经让任何买卖往返必亏。
"""

from __future__ import annotations

import math

# 每天残留比例（0.82 → 半衰期 ln2/ln(1/0.82) ≈ 3.5 天）
DECAY_PER_DAY = 0.82

# 价格系数的上下限，防御异常 pressure。下限 15% 让集中抛售能探到很低的价位。
MIN_FACTOR = 0.15
MAX_FACTOR = 1.30

# 让该类物品价格跌到 50% 所需的过剩件数（鱼类还会按自身价格缩放）。
KIND_DEPTH = {
    "king": 8.0,
    "fish": 140.0,
    "valuable": 18.0,
    "food": 36.0,
    "waste": 90.0,
    "default": 60.0,
}

# 市场每天自然吸收的件数；pressure 的静止点因此为 -consume / λ，
# 也就是「长时间没人卖」时价格略高于基准价（约 101%–103%）。
KIND_CONSUME = {
    "king": 0.02,
    "fish": 0.8,
    "valuable": 0.08,
    "food": 0.2,
    "waste": 0.5,
    "default": 0.4,
}

# 鱼类按基准价缩放 depth：以 200 金币为参照，越贵越浅、越便宜越深。
FISH_PRICE_REF = 200.0
FISH_DEPTH_SCALE = (0.5, 1.5)

LAMBDA = -math.log(DECAY_PER_DAY)


def depth(kind: str) -> float:
    return KIND_DEPTH.get(kind, KIND_DEPTH["default"])


def item_depth(kind: str, price: float = 0) -> float:
    """单件物品的 depth：鱼类按自身基准价缩放，所以每条鱼敏感度不同。"""
    base = depth(kind)
    if kind != "fish" or price <= 0:
        return base
    scale = (FISH_PRICE_REF / float(price)) ** 0.5
    low, high = FISH_DEPTH_SCALE
    return base * max(low, min(high, scale))


def rest_pressure(kind: str) -> float:
    """长时间无人出售时 pressure 收敛到的静止点。"""
    return -KIND_CONSUME.get(kind, KIND_CONSUME["default"]) / LAMBDA


def converge(pressure: float, kind: str, days: float) -> float:
    """把 pressure 按经过的天数向静止点收敛。"""
    if days <= 0:
        return pressure
    rest = rest_pressure(kind)
    return rest + (pressure - rest) * (DECAY_PER_DAY ** days)


def clamp(value: float) -> float:
    return max(MIN_FACTOR, min(MAX_FACTOR, value))


def factor(pressure: float, kind: str, price: float = 0) -> float:
    scale = item_depth(kind, price)
    return clamp(scale / max(1e-9, scale + pressure))


def average_factor(pressure: float, kind: str, number: int, price: float = 0) -> float:
    """一次卖出 ``number`` 件时的均价系数。

    每卖出一件 pressure 增加 1，价格按 ``depth / (depth + pressure)`` 连续
    下滑，所以整批成交的均价是这条曲线的积分均值：拆成多次单件出售与
    一次性卖出得到的总价几乎相同，不能靠拆单规避砸盘。
    """
    count = max(1, int(number))
    scale = item_depth(kind, price)
    start = max(1e-6, scale + pressure)
    return clamp(scale * math.log((start + count) / start) / count)
