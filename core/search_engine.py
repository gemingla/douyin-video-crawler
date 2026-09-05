"""多站聚合搜索引擎 - 同时搜索多个视频网站，聚合结果"""

import re
import time
import urllib.parse
import requests
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.site_rules import SITE_RULES, get_search_url, parse_search_results


def search_all_sites(
    keyword: str,
    sites: Optional[List[str]] = None,
    timeout: int = 20,
    max_results_per_site: int = 10,
) -> List[Dict[str, Any]]:
    """同时在多个站点搜索，聚合结果

    Args:
        keyword: 搜索关键词
        sites: 要搜索的站点域名列表，None=全部
        timeout: 每个站点超时
        max_results_per_site: 每个站点最大结果数

    Returns:
        [{"title": 标题, "url": 详情页URL, "site": 站点名, ...}, ...]
    """
    # 生成搜索 URL
    search_tasks = []
    for domain, rules in SITE_RULES.items():
        if sites and domain not in sites:
            continue
        base = rules["base_url"].rstrip("/")
        search_path = rules["search_url"].replace("{keyword}", urllib.parse.quote(keyword))
        search_tasks.append({
            "domain": domain,
            "name": rules["name"],
            "url": f"{base}{search_path}",
            "rules": rules,
        })

    # 先检测各站点可用性
    print(f"[搜索] 关键词: {keyword}")
    print(f"[搜索] 正在检测 {len(search_tasks)} 个站点可用性...")

    def check_site(base_url: str) -> bool:
        try:
            r = requests.get(base_url, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0"})
            return r.status_code == 200
        except Exception:
            return False

    alive_tasks = []
    for t in search_tasks:
        if check_site(t["rules"]["base_url"]):
            alive_tasks.append(t)
        else:
            print(f"  ⚡ {t['name']}: 站点不可达，跳过")

    print(f"[搜索] {len(alive_tasks)}/{len(search_tasks)} 个站点可用")
    if not alive_tasks:
        print("[搜索] 无可用站点，建议直接使用 YouTube/Bilibili 搜索")
        return []

    all_results = []

    def search_site(task: dict) -> List[dict]:
        try:
            resp = requests.get(
                task["url"],
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": task["rules"]["base_url"],
                },
                timeout=timeout,
            )
            resp.encoding = "utf-8"
            results = parse_search_results(resp.text, task["rules"])
            for r in results:
                r["_source"] = task["name"]
            return results[:max_results_per_site]
        except Exception as e:
            print(f"  [搜索] {task['name']}: 失败 ({e})")
            return []

    # 并行搜索所有站点
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(search_site, t): t for t in search_tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                results = future.result()
                all_results.extend(results)
                print(f"  [搜索] {task['name']}: {len(results)} 个结果")
            except Exception:
                pass

    # 去重
    seen = set()
    unique = []
    for r in all_results:
        key = f"{r['title']}|{r['site']}"
        if key not in seen:
            seen.add(key)
            unique.append(r)

    print(f"[搜索] 共找到 {len(unique)} 个结果")
    return unique


def batch_search_and_extract(
    keyword: str,
    sites: Optional[List[str]] = None,
    max_episodes: int = 5,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """完整流程：搜索 → 选结果 → 提取剧集 → 获取每集视频地址

    Args:
        keyword: 搜索关键词
        sites: 指定站点
        max_episodes: 每个剧集最多提取集数
        max_results: 最多处理的前几个结果

    Returns:
        [{"title", "episodes": [{"episode", "video_url"}, ...]}, ...]
    """
    from core.batch_extractor import extract_episodes_from_page, extract_video_url_from_episode, batch_extract

    # 1. 搜索
    search_results = search_all_sites(keyword, sites, max_results_per_site=max_results)
    if not search_results:
        print("[搜索] 无结果")
        return []

    # 2. 对每个搜索结果提取剧集和视频地址
    final_results = []
    for i, result in enumerate(search_results[:max_results]):
        print(f"\n[搜索] ({i+1}/{min(max_results, len(search_results))}) {result['title']}")
        try:
            episodes = batch_extract(result["url"], max_episodes=max_episodes, timeout=20)
            if episodes:
                final_results.append({
                    "title": result["title"],
                    "site": result["site"],
                    "url": result["url"],
                    "episodes": episodes,
                })
        except Exception as e:
            print(f"  [搜索] 提取失败: {e}")

    return final_results
