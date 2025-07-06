import httpx

from PIL import Image
from io import BytesIO
from nsfwpy import NSFW
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment
from ncatbot.core.element import (
    MessageChain,
    Text,
    At,
)

_log = get_log()

_nc = None
_nsfw_config = None


def set_nsfw_config(config):
    """设置NSFW配置实例"""
    global _nsfw_config
    _nsfw_config = config


def refresh_nsfw_config(bot_plugin):
    """刷新NSFW配置"""
    global _nsfw_config
    _nsfw_config = bot_plugin.NSFW_CONFIG
    reset_nc()  # 重置NSFW实例以应用新配置


def reset_nc():
    """重置 NSFW 实例"""
    global _nc
    _nc = None


def get_nc():
    """延迟初始化 NSFW 实例"""
    global _nc, _nsfw_config
    if _nc is None and _nsfw_config is not None:
        _nc = NSFW(model_type=_nsfw_config.nsfwpy_type)
    return _nc


def is_dangerous_content(check_result, threshold=None):
    """
    判断NSFW检测结果是否属于高危内容，请根据实际场景来修改检测逻辑
    """
    global _nsfw_config
    if threshold is None and _nsfw_config is not None:
        threshold = (
            _nsfw_config.get_group_threshold(_nsfw_config._current_group_id)
            if hasattr(_nsfw_config, "_current_group_id")
            else _nsfw_config.threshold
        )
    elif threshold is None:
        threshold = 0.85

    dangerous_categories = ["porn", "hentai", "sexy"]
    # 获取危险类别中的最大概率值
    max_prob = max(float(check_result.get(cat, "0")) for cat in dangerous_categories)

    if max_prob >= threshold:
        return True, f"max_prob:{max_prob:.2%}"
    return False, f"max_prob:{max_prob:.2%}"


async def nsfwc(message: GroupMessage, bot_plugin) -> None:
    global _nsfw_config
    if _nsfw_config is None:
        _nsfw_config = bot_plugin.NSFW_CONFIG

    # 设置当前群组ID以便获取正确的阈值
    _nsfw_config._current_group_id = message.group_id

    for msg in message.message:
        if msg["type"] == "image":
            file_url = f"http{msg['data']['url'][5::]}"
            try:
                async with httpx.AsyncClient(
                    verify=False, http2=False, trust_env=False, timeout=3
                ) as client:
                    response = await client.get(file_url)
                    if response.status_code == 200:
                        image = Image.open(BytesIO(response.content))
                        check_result = await get_nc().predict_pil_image_async(image)

                        # 使用群组特定的阈值
                        group_threshold = _nsfw_config.get_group_threshold(
                            message.group_id
                        )
                        is_dangerous, reason = is_dangerous_content(
                            check_result, group_threshold
                        )
                        if is_dangerous:
                            # 构建详细的检测结果信息
                            porn_prob = float(check_result.get("porn", "0")) * 100
                            hentai_prob = float(check_result.get("hentai", "0")) * 100
                            sexy_prob = float(check_result.get("sexy", "0")) * 100
                            max_prob = max(porn_prob, hentai_prob, sexy_prob)

                            # 获取模型类型
                            model_type = (
                                _nsfw_config.nsfwpy_type if _nsfw_config else "unknown"
                            )

                            # 构建消息内容
                            content = [
                                Text("🚨 发现危险内容\n"),
                                Text(
                                    f"检测结果：max_prob:{max_prob:.2f}% (porn:{porn_prob:.2f}%, hentai:{hentai_prob:.2f}%, sexy:{sexy_prob:.2f}%)\n"
                                ),
                                Text(f"使用模型：{model_type}\n"),
                                Text(f"检测阈值：{group_threshold}\n"),
                                Text("通知管理员："),
                            ]

                            notifiers = _nsfw_config.get_group_notifiers(
                                message.group_id
                            )
                            for notifier in notifiers:
                                at = At(notifier)
                                content.append(at)

                            _log.warning(
                                f"检测到危险内容：{reason}, group_id: {message.group_id}, user_id: {message.user_id}, file_url: {file_url}"
                            )
                            await bot_plugin.api.post_group_msg(
                                message.group_id,
                                reply=message.message_id,
                                rtf=MessageChain(content),
                            )
                        else:
                            _log.info(
                                f"NSFW Check Passed: group_id: {message.group_id}, user_id: {message.user_id}, exponent: {reason}"
                            )
            except Exception as e:
                _log.error(
                    f"NSFW Check Error: {e}, group_id: {message.group_id}, user_id: {message.user_id}, file_url: {file_url}"
                )
