"""多站点规则 - 定义各视频网站的搜索、剧集、视频地址提取规则"""

import re
import urllib.parse
from typing import Optional, Dict, Any, List

# ═══════════════════════════════════════════════════════════
# 站点规则配置
# 每个站点定义：
#   search_url:    搜索 URL 模板，{keyword} 会被替换
#   search_items:  从搜索页提取结果的正则
#   episode_url:   剧集页 URL 模板
#   episode_list:  从剧集详情页提取分集列表的正则
#   video_url:     从播放页提取 m3u8 的正则
#   domain:        域名
# ═══════════════════════════════════════════════════════════

SITE_RULES: Dict[str, Dict[str, Any]] = {
    # ⚠️ 国内剧集站普遍不稳定，经常超时或挂掉
    # 搜索前会检测站点可用性，不可用的自动跳过

    "lt0577.com": {
        "name": "狼影影院",
        "domain": "lt0577.com",
        "base_url": "http://lt0577.com",
        "search_url": "/search/{keyword}-------------.html",
        "search_items": [
            r'<a[^>]*href="(/tvplay/\d+-\d+\.html)"[^>]*title="([^"]*)"',
            r'<a[^>]*href="(/tvplay/\d+-\d+\.html)"[^>]*>(.*?)</a>',
        ],
        "search_title_clean": lambda t: re.sub(r'<[^>]+>', '', t).strip(),
        "episode_url": "/tvplay/{id}-6-{ep}.html",
        "episode_match": r'href="(/tvplay/(\d+)-6-(\d+)\.html)"',
        "video_pattern": r"""url["']?\s*[:=]\s*["']([^"']+)""",
        "video_clean": lambda u: u.replace('\\/', '/'),
    },

    "www.naifei2.org": {
        "name": "奈飞中文",
        "domain": "www.naifei2.org",
        "base_url": "https://www.naifei2.org",
        "search_url": "/vodsearch.html?wd={keyword}",
        "search_items": [
            r'<a[^>]*href="(/voddetail/\d+\.html)"[^>]*title="([^"]*)"',
        ],
        "search_title_clean": lambda t: t,
        "episode_url": "/vodplay/{id}-{source}-{ep}.html",
        "episode_match": r'href="(/vodplay/(\d+)-\d+-(\d+)\.html)"',
        "video_pattern": r"""url["']?\s*[:=]\s*["']([^"']+)""",
        "video_clean": lambda u: u.replace('\\/', '/').replace('\\\\/', '/'),
    },

    "www.knvod.com": {
        "name": "柯南影视",
        "domain": "www.knvod.com",
        "base_url": "https://www.knvod.com",
        "search_url": "/search/{keyword}-------------.html",
        "search_items": [
            r'<a[^>]*href="(/voddetail/\d+\.html)"[^>]*title="([^"]*)"',
            r'<a[^>]*href="(/tvplay/\d+-\d+\.html)"[^>]*title="([^"]*)"',
        ],
        "search_title_clean": lambda t: t,
        "episode_url": "/vodplay/{id}-{source}-{ep}.html",
        "episode_match": r'href="(/vodplay/(\d+)-\d+-(\d+)\.html)"',
        "video_pattern": r"""url["']?\s*[:=]\s*["']([^"']+)""",
        "video_clean": lambda u: u.replace('\\/', '/'),
    },

    "www.wmlia.com": {
        "name": "万幕影院",
        "domain": "www.wmlia.com",
        "base_url": "https://www.wmlia.com",
        "search_url": "/search?wd={keyword}",
        "search_items": [
            r'<a[^>]*href="(/voddetail/\d+\.html)"[^>]*title="([^"]*)"',
        ],
        "search_title_clean": lambda t: t,
        "episode_url": "/vodplay/{id}-{source}-{ep}.html",
        "episode_match": r'href="(/vodplay/(\d+)-\d+-(\d+)\.html)"',
        "video_pattern": r"""url["']?\s*[:=]\s*["']([^"']+)""",
        "video_clean": lambda u: u.replace('\\/', '/'),
    },
}


def guess_site(url: str) -> Optional[Dict[str, Any]]:
    """根据 URL 匹配对应的站点规则"""
    for domain, rules in SITE_RULES.items():
        if domain in url:
            return rules
    return None


def get_search_url(keyword: str, site_key: str = "") -> List[Dict[str, Any]]:
    """生成各站点的搜索 URL

    Args:
        keyword: 搜索关键词
        site_key: 指定站点域名，空则返回所有站点

    Returns:
        [{"site": 站点名, "domain": 域名, "url": 搜索URL, "rules": 规则}, ...]
    """
    results = []
    encoded = urllib.parse.quote(keyword)

    for domain, rules in SITE_RULES.items():
        if site_key and domain != site_key:
            continue
        base = rules["base_url"].rstrip("/")
        search_path = rules["search_url"].replace("{keyword}", encoded)
        results.append({
            "site": rules["name"],
            "domain": domain,
            "url": f"{base}{search_path}",
            "rules": rules,
        })

    return results


def parse_search_results(html: str, rules: Dict) -> List[Dict[str, str]]:
    """从搜索页 HTML 中提取结果列表

    Returns:
        [{"title": 标题, "url": 详情页URL, "site": 站点名}, ...]
    """
    results = []
    seen = set()

    for pattern in rules.get("search_items", []):
        matches = re.findall(pattern, html)
        for match in matches:
            if isinstance(match, tuple):
                href, title = match[0], match[1]
            else:
                href = match
                title = "未知"

            clean_title = rules.get("search_title_clean", lambda t: t)(title)
            if not clean_title or clean_title in seen:
                continue
            seen.add(clean_title)

            full_url = rules["base_url"].rstrip("/") + href
            results.append({
                "title": clean_title,
                "url": full_url,
                "site": rules["name"],
                "domain": rules["domain"],
            })

    return results


def get_episodes_from_detail(detail_url: str, rules: Dict, timeout: int = 30) -> List[Dict]:
    """从剧集详情页提取分集列表"""
    import requests
    from core.batch_extractor import extract_episodes_from_page

    # 使用 batch_extractor 的通用提取
    return extract_episodes_from_page(detail_url, rules, timeout)


def get_video_url(episode_url: str, rules: Dict, timeout: int = 30) -> Optional[str]:
    """从播放页提取视频地址"""
    from core.batch_extractor import extract_video_url_from_episode

    return extract_video_url_from_episode(episode_url, rules, timeout)


def get_all_sites() -> List[Dict[str, str]]:
    """获取所有已配置的站点列表"""
    return [
        {"domain": d, "name": r["name"]}
        for d, r in SITE_RULES.items()
    ]
