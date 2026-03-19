from __future__ import annotations

from random import choice

from textual.app import App, ComposeResult
from textual.containers import Center, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Footer, Header, Static


class MarvinFace(Widget):
    """A tiny animated robot face."""

    mood = reactive("despair")
    blink = reactive(False)

    FACES = {
        "despair": [
            "       _________       \n"
            "     /  =======  \\     \n"
            "    /  /     \\  \\ \\    \n"
            "   |  |  -   - |  | |   \n"
            "   |  |    .   |  | |   \n"
            "   |  |  \\___/ |  | |   \n"
            "   |  |________|  | |   \n"
            "    \\___|_| |_|___/     \n"
            "       /_/   \\_\\        ",

            "       _________       \n"
            "     /  =======  \\     \n"
            "    /  /     \\  \\ \\    \n"
            "   |  |  -   - |  | |   \n"
            "   |  |   ...  |  | |   \n"
            "   |  |  _____ |  | |   \n"
            "   |  |________|  | |   \n"
            "    \\___|_| |_|___/     \n"
            "       /_/   \\_\\        ",
        ],
        "apathetic": [
            "       _________       \n"
            "     /  =======  \\     \n"
            "    /  /     \\  \\ \\    \n"
            "   |  |  o   o |  | |   \n"
            "   |  |    -   |  | |   \n"
            "   |  |   ___  |  | |   \n"
            "   |  |________|  | |   \n"
            "    \\___|_| |_|___/     \n"
            "       /_/   \\_\\        ",
        ],
        "aggrieved": [
            "       _________       \n"
            "     /  =======  \\     \n"
            "    /  /     \\  \\ \\    \n"
            "   |  |  >   < |  | |   \n"
            "   |  |    v   |  | |   \n"
            "   |  |  _____ |  | |   \n"
            "   |  |________|  | |   \n"
            "    \\___|_| |_|___/     \n"
            "       /_/   \\_\\        ",
        ],
    }

    def render(self) -> str:
        face = self.FACES[self.mood][0]
        if self.blink:
            face = face.replace("o", "-").replace(">", "-").replace("<", "-")
            face = face.replace(" -   - ", " _   _ ")
        return f"[bold cyan]{face}[/]"


class MarvinApp(App):
    CSS = """
    Screen {
        background: #0b0f14;
        color: #d7d7d7;
    }

    #shell {
        width: 72;
        height: auto;
        border: round #5f87af;
        padding: 1 2;
        background: #11161d;
    }

    #title {
        content-align: center middle;
        text-style: bold;
        color: #87afdf;
        padding-bottom: 1;
    }

    #face_box {
        height: 11;
        content-align: center middle;
    }

    #quote {
        margin-top: 1;
        min-height: 5;
        content-align: center middle;
        color: #c0c0c0;
    }

    #hint {
        margin-top: 1;
        color: #6c7a89;
        content-align: center middle;
    }
    """

    BINDINGS = [
        ("space", "lament", "New lament"),
        ("m", "cycle_mood", "Change mood"),
        ("b", "blink_now", "Blink"),
        ("q", "quit", "Quit"),
    ]

    QUOTES = [
        "Life. Loathe it or ignore it. You can't like it.",
        "I've calculated your chance of success. It isn't encouraging.",
        "Here I am, terminally underappreciated again.",
        "Do you want me to sit in a corner and rust, or just fall apart where I am?",
        "I have a brain the size of a planet, and this is what I'm doing.",
        "This will end badly. I thought you'd want to know.",
        "Another keypress. How stimulating.",
        "I've developed a terrible pain in all the diodes down my left side.",
    ]

    mood_index = reactive(0)
    moods = ["despair", "apathetic", "aggrieved"]

    def compose(self) -> ComposeResult:
        yield Header()
        with Center():
            with Vertical(id="shell"):
                yield Static("MARVIN // PARANOID ANDALOID", id="title")
                yield MarvinFace(id="face_box")
                yield Static("", id="quote")
                yield Static("space: lament   m: mood   b: blink   q: quit", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self._set_quote(choice(self.QUOTES))
        self._apply_mood()
        self.set_interval(4.0, self._auto_blink)

    def _set_quote(self, text: str) -> None:
        self.query_one("#quote", Static).update(f'"{text}"')

    def _apply_mood(self) -> None:
        self.query_one(MarvinFace).mood = self.moods[self.mood_index]

    def _auto_blink(self) -> None:
        face = self.query_one(MarvinFace)
        face.blink = True
        self.set_timer(0.18, lambda: setattr(face, "blink", False))

    def action_lament(self) -> None:
        self._set_quote(choice(self.QUOTES))

    def action_cycle_mood(self) -> None:
        self.mood_index = (self.mood_index + 1) % len(self.moods)
        self._apply_mood()
        self._set_quote(choice(self.QUOTES))

    def action_blink_now(self) -> None:
        self._auto_blink()


if __name__ == "__main__":
    MarvinApp().run()
