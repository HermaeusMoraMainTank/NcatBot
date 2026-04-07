# 终端颜色配置


class Color:
    """
    用于在终端中显示颜色和样式。

    包含以下功能:
    - 前景: 设置颜色
    - 背景: 设置背景颜色
    - 样式: 设置样式（如加粗、下划线、反转）
    - RESET: 重置所有颜色和样式
    - from_rgb: 从 RGB 代码创建颜色
    """

    _COLOR = True

    def __getattribute__(self, name):
        if self._COLOR:
            return super().__getattribute__(name)
        else:
            return ""

    # 前景颜色
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GRAY = "\033[90m"

    # 背景颜色
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"
    BG_GRAY = "\033[100m"

    # 样式
    RESET = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    REVERSE = "\033[7m"
    ITALIC = "\033[3m"
    BLINK = "\033[5m"
    STRIKE = "\033[9m"

    @classmethod
    def from_rgb(cls, r, g, b, background=False):
        if not cls._COLOR:
            return ""
        if background:
            return f"\033[48;2;{r};{g};{b}m"
        else:
            return f"\033[38;2;{r};{g};{b}m"

    @classmethod
    def rgb(cls, r, g, b):
        return cls.from_rgb(r, g, b, background=False)

    @classmethod
    def bg_rgb(cls, r, g, b):
        return cls.from_rgb(r, g, b, background=True)

    @classmethod
    def color256(cls, color_code, background=False):
        if not cls._COLOR:
            return ""
        if background:
            return f"\033[48;5;{color_code}m"
        else:
            return f"\033[38;5;{color_code}m"

    @classmethod
    def rgb256(cls, r, g, b, background=False):
        if not cls._COLOR:
            return ""

        def rgb_to_256(r, g, b):
            if r == g == b:
                if r < 8:
                    return 16
                if r > 248:
                    return 231
                return round((r - 8) / 247 * 24) + 232
            return (
                16
                + (36 * round(r / 255 * 5))
                + (6 * round(g / 255 * 5))
                + round(b / 255 * 5)
            )

        color_code = rgb_to_256(r, g, b)
        return cls.color256(color_code, background)


__all__ = ["Color"]
