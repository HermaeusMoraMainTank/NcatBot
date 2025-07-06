#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据恢复工具
用于检查和恢复MessageStats和EmojiStats插件可能丢失的数据
"""

import os
import json
import shutil
import sys
from datetime import datetime
from typing import Dict, Any


def check_file_integrity(file_path: str) -> Dict[str, Any]:
    """检查文件完整性"""
    result = {
        "exists": False,
        "size": 0,
        "valid_json": False,
        "backup_exists": False,
        "backup_size": 0,
        "backup_valid_json": False,
        "error": None,
    }

    try:
        # 检查主文件
        if os.path.exists(file_path):
            result["exists"] = True
            result["size"] = os.path.getsize(file_path)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    json.load(f)
                result["valid_json"] = True
            except Exception as e:
                result["error"] = f"主文件JSON无效: {e}"

        # 检查备份文件
        backup_path = file_path + ".backup"
        if os.path.exists(backup_path):
            result["backup_exists"] = True
            result["backup_size"] = os.path.getsize(backup_path)

            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    json.load(f)
                result["backup_valid_json"] = True
            except Exception as e:
                result["error"] = f"备份文件JSON无效: {e}"

    except Exception as e:
        result["error"] = str(e)

    return result


def recover_file(file_path: str, force: bool = False) -> bool:
    """恢复文件"""
    try:
        backup_path = file_path + ".backup"

        # 如果主文件不存在或无效，尝试从备份恢复
        if (
            not os.path.exists(file_path)
            or not check_file_integrity(file_path)["valid_json"]
        ):
            if (
                os.path.exists(backup_path)
                and check_file_integrity(backup_path)["backup_valid_json"]
            ):
                shutil.copy2(backup_path, file_path)
                print(f"✓ 从备份恢复文件: {file_path}")
                return True
            else:
                print(f"✗ 无法恢复文件: {file_path} (备份文件不存在或无效)")
                return False

        # 如果主文件存在但备份文件不存在，创建备份
        elif (
            not os.path.exists(backup_path)
            and check_file_integrity(file_path)["valid_json"]
        ):
            shutil.copy2(file_path, backup_path)
            print(f"✓ 创建备份文件: {backup_path}")
            return True

        # 如果主文件无效但备份文件有效，从备份恢复
        elif (
            not check_file_integrity(file_path)["valid_json"]
            and check_file_integrity(backup_path)["backup_valid_json"]
        ):
            shutil.copy2(backup_path, file_path)
            print(f"✓ 从备份恢复文件: {file_path}")
            return True

        else:
            print(f"✓ 文件正常: {file_path}")
            return True

    except Exception as e:
        print(f"✗ 恢复文件失败: {file_path} - {e}")
        return False


def main():
    """主函数"""
    print("=== 数据恢复工具 ===")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 定义需要检查的文件
    files_to_check = [
        "data/json/message_group_stats.json",
        "data/json/message_user_stats.json",
        "data/json/emoji_group_stats.json",
        "data/json/emoji_user_stats.json",
    ]

    print("检查文件状态:")
    print("-" * 60)

    all_ok = True
    for file_path in files_to_check:
        print(f"\n检查文件: {file_path}")
        integrity = check_file_integrity(file_path)

        if integrity["exists"]:
            print(
                f"  主文件: {'✓' if integrity['valid_json'] else '✗'} (大小: {integrity['size']} 字节)"
            )
        else:
            print(f"  主文件: ✗ (不存在)")
            all_ok = False

        if integrity["backup_exists"]:
            print(
                f"  备份文件: {'✓' if integrity['backup_valid_json'] else '✗'} (大小: {integrity['backup_size']} 字节)"
            )
        else:
            print(f"  备份文件: ✗ (不存在)")

        if integrity["error"]:
            print(f"  错误: {integrity['error']}")
            all_ok = False

    print("\n" + "=" * 60)

    if all_ok:
        print("✓ 所有文件状态正常")
        return

    print("发现文件问题，开始恢复...")
    print("-" * 60)

    recovered_count = 0
    for file_path in files_to_check:
        if recover_file(file_path):
            recovered_count += 1

    print(f"\n恢复完成: {recovered_count}/{len(files_to_check)} 个文件")

    # 再次检查
    print("\n恢复后检查:")
    print("-" * 60)

    all_ok_after = True
    for file_path in files_to_check:
        integrity = check_file_integrity(file_path)
        if not integrity["exists"] or not integrity["valid_json"]:
            all_ok_after = False
            print(f"✗ {file_path} 仍有问题")
        else:
            print(f"✓ {file_path} 正常")

    if all_ok_after:
        print("\n✓ 所有文件已恢复正常")
    else:
        print("\n✗ 部分文件仍有问题，可能需要手动处理")


if __name__ == "__main__":
    main()
