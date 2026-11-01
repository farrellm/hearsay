"""Feeds (M1.2): parsing, OPML, subscribing, polling and the artwork cache."""

import io
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from hearsay.feeds.opml import Outline, export_opml, parse_opml
from hearsay.feeds.parser import FeedError, parse_duration, parse_feed

from .conftest import FakeWeb, svc

URL = "https://pods.example/gridlock.xml"
ART = "https://pods.example/art.jpg"
NOW = datetime.now(UTC)


def item(
    n: int,
    days_ago: float | None = None,
    *,
    guid: str | None = None,
    extra: str = "",
    audio: str | None = None,
) -> str:
    published = NOW - timedelta(days=n if days_ago is None else days_ago)
    return f"""
    <item>
      <title>Episode {n}</title>
      <guid isPermaLink="false">{guid or f"ep-{n}"}</guid>
      <pubDate>{format_datetime(published)}</pubDate>
      <enclosure url="{audio or f"https://cdn.example/{n}.mp3"}" type="audio/mpeg" length="1000"/>
      <itunes:duration>{n}:00:00</itunes:duration>
      <description>Notes for {n}</description>
      {extra}
    </item>"""


def rss(*items: str, title: str = "Gridlock", image: str | None = ART, extra: str = "") -> str:
    art = f'<itunes:image href="{image}"/>' if image else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{title}</title>
    <link>https://pods.example/</link>
    <language>en-us</language>
    <itunes:author>Dana</itunes:author>
    {art}
    {extra}
    {"".join(items)}
  </channel>
