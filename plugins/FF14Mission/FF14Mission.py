import json
import os
import logging
from typing import List, Dict

from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin


# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

bot = CompatibleEnrollment


class MissionVersion:
    def __init__(self, version: str, quests: List[str]):
        self.version = version
        self.quests = quests


class FF14Mission(BasePlugin):
    name = "FF14Mission"  # 插件名称
    version = "1.0"  # 插件版本
    mission_data: List[MissionVersion] = []
    mission_progress: Dict[str, int] = {}

    async def on_load(self):
        """异步加载插件"""
        logger.info(f"开始加载 {self.name} 插件 v{self.version}")
        await self._load_mission_data()
        logger.info(f"{self.name} 插件加载完成")

    async def _load_mission_data(self):
        """加载任务数据"""
        try:
            json_path = os.path.join("data", "json", "mission.json")
            with open(json_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
                self.mission_data = [
                    MissionVersion(item["version"], item["quests"])
                    for item in json_data
                ]

                # 初始化任务进度
                for version in self.mission_data:
                    self.mission_progress[version.version] = 0

            logger.info(f"成功加载任务数据：{len(self.mission_data)}个版本")
        except Exception as e:
            logger.error(f"任务数据加载失败: {str(e)}")

    def _search_mission_by_keyword(self, keyword: str) -> str:
        """根据关键字搜索任务"""
        result = []

        for version in self.mission_data:
            quests = version.quests
            total = len(quests)

            for i, quest in enumerate(quests):
                if keyword in quest:
                    # 找到匹配的任务，返回相关信息
                    progress = (i + 1) / total * 100
                    result.append(
                        f"当前主线版本: {version.version}\n"
                        f"任务名: {quest}\n"
                        f"任务进度: {i + 1}/{total} ({progress:.2f}%)\n"
                        f"后续剩余任务: {total - (i + 1)} 个任务\n"
                    )

        if not result:
            return "未找到相关任务。"

        return "\n".join(result)

    @bot.group_event()
    async def handle_mission_query(self, input: GroupMessage):
        """处理FF14主线任务查询"""
        message = input.raw_message

        if message.startswith("搜索任务 "):
            keyword = message.replace("搜索任务 ", "")
            response = self._search_mission_by_keyword(keyword)
            await self.api.post_group_msg(group_id=input.group_id, text=response)
