"""
LearningChat 数据模型模块
使用 aiosqlite 进行异步数据库操作
"""

import re
import json
import html
import logging
import hashlib
import requests
from pathlib import Path
from functools import cached_property
from typing import List, Optional, Tuple, Dict
import aiosqlite

try:
    import jieba_fast as jieba
    import jieba_fast.analyse as jieba_analyse
except ImportError:
    import jieba
    import jieba.analyse as jieba_analyse

from .config import config_manager

log = logging.getLogger(__name__)

# 图片缓存目录
IMAGE_CACHE_DIR = Path("data") / "LearningChat" / "image_cache"
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 图片内容哈希缓存文件
IMAGE_HASH_CACHE_FILE = Path("data") / "LearningChat" / "image_hash_cache.json"

# 数据库路径
DATABASE_PATH = Path("data") / "LearningChat" / "learning_chat.db"
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

# jieba 配置
jieba.setLogLevel(jieba.logging.INFO)
config = config_manager.config
if config.dictionary:
    for word in config.dictionary:
        jieba.add_word(word)


class DatabaseManager:
    """数据库管理器"""

    _instance: Optional["DatabaseManager"] = None
    _db: Optional[aiosqlite.Connection] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def init_db(self):
        """初始化数据库"""
        if self._db is None:
            self._db = await aiosqlite.connect(str(DATABASE_PATH))
            self._db.row_factory = aiosqlite.Row
            await self._create_tables()

    async def _create_tables(self):
        """创建数据表"""
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                raw_message TEXT NOT NULL,
                plain_text TEXT NOT NULL,
                time INTEGER NOT NULL
            );
            
            CREATE INDEX IF NOT EXISTS idx_message_group_time 
                ON message(group_id, time DESC);
            CREATE INDEX IF NOT EXISTS idx_message_id 
                ON message(message_id);
            
            CREATE TABLE IF NOT EXISTS context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                time INTEGER NOT NULL,
                count INTEGER DEFAULT 1
            );
            
            CREATE INDEX IF NOT EXISTS idx_context_keywords 
                ON context(keywords);
            CREATE INDEX IF NOT EXISTS idx_context_time 
                ON context(time DESC);
            
            CREATE TABLE IF NOT EXISTS answer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                count INTEGER DEFAULT 1,
                time INTEGER NOT NULL,
                messages TEXT NOT NULL,
                context_id INTEGER,
                FOREIGN KEY (context_id) REFERENCES context(id)
            );
            
            CREATE INDEX IF NOT EXISTS idx_answer_keywords 
                ON answer(keywords);
            CREATE INDEX IF NOT EXISTS idx_answer_context 
                ON answer(context_id);
            CREATE INDEX IF NOT EXISTS idx_answer_group 
                ON answer(group_id);
            
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                global_ban INTEGER DEFAULT 0,
                ban_group_id TEXT NOT NULL DEFAULT '[]'
            );
            
            CREATE INDEX IF NOT EXISTS idx_blacklist_keywords 
                ON blacklist(keywords);
        """)
        await self._db.commit()

    async def close(self):
        """关闭数据库连接"""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        return self._db


# 全局数据库管理器
db_manager = DatabaseManager()


class ImageCache:
    """图片缓存管理器"""

    # 清理策略配置
    # 保留最近N天内有学习记录的图片
    CLEANUP_KEEP_DAYS = 30
    # 保留学习次数排名前N的图片（热门图片不清理）
    CLEANUP_KEEP_TOP_COUNT = 50
    # 最大缓存文件数量
    MAX_CACHE_FILES = 500

    # 图片内容哈希缓存（file_id -> content_hash）
    _hash_cache: Dict[str, str] = {}
    _hash_cache_loaded = False

    @classmethod
    def _load_hash_cache(cls):
        """加载图片哈希缓存"""
        if cls._hash_cache_loaded:
            return
        try:
            if IMAGE_HASH_CACHE_FILE.exists():
                cls._hash_cache = json.loads(
                    IMAGE_HASH_CACHE_FILE.read_text(encoding="utf-8")
                )
                log.debug(
                    f"[LearningChat] 已加载 {len(cls._hash_cache)} 条图片哈希缓存"
                )
        except Exception as e:
            log.warning(f"[LearningChat] 加载图片哈希缓存失败: {e}")
            cls._hash_cache = {}
        cls._hash_cache_loaded = True

    @classmethod
    def _save_hash_cache(cls):
        """保存图片哈希缓存"""
        try:
            IMAGE_HASH_CACHE_FILE.write_text(
                json.dumps(cls._hash_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning(f"[LearningChat] 保存图片哈希缓存失败: {e}")

    @classmethod
    def get_content_hash(cls, file_id: str) -> Optional[str]:
        """
        获取图片内容的哈希值（用于判断两张图片是否相同）

        Args:
            file_id: 图片文件标识符

        Returns:
            图片内容的 MD5 哈希值，如果图片不存在返回 None
        """
        cls._load_hash_cache()

        # 去掉扩展名作为基础 key
        import os

        base_name = os.path.splitext(file_id)[0]

        # 检查缓存
        if base_name in cls._hash_cache:
            return cls._hash_cache[base_name]

        # 计算哈希
        cache_path = cls.get_cache_path(file_id)
        if not cache_path.exists():
            # 尝试查找其他扩展名
            for ext in [".jpg", ".png", ".gif", ".webp"]:
                alt_path = IMAGE_CACHE_DIR / (base_name + ext)
                if alt_path.exists():
                    cache_path = alt_path
                    break

        if not cache_path.exists():
            return None

        try:
            content = cache_path.read_bytes()
            content_hash = hashlib.md5(content).hexdigest()

            # 缓存结果
            cls._hash_cache[base_name] = content_hash
            # 定期保存（每 50 条新缓存保存一次）
            if len(cls._hash_cache) % 50 == 0:
                cls._save_hash_cache()

            return content_hash
        except Exception as e:
            log.warning(f"[LearningChat] 计算图片哈希失败 {file_id}: {e}")
            return None

    @classmethod
    def images_same_content(cls, file_id1: str, file_id2: str) -> bool:
        """
        判断两张图片是否内容相同
        QQ 的 file ID 本身就是图片内容的哈希值，直接比较文件名主体部分即可

        Args:
            file_id1: 第一张图片的文件标识符
            file_id2: 第二张图片的文件标识符

        Returns:
            内容是否相同
        """
        import os

        # QQ 的 file ID 格式如 "E91B986B51DC7736A925DFF4CA1675B4.jpg"
        # 主体部分就是图片内容的哈希值，直接比较即可
        base1 = os.path.splitext(file_id1)[0]
        base2 = os.path.splitext(file_id2)[0]

        return base1 == base2

    @staticmethod
    def get_cache_path(file_id: str) -> Path:
        """获取图片缓存路径"""
        # file_id 格式如 "33FD1CF861DF03869EB9995D577782C0.jpg"
        return IMAGE_CACHE_DIR / file_id

    @staticmethod
    def is_cached(file_id: str) -> bool:
        """检查图片是否已缓存"""
        return ImageCache.get_cache_path(file_id).exists()

    # 下载图片使用的请求头（适配 QQ NT 版本的图片 URL）
    DOWNLOAD_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://multimedia.nt.qq.com.cn/",  # QQ NT 图片服务器域名
    }

    @staticmethod
    def download_and_cache(url: str, file_id: str) -> Optional[Path]:
        """
        下载图片并缓存

        Args:
            url: 图片URL
            file_id: 图片文件标识符（如 33FD1CF861DF03869EB9995D577782C0.jpg）

        Returns:
            缓存文件路径，失败返回None
        """
        cache_path = ImageCache.get_cache_path(file_id)

        # 如果已缓存，直接返回
        if cache_path.exists():
            return cache_path

        try:
            # 解码 HTML 实体（如 &amp; -> &）
            decoded_url = html.unescape(url)

            # QQ NT 版本的图片 URL 需要特定请求头
            response = requests.get(
                decoded_url,
                headers=ImageCache.DOWNLOAD_HEADERS,
                verify=False,
                timeout=30,
            )
            if response.status_code == 200:
                # 检查是否真的是图片数据
                content_type = response.headers.get("Content-Type", "")
                if response.content and (
                    content_type.startswith("image/")
                    or len(response.content) > 100  # 至少有一些数据
                ):
                    cache_path.write_bytes(response.content)
                    log.debug(f"[LearningChat] 图片已缓存: {file_id}")
                    return cache_path
                else:
                    log.warning("[LearningChat] 下载图片失败: 响应不是有效图片")
                    return None
            else:
                log.warning(f"[LearningChat] 下载图片失败: HTTP {response.status_code}")
                return None
        except Exception as e:
            log.error(f"[LearningChat] 下载图片异常: {e}")
            return None

    @staticmethod
    def cache_from_message(raw_message: str) -> List[Path]:
        """
        从消息中提取图片并缓存（同步方式，使用 requests）

        Returns:
            缓存成功的图片路径列表
        """
        cached_paths = []

        # 提取所有图片的file和url
        image_pattern = re.compile(
            r"\[CQ:image,file=([^,\]]+)(?:,[^\]]*url=([^,\]]+))?"
        )

        for match in image_pattern.finditer(raw_message):
            file_id = match.group(1)
            url = match.group(2)

            if url:
                path = ImageCache.download_and_cache(url, file_id)
                if path:
                    cached_paths.append(path)

        return cached_paths

    @staticmethod
    async def cache_from_images(images: list) -> List[Path]:
        """
        使用 ncatbot 的 Image 对象下载并缓存图片（推荐方式）

        Args:
            images: ncatbot Image 对象列表

        Returns:
            缓存成功的图片路径列表
        """
        cached_paths = []

        for img in images:
            try:
                # 获取文件名作为 file_id
                file_id = getattr(img, "file", None)
                url = getattr(img, "url", None)
                log.debug(
                    f"[LearningChat] 处理图片: file={file_id}, url={url[:50] if url else None}..."
                )

                if not file_id:
                    log.warning("[LearningChat] 图片没有file属性，跳过")
                    continue

                cache_path = ImageCache.get_cache_path(file_id)

                # 如果已缓存，直接添加到结果
                if cache_path.exists():
                    log.debug(f"[LearningChat] 图片已存在缓存: {file_id}")
                    cached_paths.append(cache_path)
                    continue

                # 使用 ncatbot 的 download_to 方法下载
                try:
                    log.debug(f"[LearningChat] 尝试使用ncatbot下载: {file_id}")
                    downloaded_path = await img.download_to(
                        str(IMAGE_CACHE_DIR), file_id
                    )
                    if downloaded_path and Path(downloaded_path).exists():
                        cached_paths.append(Path(downloaded_path))
                        log.info(f"[LearningChat] 图片已缓存(ncatbot): {file_id}")
                    else:
                        log.warning(
                            f"[LearningChat] ncatbot下载返回但文件不存在: {downloaded_path}"
                        )
                except Exception as e:
                    log.warning(f"[LearningChat] ncatbot下载失败({e})，尝试备用方式")
                    # 备用方式：使用 URL 直接下载
                    if url:
                        path = ImageCache.download_and_cache(url, file_id)
                        if path:
                            cached_paths.append(path)
                            log.info(f"[LearningChat] 图片已缓存(备用): {file_id}")
                    else:
                        log.warning(
                            f"[LearningChat] 图片没有url属性，无法下载: {file_id}"
                        )

            except Exception as e:
                log.error(f"[LearningChat] 缓存图片异常: {e}")

        return cached_paths

    @staticmethod
    def get_image_cq_code(file_id: str) -> Optional[str]:
        """
        获取可发送的图片CQ码

        Args:
            file_id: 图片文件标识符

        Returns:
            图片CQ码，如果图片不存在返回None
        """
        cache_path = ImageCache.get_cache_path(file_id)
        if cache_path.exists():
            # 使用绝对路径
            abs_path = cache_path.resolve()
            return f"[CQ:image,file=file:///{abs_path}]"
        return None

    @staticmethod
    def convert_message_for_send(message: str, remove_missing: bool = True) -> str:
        """
        将消息中的图片转换为可发送的格式
        使用本地缓存的图片路径替换原URL

        Args:
            message: 原始消息
            remove_missing: 如果图片缓存不存在，是否移除图片CQ码

        Returns:
            转换后的消息，如果所有图片都不存在且remove_missing=True，返回None
        """
        has_valid_image = False

        def replace_image(match):
            nonlocal has_valid_image
            file_id = match.group(1)
            cq_code = ImageCache.get_image_cq_code(file_id)
            if cq_code:
                has_valid_image = True
                return cq_code
            # 图片缓存不存在
            if remove_missing:
                log.debug(f"[LearningChat] 图片缓存不存在，移除: {file_id}")
                return ""  # 移除不存在的图片
            # 保留原CQ码（但发送会失败）
            return match.group(0)

        # 替换图片CQ码
        pattern = re.compile(r"\[CQ:image,file=([^,\]]+)(?:,[^\]]+)?\]")
        result = pattern.sub(replace_image, message).strip()

        # 如果消息只有图片且图片不存在，返回 None
        if not result and "[CQ:image" in message:
            return None

        return result if result else None

    @staticmethod
    def extract_image_files_from_messages(messages: List[str]) -> set:
        """
        从消息列表中提取所有图片file_id

        Args:
            messages: 消息列表

        Returns:
            图片file_id集合
        """
        image_files = set()
        pattern = re.compile(r"\[CQ:image,file=([^,\]]+)")

        for msg in messages:
            for match in pattern.finditer(msg):
                image_files.add(match.group(1))

        return image_files

    @staticmethod
    async def get_referenced_images() -> Tuple[set, dict]:
        """
        从数据库获取所有被引用的图片及其热度

        Returns:
            (所有被引用的图片file_id集合, 图片热度字典 {file_id: count})
        """
        import time

        db = db_manager.db
        if db is None:
            return set(), {}

        referenced_images = set()
        image_popularity = {}  # {file_id: count}

        # 计算保留天数的时间戳
        cutoff_time = int(time.time()) - (ImageCache.CLEANUP_KEEP_DAYS * 24 * 3600)

        try:
            # 1. 从 answer 表获取最近有使用的图片及其热度
            cursor = await db.execute(
                """SELECT messages, count, time FROM answer 
                   WHERE messages LIKE '%[CQ:image%'"""
            )
            rows = await cursor.fetchall()

            for row in rows:
                messages_json = row["messages"]
                count = row["count"]
                msg_time = row["time"]

                try:
                    messages = json.loads(messages_json) if messages_json else []
                except (json.JSONDecodeError, TypeError):
                    continue

                for msg in messages:
                    files = ImageCache.extract_image_files_from_messages([msg])
                    for file_id in files:
                        # 累加热度
                        if file_id not in image_popularity:
                            image_popularity[file_id] = 0
                        image_popularity[file_id] += count

                        # 最近使用的图片加入引用列表
                        if msg_time >= cutoff_time:
                            referenced_images.add(file_id)

            # 2. 从 message 表获取最近消息中的图片
            cursor = await db.execute(
                """SELECT message FROM message 
                   WHERE message LIKE '%[CQ:image%' AND time >= ?""",
                (cutoff_time,),
            )
            rows = await cursor.fetchall()

            for row in rows:
                files = ImageCache.extract_image_files_from_messages([row["message"]])
                referenced_images.update(files)

            # 3. 将热门图片（排名前N）加入引用列表
            sorted_images = sorted(
                image_popularity.items(), key=lambda x: x[1], reverse=True
            )
            for file_id, _ in sorted_images[: ImageCache.CLEANUP_KEEP_TOP_COUNT]:
                referenced_images.add(file_id)

        except Exception as e:
            log.error(f"[LearningChat] 获取引用图片失败: {e}")

        return referenced_images, image_popularity

    @staticmethod
    async def cleanup_unused_images() -> Tuple[int, float]:
        """
        清理未被引用的图片缓存

        清理策略：
        1. 保留最近N天内有使用记录的图片
        2. 保留学习次数排名前N的热门图片
        3. 如果缓存文件数量超过最大限制，强制清理最旧的文件

        Returns:
            (清理的文件数量, 释放的空间MB)
        """
        if not IMAGE_CACHE_DIR.exists():
            return 0, 0.0

        try:
            # 获取所有缓存文件
            cache_files = list(IMAGE_CACHE_DIR.glob("*.*"))
            if not cache_files:
                return 0, 0.0

            # 获取被引用的图片
            (
                referenced_images,
                image_popularity,
            ) = await ImageCache.get_referenced_images()

            deleted_count = 0
            total_size_freed = 0

            # 按修改时间排序（最旧的在前）
            cache_files.sort(key=lambda f: f.stat().st_mtime)

            # 遍历缓存文件
            for file_path in cache_files:
                file_name = file_path.name

                # 检查是否应该保留
                should_keep = file_name in referenced_images

                # 如果文件数量超过限制，强制清理最旧的非热门文件
                remaining_files = len(cache_files) - deleted_count
                if remaining_files > ImageCache.MAX_CACHE_FILES:
                    # 只有非热门文件才会被强制清理
                    if (
                        file_name not in image_popularity
                        or image_popularity.get(file_name, 0) < 3
                    ):
                        should_keep = False

                if not should_keep:
                    try:
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        deleted_count += 1
                        total_size_freed += file_size
                        log.debug(f"[LearningChat] 清理图片缓存: {file_name}")
                    except Exception as e:
                        log.error(f"[LearningChat] 删除图片失败 {file_name}: {e}")

            size_mb = total_size_freed / (1024 * 1024)
            if deleted_count > 0:
                log.info(
                    f"[LearningChat] 清理了 {deleted_count} 张图片缓存，"
                    f"释放了 {size_mb:.2f} MB 空间"
                )

            return deleted_count, size_mb

        except Exception as e:
            log.error(f"[LearningChat] 清理图片缓存异常: {e}")
            return 0, 0.0

    @staticmethod
    def get_cache_stats() -> dict:
        """
        获取缓存统计信息

        Returns:
            {
                "total_files": 文件数量,
                "total_size_mb": 总大小(MB),
                "oldest_file": 最旧文件的修改时间,
                "newest_file": 最新文件的修改时间
            }
        """
        if not IMAGE_CACHE_DIR.exists():
            return {"total_files": 0, "total_size_mb": 0}

        cache_files = list(IMAGE_CACHE_DIR.glob("*.*"))
        if not cache_files:
            return {"total_files": 0, "total_size_mb": 0}

        total_size = sum(f.stat().st_size for f in cache_files)
        mtimes = [f.stat().st_mtime for f in cache_files]

        return {
            "total_files": len(cache_files),
            "total_size_mb": total_size / (1024 * 1024),
            "oldest_file": min(mtimes) if mtimes else None,
            "newest_file": max(mtimes) if mtimes else None,
        }


class ChatMessage:
    """聊天消息模型"""

    # 图片CQ码正则表达式，提取file字段
    # 图片CQ码正则，匹配 file= 参数（无论在什么位置）
    IMAGE_PATTERN = re.compile(r"\[CQ:image,[^\]]*file=([^,\]]+)")
    # 完整图片CQ码正则，用于规范化（匹配整个 CQ 码并提取 file）
    FULL_IMAGE_PATTERN = re.compile(r"\[CQ:image,[^\]]*file=([^,\]]+)[^\]]*\]")

    def __init__(
        self,
        group_id: int,
        user_id: int,
        message_id: int,
        message: str,
        raw_message: str,
        plain_text: str,
        time: int,
        id: int = None,
    ):
        self.id = id
        self.group_id = group_id
        self.user_id = user_id
        self.message_id = message_id
        self.message = message
        self.raw_message = raw_message
        self.plain_text = plain_text
        self.time = time

    @cached_property
    def is_plain_text(self) -> bool:
        """是否纯文本"""
        return "[CQ:" not in self.message

    @cached_property
    def is_image_message(self) -> bool:
        """是否包含图片"""
        return "[CQ:image" in self.message

    @cached_property
    def is_pure_image(self) -> bool:
        """是否纯图片消息（只有图片，没有文字）"""
        return self.is_image_message and not self.plain_text.strip()

    @cached_property
    def image_files(self) -> List[str]:
        """提取消息中所有图片的file字段（MD5标识符）"""
        return self.IMAGE_PATTERN.findall(self.message)

    @cached_property
    def normalized_message(self) -> str:
        """
        规范化消息：将图片CQ码简化为只包含file字段
        这样可以忽略URL等变化的部分，用于比较
        """

        def replace_image(match):
            file = match.group(1)
            return f"[CQ:image,file={file}]"

        return self.FULL_IMAGE_PATTERN.sub(replace_image, self.message)

    @cached_property
    def keyword_list(self) -> List[str]:
        """获取纯文本部分的关键词列表"""
        # 纯图片消息返回空列表，使用 image_keywords 处理
        if self.is_pure_image:
            return []
        if not self.is_plain_text and not len(self.plain_text):
            return []
        return jieba_analyse.extract_tags(
            self.plain_text, topK=config_manager.config.keywords_size
        )

    @cached_property
    def keywords(self) -> str:
        """获取关键词结果"""
        # 纯图片消息，使用图片file作为关键词
        if self.is_pure_image and self.image_files:
            return f"[IMAGE:{self.image_files[0]}]"
        if not self.is_plain_text and not len(self.plain_text):
            return self.normalized_message
        return (
            self.normalized_message
            if len(self.keyword_list) < 2
            else " ".join(self.keyword_list)
        )

    @staticmethod
    def extract_image_url(raw_message: str) -> Optional[str]:
        """从原始消息中提取图片URL"""
        url_match = re.search(r"\[CQ:image,[^\]]*url=([^,\]]+)", raw_message)
        if url_match:
            return url_match.group(1)
        return None

    @staticmethod
    def messages_equal(msg1: str, msg2: str) -> bool:
        """
        比较两条消息是否相等
        对于图片消息：
        1. 先比较文件名主体部分（不含扩展名）
        2. 如果文件名不同，再比较图片内容哈希（判断是否是同一张图片）
        """
        import os

        # 提取所有图片的 file_id
        files1 = ChatMessage.FULL_IMAGE_PATTERN.findall(msg1)
        files2 = ChatMessage.FULL_IMAGE_PATTERN.findall(msg2)

        # 如果图片数量不同，消息不同
        if len(files1) != len(files2):
            return False

        # 如果没有图片，直接比较文本
        if not files1:
            return msg1 == msg2

        # 比较每张图片
        for f1, f2 in zip(files1, files2):
            base1 = os.path.splitext(f1)[0]
            base2 = os.path.splitext(f2)[0]

            # 文件名主体相同，视为同一图片
            if base1 == base2:
                continue

            # 文件名不同，比较内容哈希
            if not ImageCache.images_same_content(f1, f2):
                return False

        # 比较非图片部分
        def remove_images(msg):
            return ChatMessage.FULL_IMAGE_PATTERN.sub("[IMG]", msg)

        return remove_images(msg1) == remove_images(msg2)

    async def save(self):
        """保存消息到数据库"""
        db = db_manager.db
        if self.id is None:
            cursor = await db.execute(
                """INSERT INTO message 
                   (group_id, user_id, message_id, message, raw_message, plain_text, time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.group_id,
                    self.user_id,
                    self.message_id,
                    self.message,
                    self.raw_message,
                    self.plain_text,
                    self.time,
                ),
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                """UPDATE message SET 
                   group_id=?, user_id=?, message_id=?, message=?, 
                   raw_message=?, plain_text=?, time=?
                   WHERE id=?""",
                (
                    self.group_id,
                    self.user_id,
                    self.message_id,
                    self.message,
                    self.raw_message,
                    self.plain_text,
                    self.time,
                    self.id,
                ),
            )
        await db.commit()

    @classmethod
    async def filter(
        cls,
        group_id: int = None,
        user_id: int = None,
        message_id: int = None,
        time__gte: int = None,
    ) -> List["ChatMessage"]:
        """查询消息"""
        db = db_manager.db
        conditions = []
        params = []

        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if message_id is not None:
            conditions.append("message_id = ?")
            params.append(message_id)
        if time__gte is not None:
            conditions.append("time >= ?")
            params.append(time__gte)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM message WHERE {where_clause} ORDER BY time DESC"

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        return [cls._from_row(row) for row in rows]

    @classmethod
    def _from_row(cls, row) -> "ChatMessage":
        """从数据库行创建对象"""
        return cls(
            id=row["id"],
            group_id=row["group_id"],
            user_id=row["user_id"],
            message_id=row["message_id"],
            message=row["message"],
            raw_message=row["raw_message"],
            plain_text=row["plain_text"],
            time=row["time"],
        )


