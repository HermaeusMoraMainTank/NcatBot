"""媒体能力：TTS、文字图、图库发图。"""

from __future__ import annotations

import asyncio
import random
import re
import shutil
import sys
import time
from pathlib import Path

from ncatbot.skill import SkillContext, SkillResult, skill
from ncatbot.types import Image

from .messages import qq_api, resolve_target

_FONT_CANDIDATES = (
    Path("data/font/可爱字体.ttf"),
    Path("data/font/荆南麦圆体.otf"),
    Path("data/font/FZMiaoWuK.TTF"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)

_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _color(value: str, fallback: str):
    from PIL import ImageColor

    raw = value if _HEX.match(value or "") else fallback
    if not raw.startswith("#"):
        raw = "#" + raw
    try:
        return ImageColor.getrgb(raw)
    except ValueError:
        return ImageColor.getrgb(fallback)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text).splitlines() or [""]:
        if not raw_line:
            lines.append("")
            continue
        current = ""
        for char in raw_line:
            candidate = current + char
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = char
        if current:
            lines.append(current)
    return lines or [""]


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


async def _local_tts(text: str, workspace) -> bytes | None:
    """本机 TTS 兜底：Windows SAPI / Linux espeak / macOS say。

    只把文本写进工作区文件，命令行里不出现用户输入，避免注入。
    """
    if workspace is None:
        return None
    try:
        source = workspace.ensure_parent("tts-input.txt")
        source.write_text(str(text), encoding="utf-8")
        output = workspace.ensure_parent("tts-local.wav")
        if output.exists():
            output.unlink()
    except Exception:
        return None

    argv: list[str] | None = None
    if sys.platform == "win32":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            return None
        script = (
            "Add-Type -AssemblyName System.Speech; "
            f"$text = Get-Content -Raw -Encoding UTF8 {_ps_quote(str(source))}; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SetOutputToWaveFile({_ps_quote(str(output))}); "
            "$s.Speak($text); $s.Dispose()"
        )
        argv = [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
    elif sys.platform == "darwin":
        say = shutil.which("say")
        if say:
            argv = [
                say,
                "-f",
                str(source),
                "-o",
                str(output),
                "--data-format=LEI16@22050",
            ]
    else:
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if binary:
            argv = [binary, "-f", str(source), "-w", str(output)]

    if argv is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=45)
    except Exception:
        return None
    finally:
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass
    if not output.is_file() or output.stat().st_size < 1024:
        return None
    try:
        return output.read_bytes()
    except OSError:
        return None


