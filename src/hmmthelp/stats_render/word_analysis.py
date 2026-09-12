import re
from collections import Counter
from functools import lru_cache
from typing import Dict, Tuple

STOP_WORDS = {"一个", "什么", "怎么", "这个", "那个", "这样", "那样"}

POS_NAMES = {
    "n": "名词",
    "v": "动词",
    "a": "形容词",
    "d": "副词",
    "p": "介词",
    "c": "连词",
    "u": "助词",
    "m": "数词",
    "q": "量词",
    "r": "代词",
    "e": "叹词",
    "o": "拟声词",
    "i": "成语",
    "j": "简称",
    "l": "习语",
    "x": "其他",
}


@lru_cache(maxsize=4096)
def extract_words_with_pos(text: str) -> Tuple[Tuple[str, str], ...]:
    text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s]+", "", text)
    if not text.strip():
        return ()
    try:
        import jieba.posseg as pseg
    except ImportError:
        return ()
    words_with_pos = pseg.cut(text)
    return tuple(
        (word.strip(), POS_NAMES.get(flag[0] if flag else "x", "其他"))
        for word, flag in words_with_pos
        if len(word.strip()) >= 2 and word.lower() not in STOP_WORDS
    )


def process_message_text(text: str) -> Tuple[Counter, Counter, int]:
    """返回 (词频, 词性频, 字符数)。"""
    word_counter: Counter = Counter()
    pos_counter: Counter = Counter()
    char_count = len(text.strip())
    if text.strip().startswith("/"):
        return word_counter, pos_counter, 0
    for word, pos in extract_words_with_pos(text):
        word_counter[word] += 1
        pos_counter[pos] += 1
    return word_counter, pos_counter, char_count


def aggregate_word_stats(
    daily_word_counts: Dict[str, Dict[str, int]] | None,
    daily_pos_counts: Dict[str, Dict[str, int]] | None,
    date_keys: set[str],
) -> Tuple[Counter, Counter]:
    words: Counter = Counter()
    pos: Counter = Counter()
    if daily_word_counts:
        for d in date_keys:
            words.update(daily_word_counts.get(d, {}))
    if daily_pos_counts:
        for d in date_keys:
            pos.update(daily_pos_counts.get(d, {}))
    return words, pos
