"""Bilibili 平台数据模型 — 非事件类结构化数据"""

from __future__ import annotations

from typing import Optional, Tuple

from pydantic import BaseModel, Field


__all__ = [
    "LiveRoomInfo",
    "RoomInfo",
    "AnchorInfo",
    "WatchedShow",
    "DynamicStatInfo",
    "DynamicVideoInfo",
    "DynamicMusicInfo",
    "DynamicArticleInfo",
    "DynamicLiveRcmdInfo",
    "VideoOwner",
    "VideoStat",
    "VideoPage",
    "VideoStaffMember",
    "VideoInfo",
    "ParsedBiliId",
]


class RoomInfo(BaseModel):
    """直播间信息"""

    uid: int = 0
    room_id: int = 0
    title: str = ""
    cover_url: str = ""
    background_url: str = ""
    description: str = ""
    tags: Tuple[str, ...] = ()
    live_status: int = 0
    live_start_time: int = 0
    parent_area_name: str = ""
    parent_area_id: int = 0
    area_name: str = ""
    area_id: int = 0
    keyframe_url: str = ""
    online: int = 0


class AnchorInfo(BaseModel):
    """主播信息"""

    name: str = ""
    face_url: str = ""
    gender: str = ""
    official_info: str = ""
    fanclub_name: str = ""
    fanclub_num: int = 0
    live_level: int = 0
    live_score: int = 0
    live_upgrade_score: int = 0


class WatchedShow(BaseModel):
    """观看榜信息"""

    num: int = 0
    text_small: str = ""
    text_large: str = ""


class LiveRoomInfo(BaseModel):
    """直播间完整信息"""

    room_info: RoomInfo = Field(default_factory=RoomInfo)
    anchor_info: AnchorInfo = Field(default_factory=AnchorInfo)
    watched_show: WatchedShow = Field(default_factory=WatchedShow)

    @classmethod
    def from_raw(cls, data: dict) -> Optional["LiveRoomInfo"]:
        """从 bilibili-api ``LiveRoom.get_room_info()`` 原始数据构造模型。"""
        try:
            ri = data.get("room_info", {})
            tags_str: str = ri.get("tags", "")
            tags = tuple(tags_str.split(",")) if tags_str else ()

            room_info = RoomInfo(
                uid=ri.get("uid", 0),
                room_id=ri.get("room_id", 0),
                title=ri.get("title", ""),
                cover_url=ri.get("cover", ""),
                background_url=ri.get("background", ""),
                description=ri.get("description", ""),
                tags=tags,
                live_status=ri.get("live_status", 0),
                live_start_time=ri.get("live_start_time", 0),
                parent_area_name=ri.get("parent_area_name", ""),
                parent_area_id=ri.get("parent_area_id", 0),
                area_name=ri.get("area_name", ""),
                area_id=ri.get("area_id", 0),
                keyframe_url=ri.get("keyframe", ""),
                online=ri.get("online", 0),
            )

            ai = data.get("anchor_info", {})
            base = ai.get("base_info", {})
            medal = ai.get("medal_info", {})
            live_info = ai.get("live_info", {})
            official = base.get("official_info", {})

            anchor_info = AnchorInfo(
                name=base.get("uname", ""),
                face_url=base.get("face", ""),
                gender=base.get("gender", ""),
                official_info=official.get("title", ""),
                fanclub_name=medal.get("medal_name", ""),
                fanclub_num=medal.get("fansclub", 0),
                live_level=live_info.get("level", 0),
                live_score=live_info.get("score", 0),
                live_upgrade_score=live_info.get("upgrade_score", 0),
            )

            ws = data.get("watched_show", {})
            watched_show = WatchedShow(
                num=ws.get("num", 0),
                text_small=ws.get("text_small", ""),
                text_large=ws.get("text_large", ""),
            )

            return cls(
                room_info=room_info,
                anchor_info=anchor_info,
                watched_show=watched_show,
            )
        except Exception:
            return None


# ==================== 动态相关模型 ====================


class DynamicStatInfo(BaseModel):
    """动态统计信息"""

    comment_count: int = 0
    like_count: int = 0
    forward_count: int = 0


class DynamicVideoInfo(BaseModel):
    """动态中的视频信息"""

    av_id: str = ""
    bv_id: str = ""
    title: str = ""
    cover: str = ""
    desc: str = ""
    duration_text: str = ""
    play_count: str = ""
    danmaku_count: str = ""
    dynamic_text: str = ""


class DynamicMusicInfo(BaseModel):
    """动态中的音乐信息"""

    music_id: str = ""
    title: str = ""
    cover: str = ""
    label: str = ""
    dynamic_text: str = ""


class DynamicArticleInfo(BaseModel):
    """动态中的专栏信息"""

    title: str = ""
    summary: str = ""
    has_more: bool = False
    article_id: int = 0


