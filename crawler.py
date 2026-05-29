import requests
import json
import time
import re
import os
import random
from html import unescape
from typing import List, Dict
from requests.exceptions import ConnectionError as RequestsConnectionError

BASE_URL = "https://forum.smart-teach.cn"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
PER_PAGE = 50
MAX_RETRIES = 3
RETRY_DELAY = 5  # 重试前等待秒数

PROGRESS_FILE = "progress.json"
OUTPUT_FILE = "search-index.json"

def clean_html(html: str) -> str:
    """去除HTML标签，转义实体，压缩空白"""
    if not html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def load_progress() -> Dict[str, bool]:
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
    """带重试机制的请求，遇到 Connection refused 则抛出异常终止翻页"""
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
        except RequestsConnectionError as e:
            print(f"  连接被拒绝 (Connection refused): {e}")
            # 连接被拒绝通常是防火墙限流，直接终止翻页
            raise StopIteration("Connection refused, stop pagination") from e
        except Exception as e:
            print(f"  请求异常: {e}")
        time.sleep(RETRY_DELAY)
    raise Exception(f"无法获取 {url}")

def fetch_post_content(post_id: str) -> str:
    """获取单个帖子的HTML内容"""
    try:
        post_resp = fetch_with_retry(f"{BASE_URL}/api/posts/{post_id}")
        return post_resp["data"]["attributes"].get("contentHtml", "")
    except Exception:
        return ""

def fetch_discussion_detail(topic_id: str) -> str:
    """当话题缺少 relationships 时，单独请求话题详情，返回第一帖内容"""
    try:
        disc_resp = fetch_with_retry(f"{BASE_URL}/api/discussions/{topic_id}")
        if "data" in disc_resp and "relationships" in disc_resp["data"]:
            first_post_rel = disc_resp["data"]["relationships"]["firstPost"]["data"]
            post_id = first_post_rel["id"]
            return fetch_post_content(post_id)
        else:
            print(f"  话题详情仍无 relationships，跳过内容")
            return ""
    except Exception as e:
        print(f"  单独获取话题详情失败 {topic_id}: {e}")
        return ""

def crawl_all():
    progress = load_progress()
    existing_docs = load_existing_docs()
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
        except StopIteration:
            print("因连接被拒绝，终止翻页")
            break
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
                continue

            attrs = topic.get("attributes", {})
            title = attrs.get("title", "无标题")
            created = attrs.get("createdAt", "")[:10]

            content_text = ""

            # 尝试通过 relationships 获取第一帖
            if "relationships" in topic and "firstPost" in topic["relationships"]:
                try:
                    post_id = topic["relationships"]["firstPost"]["data"]["id"]
                    post_obj = included_posts.get(post_id)
                    if post_obj:
                        content_html = post_obj["attributes"].get("contentHtml", "")
                    else:
                        content_html = fetch_post_content(post_id)
                    content_text = clean_html(content_html)
                except Exception as e:
                    print(f"  通过 relationships 获取内容失败 {topic_id}: {e}")
                    # 降级：尝试单独获取话题详情
                    content_text = clean_html(fetch_discussion_detail(topic_id))
            else:
                # 根本没有 relationships 字段，单独获取话题详情
                print(f"  话题 {topic_id} 缺少 relationships，尝试单独获取")
                content_text = clean_html(fetch_discussion_detail(topic_id))

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

        # 每页结束后保存进度
        save_progress(progress)
        save_docs(list(doc_map.values()))
        print(f"  本页新增 {new_count} 个话题，累计 {len(doc_map)} 个")
        new_count = 0
        page += 1
        # 增加随机延迟，降低限流风险
        time.sleep(random.uniform(3.0, 5.0))

    print(f"✅ 全量抓取完成，共 {len(doc_map)} 个话题")
    save_docs(list(doc_map.values()))
    # 清理进度文件（下次重新全量，若需要增量可保留）
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

if __name__ == "__main__":
    crawl_all()
