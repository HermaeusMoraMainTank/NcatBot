"""蜡笔风格绘制工具"""

import random
from PIL import ImageDraw


def draw_crayon_rectangle(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    width: float,
    height: float,
    base_color: tuple,
    orientation: str = "auto",
):
    if width < 1 or height < 1:
        return

    if orientation == "auto":
        orientation = "horizontal" if width > height else "vertical"

    points = []

    if orientation == "horizontal":
        num_points_vertical = max(4, int(height / 3))
        for i in range(int(width / 10) + 1):
            px = x + min(i * 10, width)
            py = y + random.uniform(-1, 1)
            points.append((px, py))
        for i in range(num_points_vertical + 1):
            px = x + width + random.uniform(0, 1.5)
            py = y + (i / num_points_vertical) * height
            points.append((px, py))
        for i in range(int(width / 10), -1, -1):
            px = x + min(i * 10, width)
            py = y + height + random.uniform(-1, 1)
            points.append((px, py))
        for i in range(num_points_vertical, -1, -1):
            px = x + random.uniform(0, 1.5)
            py = y + (i / num_points_vertical) * height
            points.append((px, py))
    else:
        num_points_horizontal = max(4, int(width / 3))
        for i in range(num_points_horizontal + 1):
            px = x + (i / num_points_horizontal) * width
            py = y + random.uniform(-1, 1)
            points.append((px, py))
        for i in range(int(height / 10) + 1):
            px = x + width + random.uniform(-1, 1)
            py = y + min(i * 10, height)
            points.append((px, py))
        for i in range(num_points_horizontal, -1, -1):
            px = x + (i / num_points_horizontal) * width
            py = y + height + random.uniform(-1, 1)
            points.append((px, py))
        for i in range(int(height / 10), -1, -1):
            px = x + random.uniform(-1, 1)
            py = y + min(i * 10, height)
            points.append((px, py))

    draw.polygon(points, fill=base_color + (200,), outline=None)

    texture_color = tuple(max(0, c - 30) for c in base_color)
    highlight_color = tuple(min(255, c + 40) for c in base_color)

    num_texture = int(max(width, height) / 4)
    x2, y2 = x + width, y + height
    for _ in range(num_texture):
        tx = x + random.uniform(0, width)
        ty = y + random.uniform(0, height)
        if random.random() < 0.5:
            draw.point((tx, ty), fill=texture_color + (180,))
        else:
            r = random.uniform(1, 2)
            draw.ellipse(
                (tx - r, ty - r, tx + r, ty + r),
                fill=texture_color + (120,),
            )

    num_highlight = int(max(width, height) / 8)
    for _ in range(num_highlight):
        hx = x + random.uniform(0, width * 0.7)
        hy = y + random.uniform(0, height * 0.5)
        if x <= hx <= x2 and y <= hy <= y2:
            draw.point((hx, hy), fill=highlight_color + (100,))
