from PIL import Image
import imagehash
import os

target = r'C:\Users\27342\Downloads\temp_search.jpg'
search_dir = r'C:\Users\27342\Downloads\lalafell\lalafell'

target_hash = imagehash.phash(Image.open(target))
print(f'Target hash: {target_hash}')

files = [f for f in os.listdir(search_dir) if f.lower().endswith(('.jpg','.jpeg','.png','.gif','.webp'))]
print(f'Total files: {len(files)}')

for i, f in enumerate(files):
    try:
        h = imagehash.phash(Image.open(os.path.join(search_dir, f)))
        diff = target_hash - h
        if diff <= 10:
            print(f'FOUND! diff={diff}: {f}')
    except:
        pass
    if (i+1) % 100 == 0:
        print(f'Progress: {i+1}/{len(files)}')

print('DONE')


