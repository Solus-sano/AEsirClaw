"""网络搜索 CLI。使用 duckduckgo-search 查询。"""

import argparse
import json

from ddgs import DDGS


def main():
    parser = argparse.ArgumentParser(description="网络搜索")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--limit", type=int, default=5, help="返回结果数")
    args = parser.parse_args()

    with DDGS() as ddgs:
        results = list(ddgs.text(args.query, max_results=args.limit))

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
