"""自定义 tqdm 进度条（带颜色样式）。"""

from tqdm import tqdm as tqdm_original

from ..assets.color import Color


class tqdm(tqdm_original):
    _STYLE_MAP = {
        "BLACK": Color.BLACK,
        "RED": Color.RED,
        "GREEN": Color.GREEN,
        "YELLOW": Color.YELLOW,
        "BLUE": Color.BLUE,
        "MAGENTA": Color.MAGENTA,
        "CYAN": Color.CYAN,
        "WHITE": Color.WHITE,
    }

    def __init__(self, *args, **kwargs):
        self._custom_colour = kwargs.pop("colour", "GREEN")
        kwargs.setdefault(
            "bar_format",
            f"{Color.CYAN}{{desc}}{Color.RESET} "
            f"{Color.WHITE}{{percentage:3.0f}}%{Color.RESET} "
            f"{Color.GRAY}[{{total}}/{{n_fmt}}]{Color.RESET}"
            f"{Color.WHITE}|{{bar:20}}|{Color.RESET}"
            f"{Color.BLUE}[{{elapsed}}]{Color.RESET}",
        )
        kwargs.setdefault("ncols", 80)
        kwargs.setdefault("colour", None)
        super().__init__(*args, **kwargs)
        self.colour = self._custom_colour

    @property
    def colour(self):
        return self._colour

    @colour.setter
    def colour(self, color: str):
        color = (color or "GREEN").upper()
        valid_color = self._STYLE_MAP.get(color, Color.GREEN)
        self._colour = color
        if self.desc:
            self.desc = f"{valid_color}{self.desc}{Color.RESET}"
