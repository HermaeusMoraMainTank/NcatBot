"""
通过MD5或文件内容搜索图片
"""
import hashlib
import os
import sys
from pathlib import Path


def calculate_md5(file_path: str) -> str:
    """计算文件的MD5"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def calculate_file_size(file_path: str) -> int:
    """获取文件大小"""
    return os.path.getsize(file_path)


def find_matching_image(target_path: str, search_dir: str):
    """在目录中搜索与目标图片匹配的文件"""
    if not os.path.exists(target_path):
        print(f"错误: 目标文件不存在: {target_path}")
        return None
    
    if not os.path.exists(search_dir):
        print(f"错误: 搜索目录不存在: {search_dir}")
        return None
    
    target_md5 = calculate_md5(target_path)
    target_size = calculate_file_size(target_path)
    
    print(f"目标文件: {target_path}")
    print(f"目标MD5: {target_md5}")
    print(f"目标大小: {target_size} bytes")
    print(f"\n在 {search_dir} 中搜索...")
    print("-" * 50)
    
    matches = []
    similar_size = []
    
    for root, dirs, files in os.walk(search_dir):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                file_path = os.path.join(root, file)
                try:
                    file_size = calculate_file_size(file_path)
                    
                    # 先比较大小（快速筛选）
                    if file_size == target_size:
                        file_md5 = calculate_md5(file_path)
                        if file_md5 == target_md5:
                            matches.append(file_path)
                            print(f"✓ 完全匹配: {file_path}")
                    
                    # 记录大小相近的文件（±5%）
                    elif abs(file_size - target_size) / target_size < 0.05:
                        similar_size.append((file_path, file_size))
                        
                except Exception as e:
                    pass
    
    print("-" * 50)
    
    if matches:
        print(f"\n找到 {len(matches)} 个完全匹配的文件:")
        for m in matches:
            print(f"  - {m}")
        return matches
    else:
        print("\n没有找到完全匹配的文件")
        
        if similar_size:
            print(f"\n大小相近的文件 ({len(similar_size)} 个):")
            for path, size in similar_size[:10]:
                print(f"  - {path} ({size} bytes)")
        
        return None


if __name__ == "__main__":
    # 默认参数
    target = r"C:\Users\27342\Downloads\temp_search.jpg"
    search_dir = r"C:\Users\27342\Downloads\lalafell\lalafell"
    
    if len(sys.argv) >= 2:
        target = sys.argv[1]
    if len(sys.argv) >= 3:
        search_dir = sys.argv[2]
    
    find_matching_image(target, search_dir)

