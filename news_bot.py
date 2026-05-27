#!/usr/bin/env python3
"""
全球每日早报推送机器人（定制版）
- 垂直数据源：AI 前沿 / 国内政策 / A股市场
- 调用 DeepSeek V4 (OpenAI 兼容模式) 分板块智能总结
- 钉钉富媒体排版（封面图 + 大盘走势图）
"""

import os
import sys
import random
import logging
from datetime import date
from typing import List, Dict

import feedparser
import requests
from openai import OpenAI

# ==================== 日志配置 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ==================== 环境变量配置 ====================
# 敏感信息仅从系统环境变量读取，绝不硬编码

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)
DINGTALK_WEBHOOK_URL = os.environ.get("DINGTALK_WEBHOOK_URL")

# RSSHub 公共实例地址（如网络受限可换成自建地址）
RSSHUB_BASE = os.environ.get("RSSHUB_BASE", "https://rsshub.app")

# ==================== RSS 数据源 ====================
# 按三大板块分类，可从环境变量覆盖

RSS_FEEDS: Dict[str, str] = {
    # ──── AI 前沿观察 ────
    "AIHOT": "https://aihot.virxact.com/feed",

    # ──── 国内政策速递 ────
    "人民网 时政": f"{RSSHUB_BASE}/people/xjp",       # 习近平重要活动
    "人民网 政策": f"{RSSHUB_BASE}/people/policy",      # 政策文件解读

    # ──── A股市场风向 ────
    "财联社 电报": f"{RSSHUB_BASE}/cls/telegraph",
    "东方财富 要闻": f"{RSSHUB_BASE}/eastmoney",
}

# 每个源最多取的文章数
MAX_ARTICLES_PER_SOURCE = 5

# ==================== 封面图 / 大盘图 ====================

COVER_IMAGES = [
    # 科技金融主题 Unsplash 图片（使用稳定 photo ID）
    "https://images.unsplash.com/photo-1504639725590-34d0984388bd?w=800&h=400&fit=crop&auto=format",
    "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&h=400&fit=crop&auto=format",
    "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&h=400&fit=crop&auto=format",
    "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?w=800&h=400&fit=crop&auto=format",
]

# A 股大盘走势图（新浪财经公开接口）
STOCK_CHART_URL = "http://image.sinajs.cn/newchart/daily/n/sh000001.gif"


# ==================== RSS 获取 ====================

def fetch_source(source: str, url: str) -> List[Dict[str, str]]:
    """获取单个 RSS 源的新闻列表"""
    articles: List[Dict[str, str]] = []
    try:
        logger.info("正在获取: %s (%s)", source, url)
        feed = feedparser.parse(url)
        count = 0
        for entry in feed.entries:
            if count >= MAX_ARTICLES_PER_SOURCE:
                break
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if title:
                articles.append({
                    "source": source,
                    "title": title,
                    "link": link,
                })
                count += 1
        logger.info("  -> 获取到 %d 条", count)
    except Exception as e:
        logger.error("获取 %s 失败: %s", source, e)
    return articles


def fetch_all_news() -> Dict[str, List[Dict[str, str]]]:
    """获取所有板块的新闻，按板块分组返回"""
    sections = {
        "ai": [],
        "policy": [],
        "stock": [],
    }

    for name, url in RSS_FEEDS.items():
        articles = fetch_source(name, url)
        if "AIHOT" in name:
            sections["ai"].extend(articles)
        elif "人民网" in name:
            sections["policy"].extend(articles)
        elif "财联社" in name or "东方财富" in name:
            sections["stock"].extend(articles)
        else:
            sections["ai"].extend(articles)  # fallback

    for key in sections:
        logger.info("板块 %s 共 %d 条新闻", key, len(sections[key]))
    return sections


# ==================== AI 摘要 ====================

def build_prompt(sections: Dict[str, List[Dict[str, str]]], today_str: str) -> str:
    """构建财经主编级结构化提示词"""

    def fmt(articles: List[Dict[str, str]]) -> str:
        lines = []
        for i, a in enumerate(articles, 1):
            line = f"{i}. [{a['source']}] {a['title']}"
            if a.get("link"):
                line += f"\n   链接: {a['link']}"
            lines.append(line)
        return "\n".join(lines) if lines else "（暂无新闻）"

    ai_text = fmt(sections["ai"])
    policy_text = fmt(sections["policy"])
    stock_text = fmt(sections["stock"])

    prompt = f"""你是财经主编，负责每日早报的终审与定稿。

📅 日期：{today_str}

请根据以下新闻素材，严格按照「事件 + 深度提炼」的二级 Markdown 格式输出。

========================================
【输出格式规范】

每个板块用 ### 开头，板块内每条新闻用 **事件：** 和 **深度提炼：** 两级结构：

### 【AI 前沿观察】
**事件：** <标题>
**深度提炼：** <1-2 句话，点明行业影响、技术突破意义或竞争格局>

**事件：** <标题>
**深度提炼：** <1-2 句话，点明行业影响、技术突破意义或竞争格局>

### 【国内政策速递】
**事件：** <标题>
**深度提炼：** <1-2 句话，解读政策意图、受益行业或后续影响>

**事件：** <标题>
**深度提炼：** <1-2 句话，解读政策意图、受益行业或后续影响>

### 【A股市场风向】
**事件：** <标题>
**深度提炼：** <1-2 句话，分析资金动向、板块逻辑或短线情绪>

**事件：** <标题>
**深度提炼：** <1-2 句话，分析资金动向、板块逻辑或短线情绪>

末尾附加一行来源声明：
📰 新闻来源：<平台A>、<平台B>、<平台C>

========================================
【原始新闻素材】

===== AI 板块 =====
{ai_text}

===== 政策板块 =====
{policy_text}

===== 股市板块 =====
{stock_text}"""
    return prompt


