---
name: "web_tools"
description: "用于网络搜索和网页抓取的工具集"
---

# Skill Description
当需要获取某个网页的详细内容时使用此技能。自动提取正文、去除广告和导航。

## Constraints
- 部分网站可能有反爬限制，导致抓取失败
- 输出默认截断到 4000 字符，避免过长

## Usage

### 基本抓取
```bash
python /skills/web/src/scrape.py "https://example.com/article"
```
输出：Markdown 格式的网页正文内容。

```bash
python /skills/web/src/scrape.py "https://example.com" --max-length 8000
```
指定最大长度

### 基本搜索
```bash
python /skills/web/src/search.py "搜索关键词"
```
输出：JSON 格式搜索结果列表，每项包含 title、href、body。

```bash
python /skills/web/src/search.py "关键词" --limit 10
```
指定结果数量
