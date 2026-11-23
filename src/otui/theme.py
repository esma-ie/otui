"""A UI theme from a company's website: its brand colours, made into clean, readable tokens.

Paste a company's website address and otui re-renders in that company's
colours. The steps:

1. ``fetch_site``: the page and its style sheets (``<link rel=stylesheet>``, one level of
   ``@import``), from public addresses only, with limits on size, count and time.
2. ``extract``: every colour the page and its CSS use, weighted: the page's
   ``<meta name="theme-color">`` most, then CSS custom properties named like brand colours
   (``--brand``, ``--primary``, ``--accent``...), then any other use. Near-duplicates are
   merged; greys are kept apart from brand colours.
3. ``tokens``: otui's CSS variables (``frontend/src/app.css``) for light and dark themes, built
   in OKLCH: the brand colours keep their hue, large surfaces get calm, low-chroma tints, and
   every text pair is adjusted until it meets WCAG AA (4.5:1; 7:1 for the header bar, which
   carries the logo's white lettering).

The browser fetches nothing itself (other sites' CSS is not readable cross-origin); the otui
server does, on the user's request only. Addresses that resolve to loopback, private,
link-local or reserved networks are refused, so the feature cannot be used to reach the
user's own network (which matters once otui runs as a shared server).
"""

from __future__ import annotations

import ipaddress
import math
import re
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser

MAX_PAGE = 2_000_000  # bytes
MAX_CSS = 1_500_000
MAX_SHEETS = 12
TIMEOUT = 8  # seconds per request
USER_AGENT = "otui-theme/0.1 (reads public style sheets for colours)"

BRAND_NAME = re.compile(r"brand|primary|accent|secondary|main|theme|highlight|corporate", re.I)


class ThemeError(ValueError):
    """A theme could not be made: the message is for the user."""


# ---- colour maths: sRGB, WCAG contrast, OKLab/OKLCH -------------------------------------------


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def luminance(h: str) -> float:
    r, g, b = (_lin(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def to_oklch(h: str) -> tuple[float, float, float]:
    r, g, b = (_lin(c) for c in hex_rgb(h))
    lm = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    mm = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    sm = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(x) ** (1 / 3), x) for x in (lm, mm, sm))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _oklch_rgb(L: float, C: float, H: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    lm, mm, sm = l_**3, m_**3, s_**3
    r = 4.0767416621 * lm - 3.3077115913 * mm + 0.2309699292 * sm
    g = -1.2684380046 * lm + 2.6097574011 * mm - 0.3413193965 * sm
    bl = -0.0041960863 * lm - 0.7034186147 * mm + 1.7076147010 * sm
    return r, g, bl


def from_oklch(L: float, C: float, H: float) -> str:
    """The sRGB colour nearest (by chroma) to an OKLCH one: chroma is reduced to fit the gamut."""
    L = max(0.0, min(1.0, L))
    lo, hi = 0.0, C
    rgb = _oklch_rgb(L, C, H)
    if all(-1e-4 <= c <= 1 + 1e-4 for c in rgb):
        return rgb_hex(tuple(_gam(max(0, c)) for c in rgb))  # type: ignore[arg-type]
    for _ in range(24):
        mid = (lo + hi) / 2
        if all(-1e-4 <= c <= 1 + 1e-4 for c in _oklch_rgb(L, mid, H)):
            lo = mid
        else:
            hi = mid
    return rgb_hex(tuple(_gam(max(0, c)) for c in _oklch_rgb(L, lo, H)))  # type: ignore[arg-type]


def with_contrast(colour: str, against: str, target: float, lighter: bool | None = None) -> str:
    """``colour`` with its lightness moved (hue and chroma kept) just enough to reach ``target``."""
    if contrast(colour, against) >= target:
        return colour
    L, C, H = to_oklch(colour)
    if lighter is None:
        lighter = luminance(against) < 0.18
    lo, hi = (L, 1.0) if lighter else (0.0, L)
    best = "#ffffff" if lighter else "#000000"
    for _ in range(30):
        mid = (lo + hi) / 2
        c = from_oklch(mid, C, H)
        if contrast(c, against) >= target:
            best = c
            if lighter:
                hi = mid
            else:
                lo = mid
        elif lighter:
            lo = mid
        else:
            hi = mid
    return best


def tone(colour: str, L: float, max_chroma: float | None = None) -> str:
    """``colour``'s hue at lightness ``L``, its chroma capped (for calm, clean surfaces)."""
    _, C, H = to_oklch(colour)
    return from_oklch(L, C if max_chroma is None else min(C, max_chroma), H)


# ---- reading colours from CSS and HTML ------------------------------------------------------

HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
RGB = re.compile(
    r"rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?)(?:[\s,/]+([\d.]+%?))?\s*\)", re.I
)
HSL = re.compile(
    r"hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%(?:[\s,/]+([\d.]+%?))?\s*\)", re.I
)
CUSTOM = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+)")


