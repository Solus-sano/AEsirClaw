import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter
import argparse
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import requests
import re

async def fetch_to_markdown_free(url: str) -> dict:
    """
    完全免费的网页抓取转 Markdown
    无需 LLM，无 token 消耗
    """
    # 使用 PruningContentFilter 进行本地内容过滤（非 LLM）
    content_filter = PruningContentFilter(
        threshold=0.48,           # 内容过滤阈值
        threshold_type="fixed",
        min_word_threshold=0
    )
    
    # 配置 Markdown 生成器，传入 content_filter
    markdown_generator = DefaultMarkdownGenerator(
        content_filter=content_filter
    )
    
    config = CrawlerRunConfig(
        markdown_generator=markdown_generator,  # 使用配置好的 markdown_generator
        cache_mode="BYPASS"  # 可选：绕过缓存
    )
    
    async with AsyncWebCrawler(verbose=True) as crawler:
        print(f"start fetch {url}")
        result = await crawler.arun(url=url, config=config)
        print(f"end fetch {url}")
        return result.markdown
        # return {
        #     "success": result.success,
        #     "url": url,
        #     "markdown": result.markdown_v2.raw_markdown,  # 获取原始 markdown
        #     "title": result.metadata.get("title", ""),
        #     "links": result.links.get("internal", []) + result.links.get("external", [])
        # }

def simple_fetch(url: str) -> dict:
    """极简方案，无外部依赖（除 requests/beautifulsoup）"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; AI-Agent/1.0)'
    }
    
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    
    soup = BeautifulSoup(resp.content, 'html.parser')
    return html_to_markdown_clean(soup)
    
    # 清理干扰元素
    # for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
    #     tag.decompose()
    
    # # 提取正文
    # main = soup.find('article') or soup.find('main') or soup.find('body')
    
    # return {
    #     "success": True,
    #     "url": url,
    #     "title": soup.title.string if soup.title else "",
    #     "markdown": md(str(main), heading_style="ATX", strip=['a'])
    # }



def html_to_markdown_clean(soup):
    # 删除干扰元素
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        tag.decompose()
    
    # 将 <a> 转为 Markdown 链接格式 [text](url)
    for a in soup.find_all('a'):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        if href and text:
            a.replace_with(f"[{text}]({href})")
        else:
            a.replace_with(text)
    
    # 将 <img> 转为 Markdown 图片格式 ![alt](src)
    for img in soup.find_all('img'):
        src = img.get('src', '')
        alt = img.get('alt', 'image')
        if src:
            img.replace_with(f"![{alt}]({src})")
    
    # 然后对整个内容使用 markdownify
    from markdownify import markdownify as md
    markdown = md(str(soup), heading_style="ATX")
    
    return markdown
# 同步包装
def fetch_url(url: str):
    # return asyncio.run(fetch_to_markdown_free(url))
    return simple_fetch(url)


def main():
    parser = argparse.ArgumentParser(description="网页抓取")
    parser.add_argument("url", help="目标网页 URL")
    parser.add_argument("--max-length", type=int, default=4000, help="最大输出字符数")
    args = parser.parse_args()
    result = fetch_url(args.url)#, args.max_length)
    print(result)

# 使用示例
if __name__ == "__main__":
    main()