class MediaSkills:
    @skill(
        "tts.speak",
        summary="把一段文字转成语音（可顺便发到当前会话）",
        params=[
            {"name": "text", "type": "string", "description": "要读的文字", "required": True},
            {"name": "send", "type": "boolean", "description": "是否直接发送，默认 true"},
            {"name": "voice", "type": "string", "description": "音色，默认 mimo_default"},
        ],
        tags=["media", "voice", "write"],
        side_effects="message",
        requires=("event", "media", "network"),
        timeout=90,
    )
    async def skill_tts_speak(
        self, ctx: SkillContext, text: str, send: bool = True, voice: str = "mimo_default"
    ) -> SkillResult:
        from common.utils.AiUtil import AiUtil

        text = (text or "").strip()
        if not text:
            return SkillResult.fail("文本为空")
        if len(text) > 300:
            return SkillResult.fail("语音文本过长（最多 300 字）")
        audio = await AiUtil.synthesize_mimo_tts(text, voice=voice or "mimo_default")
        source = "mimo"
        if not audio:
            audio = await _local_tts(text, ctx.workspace)
            source = "local"
        if not audio:
            return SkillResult.fail(
                "TTS 不可用：MiMo Key 无效，且本机没有可用的语音合成后端"
            )
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")
        filename = f"voice-{int(time.time())}.wav"
        path = ctx.workspace.write_bytes(filename, audio)
        relative = str(path.relative_to(ctx.workspace.root)).replace("\\", "/")
        artifact = {
            "kind": "voice",
            "name": filename,
            "path": relative,
            "abs_path": str(path),
            "size": len(audio),
            "mime": "audio/wav",
        }
        if not send:
            return SkillResult(
                ok=True,
                content=f"语音已生成（{source}）：{filename}",
                artifacts=[artifact],
            )
        api = qq_api(ctx)
        kind, target = resolve_target(ctx)
        if api is None or not kind:
            return SkillResult(
                ok=True,
                content=f"语音已生成（{source}）：{filename}",
                artifacts=[artifact],
            )
        try:
            if kind == "group":
                await api.send_group_record(target, str(path))
            else:
                await api.send_private_record(target, str(path))
        except Exception as exc:
            return SkillResult.fail(f"语音发送失败：{type(exc).__name__}: {exc}", data=artifact)
        return SkillResult(ok=True, content=f"语音已发送（{source}）", artifacts=[artifact])

    @skill(
        "image.text",
        summary="生成一张带文字的图片",
        description="在工作区生成 PNG；发送层会自动把它发到群里。",
        params=[
            {"name": "text", "type": "string", "description": "图片上的文字", "required": True},
            {"name": "width", "type": "integer", "description": "宽度，默认 640"},
            {"name": "height", "type": "integer", "description": "高度，默认 420"},
            {"name": "bg", "type": "string", "description": "背景色 #RRGGBB，默认 #1b1a17"},
            {"name": "color", "type": "string", "description": "文字色 #RRGGBB，默认 #ffffff"},
            {"name": "font_size", "type": "integer", "description": "字号，默认 84"},
        ],
        tags=["media", "image", "write"],
        side_effects="write",
        requires=("fs.write", "media"),
        timeout=30,
    )
    async def skill_image_text(
        self,
        ctx: SkillContext,
        text: str,
        width: int = 640,
        height: int = 420,
        bg: str = "#1b1a17",
        color: str = "#ffffff",
        font_size: int = 84,
    ) -> SkillResult:
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")
        text = str(text or "").strip()
        if not text:
            return SkillResult.fail("文字为空")
        width = max(120, min(2000, int(width)))
        height = max(80, min(2000, int(height)))
        font_size = max(12, min(300, int(font_size)))
        try:
            from PIL import Image as PILImage, ImageDraw

            image = PILImage.new("RGB", (width, height), _color(bg, "#1b1a17"))
            draw = ImageDraw.Draw(image)
            font = _font(font_size)
            lines = _wrap(draw, text, font, width - 40)
            line_height = int(font_size * 1.25)
            total = line_height * len(lines)
            y = max(20, (height - total) // 2)
            fill = _color(color, "#ffffff")
            for line in lines:
                text_width = draw.textlength(line, font=font)
                draw.text(((width - text_width) / 2, y), line, font=font, fill=fill)
                y += line_height
            filename = f"text-{int(time.time())}.png"
            path = ctx.workspace.ensure_parent(filename)
            image.save(path, format="PNG")
        except Exception as exc:
            return SkillResult.fail(f"生成图片失败：{type(exc).__name__}: {exc}")
        return SkillResult(
            ok=True,
            content=f"图片已生成：{filename}",
            artifacts=[
                {
                    "kind": "image",
                    "name": filename,
                    "path": filename,
                    "abs_path": str(path),
                    "mime": "image/png",
                }
            ],
        )

    @skill(
        "image.package.list",
        summary="列出图库分组（名称、触发词、图片数）；发图前先用它确认图库名",
        params=[
            {"name": "query", "type": "string", "description": "只看名字/触发词包含该词的分组"},
        ],
        tags=["media", "image", "read"],
        side_effects="read",
    )
    async def skill_package_list(self, ctx: SkillContext, query: str = "") -> SkillResult:
        plugin = self.get_plugin("ImageSender") if hasattr(self, "get_plugin") else None
        if plugin is None:
            return SkillResult.fail("ImageSender 插件未加载")
        needle = str(query or "").strip().lower()
        rows = []
        for name, config in getattr(plugin, "commands", {}).items():
            triggers = list(config.get("triggers") or [])[:5]
            if needle and needle not in name.lower() and not any(
                needle in str(t).lower() for t in triggers
            ):
                continue
            files = plugin.get_image_files(config.get("path", ""))
            rows.append(
                {
                    "name": name,
                    "triggers": triggers,
                    "count": len(files),
                    "recall_time": config.get("recall_time"),
                }
            )
        rows.sort(key=lambda r: r["name"])
        lines = [f"{r['name']}（{r['count']} 张）触发：{'、'.join(r['triggers'])}" for r in rows]
        return SkillResult(ok=True, content="\n".join(lines) or "图库为空", data=rows)

    @skill(
        "image.package.send",
        summary="从图库抽图直接发出；tag 就是图库名（llm、doro、alice…），count 张数",
        params=[
            {"name": "tag", "type": "string", "description": "图库名（见 image.package.list）", "required": True},
            {"name": "count", "type": "integer", "description": "张数，默认 1，最多 9"},
        ],
        tags=["media", "image", "message", "write"],
        side_effects="message",
        requires=("event", "media"),
        timeout=60,
    )
    async def skill_package_send(
        self, ctx: SkillContext, tag: str, count: int = 1
    ) -> SkillResult:
        plugin = self.get_plugin("ImageSender") if hasattr(self, "get_plugin") else None
        if plugin is None:
            return SkillResult.fail("ImageSender 插件未加载")
        api = qq_api(ctx)
        kind, target = resolve_target(ctx)
        if api is None or not kind:
            return SkillResult.fail("当前没有可发送的会话")

        commands = getattr(plugin, "commands", {}) or {}
        key = str(tag or "").strip()
        config = None
        name = key
        for command, candidate in commands.items():
            triggers = candidate.get("triggers") or []
            if key == command or key in triggers:
                config, name = candidate, command
                break
        if config is None:
            lowered = key.lower()
            for command, candidate in commands.items():
                haystack = f"{command} {' '.join(candidate.get('triggers') or [])}".lower()
                if lowered and lowered in haystack:
                    config, name = candidate, command
                    break
        if config is None:
            names = "、".join(list(commands)[:20])
            return SkillResult.fail(f"没有图库分组：{key}。可用：{names}")

        allowed = config.get("allowed_users") or []
        if allowed:
            uid = str(ctx.user_id or "")
            if uid not in {str(item) for item in allowed}:
                return SkillResult.fail(f"图库 {name} 只对指定用户开放")

        files = plugin.get_image_files(config.get("path", ""))
        if not files:
            return SkillResult.fail(f"图库 {name} 里没有图片")
        count = max(1, min(9, int(count)))
        selected = random.sample(files, min(count, len(files)))
        message_ids = []
        for file in selected:
            try:
                image = Image(file=plugin._to_napcat_image(file))
                if kind == "group":
                    result = await api.post_group_msg(target, image=image)
                else:
                    result = await api.post_private_msg(target, image=image)
            except Exception as exc:
                return SkillResult.fail(f"发送失败：{type(exc).__name__}: {exc}")
            message_id = getattr(result, "message_id", None)
            if message_id:
                message_ids.append(str(message_id))
            recall_time = int(config.get("recall_time") or 0)
            if recall_time and message_id:
                asyncio.create_task(plugin.recall_message(message_id, recall_time))
        return SkillResult(
            ok=True,
            content=f"已从「{name}」发送 {len(selected)} 张图",
            data={"package": name, "message_ids": message_ids},
        )