class ChatContext:
    """聊天上下文模型"""

    def __init__(
        self,
        keywords: str,
        time: int,
        count: int = 1,
        id: int = None,
    ):
        self.id = id
        self.keywords = keywords
        self.time = time
        self.count = count

    async def save(self):
        """保存到数据库"""
        db = db_manager.db
        if self.id is None:
            cursor = await db.execute(
                "INSERT INTO context (keywords, time, count) VALUES (?, ?, ?)",
                (self.keywords, self.time, self.count),
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                "UPDATE context SET keywords=?, time=?, count=? WHERE id=?",
                (self.keywords, self.time, self.count, self.id),
            )
        await db.commit()

    @classmethod
    async def create(cls, keywords: str, time: int) -> "ChatContext":
        """创建新上下文"""
        context = cls(keywords=keywords, time=time)
        await context.save()
        return context

    @classmethod
    async def filter(
        cls,
        keywords: str = None,
        count__gte: int = None,
    ) -> List["ChatContext"]:
        """查询上下文"""
        db = db_manager.db
        conditions = []
        params = []

        if keywords is not None:
            conditions.append("keywords = ?")
            params.append(keywords)
        if count__gte is not None:
            conditions.append("count >= ?")
            params.append(count__gte)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM context WHERE {where_clause} ORDER BY time DESC"

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        return [cls._from_row(row) for row in rows]

    @classmethod
    def _from_row(cls, row) -> "ChatContext":
        """从数据库行创建对象"""
        return cls(
            id=row["id"],
            keywords=row["keywords"],
            time=row["time"],
            count=row["count"],
        )