def _alpha(s: str | None) -> float:
    if not s:
        return 1.0
    return float(s[:-1]) / 100 if s.endswith("%") else float(s)


def _hsl(h: float, s: float, li: float) -> tuple[float, float, float]:
    """CSS hsl() to sRGB (CSS Color 4's algorithm)."""
    a = s * min(li, 1 - li)

    def f(n: int) -> float:
        k = (n + h / 30) % 12
        return li - a * max(-1, min(k - 3, 9 - k, 1))

    return f(0), f(8), f(4)


def colours_in(text: str) -> list[str]:
    """Opaque colours written in ``text`` (hex, rgb(), hsl()), as #rrggbb."""
    out = []
    for m in HEX.finditer(text):
        h = m.group(1)
        if len(h) in (3, 4):
            if len(h) == 4 and int(h[3], 16) < 8:
                continue
            h = "".join(c * 2 for c in h[:3])
        elif len(h) == 8:
            if int(h[6:], 16) < 128:
                continue
            h = h[:6]
        out.append("#" + h.lower())
    for m in RGB.finditer(text):
        if _alpha(m.group(4)) < 0.5:
            continue
        vals = [float(v[:-1]) * 2.55 if v.endswith("%") else float(v) for v in m.groups()[:3]]
        out.append(rgb_hex(tuple(min(255.0, v) / 255 for v in vals)))  # type: ignore[arg-type]
    for m in HSL.finditer(text):
        if _alpha(m.group(4)) < 0.5:
            continue
        h, s, li = float(m.group(1)) % 360, float(m.group(2)) / 100, float(m.group(3)) / 100
        out.append(rgb_hex(_hsl(h, s, li)))
    return out


def weigh(css: str, weights: dict[str, float], factor: float = 1.0) -> None:
    """Add the colours of a style sheet to ``weights``: brand-named custom properties count 20x."""
    for name, value in CUSTOM.findall(css):
        if BRAND_NAME.search(name):
            for c in colours_in(value):
                weights[c] = weights.get(c, 0) + 20 * factor
    for c in colours_in(css):
        weights[c] = weights.get(c, 0) + factor


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sheets: list[str] = []
        self.styles: list[str] = []
        self.theme: list[str] = []
        self.inline: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link" and "stylesheet" in a.get("rel", "").lower() and a.get("href"):
            self.sheets.append(a["href"])
        elif tag == "meta" and a.get("name", "").lower() in (
            "theme-color",
            "msapplication-tilecolor",
        ):
            self.theme.append(a.get("content", ""))
        elif tag == "style":
            self._in_style = True
        if a.get("style"):
            self.inline.append(a["style"])

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.styles.append(data)


@dataclass
class Site:
    url: str
    html: str
    sheets: list[tuple[str, str]] = field(default_factory=list)  # (url, css)


def extract(site: Site) -> dict[str, float]:
    """Every colour of the page and its style sheets, weighted (see the module docstring)."""
    page = _Page()
    page.feed(site.html)
    weights: dict[str, float] = {}
    for t in page.theme:
        for c in colours_in(t):
            weights[c] = weights.get(c, 0) + 50
    for css in page.styles:
        weigh(css, weights)
    for style in page.inline:
        weigh(style, weights, 0.5)
    for _url, css in site.sheets:
        weigh(css, weights)
    return weights


