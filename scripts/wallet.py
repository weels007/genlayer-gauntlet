"""Load the Gauntlet deploy wallet, from tests/ or from the environment.

Local runs read `tests/tester1-keystore.json` and `tests/tester1.password`, which
are never tracked. CI instead provides `GAUNTLET_DEPLOYER_KEYSTORE` (raw JSON or
base64) and `GAUNTLET_DEPLOYER_PASSWORD`, so no key material lives in the
repository.
"""

import base64
import json
import os
from pathlib import Path

from eth_account import Account

DEFAULT_FOLDER = Path(__file__).resolve().parents[1] / "tests"
DEFAULT_WALLET = "tester1"

PREFIX = "GAUNTLET_DEPLOYER"


def wallet_folder() -> Path:
    configured = os.environ.get("GAUNTLET_WALLET_FOLDER")
    return Path(configured) if configured else DEFAULT_FOLDER


def _keystore_from_env():
    raw = os.environ.get(f"{PREFIX}_KEYSTORE", "").strip()
    if not raw:
        return None
    payload = raw
    if not raw.lstrip().startswith("{"):
        try:
            payload = base64.b64decode(raw).decode("utf-8")
        except Exception as error:
            raise SystemExit(
                f"[wallet] {PREFIX}_KEYSTORE is neither JSON nor base64"
            ) from error
    try:
        return json.loads(payload)
    except Exception as error:
        raise SystemExit(f"[wallet] {PREFIX}_KEYSTORE is not a keystore") from error


def load_account(name: str = DEFAULT_WALLET):
    """Decrypt one keystore (file or environment) and return the account."""
    folder = wallet_folder()
    keystore_path = folder / f"{name}-keystore.json"
    password_path = folder / f"{name}.password"

    password = ""
    if password_path.exists():
        password = password_path.read_text(encoding="utf-8").strip()

    env_password = os.environ.get(f"{PREFIX}_PASSWORD")
    if env_password:
        password = env_password

    data = None
    if keystore_path.exists():
        data = json.loads(keystore_path.read_text(encoding="utf-8"))
    else:
        data = _keystore_from_env()

    if data is None:
        raise SystemExit(
            f"no wallet '{name}' in {folder} and no {PREFIX}_KEYSTORE"
        )

    if not password:
        raise SystemExit(
            f"[wallet] {name}: no password (tests/{name}.password or "
            f"{PREFIX}_PASSWORD)"
        )

    return Account.from_key(Account.decrypt(data, password))
