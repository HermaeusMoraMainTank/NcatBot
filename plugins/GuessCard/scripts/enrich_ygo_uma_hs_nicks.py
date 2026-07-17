"""为 ygo / uma / hs 写入常见外号 nicknames.json。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "resources"


def _merge(path: Path, by_name: dict[str, list[str]]) -> None:
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = dict(old.get("by_name") or {})
    for k, v in by_name.items():
        cur = list(merged.get(k) or [])
        for item in v:
            item = str(item).strip()
            if item and item not in cur:
                cur.append(item)
        merged[k] = cur
    out = {"by_id": old.get("by_id") or {}, "by_name": merged}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wrote {path} keys={len(merged)}", flush=True)


def _bidirectional(n: dict[str, list[str]]) -> dict[str, list[str]]:
    n = {k: [x for x in v if x and str(x).strip()] for k, v in n.items() if k}
    extra: dict[str, list[str]] = {}
    for cn, aliases in list(n.items()):
        for a in aliases:
            a = str(a).strip()
            if not a:
                continue
            bucket = extra.setdefault(a, [])
            if cn not in bucket:
                bucket.append(cn)
    n.update(extra)
    return n


def enrich_ygo() -> None:
    n = {
        "青眼白龙": ["蓝眼白龙", "青眼", "蓝眼", "Blue-Eyes White Dragon", "BEWD", "白龙"],
        "黑魔术师": ["黑魔导", "黑魔導", "Dark Magician", "黑麦"],
        "黑魔术女孩": ["黑魔导女孩", "魔导女孩", "Dark Magician Girl", "DMG"],
        "真红眼黑龙": ["红眼黑龙", "红眼", "真红眼", "Red-Eyes Black Dragon", "REBD"],
        "被封印的艾克佐迪亚": ["艾克佐迪亚", "Exodia", "埃克佐迪亚", "Exodia the Forbidden One"],
        "欧西里斯的天空龙": ["天空龙", "欧西里斯", "奥西里斯", "Slifer the Sky Dragon"],
        "欧贝利斯克的巨神兵": ["巨神兵", "欧贝利斯克", "Obelisk the Tormentor", "Obelisk"],
        "太阳神的翼神龙": ["翼神龙", "拉神", "The Winged Dragon of Ra", "Ra"],
        "青眼究极龙": ["蓝眼究极龙", "究极龙", "Blue-Eyes Ultimate Dragon"],
        "青眼亚白龙": ["亚白龙", "Blue-Eyes Alternative White Dragon"],
        "电子龙": ["电龙", "Cyber Dragon"],
        "星尘龙": ["Stardust Dragon", "星尘"],
        "黑玫瑰龙": ["Black Rose Dragon"],
        "红莲魔龙": ["红莲", "Red Dragon Archfiend"],
        "访问码语者": ["访问码", "Accesscode Talker"],
        "防火墙龙": ["Firewall Dragon"],
        "万物创世龙": ["Ten Thousand Dragon", "万创"],
        "灰流丽": ["灰流", "Ash Blossom & Joyous Spring", "Ash", "灰"],
        "效果遮蒙者": ["毛衣", "Effect Veiler", "遮蒙"],
        "增殖的G": ["增G", "Maxx C", 'Maxx "C"', "G"],
        "浮幽樱": ["浮游樱", "Ghost Ogre & Snow Rabbit", "幽樱"],
        "幽鬼兔": ["幽鬼", "Ghost Belle & Haunted Mansion"],
        "屋敷童": ["屋敷", "Droll & Lock Bird", "droll"],
        "抹杀之指名者": ["指名者", "Called by the Grave", "墓地指名"],
        "无限泡影": ["泡影", "Infinite Impermanence", "imperm"],
        "神之宣告": ["神宣", "Solemn Judgment"],
        "神之通告": ["通告", "Solemn Strike"],
        "强制脱出装置": ["船骨", "Compulsory Evacuation Device"],
        "死者苏生": ["复活", "Monster Reborn"],
        "黑洞": ["Dark Hole"],
        "融合": ["Polymerization"],
        "原始生命态尼比鲁": ["尼比鲁", "Nibiru", "陨石"],
        "混沌战士 －开辟的使者－": ["混沌战士", "开辟", "Black Luster Soldier"],
        "破坏剑士的守护者－破坏之剑士": ["破坏剑士", "Buster Blader"],
        "英雄挑战者 火花人": ["火花人"],
        "元素英雄 新生侠": ["新生侠", "Neos"],
    }
    _merge(ROOT / "ygo" / "nicknames.json", _bidirectional(n))


def enrich_uma() -> None:
    n = {
        "特别周": ["Special Week", "スペシャルウィーク", "特雷神", "周妈", "スペちゃん"],
        "无声铃鹿": ["Silence Suzuka", "サイレンススズカ", "铃鹿", "逃马", "スズカ"],
        "东海帝王": ["Tokai Teio", "トウカイテイオー", "帝王", "大腿", "テイオー"],
        "丸善斯基": ["Maruzensky", "マルゼンスキー", "丸姐"],
        "富士奇迹": ["Fuji Kiseki", "フジキセキ", "富士"],
        "小栗帽": ["Oguri Cap", "オグリキャップ", "小栗", "白毛", "オグリ"],
        "黄金船": ["Gold Ship", "ゴールドシップ", "黄船", "船妈", "泥头车", "ゴルシ"],
        "伏特加": ["Vodka", "ウオッカ"],
        "大和赤骥": ["Daiwa Scarlet", "ダイワスカーレット", "赤骥", "大和"],
        "大树快车": ["Taiki Shuttle", "タイキシャトル", "大树"],
        "草上飞": ["Grass Wonder", "グラスワンダー", "草飞"],
        "菱亚马逊": ["Hishi Amazon", "ヒシアマゾン", "亚马逊"],
        "目白麦昆": ["Mejiro McQueen", "メジロマックイーン", "麦昆", "大小姐", "マックイーン"],
        "神鹰": ["El Condor Pasa", "エルコンドルパサー", "神雕", "コンドル"],
        "好歌剧": ["T.M. Opera O", "テイエムオペラオー", "歌剧", "天子", "オペラオー"],
        "成田白仁": ["Narita Brian", "ナリブリアン", "白仁"],
        "鲁道夫象征": ["Symboli Rudolf", "シンボリルドルフ", "鲁道夫", "皇帝"],
        "气槽": ["Air Groove", "エアグルーヴ", "女帝"],
        "爱丽数码": ["Agnes Digital", "アグネスデジタル", "数码"],
        "青云天空": ["Seiun Sky", "セイウンスカイ", "青云"],
        "玉藻十字": ["Tamamo Cross", "タマモクロス", "玉藻"],
        "美妙姿势": ["Fine Motion", "ファインモーション"],
        "琵琶晨光": ["Biwa Hayahide", "ビワハヤヒデ", "琵琶"],
        "摩耶重炮": ["Mayano Top Gun", "マヤノトップガン", "重炮", "マヤ"],
        "曼城茶座": ["Manhattan Cafe", "マンハッタンカフェ", "茶座", "咖啡"],
        "美浦波旁": ["Mihono Bourbon", "ミホノブルボン", "波旁", "机器人"],
        "目白赖恩": ["Mejiro Ryan", "メジロライアン", "赖恩"],
        "米浴": ["Rice Shower", "ライスシャワー", "米饭", "乌鸦", "ライス"],
        "爱丽速子": ["Agnes Tachyon", "アグネスタキオン", "速子", "博士", "タキオン"],
        "春乌菈菈": ["Haru Urara", "ハルウララ", "乌拉拉", "うらら", "春丽乌拉拉", "一着都没有"],
        "春丽乌拉拉": ["Haru Urara", "ハルウララ", "乌拉拉", "うらら", "春乌菈菈", "一着都没有"],
        "乌拉拉": ["Haru Urara", "ハルウララ", "春乌菈菈", "春丽乌拉拉"],
        "优秀素质": ["Nice Nature", "ナイスネイチャ", "素质", "三着"],
        "北部玄驹": ["Kitasan Black", "キタサンブラック", "北黑", "玄驹", "キタサン"],
        "里见光钻": ["Satono Diamond", "サトノダイヤモンド", "光钻", "ダイヤ"],
        "荣进闪耀": ["Sakura Bakushin O", "サクラバクシンオー", "爆进", "バクシン"],
        "双涡轮": ["Twin Turbo", "ツインターボ", "涡轮", "ターボ"],
        "大拓太阳神": ["Daitaku Helios", "ダイタクヘリオス", "太阳神", "ヘリオス"],
        "待兼福来": ["Matikanefukukitaru", "マチカネフクキタル", "福来"],
        "目白多伯": ["Mejiro Dober", "メジロドーベル", "多伯"],
        "帝王光环": ["King Halo", "キングヘイロー", "光辉"],
        "胜利奖券": ["Winning Ticket", "ウイニングチケット", "奖券"],
        "空中神宫": ["Air Shakur", "エアシャカール", "神宫"],
        "成田大进": ["Narita Taishin", "ナリタタイシン", "大进"],
        "谷野流星": ["Tanino Gimlet", "タニノギムレット", "流星"],
        "樱花千代王": ["Sakura Chiyono O", "サクラチヨノオー"],
        "美丽周日": ["Marvelous Sunday", "マーベラスサンデー", "サンデー"],
        "聪慧佳人": ["Smart Falcon", "スマートファルコン", "沙聪"],
        "奇锐骏": ["Wonder Acute", "ワンダーアキュート"],
        "黄金城市": ["Gold City", "ゴールドシチー"],
        "爱慕织姬": ["Admire Vega", "アドマイヤベガ", "织姬"],
        "菱曙": ["Hishi Akebono", "ヒシアケボノ"],
        "雪之美人": ["Yukino Bijin", "ユキノビジン"],
        "艾尼斯风神": ["Ines Fujin", "アイネスフウジン", "风神"],
        "名将怒涛": ["Meisho Doto", "メイショウドトウ", "怒涛"],
        "目白高峰": ["Mejiro Palmer", "メジロパーマー"],
        "西野花": ["Nishino Flower", "ニシノフラワー"],
        "青竹回忆": ["Bamboo Memory", "バンブーメモリー"],
        "待兼诗歌剧": ["Matikanetannhauser", "マチカネタンホイザー"],
        "目白阿尔丹": ["Mejiro Ardan", "メジロアルダン", "阿尔丹"],
        "天狼星象征": ["Sirius Symboli", "シリウスシンボリ"],
        "里见皇冠": ["Satono Crown", "サトノクラウン"],
        "音波": ["Curren Chan", "カレンチャン"],
        "八重无敌": ["Yaeno Muteki", "ヤエノムテキ"],
        "采珠": ["Seeking the Pearl", "シーキングザパール"],
    }
    _merge(ROOT / "uma" / "nicknames.json", _bidirectional(n))


def enrich_hs() -> None:
    n = {
        "克苏恩": ["C'Thun", "CThun", "Cthun", "克苏"],
        "尤格-萨隆": ["Yogg-Saron", "YoggSaron", "尤格萨隆", "尤格", "摇骰子"],
        "恩佐斯": ["N'Zoth", "NZoth", "恩佐斯"],
        "亚煞极": ["Y'Shaarj", "YShaarj"],
        "拉格纳罗斯": ["Ragnaros", "火元素", "炎魔之王", "拉格"],
        "拉格纳罗斯，炎魔之王": ["Ragnaros the Firelord", "炎魔之王拉格纳罗斯", "火元素", "拉格"],
        "加拉克苏斯大王": ["Lord Jaraxxus", "加拉克苏斯", "加拉"],
        "希尔瓦娜斯·风行者": ["Sylvanas Windrunner", "希尔瓦娜斯", "女妖之王", "Sylvanas"],
        "凯恩·血蹄": ["Cairne Bloodhoof", "凯恩", "Cairne"],
        "火车王里诺艾": ["Leeroy Jenkins", "LeeroyJenkins", "火车王", "Leeroy", "冲锋"],
        "砰砰博士": ["Dr. Boom", "Dr Boom", "Boom", "炸弹人"],
        "伊瑟拉": ["Ysera", "绿龙"],
        "阿莱克丝塔萨": ["Alexstrasza", "红龙", "Alex"],
        "玛里苟斯": ["Malygos", "蓝龙", "法强龙"],
        "奈法利安": ["Nefarian", "黑龙", "Nef"],
        "死亡之翼": ["Deathwing", "黑龙王"],
        "奥妮克希亚": ["Onyxia", "Ony"],
        "诺兹多姆": ["Nozdormu", "Noz", "青铜龙"],
        "加基森拍卖师": ["Gadgetzan Auctioneer", "拍卖师", "Auctioneer"],
        "血法师萨尔诺斯": ["Bloodmage Thalnos", "血法", "Thalnos", "萨尔诺斯"],
        "哈里森·琼斯": ["Harrison Jones", "哈里森", "Harrison"],
        "提里奥·弗丁": ["Tirion Fordring", "提里奥", "Tirion"],
        "克尔苏加德": ["Kel'Thuzad", "KelThuzad", "KT"],
        "洛欧塞布": ["Loatheb", "洛欧"],
        "格罗玛什·地狱咆哮": ["Grommash Hellscream", "格罗玛什", "Grom", "跳斧"],
        "巫妖王": ["The Lich King", "Lich King", "阿尔萨斯"],
        "伊利丹·怒风": ["Illidan Stormrage", "Illidan", "伊利丹"],
        "凯雷塞斯王子": ["Prince Kael'thas", "Kael'thas", "凯尔萨斯", "三连火球"],
        "麦迪文": ["Medivh"],
        "格鲁尔": ["Gruul"],
        "冰吼": ["Icehowl"],
        "霍格": ["Hogger"],
        "山岭巨人": ["Mountain Giant"],
        "海巨人": ["Sea Giant"],
        "熔核巨人": ["Molten Giant"],
        "吵吵机器人": ["Annoy-o-Tron", "吵吵"],
        "艾萨拉女王": ["Queen Azshara", "艾萨拉"],
        "阿努巴拉克": ["Anub'arak"],
        "戈霍恩": ["G'huun"],
    }
    _merge(ROOT / "hs" / "nicknames.json", _bidirectional(n))


def main() -> None:
    import sys

    targets = sys.argv[1:] or ["ygo", "uma", "hs"]
    for t in targets:
        print(f"[{t.upper()}] nicknames ...", flush=True)
        if t == "ygo":
            enrich_ygo()
        elif t == "uma":
            enrich_uma()
        elif t == "hs":
            enrich_hs()
        else:
            raise SystemExit(f"unknown {t}")


if __name__ == "__main__":
    main()