def palette(weights: dict[str, float]) -> tuple[list[str], list[str]]:
    """Brand colours (chromatic clusters, heaviest first) and neutrals, from weighted colours."""
    brand: list[tuple[str, float, tuple[float, float, float]]] = []
    neutral: list[str] = []
    for c, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        L, C, H = to_oklch(c)
        if C < 0.045 or L > 0.97 or L < 0.12:
            neutral.append(c)
            continue
        for i, (bc, bw, (bL, bC, bH)) in enumerate(brand):
            # merge into a cluster within a small OKLab distance
            da = C * math.cos(math.radians(H)) - bC * math.cos(math.radians(bH))
            db = C * math.sin(math.radians(H)) - bC * math.sin(math.radians(bH))
            if math.sqrt((L - bL) ** 2 + da**2 + db**2) < 0.08:
                brand[i] = (bc, bw + w, (bL, bC, bH))
                break
        else:
            brand.append((c, w, (L, C, H)))
    brand.sort(key=lambda b: -b[1])
    return [b[0] for b in brand], neutral


# ---- tokens -------------------------------------------------------------------------------


def _hue_gap(a: str, b: str) -> float:
    d = abs(to_oklch(a)[2] - to_oklch(b)[2]) % 360
    return min(d, 360 - d)


def tokens(brand: list[str]) -> dict[str, dict[str, str]]:
    """otui's CSS variables, light and dark, from a company's brand colours (heaviest first)."""
    if not brand:
        raise ThemeError("no brand colours found on that page")
    primary = brand[0]
    secondary = next((c for c in brand[1:] if _hue_gap(c, primary) > 30), None)
    if secondary is None:
        _, C, H = to_oklch(primary)
        secondary = from_oklch(0.75, max(C, 0.1), (H + 150) % 360)  # a calm complement
    _, pC, pH = to_oklch(primary)

    # categories: brand hues first (distinct by 25 degrees), filled by golden-angle steps from
    # the primary hue, all at one lightness and a capped chroma: clean, and even in weight
    hues: list[tuple[float, float]] = []
    for c in brand:
        _, C, H = to_oklch(c)
        if all(min(abs(H - h) % 360, 360 - abs(H - h) % 360) > 25 for h, _ in hues):
            hues.append((H, C))
        if len(hues) == 7:
            break
    step = 0
    while len(hues) < 7:
        step += 1
        hues.append(((pH + 137.5 * step) % 360, 0.12))

    white, black = "#ffffff", "#0b0b0b"
    bar = with_contrast(tone(primary, min(to_oklch(primary)[0], 0.42), 0.14), white, 7.0)
    light_bg = from_oklch(0.985, min(pC, 0.008), pH)
    accent = with_contrast(tone(primary, min(to_oklch(primary)[0], 0.5), 0.16), white, 4.5)
    notice = tone(secondary, 0.84, 0.13)
    notice_ink = bar if contrast(bar, notice) >= 4.5 else black
    light = {
        "--bg": light_bg,
        "--panel": white,
        "--ink": with_contrast(from_oklch(0.2, min(pC, 0.02), pH), white, 12),
        "--heading": with_contrast(bar, white, 7),
        "--muted": with_contrast(from_oklch(0.5, min(pC, 0.025), pH), light_bg, 4.6),
        "--line": from_oklch(0.9, min(pC, 0.02), pH),
        "--accent": accent,
        "--accent-ink": white if contrast(white, accent) >= 4.5 else black,
        "--rule": tone(secondary, 0.72, 0.16),
        "--bar": bar,
        "--bar-ink": white,
        "--bar-accent": with_contrast(tone(secondary, 0.8, 0.16), bar, 4.5, lighter=True),
        "--process": bar,
        "--node-line": bar,
        "--link": from_oklch(0.8, min(pC, 0.03), pH),
        "--notice": notice,
        "--notice-ink": notice_ink,
    }
    for i, (H, C) in enumerate(hues):
        light[f"--cat-{i + 1}"] = from_oklch(0.6, min(C, 0.15), H)
    light["--com-nrg"], light["--com-dem"], light["--com-mat"] = (
        light["--cat-2"],
        light["--cat-3"],
        light["--cat-4"],
    )
    light["--com-env"] = from_oklch(0.6, 0.01, pH)

    dark_bg = from_oklch(0.2, min(pC, 0.04), pH)
    dark_panel = from_oklch(0.25, min(pC, 0.045), pH)
    dark_accent = with_contrast(tone(secondary, 0.8, 0.16), dark_panel, 4.5, lighter=True)
    dark = {
        "--bg": dark_bg,
        "--panel": dark_panel,
        "--ink": white,
        "--heading": white,
        "--muted": with_contrast(
            from_oklch(0.82, min(pC, 0.03), pH), dark_panel, 4.6, lighter=True
        ),
        "--line": from_oklch(0.35, min(pC, 0.04), pH),
        "--accent": dark_accent,
        "--accent-ink": dark_bg if contrast(dark_bg, dark_accent) >= 4.5 else black,
        "--rule": tone(secondary, 0.72, 0.16),
        "--bar": from_oklch(0.16, min(pC, 0.04), pH),
        "--process": from_oklch(0.88, min(pC, 0.04), pH),
        "--node-line": "transparent",
        "--link": from_oklch(0.45, min(pC, 0.04), pH),
    }
    for i, (H, C) in enumerate(hues):
        dark[f"--cat-{i + 1}"] = from_oklch(0.78, min(C, 0.14), H)
    dark["--com-nrg"], dark["--com-dem"], dark["--com-mat"] = (
        dark["--cat-2"],
        dark["--cat-3"],
        dark["--cat-4"],
    )
    dark["--com-env"] = from_oklch(0.72, 0.01, pH)
    return {"light": light, "dark": dark}


