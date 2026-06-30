from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from src.navidrome.client import NavidromeClient
from src.navidrome.config import NavidromeConfig


class ConfigScreen(ModalScreen[NavidromeConfig]):
    """Modal asking for the Navidrome URL, username, and password."""

    def __init__(self, config: NavidromeConfig, allow_cancel: bool = False) -> None:
        super().__init__()
        self._config = config
        self._allow_cancel = allow_cancel

    def compose(self) -> ComposeResult:
        with Vertical(id="config-dialog"):
            yield Static("Connect to Navidrome", id="config-title")
            yield Label("Server URL")
            yield Input(
                value=self._config.url,
                placeholder="https://music.example.com",
                id="config-url",
            )
            yield Label("Username")
            yield Input(
                value=self._config.username,
                placeholder="username",
                id="config-username",
            )
            yield Label("Password")
            yield Input(
                value=self._config.password,
                placeholder="password",
                password=True,
                id="config-password",
            )
            yield Static("", id="config-status")
            with Horizontal(id="config-buttons"):
                yield Button("Connect", variant="primary", id="config-connect")
                if self._allow_cancel:
                    yield Button("Cancel", id="config-cancel")

    def on_mount(self) -> None:
        self.query_one("#config-url", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._attempt_connect()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "config-connect":
            self._attempt_connect()
        elif event.button.id == "config-cancel":
            self.dismiss(None)

    def _attempt_connect(self) -> None:
        url = self.query_one("#config-url", Input).value.strip()
        username = self.query_one("#config-username", Input).value.strip()
        password = self.query_one("#config-password", Input).value

        if not (url and username and password):
            self._set_status("All fields are required.", error=True)
            return

        config = NavidromeConfig(url=url, username=username, password=password)
        self._set_status("Connecting...")
        self._set_connecting(True)
        self._connect_worker(config)

    @work(thread=True, exclusive=True)
    def _connect_worker(self, config: NavidromeConfig) -> None:
        try:
            NavidromeClient(config).ping()
        except Exception as error:
            self.app.call_from_thread(
                self._set_status, f"Connection failed: {error}", True
            )
            self.app.call_from_thread(self._set_connecting, False)
            return

        config.save()
        self.app.call_from_thread(self.dismiss, config)

    def _set_connecting(self, connecting: bool) -> None:
        self.query_one("#config-connect", Button).disabled = connecting

    def _set_status(self, message: str, error: bool = False) -> None:
        status = self.query_one("#config-status", Static)
        status.update(message)
        status.set_class(error, "error")
