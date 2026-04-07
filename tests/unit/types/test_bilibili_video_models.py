"""
Bilibili 视频数据模型测试

规范:
  VI-01: VideoInfo.from_raw() 完整解析 — 全字段正确映射
  VI-02: from_raw() 空/缺失字段回退默认值
  VI-03: from_raw() 异常数据返回 None
  VI-04: VideoInfo 计算属性（duration_text / url）
  VI-05: ParsedBiliId 模型字段与 video_id 属性
"""

from ncatbot.types.bilibili.models import (
    ParsedBiliId,
    VideoInfo,
    VideoOwner,
    VideoPage,
    VideoStat,
    VideoStaffMember,
)

# ---- 真实结构的测试数据 ----

FULL_RAW = {
    "bvid": "BV1wa411Q7N6",
    "aid": 258296202,
    "title": "测试视频标题",
    "pic": "https://example.com/cover.jpg",
    "desc": "这是一段视频简介",
    "pubdate": 1700000000,
    "ctime": 1700000000,
    "duration": 3661,
    "videos": 3,
    "tid": 17,
    "tname": "单机游戏",
    "copyright": 1,
    "state": 0,
    "dynamic": "发布动态文本",
    "cid": 12345678,
    "season_id": 99,
    "owner": {
        "mid": 67890,
        "name": "TestUP",
        "face": "https://example.com/face.jpg",
    },
    "stat": {
        "view": 100000,
        "danmaku": 5000,
        "reply": 2000,
        "favorite": 3000,
        "coin": 1500,
        "share": 800,
        "like": 9000,
        "dislike": 10,
        "his_rank": 50,
        "now_rank": 0,
    },
    "pages": [
        {"cid": 111, "page": 1, "part": "第一P", "duration": 600},
        {"cid": 222, "page": 2, "part": "第二P", "duration": 1200},
        {"cid": 333, "page": 3, "part": "第三P", "duration": 1861},
    ],
    "staff": [
        {
            "mid": 67890,
            "name": "TestUP",
            "title": "UP主",
            "face": "https://example.com/face.jpg",
            "follower": 50000,
        },
        {
            "mid": 11111,
            "name": "协作者",
            "title": "剪辑",
            "face": "https://example.com/face2.jpg",
            "follower": 10000,
        },
    ],
}


class TestVideoInfoFromRawFull:
    """VI-01: VideoInfo.from_raw() 完整解析"""

    def test_vi01_basic_fields(self):
        """VI-01: 基础字段正确映射"""
        info = VideoInfo.from_raw(FULL_RAW)
        assert info is not None
        assert info.bvid == "BV1wa411Q7N6"
        assert info.aid == 258296202
        assert info.title == "测试视频标题"
        assert info.pic == "https://example.com/cover.jpg"
        assert info.desc == "这是一段视频简介"
        assert info.pubdate == 1700000000
        assert info.ctime == 1700000000
        assert info.duration == 3661
        assert info.videos == 3
        assert info.tid == 17
        assert info.tname == "单机游戏"
        assert info.copyright == 1
        assert info.state == 0
        assert info.dynamic == "发布动态文本"
        assert info.cid == 12345678
        assert info.season_id == 99

    def test_vi01_owner_fields(self):
        """VI-01: owner 字段正确映射"""
        info = VideoInfo.from_raw(FULL_RAW)
        assert info is not None
        assert isinstance(info.owner, VideoOwner)
        assert info.owner.mid == 67890
        assert info.owner.name == "TestUP"
        assert info.owner.face == "https://example.com/face.jpg"

    def test_vi01_stat_fields(self):
        """VI-01: stat 字段正确映射"""
        info = VideoInfo.from_raw(FULL_RAW)
        assert info is not None
        assert isinstance(info.stat, VideoStat)
        assert info.stat.view == 100000
        assert info.stat.danmaku == 5000
        assert info.stat.reply == 2000
        assert info.stat.favorite == 3000
        assert info.stat.coin == 1500
        assert info.stat.share == 800
        assert info.stat.like == 9000
        assert info.stat.dislike == 10
        assert info.stat.his_rank == 50
        assert info.stat.now_rank == 0

    def test_vi01_pages_fields(self):
        """VI-01: pages 字段正确映射"""
        info = VideoInfo.from_raw(FULL_RAW)
        assert info is not None
        assert len(info.pages) == 3
        assert all(isinstance(p, VideoPage) for p in info.pages)
        assert info.pages[0].cid == 111
        assert info.pages[0].page == 1
        assert info.pages[0].part == "第一P"
        assert info.pages[0].duration == 600
        assert info.pages[2].page == 3

    def test_vi01_staff_fields(self):
        """VI-01: staff 字段正确映射"""
        info = VideoInfo.from_raw(FULL_RAW)
        assert info is not None
        assert len(info.staff) == 2
        assert all(isinstance(s, VideoStaffMember) for s in info.staff)
        assert info.staff[0].mid == 67890
        assert info.staff[0].name == "TestUP"
        assert info.staff[0].title == "UP主"
        assert info.staff[1].name == "协作者"
        assert info.staff[1].follower == 10000


