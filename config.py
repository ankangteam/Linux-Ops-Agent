import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
DB_PATH = Path(os.environ.get("OPS_AGENT_DB", ROOT / "audit.sqlite3"))
PROVIDER = os.environ.get("OPS_PROVIDER", "deepseek").lower()
PROVIDERS = {
    "deepseek": ("DEEPSEEK", "https://api.deepseek.com", "deepseek-flash"),
    "openai": ("OPENAI", "https://api.openai.com/v1", "gpt-6"),
    "qianwen": ("QIANWEN", "https://maas.qianwenaiapi.com/compatible-mode/v1", ""),
}
PROVIDER_PREFIX, BASE_URL, DEFAULT_MODEL = PROVIDERS.get(PROVIDER, ("", "", ""))
MODEL = os.environ.get(f"{PROVIDER_PREFIX}_MODEL", DEFAULT_MODEL).strip()
SSH_TARGET = os.environ.get("OPS_SSH_TARGET", "")
SSH_PORT = int(os.environ.get("OPS_SSH_PORT", "22"))
SSH_AUTH = os.environ.get("OPS_SSH_AUTH", "password").strip().lower()
SSH_PASSWORD = os.environ.get("OPS_SSH_PASSWORD", "")
SSH_IDENTITY = os.environ.get("OPS_SSH_IDENTITY", str(Path.home() / ".ssh" / "ops_agent"))


def nonnegative(name, default, legacy=None):
    value = int(os.environ.get(name, os.environ.get(legacy, str(default)) if legacy else str(default)))
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


COMMAND_TIMEOUT = nonnegative("OPS_COMMAND_TIMEOUT", 600, "OPS_DOCKER_TIMEOUT")
COMMAND_OUTPUT_LIMIT = nonnegative("OPS_COMMAND_OUTPUT_LIMIT", 48000, "OPS_DOCKER_OUTPUT_LIMIT")
MAX_STEPS = nonnegative("OPS_MAX_STEPS", 128)
