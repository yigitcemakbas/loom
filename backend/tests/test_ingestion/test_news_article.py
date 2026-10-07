"""Retrieving the body of a news article, and declining to invent one.

News is Loom's largest corpus and was its least useful: Finnhub returns a
headline and one line of summary, a median of 247 characters against 324,000
for a 10-K, which is why news yielded 0.02 findings per document against 0.68.

The tests about *not* storing something matter as much as the extraction ones.
A nav bar or a paywall teaser stored as article text would put words in front of
a model that no journalist wrote, and a model cannot tell the difference.
"""

from app.ingestion.scrapers.news_article import (
    MIN_BODY_CHARS,
    ArticleBody,
    extract_body,
    is_paywalled,
)

_SENTENCE = "The company reported revenue growth across its principal segments. "


def _long(times: int = 12) -> str:
    return _SENTENCE * times


# ---- extraction ------------------------------------------------------------


def test_an_article_container_is_preferred_over_the_whole_page():
    html = (
        "<html><body><nav>Home Markets Opinion</nav>"
        f"<article><p>{_long()}</p></article>"
        "<aside class='related'>More like this</aside></body></html>"
    )
    body = extract_body(html)

    assert body and len(body) >= MIN_BODY_CHARS
    assert "Home Markets Opinion" not in body
    assert "More like this" not in body


def test_page_furniture_is_removed_before_extraction_not_filtered_after():
    """Removed first because once promo text is concatenated into the body
    there is no reliable way to tell it from a paragraph."""
    html = (
        "<html><body><article>"
        "<div class='newsletter'>Sign up for our daily briefing</div>"
        f"<p>{_long()}</p>"
        "<div class='advert'>Sponsored content</div>"
        "</article></body></html>"
    )
    body = extract_body(html)

    assert body
    assert "daily briefing" not in body
    assert "Sponsored" not in body


def test_a_page_with_no_semantic_container_falls_back_to_paragraphs():
    """A meaningful share of publishers mark up an article with no container at
    all; the alternative to a heuristic there is storing nothing for them."""
    paragraphs = "".join(f"<p>{_SENTENCE * 2}</p>" for _ in range(6))
    body = extract_body(f"<html><body><div>{paragraphs}</div></body></html>")

    assert body and len(body) >= MIN_BODY_CHARS


def test_captions_and_bylines_are_not_mistaken_for_the_article():
    paragraphs = "".join(f"<p>{_SENTENCE * 2}</p>" for _ in range(6))
    html = f"<html><body><div>{paragraphs}<p>Photo: Reuters</p><p>By A. Writer</p></div></body></html>"

    body = extract_body(html)

    assert body
    assert "Photo: Reuters" not in body
    assert "By A. Writer" not in body


# ---- declining to store something ------------------------------------------


def test_a_page_that_yields_too_little_returns_nothing():
    """Below the floor, what came back is a cookie wall or a nav bar. The stub
    it would displace is about 250 characters, so a short "body" is no
    improvement and is worse for reading like an article without being one."""
    assert extract_body("<html><body><article><p>Too short.</p></article></body></html>") is None
    assert extract_body("<html><body></body></html>") is None
    assert MIN_BODY_CHARS > 250


def test_a_paywall_teaser_is_recognised():
    assert is_paywalled("Subscribe to continue reading this article. " + "x" * 900)
    assert is_paywalled("Sign in to read the full story. " + "x" * 900)


def test_a_subscription_appeal_at_the_end_does_not_discard_a_whole_article():
    """A publisher who closes with a pitch has still given Loom the article,
    and rejecting it over the footer would discard usable text."""
    assert not is_paywalled("x" * 1400 + " Subscribe to continue reading.")


# ---- what the caller is told -----------------------------------------------


def test_an_outcome_is_always_reported_so_the_caller_can_be_honest():
    """The evidence API states per document whether Loom holds the article or
    only its headline. That is only possible if every attempt records what
    happened, including the ones that retrieved nothing."""
    assert ArticleBody(None, "blocked_by_robots").usable is False
    assert ArticleBody(None, "paywalled").usable is False
    assert ArticleBody(None, "unavailable").usable is False
    assert ArticleBody("the article text", "fetched").usable is True
    # An outcome of fetched with no text is not usable either.
    assert ArticleBody(None, "fetched").usable is False
