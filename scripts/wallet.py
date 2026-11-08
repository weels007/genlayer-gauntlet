"""Load the Gauntlet deploy wallet, from tests/ or from the environment.

Local runs read `tests/tester1-keystore.json` and `tests/tester1.password`, which
are never tracked. CI instead provides `GAUNTLET_DEPLOYER_KEYSTORE` (raw JSON or
base64) and `GAUNTLET_DEPLOYER_PASSWORD`, so no key material lives in the
repository.

A second wallet is looked up under its own name — `GAUNTLET_TESTER2_KEYSTORE`, or
the same pair written as `GAUNTLET_TESTER_KEYSTORE2` — and never under the
deployer's.
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


def _env_names(name: str, suffix: str) -> list:
    """Every environment name this wallet may be filed under.

    `GAUNTLET_TESTER2_KEYSTORE` is the canonical spelling, but a trailing number
    is just as easy to put on the other side of the field name
    (`GAUNTLET_TESTER_KEYSTORE2`), so both are accepted rather than losing a
    perfectly good secret to word order.
    """
    upper = name.upper()
    names = [f"GAUNTLET_{upper}_{suffix}"]
    stem = upper.rstrip("0123456789")
    digits = upper[len(stem) :]
    if digits:
        names.append(f"GAUNTLET_{stem}_{suffix}{digits}")
        names.append(f"GAUNTLET_{stem}_{digits}_{suffix}")
    return names


def _first_env(names) -> str:
    for key in names:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def named_env_present(name: str) -> bool:
    """Whether this named wallet has a keystore of its own in the environment.

    False means a lookup for the name falls back to the deployer's keystore, so
    the caller would be signing with the deployer under another name.
    """
    return bool(_first_env(_env_names(name, "KEYSTORE")))


def _keystore_from_env(name: str):
    """The keystore for this name, or the deployer's.

    A named wallet is looked up first (`GAUNTLET_TESTER2_KEYSTORE`), and only the
    deployer falls back to `GAUNTLET_DEPLOYER_KEYSTORE`. Callers that need a
    *different* account must check the address they get back: a single deployer
    secret is not two wallets.
    """
    raw = _first_env(_env_names(name, "KEYSTORE"))
    if not raw:
        raw = os.environ.get(f"{PREFIX}_KEYSTORE", "").strip()
    if not raw:
        return None
    payload = raw
    if not raw.lstrip().startswith("{"):
        try:
            payload = base64.b64decode(raw).decode("utf-8")
        except Exception as error:
            raise SystemExit(
                f"[wallet] {name}: keystore is neither JSON nor base64"
            ) from error
    try:
        return json.loads(payload)
    except Exception as error:
        raise SystemExit(f"[wallet] {name}: keystore is not a keystore") from error


def load_account(name: str = DEFAULT_WALLET):
    """Decrypt one keystore (file or environment) and return the account."""
    folder = wallet_folder()
    keystore_path = folder / f"{name}-keystore.json"
    password_path = folder / f"{name}.password"

    password = ""
    if password_path.exists():
        password = password_path.read_text(encoding="utf-8").strip()

    env_password = _first_env(_env_names(name, "PASSWORD"))
    if not env_password:
        env_password = os.environ.get(f"{PREFIX}_PASSWORD", "").strip()
    if env_password:
        password = env_password

    data = None
    if keystore_path.exists():
        data = json.loads(keystore_path.read_text(encoding="utf-8"))
    else:
        data = _keystore_from_env(name)

    if data is None:
        raise SystemExit(
            f"no wallet '{name}' in {folder} and no "
            + " or ".join(_env_names(name, "KEYSTORE"))
        )

    if not password:
        raise SystemExit(
            f"[wallet] {name}: no password (tests/{name}.password, "
            + " or ".join(_env_names(name, "PASSWORD"))
            + ")"
        )

    return Account.from_key(Account.decrypt(data, password))
