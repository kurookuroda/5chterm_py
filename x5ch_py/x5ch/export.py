"""アーカイブ用エクスポート。Crystal版 fivechbrowser/export.cr に対応。"""
from __future__ import annotations

import html as html_mod
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from selectolax.parser import HTMLParser

from .parse import H_RESTORE_PATTERN, HTML_TAG_PATTERN, inner_html, post_nodes

MAIL_LINK_PATTERN = re.compile(
    r'<a\s+[^>]*href="/cdn-cgi/l/email-protection#([0-9a-fA-F]+)"[^>]*>(.*?)</a>'
)
REPLY_LINK_TAG_PATTERN = re.compile(r'<a[^>]*class="reply_link"[^>]*>')
HREF_NUM_PATTERN = re.compile(r'href="[^"]*?/(\d+)"')
POSTED_AT_PATTERN = re.compile(
    r"^(\d{4})/(\d{2})/(\d{2})\([月火水木金土日]\)\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
)
LD_JSON_PATTERN = re.compile(r'<script\s+type="application/ld\+json">(.*?)</script>', re.DOTALL)

JST = timezone(timedelta(hours=9))


@dataclass
class ExportPost:
    external_id: str
    num: int
    author_name_display: str
    user_id: str
    posted_at_raw: str
    body_raw: str
    body_display: str
    body_html_original: str
    mail_encoded: str | None = None
    mail_decoded: str | None = None
    posted_at: str | None = None
    reply_to: list[int] | None = None


@dataclass
class ExportThread:
    external_id: str
    title: str
    post_count: int
    board_name: str | None = None
    created_at: str | None = None


@dataclass
class ExportSource:
    provider: str
    board_url: str
    dat_file: str
    thread_url: str
    scraped_at: str


@dataclass
class ExportResult:
    source: ExportSource
    thread: ExportThread
    posts: list[ExportPost] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_pretty_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        """このJSON構造(source/thread/posts)にできるだけ対応させたMarkdown表現を作る。"""
        lines: list[str] = []
        lines.append(f"# {self.thread.title}\n")
        lines.append(f"- 板: {self.thread.board_name or '(不明)'}")
        lines.append(f"- board_url: {self.source.board_url}")
        lines.append(f"- thread_url: {self.source.thread_url}")
        lines.append(f"- dat_file: {self.source.dat_file}")
        lines.append(f"- external_id: {self.thread.external_id}")
        lines.append(f"- 作成日時: {self.thread.created_at or '(不明)'}")
        lines.append(f"- 取得日時: {self.source.scraped_at}")
        lines.append(f"- レス数(全体): {self.thread.post_count}")
        lines.append(f"- レス数(このファイル): {len(self.posts)}")
        lines.append("\n---\n")

        for p in self.posts:
            header = f"## {p.num} {p.author_name_display}"
            if p.user_id:
                header += f" ID:{p.user_id}"
            header += f" {p.posted_at or p.posted_at_raw}"
            lines.append(header + "\n")
            if p.reply_to:
                lines.append(f"> Reply to: {', '.join(str(n) for n in p.reply_to)}\n")
            lines.append(p.body_display + "\n")
            lines.append("---\n")

        return "\n".join(lines)


def decode_cf_email(hex_str: str) -> str | None:
    """CloudflareのメールXOR難読化(cdn-cgi/l/email-protection)をデコードする。"""
    if len(hex_str) < 2 or len(hex_str) % 2 != 0:
        return None
    try:
        key = int(hex_str[0:2], 16)
        raw = bytes(
            int(hex_str[i : i + 2], 16) ^ key for i in range(2, len(hex_str), 2)
        )
    except ValueError:
        return None
    return raw.decode("utf-8", errors="replace")


def extract_reply_to(content_html: str) -> list[int]:
    """本文HTML中の class="reply_link" アンカーから返信先レス番号を抽出する。"""
    result: list[int] = []
    for tag_match in REPLY_LINK_TAG_PATTERN.finditer(content_html):
        m = HREF_NUM_PATTERN.search(tag_match.group(0))
        if m:
            result.append(int(m.group(1)))
    return result