class TestVideoInfoFromRawDefaults:
    """VI-02: 空/缺失字段回退默认值"""

    def test_vi02_empty_dict(self):
        """VI-02: 传入空 dict 返回全默认值的 VideoInfo"""
        info = VideoInfo.from_raw({})
        assert info is not None
        assert info.bvid == ""
        assert info.aid == 0
        assert info.title == ""
        assert info.duration == 0
        assert info.videos == 1
        assert info.owner.mid == 0
        assert info.owner.name == ""
        assert info.stat.view == 0
        assert info.pages == ()
        assert info.staff == ()
        assert info.season_id is None

    def test_vi02_partial_data(self):
        """VI-02: 仅部分字段，其余回退默认值"""
        raw = {"bvid": "BV1test00000", "title": "部分标题", "aid": 123}
        info = VideoInfo.from_raw(raw)
        assert info is not None
        assert info.bvid == "BV1test00000"
        assert info.title == "部分标题"
        assert info.aid == 123
        assert info.desc == ""
        assert info.owner.mid == 0
        assert info.stat.view == 0

    def test_vi02_no_staff(self):
        """VI-02: 没有 staff 键时，staff 为空元组"""
        raw = {"bvid": "BV1test00000"}
        info = VideoInfo.from_raw(raw)
        assert info is not None
        assert info.staff == ()


class TestVideoInfoFromRawError:
    """VI-03: 异常数据返回 None"""

    def test_vi03_none_input(self):
        """VI-03: 传入 None 时返回 None"""
        result = VideoInfo.from_raw(None)  # type: ignore[arg-type]
        assert result is None

    def test_vi03_non_dict_input(self):
        """VI-03: 传入非 dict 类型返回 None"""
        result = VideoInfo.from_raw("not a dict")  # type: ignore[arg-type]
        assert result is None


class TestVideoInfoProperties:
    """VI-04: VideoInfo 计算属性"""

    def test_vi04_duration_text_with_hours(self):
        """VI-04: duration_text 含小时"""
        info = VideoInfo(duration=3661)
        assert info.duration_text == "01:01:01"

    def test_vi04_duration_text_without_hours(self):
        """VI-04: duration_text 不含小时"""
        info = VideoInfo(duration=125)
        assert info.duration_text == "02:05"

    def test_vi04_duration_text_zero(self):
        """VI-04: duration_text 零时长"""
        info = VideoInfo(duration=0)
        assert info.duration_text == "00:00"

    def test_vi04_url(self):
        """VI-04: url 属性"""
        info = VideoInfo(bvid="BV1wa411Q7N6")
        assert info.url == "https://www.bilibili.com/video/BV1wa411Q7N6"

    def test_vi04_tname_v2_priority(self):
        """VI-04: tname_v2 优先于 tname"""
        raw = {"tname": "旧分区", "tname_v2": "新分区"}
        info = VideoInfo.from_raw(raw)
        assert info is not None
        assert info.tname == "新分区"


class TestParsedBiliId:
    """VI-05: ParsedBiliId 模型字段与 video_id 属性"""

    def test_vi05_bv_video_id(self):
        """VI-05: BV 类型 video_id 返回 bvid"""
        p = ParsedBiliId(id_type="bv", bvid="BV1wa411Q7N6")
        assert p.video_id == "BV1wa411Q7N6"

    def test_vi05_av_video_id(self):
        """VI-05: AV 类型 video_id 返回 av{aid}"""
        p = ParsedBiliId(id_type="av", aid=258296202)
        assert p.video_id == "av258296202"

    def test_vi05_default_fields(self):
        """VI-05: 默认字段"""
        p = ParsedBiliId()
        assert p.id_type == ""
        assert p.bvid == ""
        assert p.aid == 0
        assert p.raw_url == ""
        assert p.redirected_url == ""

    def test_vi05_full_fields(self):
        """VI-05: 全字段"""
        p = ParsedBiliId(
            id_type="bv",
            bvid="BV1wa411Q7N6",
            aid=0,
            raw_url="https://b23.tv/abc",
            redirected_url="https://www.bilibili.com/video/BV1wa411Q7N6",
        )
        assert p.raw_url == "https://b23.tv/abc"
        assert p.redirected_url == "https://www.bilibili.com/video/BV1wa411Q7N6"
