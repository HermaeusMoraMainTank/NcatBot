# -*- coding: utf-8 -*-
"""一次性：meme → stickers 命名整理。"""
from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "meme"
DST = ROOT / "stickers"

# (source_filename, sticker_name, meaning, scenarios)
ENTRIES = [
    ("10E5A08896ADD0C45E703D544D6C9967.png", "害羞", "捂脸害羞/慌张", "被夸、被调戏、说错话想躲"),
    ("1421B8E893E9EA3832D44594DDA66075.jpg", "夜视", "暗处发光眼蹲着盯人", "凌晨在线、默默围观、盯着稀奇发言"),
    ("169585A5BC6C320FD506F701EB85B8FE.png", "震惊", "双手捂脸极度吃惊", "离谱新闻、反转、不可思议"),
    ("215D02461AFE388AF61EB9202B8832FB.png", "疑惑", "歪头头顶??", "听不懂、话说半截、莫名其妙"),
    ("2C94F52030C328C0360560E00E7F8ED3.png", "眨眼", "单眼眨眼吐舌", "开玩笑、卖关子、小得意"),
    ("2DEF6CB8CF1B8E3F3A9F183CBBEA86CF.png", "鼻血赞", "鼻血狂喷还竖拇指", "过于好看/刺激、夸张认可"),
    ("31D81745306E7F905CDC404CC21569CD.png", "伸爪", "伸爪摸向镜头", "打招呼、亲昵、想碰一碰"),
    ("31EC8FEE38064A33BA3181CBE4F95964.png", "指你", "蹲着吐舌指人", "就是你、逮到你、开玩笑点名"),
    ("3466E44222D371A9033904CA7137477D.jpg", "捂嘴", "双手捂住嘴偷瞄", "小声惊喜、害羞偷看"),
    ("4049EE62B59B0157CBEA01D8090A4D7E.jpg", "躺平", "趴平死鱼眼", "累了、摆烂、不想说话"),
    ("46BE5066EECF6217AEDA88DC561EFA8A.jpg", "舔屏", "舌头怼镜头特写", "犯傻、亲昵闹腾"),
    ("4B3576555EF55E88246D0792E14741C3.png", "咬手", "假装生气咬人手", "被摸多了抗议、撒娇闹脾气"),
    ("53A9D0691035C21F090B9D1E49B4067D.png", "脑空", "星空背景空洞眼吐舌", "信息过载、整肃懵"),
    ("665E727B98DEA9FD35E6CAD0D6072E38.png", "偷看", "墙后探头旁有?", "插话前观望、啥情况"),
    ("732780293F3474AFD77F5B2F235BEC7D.jpg", "点赞", "闭眼微笑竖拇指", "同意、任务成功、鼓励"),
    ("73534D02C88AFB06FE8D885F76FC2ED7.png", "发射", "张嘴喷粉光束", "嗨起来、玩闹攻击"),
    ("75DA83666CD82079B73530C77E493598.png", "星星眼", "趴着星星眼摇尾", "期待、崇拜、好想要"),
    ("810059B3919EB8FCD80009BAAE94DCCA.jpg", "挣脱", "幼儿园服断链疯笑", "放飞自我、解封归来、整活"),
    ("810E1FE38B6B7787BA93721484BE1593.png", "发呆", "劈叉坐着放空", "走神、呆萌待机"),
    ("8615B30D7D4D4AADD18F6323E8C6A795.jpg", "责骂", "黄眼瞪视+粗口文案(敏感)", "吐槽色色发言；二校可删"),
    ("8A30E91695629B36183A09E3B5A0FA8E.png", "倾听", "黑底托腮大眼", "继续说、八卦模式"),
    ("8BC3FA5490AD80E50FB3DB0EAA5E0EF5.png", "开心趴", "趴地眯眼大笑", "开心见到你、好消息"),
    ("8DFCBD7B1B2BCDB61F85BDE7F09D0055.png", "戳便便", "幼儿园服拿棍戳便便", "整活/没事找事；二校可删"),
    ("8EFAAE913652A897E5D28A8A03752370.png", "炸毛", "四肢乱踢大喊", "兴奋过头、坐不住"),
    ("94B813EB2AD54B8827D4793B3AF4EE1E.png", "调皮", "近景吐舌调侃", "嘴硬、抬杠、嘿嘿"),
    ("B1CF9B611E4D81675A912FB167256669.png", "蹭饭", "捧碗吃饭「吃你家大米汪」", "真在吃饭、怼多管闲事"),
    ("C24401B6CEBF25D88AD28F7C2E25C255.jpg", "叼骨跑", "叼骨头高速奔跑", "马上办、开溜"),
    ("CB6FB9EC5DE4D9F6555D254624B6905D.png", "嘿嘿", "托腮眯眼坏笑", "小聪明得逞、被夸得意"),
    ("CC5EF0CB80E7B6E80D3C9663F5933AFE.jpg", "宕机", "坐地无神吐舌", "脑子烧了、装傻"),
    ("CED2C9AA6B2BBEA54908877769DCBAFF.jpg", "亮肉垫", "双手举爪吐舌", "卖萌亮爪"),
    ("D4B25CC62EF51293E0143FFBA99907D4.png", "嘲讽", "吐长舌嬉皮笑脸", "损你一句、打哈哈"),
    ("DCAC72CCD6EC56C0F10ACE5F8FD7A7AA.png", "得意", "半眯眼吐舌托爪", "略得意、讨打可爱"),
    ("DCEC48D5957FB2701EEE768D51433016.jpg", "睡觉", "盖被睡觉冒泡泡", "晚安、犯困"),
    ("EA3721B084FD25BA8053AAD936E7F9CC.png", "长条猫", "长条猫举爪干瞪眼", "人在、无辜存在感"),
    ("EB44D2506C79C41DC45DAF0E4D827E91.jpg", "装酷", "墨镜站石举手装逼耍酷", "耍帅登场、自我感觉良好"),
    ("F3A24AA0AFD868E8DC0FAF43F00A4AC9.png", "被摸", "被人摸下巴眯眼", "被夸舒服、求摸摸成功"),
    ("FAA8653E3754C9D9CCCA00FF1ACA85E3.jpg", "比心", "爪子比心眨眼", "比心、谢谢、示好"),
    ("9D77DB5E18083050D1A4D242003933B8.jpg", "吐舌", "黑底吐舌亮爪", "轻松闹腾"),
    (
        "1042667215_20251128_153850_A62ED6ED168A8E94F8C8C9D33404F242.jpg",
        "累瘫",
        "仰躺X眼吐舌",
        "彻底累垮、被整麻了",
    ),
    (
        "2013757427_20250821_171426_13B2478CEDA15D31B614BB221CB8CDD5.jpg",
        "打滚",
        "仰面蹬腿打滚",
        "撒娇打滚、兴奋蹦迪",
    ),
    (
        "2013757427_20251002_003011_FCA2770FDA2D97E84485AD5385232629.png",
        "不是哥们",
        "虾睡袋梗：不是哥们/整不会了",
        "离谱举动后懵圈吐槽、虾梗联动",
    ),
    (
        "273421673_20250811_164755_4703EFE2E3ACB73F5A6FA0A8A3EA487F.jpg",
        "垃圾桶",
        "人从垃圾桶探头",
        "自嘲废物、社死",
    ),
    (
        "273421673_20250811_164805_0BDAA4608F26FE2A8D492A24CFED22C6.jpg",
        "疯跑",
        "四足吐舌玩闹姿",
        "兽化发疯、纯乐子",
    ),
    (
        "273421673_20250814_170643_539ED8625555745179E33CEF319074C4.png",
        "求摸摸",
        "叼毛绒大眼求关注",
        "求求、想被夸",
    ),
    (
        "273421673_20250818_213742_EE971F5F704513C225ADA8870DE1C001.jpg",
        "撒泼",
        "仰面倒地乱蹬",
        "小脾气、又嗨又闹",
    ),
    (
        "273421673_20250821_171327_2BD40D1BD4357FFFD09F64D52DA519B9.jpg",
        "哭哭",
        "挤眼落泪捂胸",
        "委屈、求哄",
    ),
    (
        "491326161_20251128_162123_E2C85626932B15050056D45D3453F187.jpg",
        "何意味",
        "「何异味」谐音何意味：为啥这样/什么意思",
        "质疑动机、你为什么突然这样",
    ),
    (
        "995954190_20250823_215243_B371B63371120FF51E26CA89A431F5C1.jpg",
        "回头",
        "回头翘屁股挑衅",
        "故意讨打；二校可删",
    ),
    (
        "995954190_20250901_014939_B9B5F151410EBEC80CA333F4CCA20F87.png",
        "出拳",
        "朝镜头挥拳",
        "加油、碰拳",
    ),
    (
        "995954190_20250916_171337_7308B7329F828F700D965E0085C19390.png",
        "叼骨头",
        "叼骨头扑出来",
        "领赏、交作业",
    ),
    (
        "995954190_20260104_221636_E2ECC280CB2B18B1773D3BDD5751D7A4.png",
        "嘲笑",
        "蹲着指人狂笑",
        "笑话别人、抓包偷乐",
    ),
    (
        "995954190_20260130_173550_AE596E4CF642097F567BD5F161CE062E.jpg",
        "卖萌",
        "抱爪竖耳乖巧站立",
        "乖巧卖萌、求夸",
    ),
    (
        "995954190_20260413_171627_DE71EA10EA5E25FC8E98F659A6B69657.png",
        "被遗弃",
        "雨夜纸箱小猫",
        "被冷落、求陪",
    ),
    (
        "995954190_20260516_212256_5D5F95668C8FE33790C733F2B5D3793A.jpg",
        "恼火",
        "攥拳皱眉抬头",
        "气鼓鼓但仍可爱",
    ),
]

