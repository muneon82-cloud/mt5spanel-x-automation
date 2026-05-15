import json
import os
import random
import re
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import requests
from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from requests_oauthlib import OAuth1


HISTORY_LIMIT = 30
MAX_RETRIES = 5
API_RETRIES = 4
PROMO_RATE = 0.2
MIN_LENGTH = 80
MAX_LENGTH = 150
SIMILARITY_LIMIT = 0.55

PROMO_URLS = [
    "https://note.com/rosy_carp7757/n/n6b24af9946f3",
    "https://umapaka.booth.pm/items/8362889",
]

NORMAL_TYPES = [
    "MT5不便あるある",
    "スキャルピング話題",
    "開発進捗",
    "UI改善",
    "EA高速化",
    "トレード環境",
    "軽い雑談",
]

BANNED_WORDS = [
    "高速",
    "爆速",
    "革命",
    "AIっぽい",
    "次世代",
    "圧倒的",
    "最強",
    "完全自動",
    "誰でも簡単",
]

HASH_TAG_RE = re.compile(r"(?:^|\s)#\S+")
URL_RE = re.compile(r"https?://\S+")


@dataclass
class Candidate:
    text: str
    post_type: str
    is_promo: bool
    topic_hint: str


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return data


def save_history(path: Path, history: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(history[-300:], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def post_start(text: str) -> str:
    clean = URL_RE.sub("", text).strip()
    return clean[:12]


def sentence_endings(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[。！？\n]+", URL_RE.sub("", text)) if p.strip()]
    endings = []
    for part in parts:
        endings.append(part[-4:] if len(part) >= 4 else part)
    return endings


def has_repeated_endings(text: str) -> bool:
    endings = sentence_endings(text)
    return any(a == b for a, b in zip(endings, endings[1:]))


def count_dekimasu(text: str) -> int:
    return text.count("できます")


def normalize_for_similarity(text: str) -> str:
    text = URL_RE.sub("", text)
    text = HASH_TAG_RE.sub("", text)
    return re.sub(r"\s+", "", text)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_for_similarity(a), normalize_for_similarity(b)).ratio()


def validate_candidate(candidate: Candidate, recent_history: list[dict[str, Any]]) -> list[str]:
    text = candidate.text.strip()
    errors = []

    if not (MIN_LENGTH <= len(text) <= MAX_LENGTH):
        errors.append(f"文字数が範囲外: {len(text)}")
    for word in BANNED_WORDS:
        if word in text:
            errors.append(f"禁止語を含む: {word}")
    if count_dekimasu(text) > 1:
        errors.append("「できます」が多い")
    if len(HASH_TAG_RE.findall(text)) > 1:
        errors.append("ハッシュタグが多い")
    if has_repeated_endings(text):
        errors.append("連続する文の語尾が似ている")

    urls = URL_RE.findall(text)
    if candidate.is_promo:
        if not any(url in text for url in PROMO_URLS):
            errors.append("宣伝投稿にURLがない")
    elif urls:
        errors.append("通常投稿にURLがある")

    current_start = post_start(text)
    for item in recent_history:
        old_text = str(item.get("text", ""))
        if current_start and current_start == post_start(old_text):
            errors.append("過去投稿と冒頭表現が同じ")
            break

    for item in recent_history:
        old_text = str(item.get("text", ""))
        if old_text and similarity(text, old_text) >= SIMILARITY_LIMIT:
            errors.append("過去30投稿と内容が近い")
            break

    return errors


def fetch_recent_topics(limit: int = 5) -> list[str]:
    query = quote("MT5 OR MetaTrader 5 OR FX")
    url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    request = Request(url, headers={"User-Agent": "mt5spanel-x-automation/1.0"})
    try:
        with urlopen(request, timeout=8) as response:
            xml = response.read()
        root = ElementTree.fromstring(xml)
        titles = []
        for item in root.findall(".//item"):
            title_node = item.find("title")
            if title_node is not None and title_node.text:
                title = re.sub(r"\s+-\s+[^-]+$", "", title_node.text).strip()
                if title and title not in titles:
                    titles.append(title)
            if len(titles) >= limit:
                break
        return titles
    except Exception:
        return [
            "ドル円や米金利の動きで短期売買の判断が難しい",
            "MetaTrader 5のアップデート後に細かい操作感を見直す人がいる",
            "EA運用では約定、ログ、チャート表示の整理が話題になりやすい",
        ]


def choose_post_type(history: list[dict[str, Any]]) -> tuple[str, bool]:
    recent_types = [str(item.get("post_type", "")) for item in history[-5:]]
    is_promo = random.random() < PROMO_RATE
    if is_promo:
        return "たまに宣伝", True

    choices = [name for name in NORMAL_TYPES if name not in recent_types[-2:]]
