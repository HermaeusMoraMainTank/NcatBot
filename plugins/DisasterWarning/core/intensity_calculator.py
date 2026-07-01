import math


class IntensityCalculator:
    """
    地震烈度计算器
    用于根据震级和距离估算本地烈度
    """

    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        计算两点间的地表距离（海夫赛文公式），单位：公里
        """
        R = 6371.0  # 地球半径（公里）

        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)

        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(d_lon / 2) ** 2
        )

        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        distance = R * c
        return distance

    @staticmethod
    def calculate_estimated_intensity(
        magnitude: float,
        distance_km: float,
        depth_km: float = 10.0,
        event_longitude: float = None,
    ) -> float:
        """
        估算本地烈度
        使用陈达生、汪素云等提出的椭圆烈度衰减模型（GB 18306 参考）
        区分中国东部和西部地区

        :param magnitude: 震级
        :param distance_km: 震中距（公里）
        :param depth_km: 震源深度（公里），默认10km
        :param event_longitude: 震中经度，用于判定东/西部地区（以105度为界）
        :return: 预估烈度
        """
        # 计算震源距 R
        hypocentral_distance = math.sqrt(distance_km**2 + depth_km**2)
        R = max(hypocentral_distance, 5.0)

        # 判定区域
        # 默认使用东部公式（人口稠密区），如果提供经度且 < 105 则使用西部公式
        is_west = False
        if event_longitude is not None and event_longitude < 105.0:
            is_west = True

        if is_west:
            # 西部地区 (参考: GB 18306-2001 西部/新疆/青藏区综合)
            # I = 5.643 + 1.538*M - 2.109*ln(R + 25)
            # 也有文献使用 I = 5.760 + 1.474*M - 3.737*ln(R + 23) 但上述公式与东部形式统一，更为常用
            A, B, C, R0 = 5.643, 1.538, 2.109, 25.0
        else:
            # 东部地区 (参考: GB 18306-2001 东部/中强区)
            # I = 6.046 + 1.480*M - 2.081*ln(R + 25)
            A, B, C, R0 = 6.046, 1.480, 2.081, 25.0

        # 计算
        # 公式: I = A + B*M - C*ln(R + R0)
        log_term = math.log(R + R0)
        intensity = A + B * magnitude - C * log_term

        # 烈度通常不小于0，最大通常不超过12
        return max(0.0, min(12.0, intensity))

    @staticmethod
    def get_intensity_description(intensity: float) -> str:
        """
        获取烈度描述（带颜色Emoji）
        参考 GB/T 17742-2020 中国地震烈度表
        """
        if intensity < 1.0:
            return "⚪ 无感"
        elif intensity < 2.0:
            return "⚪ 微有感"
        elif intensity < 3.0:
            return "🔵 轻微有感"
        elif intensity < 4.0:
            return "🔵 室内有感"
        elif intensity < 5.0:
            return "🟢 震感明显"
        elif intensity < 6.0:
            return "🟡 震感强烈"
        elif intensity < 7.0:
            return "🟠 惊慌逃生"
        elif intensity < 8.0:
            return "🟠 房屋损坏"
        elif intensity < 9.0:
            return "🔴 严重破坏"
        elif intensity < 10.0:
            return "🔴 毁灭性"
        else:
            return "🟣 极度毁灭"

