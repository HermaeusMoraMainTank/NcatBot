"""
快速搜索视觉相似的图片（带进度显示）
"""
import os
from PIL import Image
import imagehash

target_path = r"C:\Users\27342\Downloads\temp_search.jpg"
search_dir = r"C:\Users\27342\Downloads\lalafell\lalafell"
threshold = 10

# 计算目标图片的哈希
target_img = Image.open(target_path)
target_hash = imagehash.phash(target_img)
print(f"目标哈希: {target_hash}")
print(f"搜索目录: {search_dir}")
print("-" * 50)

# 获取所有图片文件
files = [f for f in os.listdir(search_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))]
total = len(files)
print(f"共 {total} 个图片文件")

matches = []
for i, file in enumerate(files):
    if (i + 1) % 50 == 0:
        print(f"进度: {i + 1}/{total}")
    
    file_path = os.path.join(search_dir, file)
    try:
        img = Image.open(file_path)
        file_hash = imagehash.phash(img)
        diff = target_hash - file_hash
        
        if diff <= threshold:
            matches.append((file_path, diff))
            print(f"✓ 找到! 差异={diff}: {file}")
    except:
        pass

print("-" * 50)
if matches:
    print(f"\n找到 {len(matches)} 个相似图片:")
    for path, diff in sorted(matches, key=lambda x: x[1]):
        print(f"  [{diff}] {path}")
else:
    print("没有找到相似图片")


