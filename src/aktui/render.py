"""Text-only card rendering; never execute HTML, JavaScript, or terminal escapes."""
from html.parser import HTMLParser
import re
import unicodedata


def safe_text(value: str) -> str:
    return "".join(c for c in value if c in "\n\t" or not unicodedata.category(c).startswith("C"))


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("br", "p", "div", "hr", "li", "tr"):
            self.parts.append("\n")
        if tag == "img":
            a = dict(attrs)
            self.parts.append(f"[image: {a.get('alt') or a.get('src', '')}]")
        if tag in ("audio", "video"):
            self.parts.append(f"[{tag}]")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div", "li", "tr") and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(html: str) -> str:
    parser = TextParser()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"\[sound:([^\]]+)\]", r"[audio: \1]", text)
    return safe_text(re.sub(r"\n{3,}", "\n\n", text).strip())