class DynamicLiveRcmdInfo(BaseModel):
    """动态中的直播推荐信息"""

    room_id: int = 0
    live_status: int = 0
    title: str = ""
    cover: str = ""
    online: int = 0
    area_id: int = 0
    area_name: str = ""
    parent_area_id: int = 0
    parent_area_name: str = ""
    live_start_time: int = 0


# ==================== 视频信息模型 ====================


class VideoOwner(BaseModel):
    """视频 UP 主信息"""

    mid: int = 0
    name: str = ""
    face: str = ""


class VideoStat(BaseModel):
    """视频统计信息"""

    view: int = 0
    danmaku: int = 0
    reply: int = 0
    favorite: int = 0
    coin: int = 0
    share: int = 0
    like: int = 0
    dislike: int = 0
    his_rank: int = 0
    now_rank: int = 0


class VideoPage(BaseModel):
    """视频分 P 信息"""

    cid: int = 0
    page: int = 0
    part: str = ""
    duration: int = 0


class VideoStaffMember(BaseModel):
    """联合投稿成员"""

    mid: int = 0
    name: str = ""
    title: str = ""
    face: str = ""
    follower: int = 0


class VideoInfo(BaseModel):
    """视频完整信息"""

    bvid: str = ""
    aid: int = 0
    title: str = ""
    pic: str = ""
    desc: str = ""
    pubdate: int = 0
    ctime: int = 0
    duration: int = 0
    videos: int = 1
    tid: int = 0
    tname: str = ""
    copyright: int = 0
    state: int = 0
    owner: VideoOwner = Field(default_factory=VideoOwner)
    stat: VideoStat = Field(default_factory=VideoStat)
    pages: Tuple[VideoPage, ...] = ()
    staff: Tuple[VideoStaffMember, ...] = ()
    dynamic: str = ""
    cid: int = 0
    season_id: Optional[int] = None

    @property
    def duration_text(self) -> str:
        """格式化时长 HH:MM:SS 或 MM:SS"""
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"

    @classmethod
    def from_raw(cls, data: dict) -> Optional["VideoInfo"]:
        """从 bilibili-api ``video.Video.get_info()`` 原始数据构造模型。"""
        try:
            owner_raw = data.get("owner", {})
            owner = VideoOwner(
                mid=owner_raw.get("mid", 0),
                name=owner_raw.get("name", ""),
                face=owner_raw.get("face", ""),
            )

            stat_raw = data.get("stat", {})
            stat = VideoStat(
                view=stat_raw.get("view", 0),
                danmaku=stat_raw.get("danmaku", 0),
                reply=stat_raw.get("reply", 0),
                favorite=stat_raw.get("favorite", 0),
                coin=stat_raw.get("coin", 0),
                share=stat_raw.get("share", 0),
                like=stat_raw.get("like", 0),
                dislike=stat_raw.get("dislike", 0),
                his_rank=stat_raw.get("his_rank", 0),
                now_rank=stat_raw.get("now_rank", 0),
            )

            pages = tuple(
                VideoPage(
                    cid=p.get("cid", 0),
                    page=p.get("page", 0),
                    part=p.get("part", ""),
                    duration=p.get("duration", 0),
                )
                for p in data.get("pages", [])
            )

            staff = tuple(
                VideoStaffMember(
                    mid=s.get("mid", 0),
                    name=s.get("name", ""),
                    title=s.get("title", ""),
                    face=s.get("face", ""),
                    follower=s.get("follower", 0),
                )
                for s in data.get("staff", [])
            )

            return cls(
                bvid=data.get("bvid", ""),
                aid=data.get("aid", 0),
                title=data.get("title", ""),
                pic=data.get("pic", ""),
                desc=data.get("desc", ""),
                pubdate=data.get("pubdate", 0),
                ctime=data.get("ctime", 0),
                duration=data.get("duration", 0),
                videos=data.get("videos", 1),
                tid=data.get("tid", 0),
                tname=data.get("tname_v2", "") or data.get("tname", ""),
                copyright=data.get("copyright", 0),
                state=data.get("state", 0),
                owner=owner,
                stat=stat,
                pages=pages,
                staff=staff,
                dynamic=data.get("dynamic", ""),
                cid=data.get("cid", 0),
                season_id=data.get("season_id"),
            )
        except Exception:
            return None


# ==================== B站链接解析结果 ====================


class ParsedBiliId(BaseModel):
    """B站视频 ID 解析结果"""

    id_type: str = ""  # "bv" | "av"
    bvid: str = ""
    aid: int = 0
    raw_url: str = ""
    redirected_url: str = ""

    @property
    def video_id(self) -> str:
        """可直接传入 ``get_video_info`` 的视频 ID 字符串。"""
        return self.bvid if self.id_type == "bv" else f"av{self.aid}"