def summarize_news(sections: Dict[str, List[Dict[str, str]]]) -> str:
    """调用 DeepSeek V4 总结新闻"""
    if not DEEPSEEK_API_KEY:
        logger.error("未设置 DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    today_str = date.today().strftime("%Y 年 %m 月 %d 日")
    prompt = build_prompt(sections, today_str)

    logger.info("正在调用 DeepSeek API 进行摘要...")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是《全球每日早报》的资深财经主编。"
                        "你以专业、精炼、有洞察力著称，善于从繁杂信息中提炼核心价值。"
                        "\n\n"
                        "【输出铁律】\n"
                        "1. 严格按三大板块输出：【AI 前沿观察】｜【国内政策速递】｜【A股市场风向】\n"
                        "2. 每条新闻采用「事件 + 深度提炼」二级 Markdown 排版：\n"
                        "   **事件：** <简明标题>\n"
                        "   **深度提炼：** <1-2 句，给出有信息增量的解读（行业影响 / 政策意图 / 资金逻辑）>\n"
                        "3. 语言精炼专业，用简体中文。不做价值评价，只说事实和推演\n"
                        "4. 每个板块至少输出 3 条，宁缺毋滥\n"
                        "5. 正文首个字符必须包含「早报」关键词"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=3000,
        )
        summary = response.choices[0].message.content
        logger.info("API 调用成功，摘要长度: %d 字符", len(summary))
        return summary
    except Exception as e:
        logger.error("API 调用失败: %s", e)
        sys.exit(1)


# ==================== 钉钉推送 ====================

def build_markdown(summary: str) -> str:
    """组装完整的钉钉 Markdown 消息（含封面图 + 大盘图）"""
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]

    # 随机选一张封面图
    cover = random.choice(COVER_IMAGES)

    # 构建消息体
    lines = [
        f"![封面]({cover})",
        "",
        f"# 🌐 全球每日早报 · {today_str} {weekday_cn}",
        "",
        "---",
        "",
        summary,
        "",
        "---",
        "### 📈 A股大盘走势",
        f"![上证指数日K线]({STOCK_CHART_URL})",
        "",
        f"*🤖 由 DeepSeek V4 自动生成 | {today_str}*",
        "",
        "**早报** · 每日 8:00 推送",
    ]
    return "\n".join(lines)


def send_dingtalk_markdown(title: str, content: str):
    """通过钉钉自定义机器人推送 Markdown 消息"""
    if not DINGTALK_WEBHOOK_URL:
        logger.error("未设置 DINGTALK_WEBHOOK_URL 环境变量")
        sys.exit(1)

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": content,
        },
    }
    headers = {"Content-Type": "application/json"}

    logger.info("正在推送消息到钉钉...")
    try:
        resp = requests.post(
            DINGTALK_WEBHOOK_URL, json=payload, headers=headers, timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode") != 0:
            logger.error("钉钉推送失败: %s", result.get("errmsg", "未知错误"))
            sys.exit(1)
        logger.info("✅ 钉钉消息推送成功！")
    except requests.RequestException as e:
        logger.error("钉钉推送请求失败: %s", e)
        sys.exit(1)


# ==================== 主流程 ====================

def main():
    logger.info("=" * 50)
    logger.info("  🌐 全球每日早报机器人（定制版）启动")
    logger.info("=" * 50)

    # 1. 获取新闻
    sections = fetch_all_news()
    total = sum(len(v) for v in sections.values())
    if total == 0:
        logger.error("未获取到任何新闻，终止运行")
        sys.exit(1)
    logger.info("共获取到 %d 条新闻", total)

    # 2. 调用 AI 总结
    summary = summarize_news(sections)

    # 3. 组装富文本消息
    today_str = date.today().strftime("%Y-%m-%d")
    title = f"全球每日早报 · {today_str}"
    message = build_markdown(summary)

    # 4. 推送到钉钉
    send_dingtalk_markdown(title, message)
    logger.info("✅ 全部任务完成！")


if __name__ == "__main__":
    main()
