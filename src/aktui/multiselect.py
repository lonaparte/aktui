"""Read opt-in HTML checkbox forms without executing card JavaScript."""
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass
class Choice:
    value: str
    label: str
    exclusive: bool = False


@dataclass
class MultiSelect:
    prompt: str
    choices: list[Choice]
    correct: set[str]
    submit_label: str

    def toggle(self, selected: set[str], index: int) -> None:
        choice = self.choices[index]
        if choice.value in selected:
            selected.remove(choice.value)
        elif choice.exclusive:
            selected.clear()
            selected.add(choice.value)
        else:
            selected.difference_update(c.value for c in self.choices if c.exclusive)
            selected.add(choice.value)

    def feedback(self, selected: set[str]) -> list[str]:
        ordered = lambda values: ", ".join(c.value for c in self.choices if c.value in values)
        return ["Correct." if selected == self.correct else "Incorrect.",
                "Selected: " + ordered(selected), "Correct: " + ordered(self.correct)]


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.attributes = None
        self.choices = []
        self.capture = None
        self.text = {"prompt": [], "submit": []}
        self.pending = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("data-aktui-multiselect") == "1":
            if self.attributes is not None:
                raise ValueError("Only one multiple-choice form is supported per card")
            self.attributes = attrs
            self.active = True
        if not self.active:
            return
        if "data-mc-prompt" in attrs:
            self.capture = "prompt"
        elif tag == "label":
            self.capture = "label"
            self.text["label"] = []
        elif tag == "input" and attrs.get("type") == "checkbox":
            if self.capture != "label" or self.pending is not None:
                raise ValueError("Each checkbox must have a wrapping label")
            self.pending = Choice(attrs.get("value", ""), "", "data-exclusive" in attrs)
        elif tag == "button":
            self.capture = "submit"

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag == "label" and self.pending is not None:
            self.pending.label = "".join(self.text["label"]).strip()
            self.choices.append(self.pending)
            self.pending = None
            self.capture = None
        elif tag == "button" or (tag == "div" and self.capture == "prompt"):
            self.capture = None
        elif tag == "form":
            self.active = False
            self.capture = None

    def handle_data(self, data):
        if self.active and self.capture:
            self.text[self.capture].append(data)


def parse_multiselect(html: str) -> MultiSelect | None:
    parser = _FormParser()
    parser.feed(html)
    if parser.attributes is None:
        return None
    attrs = parser.attributes
    separator = attrs.get("data-separator", ",")
    if not separator:
        raise ValueError("Multiple-choice answer separator must not be empty")
    correct = {value.strip() for value in attrs.get("data-answer", "").split(separator) if value.strip()}
    choices = parser.choices
    values = {choice.value for choice in choices}
    if not choices or len(values) != len(choices) or "" in values or not correct or not correct <= values:
        raise ValueError("Invalid multiple-choice options or answer")
    if len(correct) > 1 and any(choice.exclusive and choice.value in correct for choice in choices):
        raise ValueError("An exclusive answer cannot be combined with other answers")
    if parser.active or parser.pending is not None:
        raise ValueError("Incomplete multiple-choice form")
    return MultiSelect("".join(parser.text["prompt"]).strip(), choices, correct,
                       "".join(parser.text["submit"]).strip() or "Submit")
