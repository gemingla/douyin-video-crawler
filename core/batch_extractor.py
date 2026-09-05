"""批量爬取 - 针对剧集/系列类网站，自动提取所有分集视频地址"""

import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urljoin


# ── 站点规则 ──────────────────────────────────────────────
# 不同网站的剧集列表和视频地址提取规则
SITE_RULES = {
    "lt0577.com": {
        "name": "狼影影院",
        "episode_pattern": r'href="(/tvplay/(\d+)-6-(\d+)\.html)"',  # 6=极速线路
        "episode_url_template": "/tvplay/{drama_id}-{source}-{ep}.html",
        "video_url_pattern": r"""url["']?\s*[:=]\s*["']([^"']+)""",
        "title_pattern": r'<h1[^>]*>(.*?)</h1>',
        "video_clean": lambda url: url.replace('\\/', '/'),
    },
}


def guess_site_rules(url: str) -> Optional[dict]:
    """根据 URL 猜测适用的站点规则"""
    for domain, rules in SITE_RULES.items():
        if domain in url:
            return rules
    # 通用规则：尝试常见模式
    return {
        "name": "未知站点",
        "episode_pattern": r'<a[^>]*href="([^"]+)"[^>]*>第(\d+)集</a>',
        "video_url_pattern": r'(https?://[^"\'<>]+\.m3u8[^"\'<>]*)',
    }


def extract_episodes_from_page(
    url: str,
    rules: dict,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """从剧集详情页提取所有分集信息"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": url,
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.encoding = "utf-8"
    except Exception as e:
        raise ValueError(f"无法访问页面: {e}")

    html = resp.text
    episodes = []

    # 尝试提取标题
    title = ""
    if "title_pattern" in rules:
        title_matches = re.findall(rules["title_pattern"], html, re.DOTALL)
        if title_matches:
            title = re.sub(r'<[^>]+>', '', title_matches[0]).strip()

    # 提取分集链接
    ep_pattern = rules.get("episode_pattern", r'第(\d+)集.*?href="([^"]+)"')
    ep_matches = re.findall(ep_pattern, html)

    if not ep_matches:
        # 尝试更通用的模式
        ep_matches = re.findall(r'href="([^"]*\.html)"[^>]*>第(\d+)集', html)

    for match in ep_matches:
        if len(match) >= 1:
            href = match[0]
            # 从 URL 中提取集号，如 /tvplay/151756-6-1.html → 1
            ep_num = 0
            ep_parts = re.findall(r'-(\d+)\.html', href)
            if ep_parts:
                ep_num = int(ep_parts[-1])  # 最后一个数字是集号
            full_url = urljoin(url, href)
            episodes.append({
                "episode": ep_num,
                "title": f"第{ep_num}集" if ep_num > 0 else f"第{len(episodes)+1}集",
                "url": full_url,
                "drama_title": title,
            })

    if not episodes:
        raise ValueError(f"未找到剧集列表，页面结构可能不匹配")

    # 去重并排序
    seen = set()
    unique = []
    for ep in episodes:
        if ep["url"] not in seen:
            seen.add(ep["url"])
            unique.append(ep)
    unique.sort(key=lambda x: x["episode"])

    return unique


def extract_video_url_from_episode(
    episode_url: str,
    rules: dict,
    timeout: int = 30,
    retry: int = 2,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """从单集页面提取视频播放地址（m3u8）"""
    close_session = False
    if session is None:
        session = requests.Session()
        close_session = True

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": episode_url,
    })

    for attempt in range(retry):
        try:
            resp = session.get(episode_url, timeout=timeout)
            resp.encoding = "utf-8"
            html = resp.text

            # 尝试视频地址模式
            video_pattern = rules.get("video_url_pattern", r'(https?://[^"\'<>]+\.m3u8[^"\'<>]*)')
            matches = re.findall(video_pattern, html)

            if matches:
                raw_url = matches[0]
                # 清理 URL（处理转义）
                clean_fn = rules.get("video_clean", lambda x: x)
                video_url = clean_fn(raw_url)
                return video_url

            # 尝试常见 JS 变量
            for js_pattern in [
                r'video_url\s*[:=]\s*["\']([^"\']+)',
                r'"url"\s*:\s*"([^"]+)"',
                r"'url'\s*:\s*'([^']+)'",
                r'var\s+url\s*=\s*["\']([^"\']+)',
            ]:
                js_matches = re.findall(js_pattern, html)
                if js_matches:
                    clean_fn = rules.get("video_clean", lambda x: x)
                    return clean_fn(js_matches[0])

        except Exception as e:
            if attempt < retry - 1:
                time.sleep(1)
                continue
            raise ValueError(f"提取视频地址失败: {e}")

    return None


def batch_extract(
    drama_url: str,
    timeout: int = 30,
    max_episodes: int = 0,
) -> List[Dict[str, Any]]:
    """完整流程：输入剧集页 → 提取分集列表 → 提取每集视频地址

    Args:
        drama_url: 剧集详情页 URL
        timeout: 单页超时
        max_episodes: 最大提取集数（0=全部）

    Returns:
        [{"episode": N, "title": str, "url": str, "video_url": str}, ...]
    """
    rules = guess_site_rules(drama_url)
    if not rules:
        raise ValueError(f"不支持的网站: {drama_url}")

    print(f"[批量] 站点: {rules.get('name', '未知')}")
    print(f"[批量] 正在提取剧集列表...")

    episodes = extract_episodes_from_page(drama_url, rules, timeout)
    print(f"[批量] 找到 {len(episodes)} 集")

    if max_episodes > 0:
        episodes = episodes[:max_episodes]
        print(f"[批量] 限制提取 {max_episodes} 集")

    # 并行提取每集的视频地址
    results = []
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    def fetch_episode(ep: dict) -> Optional[dict]:
        try:
            video_url = extract_video_url_from_episode(
                ep["url"], rules, timeout, session=session)
            if video_url:
                return {
                    "episode": ep["episode"],
                    "title": ep["title"],
                    "drama_title": ep.get("drama_title", ""),
                    "page_url": ep["url"],
                    "video_url": video_url,
                }
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_episode, ep): ep for ep in episodes}
        done_count = 0
        for future in as_completed(futures):
            ep = futures[future]
            done_count += 1
            try:
                result = future.result()
                if result:
                    results.append(result)
                    print(f"[批量] 第{ep['episode']:02d}集 ✓ ({done_count}/{len(episodes)})")
                else:
                    print(f"[批量] 第{ep['episode']:02d}集 跳过 ({done_count}/{len(episodes)})")
            except Exception as e:
                print(f"[批量] 第{ep['episode']:02d}集 ✗ {e} ({done_count}/{len(episodes)})")

    session.close()
    results.sort(key=lambda x: x["episode"])
    print(f"[批量] 完成: 成功获取 {len(results)}/{len(episodes)} 集")
    return results
