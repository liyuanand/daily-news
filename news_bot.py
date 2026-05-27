#!/usr/bin/env python3
"""
AI HOT 双轨 PDF 新闻推送机器人
- --mode interval：每 2 小时抓取过去 2 小时的 AI 动态
- --mode daily：抓取当天 AI 日报
- 生成精美中文 PDF 供邮件发送
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import requests
from weasyprint import HTML

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ====== 常量 ======

AIHOT_BASE = "https://aihot.virxact.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 aihot-skill/0.2.0"
)

CATEGORY_LABEL = {
    "ai-models": "模型发布/更新",
    "ai-products": "产品发布/更新",
    "industry": "行业动态",
    "paper": "论文研究",
    "tip": "技巧与观点",
}

BJT = timezone(timedelta(hours=8))
PDF_FILENAME = "AI_Report.pdf"

# ====== 高亮关键词 ======

POLICY_WORDS = ["芯片", "半导体", "国务院", "降准", "红头文件"]
AI_WORDS = ["OpenAI", "Sora", "Agent", "智能体", "大模型"]

# ====== API 获取 ======


def fetch_interval() -> List[Dict[str, Any]]:
    """获取过去 2 小时的全部 AI 动态"""
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    url = f"{AIHOT_BASE}/api/public/items?mode=all&since={since}&take=100"
    logger.info("请求 interval API: %s", url)
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    logger.info("获取到 %d 条动态", len(items))
    return items


def fetch_daily() -> Dict[str, Any]:
    """获取当天 AI 日报（北京 08:00 还未生成时自动降级到昨天）"""
    url = f"{AIHOT_BASE}/api/public/daily"
    logger.info("请求 daily API: %s", url)
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    if resp.status_code == 404:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        fallback = f"{AIHOT_BASE}/api/public/daily/{yesterday}"
        logger.warning("当日日报尚未生成，降级到: %s", fallback)
        resp = requests.get(fallback, headers={"User-Agent": UA}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    logger.info(
        "日报日期: %s, 板块: %d, 快讯: %d",
        data.get("date"),
        len(data.get("sections", [])),
        len(data.get("flashes", [])),
    )
    return data


def fetch_finance_flash() -> List[Dict[str, str]]:
    """获取国内政策与股市快讯（华尔街见闻 7x24 公开 API，无需鉴权）"""
    url = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50"
    logger.info("请求华尔街见闻快讯: %s", url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    items: List[Dict[str, str]] = []

    for item in raw.get("data", {}).get("items", []):
        display_time = item.get("display_time", 0)
        if display_time < cutoff_ts:
            continue

        content_text = (item.get("content_text") or "").strip()
        if not content_text:
            continue

        # 取第一段作为标题
        title = content_text[:80].rsplit(" ", 1)[0] + "…" if len(content_text) > 80 else content_text

        dt = datetime.fromtimestamp(display_time, tz=timezone.utc)
        published = dt.astimezone(BJT).strftime("%m/%d %H:%M")

        items.append({
            "source": "华尔街见闻",
            "title": title,
            "link": "https://wallstreetcn.com/live/global",
            "summary": content_text,
            "publishedAt": published,
        })

    logger.info("华尔街见闻快讯: 获取到 %d 条", len(items))
    return items


# ====== 时间格式化 ======


def fmt_time(iso: str) -> str:
    """ISO 8601 UTC → 北京时间短格式"""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(BJT).strftime("%m/%d %H:%M")
    except Exception:
        return iso


# ====== 关键词高亮 ======


def highlight_text(text: str) -> str:
    """在纯文本中对 POLICY_WORDS / AI_WORDS 做 <span> 高亮标记"""
    if not text:
        return ""
    for w in POLICY_WORDS:
        text = text.replace(w, f'<span class="highlight-keyword">{w}</span>')
    for w in AI_WORDS:
        text = text.replace(w, f'<span class="highlight-tech">{w}</span>')
    return text


# ====== HTML → PDF ======

HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  @page { size: A4; margin: 20mm 16mm; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: Georgia, 'SimSun', 'Songti SC', 'Noto Serif SC', serif;
    font-size: 10pt; line-height: 1.5; color: #1a1a1a; background: #FCFBF9;
  }

  /* ===== WSJ 报头 ===== */
  .masthead { text-align: center; margin-bottom: 16px; }
  .masthead-rule { border-top: 3px double #111; height: 5px; margin: 6px 0; }
  .masthead-title {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 24pt; font-weight: 700; color: #111;
    letter-spacing: 2.5px; margin: 6px 0 2px;
  }
  .masthead-sub {
    font-size: 9pt; color: #666; font-family: Georgia, serif;
    font-style: italic; letter-spacing: 1px;
  }

  /* ===== 主编导语 ===== */
  .lead {
    background: #f5f3f0; border-left: 3px solid #A82315;
    padding: 10px 14px; margin-bottom: 16px;
    font-size: 9.5pt; line-height: 1.6; color: #333;
    font-style: italic; font-family: Georgia, serif;
  }

  /* ===== 板块标题（酒红色） ===== */
  .section-title {
    font-size: 11.5pt; font-weight: 700; color: #A82315;
    border-bottom: 1px solid #d5d0c8; padding-bottom: 3px;
    margin-top: 16px; margin-bottom: 8px;
    font-family: Georgia, 'SimSun', serif;
  }

  /* ===== 新闻卡片（极简报纸风） ===== */
  .card {
    background: transparent; border: none;
    border-bottom: 1px solid #ece8e2; padding: 7px 0; margin: 0;
    page-break-inside: avoid;
  }
  .card-title { font-size: 10.5pt; font-weight: 600; color: #1a1a1a; margin-bottom: 1px; }
  .card-title a { color: #1a1a1a; text-decoration: none; }
  .card-meta {
    font-size: 8pt; color: #999; font-family: Georgia, serif;
    font-style: italic; margin-bottom: 2px;
  }
  .card-summary { font-size: 9pt; color: #3a3a3a; line-height: 1.55; }

  /* ===== 快讯 ===== */
  .flash-item {
    font-size: 9pt; color: #3a3a3a;
    padding: 3px 0; border-bottom: 1px dashed #d5d0c8;
  }

  /* ===== 高亮标记 ===== */
  .highlight-keyword {
    background: #fce8e6; color: #A82315; font-weight: 700;
    padding: 0 2px; font-style: normal;
  }
  .highlight-tech {
    background: #e3f0ff; color: #1a5c9e; font-weight: 700;
    padding: 0 2px; font-style: normal;
  }

  .empty { color: #aaa; font-size: 10pt; padding: 12px 0; font-style: italic; }

  /* ===== 页脚 ===== */
  .footer {
    margin-top: 20px; padding-top: 8px; border-top: 1px solid #d5d0c8;
    text-align: center; font-size: 8pt; color: #999;
    font-family: Georgia, serif; font-style: italic;
  }
</style>
</head>
<body>
"""

