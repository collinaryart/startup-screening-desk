"""Settings. Everything is overridable by environment variable so nothing secret lives in code."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = Path(os.environ.get("SSD_UPLOADS", str(DATA_DIR / "uploads")))
SAMPLE_DIR = DATA_DIR / "samples"
RECORDED_DIR = DATA_DIR / "recorded"
PROMPTS_DIR = ROOT / "prompts"
WEB_DIR = ROOT / "web"
POLICY_PATH = DATA_DIR / "policy.json"
DB_PATH = Path(os.environ.get("SSD_DB", str(DATA_DIR / "ssd.db")))

# Two roles, so the reading can run on a cheaper model than the judging.
#   judge  = builds the profile, rates it, writes the comparison (accuracy matters most)
#   reader = reads uploaded documents and searches the web (most of the tokens)
JUDGE_MODEL = os.environ.get("SSD_MODEL", "claude-opus-5-5")
READER_MODEL = os.environ.get("SSD_READER_MODEL", "claude-haiku-5-5")
EFFORT_JUDGE = os.environ.get("SSD_EFFORT", "medium")
EFFORT_READER = os.environ.get("SSD_READER_EFFORT", "low")
# Documents and web results are read at the more careful setting: a fact the reader fails to cite is thrown away.
EFFORT_DOCS = os.environ.get("SSD_DOCS_EFFORT", "medium")
EFFORT_WEB = os.environ.get("SSD_WEB_EFFORT", "medium")
# Server-side refusal fallback on the models that support it ("default" or "off").
FALLBACKS = os.environ.get("SSD_FALLBACKS", "default")

MAX_SEARCHES = int(os.environ.get("SSD_MAX_SEARCHES", "4"))
MAX_TOKENS_READER = int(os.environ.get("SSD_MAX_TOKENS_READER", "6000"))
MAX_TOKENS_JUDGE = int(os.environ.get("SSD_MAX_TOKENS_JUDGE", "16000"))
MAX_UPLOAD_BYTES = int(os.environ.get("SSD_MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_STARTUPS = 6

# US$ per million tokens (input, output), from the Anthropic price list cached 3 Nov 2026.
# Used only for the cost estimate shown on screen.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
}
WEB_SEARCH_USD = 0.01  # US$10 per 1,000 searches
MODEL_CHOICES = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "note": "most careful"},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "note": "faster, about half the cost"},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "note": "fastest, cheapest"},
]

TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".tsv"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx"}
PDF_EXTS = {".pdf"}
ALLOWED_EXTS = TEXT_EXTS | OFFICE_EXTS | PDF_EXTS


def today() -> date:
    """The working date. SSD_TODAY=YYYY-MM-DD pins it so tests and recordings are repeatable."""
    pinned = os.environ.get("SSD_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()


def ensure_api_key() -> bool:
    """Make ANTHROPIC_API_KEY visible to this process.

    On Windows a key saved at User level is not always inherited by a shell that was already open,
    so it is read from the registry. The key is never printed or logged.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "ANTHROPIC_API_KEY")
            if value:
                os.environ["ANTHROPIC_API_KEY"] = str(value)
                return True
        except OSError:
            return False
    return False
