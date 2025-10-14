#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
from datetime import datetime, date, timedelta


def debug_stats():
    """调试统计数据"""

    # 读取群组数据
    group_file = "data/json/message_group_stats.json"
    user_file = "data/json/message_user_stats.json"

    print("=== 调试统计数据 ===")

    # 检查群组数据
    if os.path.exists(group_file):
        with open(group_file, "r", encoding="utf-8") as f:
            group_data = json.load(f)

        print(f"群组数据文件存在，包含 {len(group_data.get('group_stats', {}))} 个群组")

        # 检查特定群组 1064163905
        group_1064163905 = group_data.get("group_stats", {}).get("1064163905")
        if group_1064163905:
            print(f"\n群组 1064163905 的数据:")
            print(f"  daily_counts: {group_1064163905.get('daily_counts', {})}")
            print(f"  hourly_counts: {group_1064163905.get('hourly_counts', {})}")
            print(f"  last_message: {group_1064163905.get('last_message', '')}")

            # 计算总发言数
            total_daily = sum(group_1064163905.get("daily_counts", {}).values())
            total_hourly = sum(group_1064163905.get("hourly_counts", {}).values())
            print(f"  总发言数 (daily): {total_daily}")
            print(f"  总发言数 (hourly): {total_hourly}")
        else:
            print("群组 1064163905 的数据不存在")
    else:
        print("群组数据文件不存在")

    # 检查用户数据
    if os.path.exists(user_file):
        with open(user_file, "r", encoding="utf-8") as f:
            user_data = json.load(f)

        print(f"\n用户数据文件存在，包含 {len(user_data.get('user_stats', {}))} 个群组")

        # 检查特定群组 1064163905 的用户数据
        group_1064163905_users = user_data.get("user_stats", {}).get("1064163905", {})
        if group_1064163905_users:
            print(f"\n群组 1064163905 的用户数据:")
            for user_id, user_stats in group_1064163905_users.items():
                total_daily = sum(user_stats.get("daily_counts", {}).values())
                print(f"  用户 {user_id}: {total_daily} 条发言")
        else:
            print("群组 1064163905 的用户数据不存在")
    else:
        print("用户数据文件不存在")


if __name__ == "__main__":
    debug_stats()
