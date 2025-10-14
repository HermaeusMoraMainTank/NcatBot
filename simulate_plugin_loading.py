#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
from datetime import datetime, date, timedelta
from dataclasses import dataclass


@dataclass
class MessageStats:
    daily_counts: dict = None
    hourly_counts: dict = None
    last_message: datetime = None

    def __post_init__(self):
        if self.daily_counts is None:
            self.daily_counts = {}
        if self.hourly_counts is None:
            self.hourly_counts = {}

    def to_dict(self) -> dict:
        return {
            "daily_counts": self.daily_counts,
            "hourly_counts": self.hourly_counts,
            "last_message": self.last_message.isoformat()
            if self.last_message
            else datetime.now().isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MessageStats":
        daily_counts = data.get("daily_counts", {})
        if not isinstance(daily_counts, dict):
            daily_counts = {}

        hourly_counts = data.get("hourly_counts", {})
        if not isinstance(hourly_counts, dict):
            hourly_counts = {}

        last_message = data.get("last_message")
        if isinstance(last_message, str):
            try:
                last_message = datetime.fromisoformat(last_message)
            except ValueError:
                last_message = datetime.now()
        elif not isinstance(last_message, datetime):
            last_message = datetime.now()

        return cls(
            daily_counts=daily_counts,
            hourly_counts=hourly_counts,
            last_message=last_message,
        )


def simulate_plugin_loading():
    """模拟插件数据加载过程"""

    print("=== 模拟插件数据加载过程 ===")

    # 模拟插件的数据结构
    group_stats = {}
    user_stats = {}

    # 加载群组数据
    group_file = "data/json/message_group_stats.json"
    if os.path.exists(group_file):
        with open(group_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        group_stats_data = data.get("group_stats", {})
        print(f"从文件加载群组数据: {len(group_stats_data)} 个群组")

        for k, v in group_stats_data.items():
            try:
                group_id = int(k)
                group_stats[group_id] = MessageStats.from_dict(v)
                print(f"成功加载群组 {group_id} 的统计数据")
            except Exception as e:
                print(f"加载群组数据失败: {e}")
                continue

        print(f"内存中群组数据: {len(group_stats)} 个群组")

    # 加载用户数据
    user_file = "data/json/message_user_stats.json"
    if os.path.exists(user_file):
        with open(user_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        user_stats_data = data.get("user_stats", {})
        print(f"从文件加载用户数据: {len(user_stats_data)} 个群组")

        for k, v in user_stats_data.items():
            try:
                group_id = int(k)
                user_stats[group_id] = {}

                for k2, v2 in v.items():
                    try:
                        user_id = int(k2)
                        user_stats[group_id][user_id] = MessageStats.from_dict(v2)
                        print(f"成功加载用户 {user_id} 在群组 {group_id} 的统计数据")
                    except Exception as e:
                        print(f"加载用户数据失败: {e}")
                        continue
            except Exception as e:
                print(f"加载群组数据失败: {e}")
                continue

        total_users = sum(len(users) for users in user_stats.values())
        print(f"内存中用户数据: {len(user_stats)} 个群组，{total_users} 个用户")

    # 检查特定群组 1064163905
    print(f"\n=== 检查群组 1064163905 ===")
    group_1064163905 = group_stats.get(1064163905)
    if group_1064163905:
        print(f"群组 1064163905 在内存中存在")
        print(f"  daily_counts: {group_1064163905.daily_counts}")
        print(f"  hourly_counts: {group_1064163905.hourly_counts}")
        print(f"  last_message: {group_1064163905.last_message}")

        # 计算总发言数
        total_daily = sum(group_1064163905.daily_counts.values())
        total_hourly = sum(group_1064163905.hourly_counts.values())
        print(f"  总发言数 (daily): {total_daily}")
        print(f"  总发言数 (hourly): {total_hourly}")
    else:
        print("群组 1064163905 在内存中不存在")

    # 检查用户数据
    group_1064163905_users = user_stats.get(1064163905, {})
    if group_1064163905_users:
        print(f"\n群组 1064163905 的用户数据:")
        for user_id, user_stat in group_1064163905_users.items():
            total_daily = sum(user_stat.daily_counts.values())
            print(f"  用户 {user_id}: {total_daily} 条发言")
    else:
        print("群组 1064163905 的用户数据不存在")


if __name__ == "__main__":
    simulate_plugin_loading()
