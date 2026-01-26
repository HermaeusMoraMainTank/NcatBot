"""
通过感知哈希搜索视觉相似的图片
"""
import os
import sys
from PIL import Image
import imagehash


def find_similar_image(target_path: str, search_dir: str, threshold: int = 10):
    """在目录中搜索与目标图片视觉相似的文件
    
    Args:
        target_path: 目标图片路径
        search_dir: 搜索目录
        threshold: 哈希差异阈值，越小越相似（0=完全相同，建议5-15）
    """
    if not os.path.exists(target_path):
        print(f"错误: 目标文件不存在: {target_path}")
        return None
    
    if not os.path.exists(search_dir):
        print(f"错误: 搜索目录不存在: {search_dir}")
        return None
    
    # 计算目标图片的感知哈希
    try:
        target_img = Image.open(target_path)
        target_hash = imagehash.phash(target_img)
        print(f"目标文件: {target_path}")
        print(f"目标哈希: {target_hash}")
        print(f"\n在 {search_dir} 中搜索...")
        print(f"相似度阈值: {threshold} (差异越小越相似)")
        print("-" * 60)
    except Exception as e:
        print(f"无法读取目标图片: {e}")
        return None
    
    matches = []
    
    for root, dirs, files in os.walk(search_dir):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                file_path = os.path.join(root, file)
                try:
                    img = Image.open(file_path)
                    file_hash = imagehash.phash(img)
                    diff = target_hash - file_hash
                    
                    if diff <= threshold:
                        matches.append((file_path, diff))
                        print(f"✓ 找到相似图片 (差异={diff}): {file_path}")
                        
                except Exception as e:
                    pass
    
    print("-" * 60)
    
    if matches:
        # 按相似度排序
        matches.sort(key=lambda x: x[1])
        print(f"\n找到 {len(matches)} 个相似图片:")
        for path, diff in matches:
            status = "【完全相同】" if diff == 0 else f"【差异={diff}】"
            print(f"  {status} {path}")
        return matches
    else:
        print("\n没有找到相似的图片")
        return None


if __name__ == "__main__":
    target = r"C:\Users\27342\Downloads\temp_search.jpg"
    search_dir = r"C:\Users\27342\Downloads\lalafell\lalafell"
    threshold = 10
    
    if len(sys.argv) >= 2:
        target = sys.argv[1]
    if len(sys.argv) >= 3:
        search_dir = sys.argv[2]
    if len(sys.argv) >= 4:
        threshold = int(sys.argv[3])
    
    find_similar_image(target, search_dir, threshold)