class ChatAnswer:
    """聊天回答模型"""

    def __init__(
        self,
        keywords: str,
        group_id: int,
        time: int,
        messages: List[str] = None,
        count: int = 1,
        context_id: int = None,
        id: int = None,
    ):
        self.id = id
        self.keywords = keywords
        self.group_id = group_id
        self.time = time
        self.messages = messages or []
        self.count = count
        self.context_id = context_id

    async def save(self):
        """保存到数据库"""
        db = db_manager.db
        messages_json = json.dumps(self.messages, ensure_ascii=False)

        if self.id is None:
            cursor = await db.execute(
                """INSERT INTO answer 
                   (keywords, group_id, count, time, messages, context_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    self.keywords,
                    self.group_id,
                    self.count,
                    self.time,
                    messages_json,
                    self.context_id,
                ),
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                """UPDATE answer SET 
                   keywords=?, group_id=?, count=?, time=?, messages=?, context_id=?
                   WHERE id=?""",
                (
                    self.keywords,
                    self.group_id,
                    self.count,
                    self.time,
                    messages_json,
                    self.context_id,
                    self.id,
                ),
            )
        await db.commit()

    @classmethod
    async def create(
        cls,
        keywords: str,
        group_id: int,
        time: int,
        context: ChatContext,
        messages: List[str] = None,
    ) -> "ChatAnswer":
        """创建新回答"""
        answer = cls(
            keywords=keywords,
            group_id=group_id,
            time=time,
            messages=messages or [],
            context_id=context.id,
        )
        await answer.save()
        return answer

    @classmethod
    async def filter(
        cls,
        keywords: str = None,
        group_id: int = None,
        context: ChatContext = None,
        count__gte: int = None,
        keywords__in: List[str] = None,
    ) -> List["ChatAnswer"]:
        """查询回答"""
        db = db_manager.db
        conditions = []
        params = []

        if keywords is not None:
            conditions.append("keywords = ?")
            params.append(keywords)
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        if context is not None:
            conditions.append("context_id = ?")
            params.append(context.id)
        if count__gte is not None:
            conditions.append("count >= ?")
            params.append(count__gte)
        if keywords__in is not None and keywords__in:
            placeholders = ",".join(["?" for _ in keywords__in])
            conditions.append(f"keywords IN ({placeholders})")
            params.extend(keywords__in)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM answer WHERE {where_clause} ORDER BY time DESC"

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        return [cls._from_row(row) for row in rows]

    @classmethod
    async def delete_by_keywords(cls, keywords: str, group_id: int = None):
        """删除指定关键词的回答"""
        db = db_manager.db
        if group_id is not None:
            await db.execute(
                "DELETE FROM answer WHERE keywords = ? AND group_id = ?",
                (keywords, group_id),
            )
        else:
            await db.execute("DELETE FROM answer WHERE keywords = ?", (keywords,))
        await db.commit()

    @classmethod
    async def get_cross_group_keywords(cls, threshold: int) -> List[str]:
        """获取跨群回复的关键词列表"""
        db = db_manager.db
        cursor = await db.execute(
            """SELECT keywords FROM answer 
               GROUP BY keywords 
               HAVING COUNT(DISTINCT group_id) >= ?""",
            (threshold,),
        )
        rows = await cursor.fetchall()
        return [row["keywords"] for row in rows]

    @classmethod
    def _from_row(cls, row) -> "ChatAnswer":
        """从数据库行创建对象"""
        messages = json.loads(row["messages"]) if row["messages"] else []
        return cls(
            id=row["id"],
            keywords=row["keywords"],
            group_id=row["group_id"],
            time=row["time"],
            messages=messages,
            count=row["count"],
            context_id=row["context_id"],
        )


class ChatBlackList:
    """聊天黑名单模型"""

    def __init__(
        self,
        keywords: str,
        global_ban: bool = False,
        ban_group_id: List[int] = None,
        id: int = None,
    ):
        self.id = id
        self.keywords = keywords
        self.global_ban = global_ban
        self.ban_group_id = ban_group_id or []

    async def save(self):
        """保存到数据库"""
        db = db_manager.db
        ban_group_json = json.dumps(self.ban_group_id)

        if self.id is None:
            cursor = await db.execute(
                """INSERT INTO blacklist 
                   (keywords, global_ban, ban_group_id)
                   VALUES (?, ?, ?)""",
                (self.keywords, int(self.global_ban), ban_group_json),
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                """UPDATE blacklist SET 
                   keywords=?, global_ban=?, ban_group_id=?
                   WHERE id=?""",
                (self.keywords, int(self.global_ban), ban_group_json, self.id),
            )
        await db.commit()

    @classmethod
    async def filter(cls, keywords: str = None) -> List["ChatBlackList"]:
        """查询黑名单"""
        db = db_manager.db
        if keywords is not None:
            cursor = await db.execute(
                "SELECT * FROM blacklist WHERE keywords = ?", (keywords,)
            )
        else:
            cursor = await db.execute("SELECT * FROM blacklist")

        rows = await cursor.fetchall()
        return [cls._from_row(row) for row in rows]

    @classmethod
    def _from_row(cls, row) -> "ChatBlackList":
        """从数据库行创建对象"""
        ban_group_id = json.loads(row["ban_group_id"]) if row["ban_group_id"] else []
        return cls(
            id=row["id"],
            keywords=row["keywords"],
            global_ban=bool(row["global_ban"]),
            ban_group_id=ban_group_id,
        )
