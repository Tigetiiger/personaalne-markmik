"""Server-side Markdown rendering (app/render.py). Ported from Steam's test_render.py (without page
splitting, drawings and status chips), plus sanitising."""

import time
from html.parser import HTMLParser

from app.render import render_markdown


def text_of(html):
    parts = []
    parser = HTMLParser()
    parser.handle_data = parts.append
    parser.feed(html)
    return "".join(parts)


def test_mermaid_fence_becomes_pre_mermaid():
    html = render_markdown("```mermaid\ngraph LR\n  A --> B\n```\n")
    assert '<pre class="mermaid">' in html and "language-mermaid" not in html
    assert "graph LR\n  A --> B\n" in text_of(html)  # Mermaid reads the text, entities decoded


def test_other_fences_stay_escaped_code():
    html = render_markdown("```python\nif a > b: pass\n```\n")
    assert '<code class="language-python">' in html
    assert "a &gt; b" in html
    # Heading-like lines in code stay as they are (render adds blank lines after real headings).
    assert "# kommentaar\nx\n</code>" in render_markdown("```py\n# kommentaar\nx\n```\n## P\ntekst\n")


def test_gfm_table_renders_in_a_scroll_box():
    html = render_markdown("| a | b |\n|:-:|---|\n| 1 | 2 |\n")
    assert '<div class="tablewrap"><table>' in html
    assert '<td style="text-align:center">1</td>' in html and "<td>2</td>" in html


def test_marktext_extras_render():
    html = render_markdown("~~vana~~ ~ üksik\n\n- [x] tehtud\n- [ ] teha @reede\n\nTekst[^1]\n\n[^1]: Allikas.\n")
    assert "<del>vana</del> ~ üksik" in html
    assert html.count('<input type="checkbox"') == 2 and html.count('disabled="disabled"') == 2
    assert 'checked=""' in html and "teha @reede" in html
    assert 'class="footnote"' in html and 'id="fn:1"' in html and 'href="#fn:1"' in html


def test_math_is_left_for_katex():
    html = render_markdown("Kiirus $v = s / t$.\n\n$$\nE = mc^2\n$$\n")
    assert '<span class="arithmatex">\\(v = s / t\\)</span>' in html
    assert '<div class="arithmatex">\\[' in html


def test_links_and_images_to_notes_and_pictures():
    html = render_markdown("[Teine märge](/m/teine) ja ![x](/pildid/abc.png)\n")
    assert '<a href="/m/teine" rel="noopener noreferrer">Teine märge</a>' in html
    assert '<img alt="x" src="/pildid/abc.png">' in html


def test_aligned_image_becomes_figure_with_caption():
    html = render_markdown(
        '<img src="/pildid/a.png" alt="Joonis" title="Skeem &amp; mõõdud" data-align="right">\n\n'
        "Tekst selle kõrval.\n\n"
        'Lause sees <img src="/x.png" alt="x" data-align="left"> ja edasi.\n\n'
        "![tavaline](/y.png)\n"
    )
    assert (
        '<figure class="aligned aligned-right"><img src="/pildid/a.png" alt="Joonis" title="Skeem &amp; mõõdud" data-align="right">'
        "<figcaption>Skeem &amp; mõõdud</figcaption></figure>"
    ) in html
    assert "<p><figure" not in html
    assert '<figure class="aligned aligned-left"><img src="/x.png" alt="x" data-align="left"></figure>' in html
    assert '<p><img alt="tavaline" src="/y.png"></p>' in html or '<p><img src="/y.png" alt="tavaline"></p>' in html
    assert "<figcaption>" not in render_markdown('<img src="/a.png" title="" alt="a" data-align="right">\n')


def test_callouts_like_github_become_titled_boxes():
    out = render_markdown("> [!WARNING]\n> Aku **36 V**.\n\nVahel.\n\n> [!TIP]\n>\n> Eraldi lõigus.\n")
    assert out.startswith('<div class="callout callout-warning" role="note"><p class="callout-title">Hoiatus</p><p>Aku <strong>36 V</strong>.</p></div>')
    assert '<div class="callout callout-tip" role="note"><p class="callout-title">Nõuanne</p><p>Eraldi lõigus.</p></div>' in out
    assert "callout-caution" in render_markdown("> \\[!CAUTION\\]\n> Pinge.\n")
    assert render_markdown("> Tsitaat.\n") == "<blockquote>\n<p>Tsitaat.</p>\n</blockquote>"
    assert "callout" not in render_markdown("> [!DANGER]\n> x\n")
    assert "callout" not in render_markdown("```\n> [!NOTE]\n```\n")
    assert "callout" not in render_markdown("> Vaata [!NOTE]\n")


def test_callout_holding_a_quote_keeps_it_whole():
    out = render_markdown("> [!NOTE]\n> Välimine.\n>\n> > Sisemine.\n\nPärast.\n")
    assert out.count("<blockquote>") == 1 and out.count("</blockquote>") == 1
    assert out.endswith("</div>\n<p>Pärast.</p>")


def test_raw_html_is_sanitised():
    out = render_markdown(
        '<img src="x" onerror="alert(1)">\n\n<script>alert(2)</script>\n\n'
        '<a href="javascript:alert(3)">a</a> <a href="data:text/html,x">b</a>\n\n'
        '<iframe src="https://evil.tld"></iframe><style>body{}</style>\n\n'
        '<div id="toast" style="position:fixed" onclick="x()">y</div>\n\n'
        '<input type="text" value="x"><form action="/logout"><button>b</button></form>\n\n'
        '<svg><use href="#x"/></svg><object data="x"></object>\n'
    )
    for bad in ("onerror", "alert(2)", "javascript", "data:text", "iframe", "<style", "body{}", 'id="toast"',
                "position", "onclick", 'type="text"', "<form", "<button", "<svg", "<object"):
        assert bad not in out, bad
    assert '<img src="x">' in out and ">y</div>" in out


def test_big_or_broken_input_renders_in_linear_time():
    cases = [
        "## a\n" * 20000,
        '<img data-align="right" src="x"> ' * 3000 + "x" * 100000,
        '<pre><code class="language-mermaid">' * 2000 + "z" * 100000,
        "<table>" * 2000 + "t" * 100000,
        "> [!NOTE]\n" * 5000,
        "## a\nb\n" * 5000,
    ]
    for text in cases:
        start = time.perf_counter()
        render_markdown(text)
        assert time.perf_counter() - start < 2, text[:30]