# ---- fetching -----------------------------------------------------------------------------


def check_public(url: str) -> str:
    """``url`` if it is http(s) on a public address; otherwise a ThemeError."""
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ThemeError("give a web address starting http:// or https://")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except OSError as e:
        raise ThemeError(f"cannot find {u.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ThemeError(f"{u.hostname} is not a public address")
    return u.geturl()


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirect)


def _get(url: str, limit: int) -> tuple[str, str]:
    """(final url, text) of a public address, at most ``limit`` bytes."""
    req = urllib.request.Request(check_public(url), headers={"User-Agent": USER_AGENT})
    try:
        with _opener.open(req, timeout=TIMEOUT) as r:
            data = r.read(limit + 1)
            charset = r.headers.get_content_charset() or "utf-8"
            final = r.geturl()
    except ThemeError:
        raise
    except Exception as e:  # noqa: BLE001 - network errors, shown to the user
        raise ThemeError(f"could not read {url}: {e}") from e
    if len(data) > limit:
        data = data[:limit]
    return final, data.decode(charset, errors="replace")


IMPORT = re.compile(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", re.I)


Getter = Callable[[str, int], tuple[str, str]]


def fetch_site(url: str, get: Getter | None = None) -> Site:
    """The page at ``url`` and its style sheets (and their ``@import``s, one level)."""
    get = get or _get
    if "://" not in url:
        url = "https://" + url.strip()
    final, html = get(url, MAX_PAGE)
    page = _Page()
    page.feed(html)
    site = Site(final, html)
    queue = [urllib.parse.urljoin(final, h) for h in page.sheets]
    for css in page.styles:
        queue += [urllib.parse.urljoin(final, h) for h in IMPORT.findall(css)]
    seen: set[str] = set()
    while queue and len(site.sheets) < MAX_SHEETS:
        s = queue.pop(0)
        if s in seen:
            continue
        seen.add(s)
        try:
            surl, css = get(s, MAX_CSS)
        except ThemeError:
            continue  # one unreadable sheet is not fatal
        site.sheets.append((surl, css))
        if s in page.sheets or len(seen) <= len(page.sheets):
            queue += [urllib.parse.urljoin(surl, h) for h in IMPORT.findall(css)]
    return site


def from_url(url: str, get: Getter | None = None) -> dict:
    """The theme for a company's website: its palette, and otui's tokens for light and dark."""
    site = fetch_site(url, get)
    brand, neutral = palette(extract(site))
    return {
        "source": site.url,
        "sheets": len(site.sheets),
        "brand": brand[:8],
        "neutral": neutral[:6],
        **tokens(brand),
    }