RISK = {"责骂", "戳便便", "回头", "鼻血赞"}


def main() -> None:
    names = [e[1] for e in ENTRIES]
    dups = [n for n, c in Counter(names).items() if c > 1]
    if dups:
        raise SystemExit(f"duplicate names: {dups}")

    DST.mkdir(parents=True, exist_ok=True)
    for p in list(DST.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)

    catalog = []
    missing = []
    for fname, name, meaning, scene in ENTRIES:
        sp = SRC / fname
        if not sp.exists():
            missing.append(fname)
            continue
        out = DST / f"{name}{sp.suffix.lower()}"
        shutil.copy2(sp, out)
        catalog.append((name, meaning, scene, out.name))

    lines = [
        "# FakeAi 贴纸清单（I6 · 待你二校）",
        "",
        "> 原图仍在 `data/fakeai/meme/`，已 **复制** 到 `data/fakeai/stickers/`。",
        "> 模型用法：正文插 `:贴纸名:` ，系统会另发一张图。改名/删除后热重载即可。",
        "",
        "| 贴纸名 | 含义 | 适合场景 | 备注 |",
        "| --- | --- | --- | --- |",
    ]
    for name, meaning, scene, _ in catalog:
        tip = "⚠️二校关注" if name in RISK else ""
        lines.append(f"| `:{name}:` | {meaning} | {scene} | {tip} |")

    lines += [
        "",
        "## 常用推荐",
        "",
        "害羞、疑惑、震惊、点赞、躺平、哭哭、嘿嘿、星星眼、比心、睡觉、指你、嘲讽、异味、求摸摸、卖萌、被摸、打滚、累瘫",
        "",
        f"共 **{len(catalog)}** 张。",
        "",
    ]
    (DST / "CATALOG.md").write_text("\n".join(lines), encoding="utf-8")

    thumb = ROOT / "_thumb"
    if thumb.exists():
        shutil.rmtree(thumb)

    print(f"copied={len(catalog)} missing={len(missing)}")
    if missing:
        print("MISSING:", *missing, sep="\n  ")


if __name__ == "__main__":
    main()
