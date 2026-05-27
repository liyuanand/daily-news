#!/usr/bin/env python3
"""
全球每日早报推送机器人
- 从多家主流媒体 RSS 获取新闻
- 调用 DeepSeek V4 API (OpenAI 兼容模式) 进行智能总结
- 推送到钉钉群机器人
"""

import os
import sys
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
# 所有敏感信息均从系统环境变量读取，绝不硬编码

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)

# 钉钉机器人 Webhook URL（从环境变量 DINGTALK_WEBHOOK_URL 读取）
DINGTALK_WEBHOOK_URL = os.environ.get("DINGTALK_WEBHOOK_URL")

# ==================== RSS 新闻源 ====================

RSS_FEEDS: Dict[str, str] = {
    "BBC 国际": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "卫报": "https://www.theguardian.com/world/rss",
    "NPR": "https://feeds.npr.org/1001/rss.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "AP News": "https://apnews.com/rss",
}

MAX_ARTICLES_PER_SOURCE = 5


def fetch_all_news() -> List[Dict[str, str]]:
    """从所有 RSS 源获取新闻标题与链接"""
    articles: List[Dict[str, str]] = []
    for source, url in RSS_FEEDS.items():
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


def build_prompt(articles: List[Dict[str, str]], today_str: str) -> str:
    """构建发送给 LLM 的提示词"""
    news_lines = []
    for i, a in enumerate(articles, 1):
        line = f"{i}. [{a['source']}] {a['title']}"
        if a.get("link"):
            line += f"\n   链接: {a['link']}"
        news_lines.append(line)

    news_text = "\n".join(news_lines)

    prompt = f"""你是全球新闻摘要专家。请根据以下 {len(articles)} 条新闻，制作一份「全球每日早报」。

📅 日期：{today_str}

【格式要求】
- 将新闻按主题分类（如：国际局势、科技趋势、财经要闻、社会热点等）
- 每个类别下列出相关新闻，每条用 1-2 句话概括核心信息
- 语言：简体中文，简洁精炼
- 末尾列出本次新闻来源

【原始新闻素材】
{news_text}"""
    return prompt


def summarize_news(articles: List[Dict[str, str]]) -> str:
    """调用 DeepSeek V4 (deepseek-chat) 总结新闻"""
    if not DEEPSEEK_API_KEY:
        logger.error("未设置 DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    today_str = date.today().strftime("%Y 年 %m 月 %d 日")
    prompt = build_prompt(articles, today_str)

    logger.info("正在调用 DeepSeek API 进行摘要...")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个专业的新闻摘要助手。"
                        "请将提供的多条新闻整理成结构清晰、语言简洁的每日早报，使用简体中文。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2500,
        )
        summary = response.choices[0].message.content
        logger.info("API 调用成功，摘要长度: %d 字符", len(summary))
        return summary
    except Exception as e:
        logger.error("API 调用失败: %s", e)
        sys.exit(1)


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
        logger.info("钉钉消息推送成功！")
    except requests.RequestException as e:
        logger.error("钉钉推送请求失败: %s", e)
        sys.exit(1)


def main():
    logger.info("=" * 50)
    logger.info("  🌐 全球每日早报机器人启动")
    logger.info("=" * 50)

    # 1. 获取新闻
    articles = fetch_all_news()
    if not articles:
        logger.error("未获取到任何新闻，终止运行")
        sys.exit(1)
    logger.info("共获取到 %d 条新闻", len(articles))

    # 2. 调用 AI 总结
    summary = summarize_news(articles)

    # 3. 组装最终 Markdown 消息
    today_str = date.today().strftime("%Y-%m-%d")
    title = f"全球每日早报 · {today_str}"
    message = f"# 🌐 全球每日早报 · {today_str}\n\n{summary}\n\n---\n*🤖 由 DeepSeek V4 自动生成 | {today_str}*"

    # 4. 推送到钉钉
    send_dingtalk_markdown(title, message)
    logger.info("✅ 任务完成！")


if __name__ == "__main__":
    main()