HTML_TAIL = """<div class="footer">
  The Personal Decision Briefing &nbsp;·&nbsp; Generated by AI HOT &nbsp;·&nbsp; aihot.virxact.com
</div>
</body>
</html>"""


def _build_cards(items: List[Dict], *, show_cat_label: bool = False) -> str:
    """构建一组卡片 HTML（含自动关键词高亮）"""
    html = ""
    for it in items:
        title = it.get("title") or "无标题"
        url = it.get("url") or it.get("sourceUrl", "")
        source = it.get("source") or it.get("sourceName", "")
        summary = it.get("summary", "") or ""
        published = fmt_time(it.get("publishedAt", ""))
        cat = it.get("category", "")
        meta_parts = [s for s in [source, published, CATEGORY_LABEL.get(cat, "")] if s]
        meta = " · ".join(meta_parts) if meta_parts else ""
        html += f"""<div class="card">
<div class="card-title"><a href="{url}">{highlight_text(title)}</a></div>
<div class="card-meta">{meta}</div>
<div class="card-summary">{highlight_text(summary)}</div>
</div>
"""
    return html


def generate_pdf_interval(
    items: List[Dict[str, Any]],
    cls_items: List[Dict[str, str]] = None,
) -> str:
    """生成 interval 模式 PDF"""
    now = datetime.now(BJT)
    ts = now.strftime("%Y-%m-%d %H:%M")
    total = len(items) + len(cls_items or [])

    # AIHOT — 按 category 分组
    body = ""
    grouped: Dict[str, list] = {}
    for it in items:
        cat = it.get("category") or "uncategorized"
        grouped.setdefault(cat, []).append(it)
    for cat, cat_items in grouped.items():
        label = CATEGORY_LABEL.get(cat, cat)
        body += f'<div class="section-title">{label}（{len(cat_items)}）</div>\n'
        body += _build_cards(cat_items)

    # 财经快讯板块
    if cls_items:
        body += f'<div class="section-title">🇨🇳 国内政策与股市快讯（{len(cls_items)}）</div>\n'
        for it in cls_items:
            title = it.get("title", "无标题")
            url = it.get("link", "")
            summary = it.get("summary", "")
            meta = it.get("publishedAt", "")
            body += f"""<div class="card">
<div class="card-title"><a href="{url}">{highlight_text(title)}</a></div>
<div class="card-meta">华尔街见闻 · {meta}</div>
<div class="card-summary">{highlight_text(summary)}</div>
</div>
"""

    if not body:
        body = '<div class="empty">⏳ 过去 2 小时内暂无新动态</div>'

    html = (
        HTML_HEAD
        + f"""<div class="masthead">
<div class="masthead-rule"></div>
<h1 class="masthead-title">THE PERSONAL DECISION BRIEFING</h1>
<div class="masthead-sub">{ts} · 动态速报 · 共 {total} 条</div>
<div class="masthead-rule"></div>
</div>
{body}"""
        + HTML_TAIL
    )

    HTML(string=html).write_pdf(PDF_FILENAME)
    logger.info("PDF 已生成: %s", PDF_FILENAME)
    return PDF_FILENAME


