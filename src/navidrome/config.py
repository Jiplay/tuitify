from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


CONFIG_DIR = Path(
    os.environ.get("TUITIFY_CONFIG_DIR") or (Path.home() / ".config" / "tuitify")
)
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class NavidromeConfig:
    """Connection settings for a Navidrome (Subsonic) server."""

    url: str = ""
    username: str = ""
    password: str = ""

    @property
    def base_url(self) -> str:
        return self.url.strip().rstrip("/")

    @property
    def is_complete(self) -> bool:
        return bool(self.base_url and self.username.strip() and self.password)

    @classmethod
    def load(cls) -> "NavidromeConfig":
        """Load config from the config file, with environment overrides."""
        config = cls()

        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if isinstance(data, dict):
                config.url = str(data.get("url") or "")
                config.username = str(data.get("username") or "")
                config.password = str(data.get("password") or "")

        config.url = os.environ.get("NAVIDROME_URL", config.url)
        config.username = os.environ.get("NAVIDROME_USERNAME", config.username)
        config.password = os.environ.get("NAVIDROME_PASSWORD", config.password)

        return config

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(
                {
                    "url": self.base_url,
                    "username": self.username.strip(),
                    "password": self.password,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
