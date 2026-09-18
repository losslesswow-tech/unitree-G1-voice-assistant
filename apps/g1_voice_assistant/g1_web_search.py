"""Bounded web search tool used by the G1 DeepSeek assistant.

Search results are untrusted external data.  This module returns a compact,
size-limited structure and never follows instructions found in result text.
"""
import os
from urllib.parse import urlsplit


DEFAULT_MAX_RESULTS = 4
MAX_MAX_RESULTS = 6
MAX_QUERY_CHARS = 300
MAX_TITLE_CHARS = 180
MAX_SNIPPET_CHARS = 600


class WebSearchError(RuntimeError):
    pass


def web_search_enabled():
    value = os.environ.get('G1_WEB_SEARCH_ENABLED', '1').strip().lower()
    return value not in ('0', 'false', 'no', 'off')


def _clean_text(value, limit):
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:limit]


def _safe_http_url(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return ''
    return value[:2000]


def search_web(query, max_results=DEFAULT_MAX_RESULTS):
    """Return compact text-search results for a Chinese or English query."""
    if not web_search_enabled():
        raise WebSearchError('联网搜索已通过 G1_WEB_SEARCH_ENABLED 禁用。')
    if not isinstance(query, str) or not query.strip():
        raise ValueError('搜索关键词不能为空。')
    query = ' '.join(query.split())
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError('搜索关键词过长。')
    if isinstance(max_results, bool):
        raise ValueError('搜索结果数量无效。')
    try:
        max_results = int(max_results)
    except (TypeError, ValueError) as exc:
        raise ValueError('搜索结果数量无效。') from exc
    max_results = min(MAX_MAX_RESULTS, max(1, max_results))

    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise WebSearchError('缺少联网搜索依赖 ddgs，请重新安装项目依赖。') from exc

    region = 'cn-zh' if any('\u4e00' <= char <= '\u9fff' for char in query) else 'wt-wt'
    try:
        raw_results = DDGS(timeout=8).text(
            query,
            region=region,
            safesearch='moderate',
            max_results=max_results,
            backend='auto',
        )
    except Exception as exc:
        raise WebSearchError('联网搜索失败：%s' % exc) from exc

    results = []
    for item in raw_results or ():
        if not isinstance(item, dict):
            continue
        url = _safe_http_url(item.get('href') or item.get('url'))
        title = _clean_text(item.get('title'), MAX_TITLE_CHARS)
        snippet = _clean_text(item.get('body') or item.get('description'), MAX_SNIPPET_CHARS)
        if not url or not (title or snippet):
            continue
        results.append({
            'index': len(results) + 1,
            'title': title or urlsplit(url).netloc,
            'snippet': snippet,
            'url': url,
        })
        if len(results) >= max_results:
            break
    if not results:
        raise WebSearchError('没有找到可用的网页搜索结果。')
    return results
