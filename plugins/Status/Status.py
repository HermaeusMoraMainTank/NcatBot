import io
import os
import platform
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.types import Image as QQImage

from common.constants.HMMT import HMMT
from common.utils.CommonUtil import CommonUtil


class Status(NcatBotPlugin):
    name = "Status"
    version = "1.1.0"
    START_TIME = time.time()

    _FONT_CANDIDATES = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )

    @registrar.qq.on_group_message()
    async def handle_status(self, input: GroupMessage) -> None:
        if input.raw_message == "状态" and input.sender.user_id == HMMT.HMMT_ID:
            png = self._render_status_png()
            cache = Path(__file__).resolve().parent / "data"
            cache.mkdir(parents=True, exist_ok=True)
            out = cache / "status_card.png"
            out.write_bytes(png)
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                image=QQImage(file=str(out)),
            )

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for p in self._FONT_CANDIDATES:
            if p.is_file():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def _format_uptime(self, seconds: float) -> str:
        s = int(seconds)
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        m, s = divmod(s, 60)
        parts = []
        if d:
            parts.append(f"{d}天")
        if h or d:
            parts.append(f"{h}时")
        parts.append(f"{m}分{s}秒")
        return "".join(parts)

    def _human_bytes(self, n: float) -> str:
        for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
            if n >= div or unit == "KB":
                return f"{n / div:.2f}{unit}"
        return f"{n:.0f}B"

    def _collect_metrics(self) -> dict:
        cpu_count_logical = psutil.cpu_count() or 1
        cpu_count_phys = psutil.cpu_count(logical=False)
        cpu_per = psutil.cpu_percent(interval=1, percpu=True)
        cpu_avg = sum(cpu_per) / max(len(cpu_per), 1)
        cpu_max = max(cpu_per) if cpu_per else 0.0

        freq_line = "—"
        try:
            cf = psutil.cpu_freq()
            if cf and cf.current:
                mx = cf.max or cf.current
                freq_line = f"{cf.current:.0f} / {mx:.0f} MHz"
        except (AttributeError, OSError, RuntimeError):
            pass

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        proc = psutil.Process(os.getpid())
        with proc.oneshot():
            mi = proc.memory_info()
            rss = mi.rss
            threads = proc.num_threads()

        disks = []
        for part in psutil.disk_partitions():
            try:
                u = psutil.disk_usage(part.mountpoint)
                disks.append(
                    {
                        "mp": part.mountpoint,
                        "used": u.used,
                        "total": u.total,
                        "pct": u.percent,
                    }
                )
            except PermissionError:
                continue
        disks.sort(key=lambda x: x["pct"], reverse=True)
        disks = disks[:6]

        net = psutil.net_io_counters()
        boot = datetime.fromtimestamp(psutil.boot_time())
        sys_uptime = (datetime.now() - boot).total_seconds()
        bot_uptime = time.time() - self.START_TIME

        load = None
        if hasattr(os, "getloadavg"):
            try:
                load = os.getloadavg()
            except OSError:
                pass

        plat = f"{platform.system()} {platform.release()}"
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        return {
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hostname": socket.gethostname()[:32],
            "platform": plat[:40],
            "python": py_ver,
            "boot": boot.strftime("%Y-%m-%d %H:%M"),
            "sys_uptime": self._format_uptime(sys_uptime),
            "bot_uptime": self._format_uptime(bot_uptime),
            "cpu_logical": cpu_count_logical,
            "cpu_physical": cpu_count_phys,
            "cpu_avg": cpu_avg,
            "cpu_max": cpu_max,
            "cpu_per": cpu_per,
            "freq_line": freq_line,
            "mem_total": mem.total,
            "mem_used": mem.used,
            "mem_pct": mem.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_pct": swap.percent,
            "rss": rss,
            "threads": threads,
            "disks": disks,
            "net_sent": net.bytes_sent,
            "net_recv": net.bytes_recv,
            "net_ps": net.packets_sent,
            "net_pr": net.packets_recv,
            "proc_count": len(psutil.pids()),
            "load": load,
        }

    def _bar_color(self, pct: float) -> tuple[int, int, int]:
        if pct >= 90:
            return (244, 67, 54)
        if pct >= 75:
            return (255, 152, 0)
        return (76, 175, 80)

    def _draw_panel(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        *,
        radius: int = 24,
        fill: tuple[int, int, int] = (255, 255, 255),
        border: tuple[int, int, int] = (233, 236, 242),
    ) -> None:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=2)

    def _draw_ring(
        self,
        draw: ImageDraw.ImageDraw,
        center: tuple[int, int],
        radius: int,
        pct: float,
        *,
        color: tuple[int, int, int],
        bg: tuple[int, int, int] = (236, 239, 245),
        width: int = 18,
    ) -> None:
        x, y = center
        box = (x - radius, y - radius, x + radius, y + radius)
        draw.arc(box, start=0, end=359, fill=bg, width=width)
        end = int(360 * max(0.0, min(100.0, pct)) / 100.0)
        if end > 0:
            draw.arc(box, start=-90, end=-90 + end, fill=color, width=width)

    def _draw_anime_decor(
        self, draw: ImageDraw.ImageDraw, w: int, h: int, font: ImageFont.ImageFont
    ) -> None:
        for r in range(260, 20, -24):
            c = (255, 236 + (260 - r) // 12, 246)
            draw.ellipse((w - 520 - r, -160 - r, w - 520 + r, -160 + r), fill=c)

        # simple anime-like silhouette on right
        bx, by = w - 250, h - 310
        draw.ellipse((bx - 120, by - 220, bx + 20, by - 80), fill=(231, 236, 248))
        draw.polygon(
            [(bx - 35, by - 72), (bx + 92, by + 180), (bx - 178, by + 180)],
            fill=(233, 238, 250),
        )
        draw.ellipse((bx - 82, by - 170, bx - 36, by - 126), fill=(246, 250, 255))

        petals = [
            (150, 105, 18),
            (208, 168, 14),
            (w - 270, 115, 16),
            (w - 178, 214, 12),
            (w - 358, h - 165, 15),
        ]
        for x, y, s in petals:
            pink = (255, 170, 208)
            draw.ellipse((x - s, y - s, x + s, y + s), fill=pink)
            draw.ellipse((x - s - 6, y, x + s - 6, y + s * 2), fill=pink)
            draw.ellipse((x + 2, y - s - 6, x + s * 2, y + s - 6), fill=pink)

        quote = "「 凡是过往，皆为序章 」"
        qw = draw.textbbox((0, 0), quote, font=font)[2]
        draw.text(((w - qw) // 2, h - 50), quote, font=font, fill=(177, 128, 176))

    def _load_avatar_image(self, user_id: int, size: int = 70) -> PILImage.Image:
        """加载并裁剪圆形头像，失败时回退纯色占位。"""
        avatar = PILImage.new("RGB", (size, size), (224, 233, 252))
        try:
            avatar_path = CommonUtil.get_avatar(str(user_id))
            if avatar_path and os.path.exists(avatar_path):
                avatar = PILImage.open(avatar_path).convert("RGB").resize((size, size))
        except Exception:
            pass

        mask = PILImage.new("L", (size, size), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.ellipse((0, 0, size, size), fill=255)
        out = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(avatar, (0, 0), mask)
        return out

    def _render_status_png(self) -> bytes:
        m = self._collect_metrics()
        W, H = 1500, 860
        canvas = PILImage.new("RGB", (W, H), (248, 250, 255))
        draw = ImageDraw.Draw(canvas)

        font_title = self._load_font(36)
        font_h2 = self._load_font(28)
        font_h3 = self._load_font(24)
        font_text = self._load_font(20)
        font_small = self._load_font(16)

        def text_w(s: str, font: ImageFont.ImageFont) -> int:
            return int(draw.textbbox((0, 0), s, font=font)[2])

        for y in range(H):
            t = y / max(H - 1, 1)
            draw.line(
                [(0, y), (W, y)],
                fill=(
                    int(248 + (241 - 248) * t),
                    int(250 + (246 - 250) * t),
                    int(255 + (252 - 255) * t),
                ),
            )

        # subtle anime watermark
        draw.ellipse((W - 410, 100, W - 90, 420), fill=(236, 241, 252))
        draw.polygon(
            [(W - 220, 220), (W - 130, 450), (W - 310, 450)],
            fill=(240, 244, 253),
        )

        margin = 24
        gap = 16
        left_w = int((W - margin * 2 - gap) * 0.31)
        right_w = W - margin * 2 - gap - left_w
        top_h = 470
        H - margin * 2 - top_h - gap

        left_top = (margin, margin, margin + left_w, margin + top_h)
        right_top = (
            left_top[2] + gap,
            margin,
            left_top[2] + gap + right_w,
            margin + top_h,
        )
        left_bottom = (margin, left_top[3] + gap, margin + left_w, H - margin)
        right_bottom = (right_top[0], right_top[3] + gap, W - margin, H - margin)

        self._draw_panel(draw, left_top, fill=(255, 255, 255), border=(231, 236, 245))
        self._draw_panel(draw, right_top, fill=(255, 255, 255), border=(231, 236, 245))
        self._draw_panel(
            draw, left_bottom, radius=18, fill=(255, 255, 255), border=(231, 236, 245)
        )
        self._draw_panel(
            draw, right_bottom, radius=18, fill=(255, 255, 255), border=(231, 236, 245)
        )

        # left: profile + system rows
        x0, y0, x1, _ = left_top
        avatar_box = (x0 + 20, y0 + 20, x0 + 90, y0 + 90)
        draw.ellipse(avatar_box, fill=(224, 233, 252), outline=(208, 222, 248), width=2)
        avatar_img = self._load_avatar_image(HMMT.HMMT_ID, size=66)
        canvas.paste(avatar_img, (x0 + 22, y0 + 22), avatar_img)
        draw.text((x0 + 108, y0 + 26), "蓝晴", font=font_title, fill=(45, 52, 65))
        draw.text(
            (x0 + 110, y0 + 72),
            f"{HMMT.HMMT_ID}",
            font=font_small,
            fill=(120, 130, 150),
        )
        draw.line(
            [(x0 + 20, y0 + 114), (x1 - 20, y0 + 114)], fill=(236, 239, 245), width=2
        )

        draw.text((x0 + 24, y0 + 132), "系统信息", font=font_h3, fill=(58, 66, 84))
        info_rows = [
            ("系统版本", m["platform"]),
            ("Python", m["python"]),
            ("系统启动", m["boot"]),
            ("系统运行", m["sys_uptime"]),
            ("进程运行", m["bot_uptime"]),
        ]
        row_y = y0 + 182
        label_x = x0 + 28
        value_x = x0 + 170
        for k, v in info_rows:
            draw.text((label_x, row_y), k, font=font_text, fill=(103, 113, 132))
            draw.text((value_x, row_y), v, font=font_text, fill=(51, 58, 72))
            row_y += 52

        # right top: cpu + mem + rings aligned
        x0, y0, x1, _ = right_top
        draw.text((x0 + 24, y0 + 24), "CPU", font=font_h2, fill=(58, 66, 84))
        cpu_rows = [
            ("内核数", f"{m['cpu_logical']}"),
            ("主频", m["freq_line"]),
            ("使用率", f"{m['cpu_avg']:.2f}%"),
            ("进程数", str(m["proc_count"])),
        ]
        row_y = y0 + 78
        label_x = x0 + 28
        value_x = x0 + 200
        for k, v in cpu_rows:
            draw.text((label_x, row_y), k, font=font_text, fill=(103, 113, 132))
            draw.text((value_x, row_y), v, font=font_text, fill=(51, 58, 72))
            row_y += 46

        draw.text((x0 + 24, y0 + 258), "内存", font=font_h2, fill=(58, 66, 84))
        mem_rows = [
            ("总量", self._human_bytes(m["mem_total"])),
            ("使用量", self._human_bytes(m["mem_used"])),
            ("主进程", self._human_bytes(m["rss"])),
        ]
        row_y = y0 + 312
        for k, v in mem_rows:
            draw.text((label_x, row_y), k, font=font_text, fill=(103, 113, 132))
            draw.text((value_x, row_y), v, font=font_text, fill=(51, 58, 72))
            row_y += 46

        ring_x = x1 - 140
        self._draw_ring(
            draw, (ring_x, y0 + 150), 78, m["cpu_avg"], color=(233, 112, 156)
        )
        self._draw_ring(
            draw, (ring_x, y0 + 356), 78, m["mem_pct"], color=(208, 88, 238)
        )
        cpu_pct = f"{m['cpu_avg']:.0f}%"
        mem_pct = f"{m['mem_pct']:.0f}%"
        draw.text(
            (ring_x - text_w("CPU占用", font_small) // 2, y0 + 126),
            "CPU占用",
            font=font_small,
            fill=(122, 132, 150),
        )
        draw.text(
            (ring_x - text_w(cpu_pct, font_h3) // 2, y0 + 152),
            cpu_pct,
            font=font_h3,
            fill=(35, 42, 55),
        )
        draw.text(
            (ring_x - text_w("内存占用", font_small) // 2, y0 + 332),
            "内存占用",
            font=font_small,
            fill=(122, 132, 150),
        )
        draw.text(
            (ring_x - text_w(mem_pct, font_h3) // 2, y0 + 358),
            mem_pct,
            font=font_h3,
            fill=(35, 42, 55),
        )

        # bottom left
        x0, y0, _, _ = left_bottom
        draw.text((x0 + 24, y0 + 22), "网络配置", font=font_h3, fill=(58, 66, 84))
        draw.text(
            (x0 + 28, y0 + 80),
            f"发送: {self._human_bytes(m['net_sent'])}",
            font=font_text,
            fill=(51, 58, 72),
        )
        draw.text(
            (x0 + 28, y0 + 124),
            f"接收: {self._human_bytes(m['net_recv'])}",
            font=font_text,
            fill=(51, 58, 72),
        )
        draw.text(
            (x0 + 28, y0 + 168),
            f"上行包: {m['net_ps']}",
            font=font_text,
            fill=(51, 58, 72),
        )
        draw.text(
            (x0 + 28, y0 + 212),
            f"下行包: {m['net_pr']}",
            font=font_text,
            fill=(51, 58, 72),
        )

        # bottom right
        x0, y0, x1, y1 = right_bottom
        draw.text((x0 + 24, y0 + 22), "磁盘与负载", font=font_h3, fill=(58, 66, 84))
        yy = y0 + 70
        for d in m["disks"][:3]:
            label = d["mp"] if len(d["mp"]) <= 18 else "…" + d["mp"][-17:]
            right_text = f"{self._human_bytes(d['used'])}/{self._human_bytes(d['total'])}  {d['pct']:.0f}%"
            draw.text((x0 + 24, yy), label, font=font_text, fill=(88, 98, 118))
            draw.text(
                (x1 - 24 - text_w(right_text, font_text), yy),
                right_text,
                font=font_text,
                fill=(51, 58, 72),
            )
            by = yy + 34
            draw.rounded_rectangle(
                (x0 + 24, by, x1 - 24, by + 10), radius=5, fill=(236, 239, 245)
            )
            fill_w = int((x1 - 48 - x0) * min(100.0, d["pct"]) / 100.0)
            draw.rounded_rectangle(
                (x0 + 24, by, x0 + 24 + fill_w, by + 10),
                radius=5,
                fill=self._bar_color(d["pct"]),
            )
            yy += 62

        if m["load"]:
            load_text = (
                f"Load: {m['load'][0]:.2f} / {m['load'][1]:.2f} / {m['load'][2]:.2f}"
            )
            draw.text(
                (x0 + 24, y1 - 72), load_text, font=font_text, fill=(92, 102, 122)
            )
        ts = m["now"]
        draw.text(
            (x1 - 24 - text_w(ts, font_small), y1 - 38),
            ts,
            font=font_small,
            fill=(126, 136, 156),
        )

        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
