FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg curl wget ddgr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    requests httpx \
    duckduckgo-search \
    readability-lxml markdownify lxml \
    pillow \
    pyyaml ddgs 
WORKDIR /
