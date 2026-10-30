"""Search and fetch: the researcher agents' two tools.

Search returns candidate URLs; fetch downloads a page over plain HTTP, refuses internal addresses (SSRF
guard), caps size and time, and extracts readable text plus a publish date. Only URLs returned by the
search provider are ever fetched — a page cannot make the agent visit a link it contains.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "DeepResearchAgent/1.0 (portfolio research bot; respects robots via search APIs)"

# Second-level labels under which the registrable domain has three labels (bbc.co.uk, abc.net.au).
_MULTI_PART_SUFFIXES = {"co", "com", "net", "org", "gov", "ac", "edu"}


class ToolError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str = ""
    published: str | None = None
    content: str | None = None  # some search APIs return page text; used only if our own fetch fails


@dataclass
class FetchedPage:
    url: str
    final_url: str
    title: str | None
    text: str
    raw: str
    content_type: str
    published_at: date | None
    content_hash: str
    shingle_hash: str
    domain: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[SearchHit]: ...


def registrable_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _MULTI_PART_SUFFIXES and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def normalize_text(text: str) -> str:
    """Whitespace-, case- and quote-style-insensitive form used to check that a quote is verbatim."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    q = normalize_text(quote).strip(" .\"'")
    return len(q) >= 8 and q in normalize_text(text)


def shingle_fingerprint(text: str, size: int = 8, keep: int = 64) -> str:
    """A cheap near-duplicate fingerprint: hash of the smallest word-shingle hashes (MinHash-style).

    Ten sites republishing one press release produce the same fingerprint, so they count as one origin.
    """
    words = normalize_text(text).split()
    if len(words) < size:
        return hashlib.sha256(" ".join(words).encode()).hexdigest()
    hashes = sorted({hashlib.md5(" ".join(words[i:i + size]).encode()).hexdigest()[:12]
                     for i in range(0, len(words) - size + 1)})[:keep]
    return hashlib.sha256("".join(hashes).encode()).hexdigest()


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


async def check_url_allowed(url: str, allow_private: bool = False) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError("blocked_url", "Only http(s) URLs can be fetched.")
    if not allow_private and not await asyncio.to_thread(_is_public_host, parts.hostname):
        raise ToolError("blocked_url", "Refusing to fetch an internal or unresolvable address.")


_DATE_META = ("article:published_time", "datePublished", "pubdate", "publishdate", "date", "dc.date",
              "dc.date.issued", "citation_publication_date", "og:published_time", "article:modified_time")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value) or re.search(r"(\d{4})/(\d{2})/(\d{2})", value)
    if not match:
        return None
    try:
        parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return parsed if date(1990, 1, 1) <= parsed <= datetime.now().date() else None


def extract_page(html: str) -> tuple[str | None, str, date | None]:
    """Readable text, title and publish date from HTML. A small readability-style extractor."""
    soup = BeautifulSoup(html, "html.parser")
    published = None
    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or meta.get("itemprop") or "").strip()
        if key in _DATE_META:
            published = _parse_date(meta.get("content"))
            if published:
                break
    if published is None:
        time_tag = soup.find("time", attrs={"datetime": True})
        published = _parse_date(time_tag.get("datetime")) if time_tag else None
    if published is None:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', script.get_text() or "")
            if match and (published := _parse_date(match.group(1))):
                break
    title = soup.title.get_text(strip=True) if soup.title else None
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg",
                     "iframe", "button", "figure"]):
        tag.decompose()
    for tag in soup.select("sup.reference, .mw-editsection, .navbox, .infobox, .sidebar, table.metadata"):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = []
    for node in root.find_all(["p", "li", "h1", "h2", "h3", "h4", "td", "blockquote", "pre"]):
        if node.find(["p", "li"]):
            continue  # take leaf blocks only, so text is not duplicated
        line = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(line) >= 25 or node.name in ("h1", "h2", "h3"):
            blocks.append(line)
    text = "\n".join(blocks)
    if len(text) < 200:  # pages without semantic markup
        text = re.sub(r"\s+", " ", root.get_text(" ", strip=True))
    return title, text, published


def page_from_html(url: str, final_url: str, raw: str, content_type: str, raw_cap: int) -> FetchedPage:
    if "html" in content_type or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, text, published = extract_page(raw)
    else:
        title, text, published = None, raw, None
    return FetchedPage(url=url, final_url=final_url, title=title, text=text, raw=raw[:raw_cap],
                       content_type=content_type, published_at=published,
                       content_hash=hashlib.sha256(text.encode()).hexdigest(),
                       shingle_hash=shingle_fingerprint(text), domain=registrable_domain(final_url))