def parse_posted_at(raw: str) -> str | None:
    """"2025/12/16(火) 05:05:09.95" 形式をISO8601(JST、ナノ秒精度相当)に変換する。"""
    m = POSTED_AT_PATTERN.match(raw)
    if not m:
        return None

    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    frac = m.group(7)
    microsecond = 0
    if frac:
        microsecond = int((frac.ljust(6, "0"))[:6])

    try:
        dt = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=JST)
    except ValueError:
        return None

    return format_rfc3339_nano(dt, frac)


def format_rfc3339_nano(dt: datetime, frac_digits: str | None) -> str:
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    frac_part = ""
    if frac_digits:
        trimmed = frac_digits.rstrip("0")
        if trimmed:
            frac_part = f".{trimmed}"
    offset = dt.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "-" if total_minutes < 0 else "+"
    total_minutes = abs(total_minutes)
    oh, om = divmod(total_minutes, 60)
    return f"{base}{frac_part}{sign}{oh:02d}:{om:02d}"


def extract_plain_text(content_html: str) -> str:
    text = content_html.replace("<br>", "\n")
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = html_mod.unescape(text)
    return text.strip()


def parse_posts_for_export(html_content: str, thread_external_id: str) -> list[ExportPost]:
    """スレッドHTMLをアーカイブ用の完全な構造でパースする。"""
    tree = HTMLParser(html_content)
    posts: list[ExportPost] = []

    for node in post_nodes(tree):
        id_node = node.css_first("span.postid")
        if id_node is None:
            continue
        try:
            num = int(id_node.text(strip=True))
        except ValueError:
            continue

        author_display = "名無し"
        mail_encoded: str | None = None
        mail_decoded: str | None = None

        name_node = node.css_first("span.postusername")
        if name_node is not None:
            raw_name = inner_html(name_node)
            mm = MAIL_LINK_PATTERN.search(raw_name)
            if mm:
                mail_encoded = mm.group(1)
                author_display = HTML_TAG_PATTERN.sub("", mm.group(2)).strip()
                mail_decoded = decode_cf_email(mail_encoded)
            else:
                author_display = HTML_TAG_PATTERN.sub("", raw_name).strip() or author_display

        posted_at_raw = ""
        date_node = node.css_first("span.date")
        if date_node is not None:
            posted_at_raw = date_node.text(strip=True)

        user_id = ""
        uid_node = node.css_first("span.uid")
        if uid_node is not None:
            uid_text = uid_node.text(strip=True)
            user_id = re.sub(r"^ID:", "", uid_text).strip()

        content_node = node.css_first("div.post-content")
        body_html = inner_html(content_node)

        reply_to = extract_reply_to(body_html)
        body_raw = extract_plain_text(body_html)
        body_display = H_RESTORE_PATTERN.sub(lambda m: f"h{m.group(0)}", body_raw)

        posts.append(
            ExportPost(
                external_id=f"{thread_external_id}#{num}",
                num=num,
                author_name_display=author_display,
                mail_encoded=mail_encoded or None,
                mail_decoded=mail_decoded or None,
                user_id=user_id,
                posted_at=parse_posted_at(posted_at_raw),
                posted_at_raw=posted_at_raw,
                reply_to=reply_to or None,
                body_raw=body_raw,
                body_display=body_display,
                body_html_original=body_html.strip(),
            )
        )

    return posts


def extract_board_name(full_html: str) -> str:
    """ページ全体のHTMLからJSON-LDパンくずリストの板名(position=2)を抽出する。"""
    m = LD_JSON_PATTERN.search(full_html)
    if not m:
        return ""

    try:
        raw_items = json.loads(m.group(1))
    except json.JSONDecodeError:
        return ""

    if not isinstance(raw_items, list):
        return ""

    for item in raw_items:
        if not isinstance(item, dict) or item.get("@type") != "BreadcrumbList":
            continue
        for el in item.get("itemListElement") or []:
            if not isinstance(el, dict):
                continue
            if el.get("position") == 2 and el.get("name"):
                return el["name"]

    return ""


def dat_timestamp_to_rfc3339(dat_file: str) -> str:
    """dat ファイル名(スレ立て時刻のUNIXタイムスタンプ)をISO8601(UTC)に変換する。"""
    ts_str = re.sub(r"\.dat$", "", dat_file)
    try:
        ts = int(ts_str)
    except ValueError:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
