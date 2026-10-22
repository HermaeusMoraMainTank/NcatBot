"""Tarot 特殊牌资源与文案映射测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from Tarot.Tarot import Tarot


def test_girl_of_nihility_cards_are_registered_with_images(monkeypatch):
    """TAROT-01: 两张 Girl of Nihility 特殊牌都有对应图片和独特文案。"""
    monkeypatch.chdir(ROOT)

    cards = Tarot().load_blacksouls_tarot_data()
    cards_by_image = {card.image_name: card for card in cards}

    expected = {
        "Girl_of_Nihility_1.jpg": (
            "此刻涌进我心中的那炽热的心情\n一定，和人类所拥有的感情是相同的吧"
        ),
        "Girl_of_Nihility_2.jpg": "请不要哭泣，该告别了\n………请你闭上眼？",
    }

    for image_name, message in expected.items():
        assert image_name in cards_by_image
        assert cards_by_image[image_name].positive == message
        assert (ROOT / "data" / "image" / "Tarot" / "Blacksouls" / image_name).is_file()
