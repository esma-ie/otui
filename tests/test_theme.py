"""A UI theme from a company's website, with a fake fetcher (no network)."""

import pytest

from otui import theme
from otui.theme import ThemeError, contrast

HTML = """<!doctype html><html><head>
<meta name="theme-color" content="#c8102e">
<link rel="stylesheet" href="/css/site.css">
<style>@import url("extra.css"); .x { color: #1d1d1d; }</style>
</head><body style="background: #ffffff"><p style="color: rgb(0, 75, 135)">Hi</p></body></html>"""

CSS = {
    "https://example.com/css/site.css": """
:root { --brand-primary: #c8102e; --brand-secondary: #004b87; --grey-100: #f2f2f2; }
a { color: #004b87; } a:hover { color: #c8102e; } .muted { color: #6b6b6b; }
.btn { background: rgba(200, 16, 46, 0.95); } .ghost { color: rgba(0,0,0,0.1); }
.badge { background: hsl(145, 63%, 42%); }
""",
    "https://example.com/extra.css": ".hero { background: #c8102f; }",
}


def fake_get(url, limit):
    if url == "https://example.com":
        return "https://example.com/", HTML
    if url in CSS:
        return url, CSS[url]
    raise ThemeError(f"could not read {url}")


def test_colours_in_css():
    got = theme.colours_in(
        "#abc #aabbccdd #00000010 rgb(10%, 0, 255) hsl(0, 100%, 50%) rgba(1,2,3,.2)"
    )
    assert got == ["#aabbcc", "#aabbcc", "#1a00ff", "#ff0000"]  # transparent ones dropped


def test_brand_colours_come_first():
    site = theme.fetch_site("example.com", fake_get)
    assert [u for u, _ in site.sheets] == list(CSS)
    brand, neutral = theme.palette(theme.extract(site))
    assert brand[0] == "#c8102e"  # theme-color, brand variable, and uses (#c8102f merged in)
    assert brand[1] == "#004b87"
    assert "#c8102f" not in brand
    assert "#6b6b6b" in neutral and "#ffffff" in neutral


def test_tokens_are_readable():
    t = theme.from_url("https://example.com", fake_get)
    assert t["brand"][:2] == ["#c8102e", "#004b87"]
    light, dark = t["light"], t["dark"]
    # text pairs meet WCAG AA in both themes; the bar carries the logo's white lettering at 7:1
    assert contrast(light["--ink"], light["--bg"]) >= 4.5
    assert contrast(light["--muted"], light["--bg"]) >= 4.5
    assert contrast(light["--accent"], light["--panel"]) >= 4.5
    assert contrast(light["--accent-ink"], light["--accent"]) >= 4.5
    assert contrast(light["--bar-ink"], light["--bar"]) >= 7
    assert contrast(light["--bar-accent"], light["--bar"]) >= 4.5
    assert contrast(light["--notice-ink"], light["--notice"]) >= 4.5
    assert contrast(light["--heading"], light["--panel"]) >= 4.5
    assert contrast(dark["--ink"], dark["--panel"]) >= 4.5
    assert contrast(dark["--muted"], dark["--panel"]) >= 4.5
    assert contrast(dark["--accent"], dark["--panel"]) >= 4.5
    assert contrast(dark["--accent-ink"], dark["--accent"]) >= 4.5
    assert all(f"--cat-{i}" in light and f"--cat-{i}" in dark for i in range(1, 8))
    # the bar keeps the brand's hue: a red company gets a deep red bar
    assert abs(theme.to_oklch(light["--bar"])[2] - theme.to_oklch("#c8102e")[2]) < 10


def test_no_brand_colours():
    with pytest.raises(ThemeError, match="no brand colours"):
        theme.tokens([])


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "http://127.0.0.1/",
        "http://localhost:8642/",
        "http://10.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/",
    ],
)
def test_only_public_web_addresses(url):
    with pytest.raises(ThemeError):
        theme.check_public(url)


def test_theme_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from otui.server import create_app

    monkeypatch.setattr(theme, "_get", fake_get)
    with TestClient(create_app(compiler=lambda r, o: {}, root=tmp_path)) as c:
        r = c.post("/api/theme", data={"url": "example.com"})
        assert r.status_code == 200 and r.json()["brand"][0] == "#c8102e"
        monkeypatch.undo()  # the real fetcher: refuses a local address before any request
        bad = c.post("/api/theme", data={"url": "http://127.0.0.1:8642/"})
        assert bad.status_code == 400 and "not a public address" in bad.json()["detail"]


def test_oklch_round_trip():
    for h in ("#c8102e", "#004539", "#eaaa00", "#00a3cb", "#ffffff", "#000000"):
        assert theme.from_oklch(*theme.to_oklch(h)) == h