def generate_pdf_daily(data: Dict[str, Any]) -> str:
    """生成 daily 模式 PDF"""
    date_str = data.get("date", "")
    lead = data.get("lead", {})
    sections = data.get("sections", [])
    flashes = data.get("flashes", [])

    lead_html = ""
    if lead and lead.get("leadParagraph"):
        lead_html = f'<div class="lead">{lead["leadParagraph"]}</div>'

    body = ""
    for sec in sections:
        label = sec.get("label", "")
        items = sec.get("items", [])
        body += f'<div class="section-title">{label}（{len(items)}）</div>\n'
        body += _build_cards(items, show_cat_label=False)

    if flashes:
        body += '<div class="section-title">快讯（{len(flashes)}）</div>\n'
        for f in flashes:
            t = f.get("title", "")
            s = f.get("sourceName", "")
            t_meta = fmt_time(f.get("publishedAt", ""))
            body += f'<div class="flash-item">• {t} — {s}　{t_meta}</div>\n'

    if not body:
        body = '<div class="empty">暂无日报内容</div>'

    html = (
        HTML_HEAD
        + f"""<div class="masthead">
<div class="masthead-rule"></div>
<h1 class="masthead-title">THE PERSONAL DECISION BRIEFING</h1>
<div class="masthead-sub">{date_str} · AI 日报</div>
<div class="masthead-rule"></div>
</div>
{lead_html}
{body}"""
        + HTML_TAIL
    )

    HTML(string=html).write_pdf(PDF_FILENAME)
    logger.info("PDF 已生成: %s", PDF_FILENAME)
    return PDF_FILENAME


# ====== 主入口 ======


def main():
    parser = argparse.ArgumentParser(description="AI HOT PDF 新闻推送")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["interval", "daily"],
        help="interval=每2小时动态 / daily=日报",
    )
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("  🤖 AI HOT 双轨 PDF 推送 → mode=%s", args.mode)
    logger.info("=" * 50)

    if args.mode == "interval":
        items = fetch_interval()
        cls_items = fetch_finance_flash()
        generate_pdf_interval(items, cls_items)
    else:
        data = fetch_daily()
        generate_pdf_daily(data)

    logger.info("✅ 完成，输出文件: %s", PDF_FILENAME)


if __name__ == "__main__":
    main()