@dataclass
class HttpFetcher:
    timeout: float = 12.0
    max_bytes: int = 3_000_000
    allow_private: bool = False
    client: httpx.AsyncClient | None = None

    async def fetch(self, url: str) -> FetchedPage:
        await check_url_allowed(url, self.allow_private)
        client = self.client or httpx.AsyncClient(follow_redirects=False, timeout=self.timeout,
                                                  headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
        try:
            current = url
            for _ in range(5):  # follow redirects manually so every hop passes the SSRF check
                async with client.stream("GET", current) as response:
                    if response.is_redirect and response.headers.get("location"):
                        current = str(response.url.join(response.headers["location"]))
                        await check_url_allowed(current, self.allow_private)
                        continue
                    if response.status_code >= 400:
                        raise ToolError("http_error", f"HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").lower()
                    if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml")):
                        raise ToolError("unsupported_type", f"Unsupported content type {content_type or 'unknown'}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise ToolError("too_large", "Page is larger than the fetch limit.")
                    encoding = response.encoding or "utf-8"
                    raw = body.decode(encoding, errors="replace")
                    return page_from_html(url, current, raw, content_type, self.max_bytes)
            raise ToolError("too_many_redirects", "Too many redirects.")
        except httpx.TimeoutException as exc:
            raise ToolError("timeout", "The page took too long to load.") from exc
        except httpx.HTTPError as exc:
            raise ToolError("network_error", f"Could not fetch the page ({type(exc).__name__}).") from exc
        finally:
            if self.client is None:
                await client.aclose()


@dataclass
class TavilySearch:
    api_key: str
    timeout: float = 15.0
    name: str = "tavily"

    async def search(self, query: str, limit: int) -> list[SearchHit]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post("https://api.tavily.com/search",
                                             headers={"Authorization": f"Bearer {self.api_key}"},
                                             json={"query": query, "max_results": limit, "search_depth": "basic",
                                                   "include_raw_content": False})
            except httpx.HTTPError as exc:
                raise ToolError("network_error", f"Search request failed ({type(exc).__name__}).") from exc
        if response.status_code in (401, 403):
            raise ToolError("auth_error", "The Tavily API key was rejected (check TAVILY_API_KEY).")
        if response.status_code == 429 or response.status_code == 432:
            raise ToolError("rate_limited", "Search quota or rate limit reached.")
        if response.status_code >= 400:
            raise ToolError("search_error", f"Search failed with HTTP {response.status_code}.")
        results = response.json().get("results", [])
        return [SearchHit(url=r["url"], title=r.get("title") or r["url"], snippet=(r.get("content") or "")[:500],
                          published=r.get("published_date"), content=r.get("content"))
                for r in results if r.get("url")]


@dataclass
class WikipediaSearch:
    """Keyless fallback. Narrow (one publisher), so claims from it count as a single domain."""

    timeout: float = 15.0
    name: str = "wikipedia"

    async def search(self, query: str, limit: int) -> list[SearchHit]:
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": USER_AGENT}) as client:
            try:
                response = await client.get("https://en.wikipedia.org/w/rest.php/v1/search/page",
                                            params={"q": query, "limit": limit})
            except httpx.HTTPError as exc:
                raise ToolError("network_error", f"Search request failed ({type(exc).__name__}).") from exc
        if response.status_code >= 400:
            raise ToolError("search_error", f"Wikipedia search failed with HTTP {response.status_code}.")
        hits = []
        for page in response.json().get("pages", []):
            title = page.get("title") or page.get("key")
            snippet = re.sub(r"<[^>]+>", "", page.get("excerpt") or "")
            hits.append(SearchHit(url=f"https://en.wikipedia.org/wiki/{quote(page['key'])}", title=title,
                                  snippet=snippet))
        return hits


@dataclass
class StaticWeb:
    """An offline web for tests, demos and the simulated benchmark: canned search results and pages."""

    results: dict[str, list[SearchHit]] = field(default_factory=dict)  # query substring -> hits
    pages: dict[str, str] = field(default_factory=dict)                 # url -> html
    default_hits: list[SearchHit] = field(default_factory=list)
    name: str = "static"
    latency: float = 0.0
    queries: list[str] = field(default_factory=list)

    async def search(self, query: str, limit: int) -> list[SearchHit]:
        self.queries.append(query)
        if self.latency:
            await asyncio.sleep(self.latency)
        for needle, hits in self.results.items():
            if needle.lower() in query.lower():
                return hits[:limit]
        return self.default_hits[:limit]

    async def fetch(self, url: str) -> FetchedPage:
        if self.latency:
            await asyncio.sleep(self.latency)
        if url not in self.pages:
            raise ToolError("http_error", "HTTP 404")
        return page_from_html(url, url, self.pages[url], "text/html", 1_000_000)
