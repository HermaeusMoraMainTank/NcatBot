import hashlib
import logging
from datetime import date

from ncatpy.common.constants.HMMT import HMMT
from ncatpy.common.utils.CommonUtil import CommonUtil
from ncatpy.message import GroupMessage

# 日志配置
log = logging.getLogger(__name__)


class JRRP:
    def __init__(self):
        pass

    async def handle_jrrp(self, input: GroupMessage):
        """
        处理今日人品（JRRP）功能
        """
        if input.raw_message in ["jrrp", "今日人品"]:
            sender = input.sender
            log.info(f"JRRP request from {sender.nickname} ({sender.user_id})")
            if sender.user_id in [HMMT.HMMT_ID, 1042081663, 864772045]:
                await input.add_text(f"{sender.nickname} 的每日人品都会是：101。").reply()
                return
            # 计算今日人品值
            message_digest = hashlib.sha256()
            message_digest.update(str(sender.user_id).encode())
            message_digest.update(CommonUtil.calculate_current_day().encode())
            digest = message_digest.digest()
            luck_value = abs(CommonUtil.bytes_to_long(digest)) % 101
            log.info(f"Calculated luck value: {luck_value}")

            # 根据人品值返回不同的消息
            if luck_value == 0:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。怎，怎么会这样……").reply()
            elif 0 < luck_value <= 20:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。推荐闷头睡大觉。").reply()
            elif 20 < luck_value <= 40:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。也许今天适合摆烂。").reply()
            elif 40 < luck_value <= 60 and luck_value != 42:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。又是平凡的一天。").reply()
            elif 60 < luck_value <= 80 and luck_value != 77:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。太阳当空照，花儿对你笑。").reply()
            elif 80 < luck_value < 100:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。出门可能捡到{luck_value}块钱。").reply()
            elif luck_value == 42:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。感觉可以参透宇宙的真理。").reply()
            elif luck_value == 77:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。要不要去抽一发卡试试呢……").reply()
            elif luck_value == 100:
                await input.add_text(f"{sender.nickname} 的今日人品是：{luck_value}。买彩票可能会中大奖哦！").reply()