</rss>"""


def subscribe(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/feeds", json={"url": URL, **body})
    assert response.status_code == 201, response.text
    feed: dict[str, Any] = response.json()
    return feed


def tasks(client: TestClient) -> list[tuple[str | None, str, int]]:
    rows = svc(client).db.conn.execute(
        "SELECT e.title, t.stage, t.priority FROM tasks t JOIN episodes e ON e.id = t.episode_id"
        " ORDER BY e.published_at DESC"
    )
    return [tuple(r) for r in rows]


# -- parsing -------------------------------------------------------------------------------


def test_parse_feed_reads_podcast_fields() -> None:
    extra = """
      <content:encoded><![CDATA[<p>Full notes <script>alert(1)</script><b>here</b></p>]]>
      </content:encoded>
      <itunes:image href="https://pods.example/e1.jpg"/>
      <podcast:transcript url="https://pods.example/1.html" type="text/html"/>
      <podcast:transcript url="https://pods.example/1.vtt" type="text/vtt"/>
      <podcast:chapters url="https://pods.example/1.json" type="application/json+chapters"/>
      <podcast:person role="guest" href="https://marcus.example">Marcus</podcast:person>
      <podcast:person>Dana</podcast:person>"""
    feed = parse_feed(rss(item(1, extra=extra), "<item><title>Text post</title></item>").encode())
    assert (feed.title, feed.author, feed.language, feed.image) == (
        "Gridlock",
        "Dana",
        "en-us",
        ART,
    )
    [ep] = feed.episodes  # the item without an enclosure is skipped
    assert ep.guid == "ep-1"
    assert ep.enclosure_url == "https://cdn.example/1.mp3"
    assert ep.enclosure_length == 1000
    assert ep.duration_s == 3600
    assert ep.published_at is not None
    assert datetime.fromisoformat(ep.published_at).tzinfo is not None
    assert ep.description == "<p>Full notes <b>here</b></p>"  # the longer, sanitized
    assert ep.image == "https://pods.example/e1.jpg"
    assert ep.chapters_url == "https://pods.example/1.json"
    assert (ep.transcript_url, ep.transcript_type) == ("https://pods.example/1.vtt", "text/vtt")
    assert [(p.name, p.role) for p in ep.persons] == [("Marcus", "guest"), ("Dana", "host")]


def test_parse_feed_without_guid_keys_by_enclosure() -> None:
    xml = rss(
        "<item><title>A</title>"
        '<enclosure url="https://cdn.example/a.mp3" type="audio/mpeg"/></item>'
    )
    [ep] = parse_feed(xml.encode()).episodes
    assert ep.guid == "https://cdn.example/a.mp3"


def test_web_page_is_not_a_feed() -> None:
    with pytest.raises(FeedError, match="isn't an RSS feed"):
        parse_feed(b"<!doctype html><html><body><h1>Gridlock</h1></body></html>")


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("3600", 3600), ("62:03", 3723), ("1:02:03", 3723), ("90.5", 90.5), ("", None), ("x", None)],
)
def test_parse_duration(value: str, seconds: float | None) -> None:
    assert parse_duration(value) == seconds


# -- OPML ----------------------------------------------------------------------------------


def test_opml_round_trip_flattens_folders() -> None:
    data = b"""<?xml version="1.0"?>
    <opml version="1.0"><head/><body>
      <outline text="News">
        <outline type="rss" text="Gridlock" xmlUrl="https://a.example/f.xml"/>
        <outline type="rss" title="Long Division" xmlUrl="https://b.example/f.xml"/>
      </outline>
      <outline type="rss" text="dupe" xmlUrl="https://a.example/f.xml"/>
      <outline type="link" text="not a feed"/>
    </body></opml>"""
    outlines = parse_opml(data)
    assert outlines == [
        Outline("https://a.example/f.xml", "Gridlock"),
        Outline("https://b.example/f.xml", "Long Division"),
    ]
    assert parse_opml(export_opml(outlines)) == outlines


def test_opml_rejects_other_files() -> None:
    with pytest.raises(FeedError, match="isn't OPML"):
        parse_opml(b"<rss/>")
    with pytest.raises(FeedError, match="isn't OPML"):
        parse_opml(b"not xml")


# -- subscribing ---------------------------------------------------------------------------


def test_preview_shows_title_and_durations(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(2), item(1), item(3)))
    response = client.post("/api/feeds/preview", json={"url": URL})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Gridlock"
    assert body["feed_id"] is None
    assert body["durations"] == [3600, 7200, 10800]  # newest first


def test_subscribe_queues_the_newest_backfill_n(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(*(item(n) for n in range(1, 6))))
    web.image(ART)
    feed = subscribe(client, backfill_n=2)
    assert feed["title"] == "Gridlock"
    assert feed["first_polled_at"] is not None
    assert tasks(client) == [("Episode 1", "download", 0), ("Episode 2", "download", 0)]

    [summary] = client.get("/api/feeds").json()
    assert summary["episode_count"] == 5
    assert summary["unplayed"] == 2
    assert summary["newest_title"] == "Episode 1"
    assert summary["artwork"] is not None

    detail = client.get(f"/api/feeds/{feed['id']}").json()
    assert [e["title"] for e in detail["episodes"]][:2] == ["Episode 1", "Episode 2"]
    assert [e["task"] for e in detail["episodes"]] == ["pending", "pending", None, None, None]
    assert detail["feed"]["artwork"] == summary["artwork"]


def test_subscribe_errors(client: TestClient, web: FakeWeb) -> None:
    web.routes["https://pods.example/page"] = lambda _r: httpx2.Response(
        200, text="<html><body>A podcast</body></html>"
    )
    response = client.post("/api/feeds", json={"url": "https://pods.example/page"})
    assert response.status_code == 422
    assert "isn't an RSS feed" in response.json()["detail"]

    response = client.post("/api/feeds", json={"url": "https://pods.example/missing"})
    assert response.status_code == 422
    assert response.json()["detail"] == "pods.example answered HTTP 404 for the feed."

    response = client.post("/api/feeds", json={"url": "gridlock"})
    assert response.status_code == 422
    assert "isn't a web link" in response.json()["detail"]

    web.feed(URL, rss(item(1)))
    subscribe(client)
    response = client.post("/api/feeds", json={"url": URL})
    assert response.status_code == 409
    assert response.json()["detail"] == "You already subscribe to Gridlock."
    assert client.post("/api/feeds/preview", json={"url": URL}).json()["feed_id"] is not None


def test_subscribe_follows_a_permanent_move(client: TestClient, web: FakeWeb) -> None:
    web.redirect(URL, "https://new.example/feed")
    web.feed("https://new.example/feed", rss(item(1)))
    feed = subscribe(client)
    assert feed["url"] == "https://new.example/feed"


# -- polling -------------------------------------------------------------------------------


def test_new_episodes_of_auto_feeds_are_queued(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(5), item(6)))
    feed = subscribe(client, backfill_n=0)
    assert tasks(client) == []

    # A new episode, and an old one the host republished: only the new one is queued.
    web.feed(URL, rss(item(1, guid="new"), item(5), item(6), item(400, guid="old")))
    refreshed = client.post(f"/api/feeds/{feed['id']}/refresh").json()
    assert refreshed["error"] is None
    assert tasks(client) == [("Episode 1", "download", 10)]
    assert client.get("/api/feeds").json()[0]["unplayed"] == 2  # new + republished


def test_manual_feeds_queue_nothing_on_their_own(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(5)))
    feed = subscribe(client, backfill_n=0, policy="manual")
    web.feed(URL, rss(item(1), item(5)))
    client.post(f"/api/feeds/{feed['id']}/refresh")
    assert tasks(client) == []

    episodes = client.get(f"/api/feeds/{feed['id']}").json()["episodes"]
    assert client.post(f"/api/episodes/{episodes[0]['id']}/process").status_code == 204
    assert tasks(client) == [("Episode 1", "download", 10)]
    # Transcribe now raises it; asking again doesn't add a second task.
    client.post(f"/api/episodes/{episodes[0]['id']}/process", json={"now": True})
    assert tasks(client) == [("Episode 1", "download", 30)]
    assert client.post("/api/episodes/999/process").status_code == 404


def test_conditional_get_and_changed_enclosure(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(1)), etag='"v1"')
    feed = subscribe(client)
    client.post(f"/api/feeds/{feed['id']}/refresh")
    assert web.requests[-1].headers["if-none-match"] == '"v1"'
    episode = svc(client).db.conn.execute("SELECT id, enclosure_url FROM episodes").fetchone()

    # The host moves the file but keeps the guid: same episode, new URL.
    web.feed(URL, rss(item(1, audio="https://cdn2.example/1.mp3")), etag='"v2"')
    client.post(f"/api/feeds/{feed['id']}/refresh")
    rows = svc(client).db.conn.execute("SELECT id, enclosure_url FROM episodes").fetchall()
    assert [tuple(r) for r in rows] == [(episode["id"], "https://cdn2.example/1.mp3")]


def test_poll_failure_is_recorded_and_cleared(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(1)))
    feed = subscribe(client)
    del web.routes[URL]
    failed = client.post(f"/api/feeds/{feed['id']}/refresh").json()
    assert failed["error"] == "pods.example answered HTTP 404 for the feed."
    assert failed["next_poll_at"] > failed["last_polled_at"]
    assert client.get("/api/feeds").json()[0]["error"] == failed["error"]

    web.feed(URL, rss(item(1)))
    assert client.post(f"/api/feeds/{feed['id']}/refresh").json()["error"] is None


def test_dormant_feeds_are_polled_daily(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(1, days_ago=200)))
    feed = subscribe(client)
    due = datetime.fromisoformat(feed["next_poll_at"]) - datetime.now(UTC)
    assert timedelta(hours=21) < due < timedelta(hours=27)

    web.feed("https://pods.example/live.xml", rss(item(1), title="Live"))
    live = client.post("/api/feeds", json={"url": "https://pods.example/live.xml"}).json()
    due = datetime.fromisoformat(live["next_poll_at"]) - datetime.now(UTC)
    assert timedelta(minutes=50) < due < timedelta(minutes=70)


async def test_poller_polls_due_feeds_and_backfills_on_first_success(
    client: TestClient, web: FakeWeb
) -> None:
    services = svc(client)
    services.feeds.import_opml(
        f'<opml version="2.0"><body><outline xmlUrl="{URL}" text="G"/></body></opml>'.encode(),
        backfill_n=1,
    )
    [feed_id] = services.db.feeds_due(datetime.now(UTC).isoformat())
    web.feed(URL, rss(item(1), item(2)))
    # The poller's own loop body: what's due, polled.
    await services.feeds.poll(feed_id)
    assert services.db.feeds_due(datetime.now(UTC).isoformat()) == []
    assert tasks(client) == [("Episode 1", "download", 0)]


def test_unsubscribe_and_patch(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(1)))
    feed = subscribe(client)
    patched = client.patch(f"/api/feeds/{feed['id']}", json={"policy": "manual"}).json()
    assert patched["policy"] == "manual"
    assert client.delete(f"/api/feeds/{feed['id']}").status_code == 204
    assert client.get("/api/feeds").json() == []
    assert client.get(f"/api/feeds/{feed['id']}").status_code == 404
    assert svc(client).db.conn.execute("SELECT count(*) FROM episodes").fetchone()[0] == 0


# -- OPML over the API ---------------------------------------------------------------------


def test_import_and_export_opml(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(1)))
    subscribe(client)
    opml = f"""<opml version="2.0"><body>
      <outline type="rss" text="Gridlock" xmlUrl="{URL}"/>
      <outline type="rss" text="Long Division" xmlUrl="https://b.example/f.xml"/>
    </body></opml>"""
    response = client.post("/api/feeds/import-opml", content=opml)
    assert response.json() == {"added": 1, "skipped": 1}
    titles = {f["title"] for f in client.get("/api/feeds").json()}
    assert titles == {"Gridlock", "Long Division"}

    exported = client.get("/api/feeds/export-opml")
    assert exported.headers["content-type"].startswith("text/x-opml")
    assert {o.url for o in parse_opml(exported.content)} == {URL, "https://b.example/f.xml"}

    assert client.post("/api/feeds/import-opml", content=b"nope").status_code == 422


# -- artwork -------------------------------------------------------------------------------


def test_artwork_is_cached_once_and_served(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(1)))
    web.image(ART, size=(1400, 1000))
    feed = subscribe(client)
    sha = client.get("/api/feeds").json()[0]["artwork"]
    for size, dims in ((512, (512, 366)), (128, (128, 91))):
        response = client.get(f"/api/images/{sha}-{size}.webp")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert "immutable" in response.headers["cache-control"]
        assert Image.open(io.BytesIO(response.content)).size == dims
    client.post(f"/api/feeds/{feed['id']}/refresh")
    assert web.fetched(ART) == 1
    assert client.get("/api/images/../hearsay.db").status_code == 404
    assert client.get(f"/api/images/{sha}-64.webp").status_code == 404


def test_broken_artwork_is_not_retried_at_once(client: TestClient, web: FakeWeb) -> None:
    web.feed(URL, rss(item(1)))
    web.routes[ART] = lambda _r: httpx2.Response(200, content=b"not an image")
    feed = subscribe(client)
    assert client.get("/api/feeds").json()[0]["artwork"] is None
    client.post(f"/api/feeds/{feed['id']}/refresh")
    assert web.fetched(ART) == 1
