import requests
import json
import time
import re
import os
import random
from html import unescape
from typing import List, Dict

BASE_URL = "https://forum.smart-teach.cn"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
PER_PAGE = 50
MAX_RETRIES = 3
RETRY_DELAY = 3

PROGRESS_FILE = "progress.json"
OUTPUT_FILE = "search-index.json"

def clean_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def load_progress() -> Dict[str, bool]:
    """加载已抓取的话题ID集合"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_progress(progress: Dict[str, bool]):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)

def load_existing_docs() -> List[Dict]:
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_docs(docs: List[Dict]):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)

def fetch_with_retry(url: str, params: dict = None) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = RETRY_DELAY * (attempt + 1)
                print(f"  触发限流，等待 {wait} 秒...")
                time.sleep(wait)
            else:
                print(f"  非200状态码: {resp.status_code}")
        except Exception as e:
            print(f"  请求异常: {e}")
        time.sleep(RETRY_DELAY)
    raise Exception(f"无法获取 {url}")

def crawl_all():
    progress = load_progress()
    existing_docs = load_existing_docs()
    # 将现有文档转为 id -> doc 映射，便于合并
    doc_map = {doc["id"]: doc for doc in existing_docs}
    new_count = 0
    page = 1

    while True:
        print(f"抓取第 {page} 页...")
        try:
            data = fetch_with_retry(
                f"{BASE_URL}/api/discussions",
                params={
                    "page[number]": page,
                    "page[limit]": PER_PAGE,
                    "include": "firstPost"
                }
            )
        except Exception as e:
            print(f"第 {page} 页抓取失败: {e}，终止运行")
            break

        discussions = data.get("data", [])
        if not discussions:
            print("没有更多话题，抓取完毕")
            break

        # 建立 included posts 映射
        included_posts = {}
        for inc in data.get("included", []):
            if inc.get("type") == "posts":
                included_posts[inc["id"]] = inc

        for topic in discussions:
            topic_id = topic["id"]
            if topic_id in progress:
                continue  # 已抓取过

            attrs = topic["attributes"]
            title = attrs.get("title", "无标题")
            created = attrs.get("createdAt", "")[:10]

            # 获取第一帖内容
            try:
                first_post_rel = topic["relationships"]["firstPost"]["data"]
                post_id = first_post_rel["id"]
                post_obj = included_posts.get(post_id)
                if post_obj:
                    content_html = post_obj["attributes"].get("contentHtml", "")
                else:
                    # 降级单独请求
                    post_resp = fetch_with_retry(f"{BASE_URL}/api/posts/{post_id}")
                    content_html = post_resp["data"]["attributes"].get("contentHtml", "")
            except Exception as e:
                print(f"  获取帖子内容失败 {topic_id}: {e}")
                content_html = ""

            content_text = clean_html(content_html)

            doc = {
                "id": topic_id,
                "title": title,
                "content": content_text[:5000],
                "url": f"{BASE_URL}/d/{topic_id}",
                "created": created
            }
            doc_map[topic_id] = doc
            progress[topic_id] = True
            new_count += 1

        # 每页抓取后保存进度和文档
        save_progress(progress)
        save_docs(list(doc_map.values()))

        print(f"  本页新增 {new_count} 个话题，累计 {len(doc_map)} 个")
        new_count = 0
        page += 1
        time.sleep(random.uniform(1.0, 2.0))   # 随机延迟

    print(f"✅ 全量抓取完成，共 {len(doc_map)} 个话题")
    save_docs(list(doc_map.values()))
    # 清理进度文件（下次重新全量，若你希望增量可注释）
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

if __name__ == "__main__":
    crawl_all()
