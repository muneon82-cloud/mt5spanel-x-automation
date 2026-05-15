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
from openai import OpenAI
from requests_oauthlib import OAuth1


HISTORY_LIMIT = 30
MAX_RETRIES = 5
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
    return random.choice(choices or NORMAL_TYPES), False


def build_prompt(
    post_type: str,
    is_promo: bool,
    promo_url: str | None,
    topics: list[str],
    recent_history: list[dict[str, Any]],
    retry_errors: list[str] | None = None,
) -> str:
    recent_texts = [str(item.get("text", "")) for item in recent_history]
    errors = "\n".join(f"- {error}" for error in (retry_errors or [])) or "- なし"
    promo_line = (
        f"今回は宣伝投稿。URLは必ず本文末尾に自然に1つだけ入れる: {promo_url}"
        if is_promo
        else "今回は通常投稿。URLは入れない。"
    )

    return textwrap.dedent(
        f"""
        MT5SPanelの開発者本人として、日本語のX投稿を1件だけ作成してください。

        投稿タイプ: {post_type}
        {promo_line}

        必須条件:
        - 文字数はURL込みで120文字前後、許容範囲は80〜150文字
        - 人間の個人開発者が手で書いたような自然な文
        - 宣伝臭を弱くし、MT5/FXの話題を主役にする
        - 最近話題の候補を軽く混ぜる。無理にニュース風にしない
        - 毎回同じ文体にしない
        - 指示語を減らし、具体名や状況を書く
        - ハッシュタグは0個か1個まで
        - 「高速」「爆速」「革命」「次世代」「圧倒的」「最強」「完全自動」「誰でも簡単」は禁止
        - 「できます」を連打しない。使うなら最大1回
        - 同じ語尾の文を連続させない
        - 過去30投稿と似た内容、同じ冒頭表現を避ける

        最近話題の候補:
        {json.dumps(topics, ensure_ascii=False)}

        過去30投稿:
        {json.dumps(recent_texts[-HISTORY_LIMIT:], ensure_ascii=False)}

        前回の検査エラー:
        {errors}

        JSONのみで返してください:
        {{"text":"投稿本文","topic_hint":"今回混ぜた話題を短く"}}
        """
    ).strip()


def parse_json_output(output: str) -> dict[str, Any]:
    output = output.strip()
    if output.startswith("```"):
        output = re.sub(r"^```(?:json)?\s*", "", output)
        output = re.sub(r"\s*```$", "", output)
    match = re.search(r"\{.*\}", output, flags=re.DOTALL)
    if not match:
        raise ValueError("JSON object not found in model output")
    return json.loads(match.group(0))


def generate_candidate(client: OpenAI, history: list[dict[str, Any]]) -> Candidate:
    post_type, is_promo = choose_post_type(history)
    promo_url = random.choice(PROMO_URLS) if is_promo else None
    topics = fetch_recent_topics()
    recent_history = history[-HISTORY_LIMIT:]
    retry_errors: list[str] = []
    model = os.getenv("OPENAI_MODEL", "gpt-5.1")

    for attempt in range(1, MAX_RETRIES + 1):
        prompt = build_prompt(post_type, is_promo, promo_url, topics, recent_history, retry_errors)
        response = client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=500,
        )
        data = parse_json_output(response.output_text)
        text = str(data.get("text", "")).strip()

        if is_promo and promo_url and promo_url not in text:
            text = f"{text.rstrip()} {promo_url}"

        candidate = Candidate(
            text=text,
            post_type=post_type,
            is_promo=is_promo,
            topic_hint=str(data.get("topic_hint", "")).strip(),
        )
        errors = validate_candidate(candidate, recent_history)
        if not errors:
            return candidate
        retry_errors = errors
        time.sleep(1 + attempt)

    raise RuntimeError(f"投稿文の検査を通過できませんでした: {retry_errors}")


def post_to_x(text: str) -> str:
    required = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"X API credentials are missing: {', '.join(missing)}")

    auth = OAuth1(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    response = requests.post(
        "https://api.x.com/2/tweets",
        auth=auth,
        json={"text": text},
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"X API error {response.status_code}: {response.text}")
    data = response.json()
    return str(data.get("data", {}).get("id", ""))


def append_history(
    history: list[dict[str, Any]],
    candidate: Candidate,
    dry_run: bool,
    tweet_id: str | None,
) -> list[dict[str, Any]]:
    history.append(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "text": candidate.text,
            "post_type": candidate.post_type,
            "is_promo": candidate.is_promo,
            "topic_hint": candidate.topic_hint,
            "tweet_id": tweet_id,
            "dry_run": dry_run,
            "char_count": len(candidate.text),
        }
    )
    return history


def main() -> int:
    load_dotenv(".env.local")
    load_dotenv()

    history_path = Path(os.getenv("POST_HISTORY_PATH", "data/post_history.json"))
    dry_run = env_bool("DRY_RUN")

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is missing.", file=sys.stderr)
        return 2

    history = load_history(history_path)
    client = OpenAI()
    candidate = generate_candidate(client, history)

    tweet_id = None
    if dry_run:
        print(candidate.text)
    else:
        tweet_id = post_to_x(candidate.text)
        print(f"Posted to X: {tweet_id}")
        history = append_history(history, candidate, dry_run, tweet_id)
        save_history(history_path, history)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
