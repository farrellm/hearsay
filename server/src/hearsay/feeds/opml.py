"""OPML import and export (design.md §4): the subscription lists podcast apps trade."""

from dataclasses import dataclass
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from hearsay.feeds.parser import FeedError


@dataclass(frozen=True)
class Outline:
    url: str
    title: str | None


def parse_opml(data: bytes) -> list[Outline]:
    """Every feed in an OPML file, in order, without repeats. Folders are flattened."""
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, DefusedXmlException):
        raise FeedError("That file isn't OPML. Export it again from your podcast app.") from None
    if root.tag.lower() != "opml":
        raise FeedError("That file isn't OPML. Export it again from your podcast app.")
    seen: set[str] = set()
    outlines = []
    for el in root.iter("outline"):
        url = (el.get("xmlUrl") or el.get("xmlurl") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        title = (el.get("title") or el.get("text") or "").strip() or None
        outlines.append(Outline(url, title))
    return outlines


def export_opml(outlines: list[Outline]) -> bytes:
    opml = Element("opml", version="2.0")
    head = SubElement(opml, "head")
    SubElement(head, "title").text = "Hearsay subscriptions"
    body = SubElement(opml, "body")
    for o in outlines:
        title = o.title or o.url
        SubElement(body, "outline", type="rss", text=title, title=title, xmlUrl=o.url)
    indent(opml)
    xml: bytes = tostring(opml, encoding="utf-8", xml_declaration=True)
    return xml + b"\n"
