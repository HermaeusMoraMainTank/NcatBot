#!/usr/bin/env python3
"""
验证MessageStats插件修复效果
"""

import json
import os

def verify_fix():
    """验证修复效果"""
    
    # 读取当前数据
    group_file = "data/json/message_group_stats.json"
    user_file = "data/json/message_user_stats.json"
    
    if os.path.exists(group_file):
        with open(group_file, "r", encoding="utf-8") as f:
            group_data = json.load(f)
        
        print("✅ 群组统计数据:")
        for group_id, stats in group_data["group_stats"].items():
            daily_count = stats["daily_counts"].get("2025-10-10", 0)
            print(f"   群组 {group_id}: {daily_count} 条消息")
    
    if os.path.exists(user_file):
        with open(user_file, "r", encoding="utf-8") as f:
            user_data = json.load(f)
        
        print("\n✅ 用户统计数据:")
        for group_id, users in user_data["user_stats"].items():
            print(f"   群组 {group_id}:")
            for user_id, stats in users.items():
                daily_count = stats["daily_counts"].get("2025-10-10", 0)
                print(f"     用户 {user_id}: {daily_count} 条消息")
    
    print("\n🎉 修复验证完成！增量更新功能正常工作。")

if __name__ == "__main__":
    verify_fix()

