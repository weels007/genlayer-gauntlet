"""Gauntlet end to end on the deployed contest: the paths the Studio suite leaves out.

The Studio suite takes the contest through crown → challenge → judge → payout.
This one covers the money paths around it, against the same pinned contract:

- a second challenge while one is open becomes a **credit**, not a loss;
- the challenger can withdraw the open challenge and get the fee back;
- the credit is withdrawable too, and the ledger returns to where it started;
- ``void_challenge`` is refused while it is not yet earned, and a stranger cannot
  take somebody else's fee back.

Nothing here deploys, and every assertion is a delta, so it can be re-run.

    pytest tests/e2e -v -s
"""

import json
import re
import sys
import time
from pathlib import Path

import pytest
from genlayer_py import create_client, studionet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from wallet import load_account  # noqa: E402

DEPLOYMENT_PATH = ROOT / "deployment.json"
ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")

POLL_INTERVAL = 10_000
POLL_RETRIES = 60
RETRY_PAUSE = 20

FEE = 100
CRITERIA_COUNT = 3

CHAMPION_URL = "https://raw.githubusercontent.com/weels007/genlayer-vow/main/README.md"
RIVAL_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-gauntlet/main/README.md"
)

# The challenge opened in test_03 is used by the two cases after it.
_STATE = {}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _deployment() -> dict:
    if not DEPLOYMENT_PATH.exists():
        pytest.skip("no deployment.json yet; run the Deploy Contract workflow first")
    payload = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    address = str(payload.get("address", "")).strip()
    if not ADDRESS_VALUE.fullmatch(address):
        pytest.skip(f"deployment.json has no usable address: {address!r}")
    return payload


@pytest.fixture(scope="module")
def contract() -> str:
    payload = _deployment()
    print(f"\n[gauntlet-e2e] contract {payload['address']}")
    return payload["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def stranger(account):
    """A genuinely different signer, or skip.

    The repository holds one deployer secret, and a second name resolves to the
    same keystore unless its own pair is configured. Rather than prove nothing
    with the deployer's own key, the case skips when there is only one wallet.
    """
    try:
        other = load_account("tester2")
    except SystemExit:
        pytest.skip("no second wallet secret is configured")
    if _hex(other.address) == _hex(account.address):
        pytest.skip("the second wallet resolves to the deployer's own account")
    return other


@pytest.fixture(scope="module")
def client(account):
    return create_client(studionet, account=account)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _hex(value) -> str:
    return str(value).lower().replace("0x", "")


def _succeeded(result) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("status_name") not in ("ACCEPTED", "FINALIZED"):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    if isinstance(leader, dict):
        return leader.get("execution_result") == "SUCCESS"
    return str(result.get("tx_execution_result_name", "")) == "FINISHED_WITH_RETURN"


def _definite_rollback(result) -> bool:
    if not isinstance(result, dict):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    return isinstance(leader, dict) and leader.get("execution_result") == "ERROR"


def _attempt(client, account, contract, method, args=None, value=0):
    try:
        tx_hash = client.write_contract(
            contract, method, args=args or [], value=value, account=account
        )
        return client.wait_for_transaction_receipt(
            tx_hash, retries=POLL_RETRIES, interval=POLL_INTERVAL
        )
    except Exception as error:
        return error


def _expect_ok(client, account, contract, method, args=None, value=0, verify=None):
    result = None
    for attempt in range(3):
        result = _attempt(client, account, contract, method, args, value)
        if _succeeded(result):
            return result
        if verify is not None and verify():
            return result
        if _definite_rollback(result):
            raise AssertionError(f"{method} was refused: {result}")
        if attempt < 2:
            print(f"  {method} did not reach consensus, retrying")
            time.sleep(RETRY_PAUSE)
    raise AssertionError(f"{method} did not reach consensus: {result}")


def _expect_denied(client, account, contract, method, args=None, value=0):
    result = _attempt(client, account, contract, method, args, value)
    assert not _succeeded(result), f"{method} should have been refused: {result}"
    return result


def _read(client, contract, method, args=None):
    """Read a view, retrying a provider hiccup (a 502 is not an answer)."""
    last = None
    for attempt in range(3):
        try:
            return client.read_contract(contract, method, args=args or [])
        except Exception as error:
            last = error
            if attempt < 2:
                print(f"  {method} read failed, retrying: {error}")
                time.sleep(RETRY_PAUSE)
    raise last


def _ledger(client, contract):
    return _read(client, contract, "get_ledger")


def _balance(client, contract):
    return int(_read(client, contract, "get_balance"))


def _credit(client, contract, who):
    return int(_read(client, contract, "get_credit", [who.address]))


def _invariant_holds(client, contract) -> bool:
    ledger = _ledger(client, contract)
    return int(ledger["total_in"]) == int(ledger["total_out"]) + int(ledger["locked_total"])


def _wait_balance(client, contract, expected, attempts=30, pause=10):
    for _ in range(attempts):
        if _balance(client, contract) == expected:
            return expected
        time.sleep(pause)
    return _balance(client, contract)


def _open_challenge(client, account, contract, url=RIVAL_URL, fee=FEE) -> str:
    _expect_ok(
        client,
        account,
        contract,
        "challenge",
        [url],
        value=fee,
        verify=lambda: _read(client, contract, "get_open_challenge") != "",
    )
    challenge_id = _read(client, contract, "get_open_challenge")
    assert challenge_id, "the challenge was not opened"
    return challenge_id


# ---------------------------------------------------------------------------
# the deployed contest
# ---------------------------------------------------------------------------


def test_01_the_contest_is_live(client, contract):
    contest = _read(client, contract, "get_contest")
    assert int(contest["criteria_count"]) == CRITERIA_COUNT
    assert contest["brief"] and contest["criteria"]
    assert _invariant_holds(client, contract)


def test_02_the_belt_has_an_owner(client, account, contract):
    champion = _read(client, contract, "get_champion")
    if not champion["defended"]:
        _expect_ok(
            client,
            account,
            contract,
            "claim_throne",
            [CHAMPION_URL],
            verify=lambda: _read(client, contract, "get_champion")["defended"],
        )
        champion = _read(client, contract, "get_champion")

    assert champion["defended"] is True
    assert len(champion["vector"]) == CRITERIA_COUNT
    assert int(champion["score"]) == champion["vector"].count("1")
    assert champion["quote"]


# ---------------------------------------------------------------------------
# the credit path: a second challenge while one is open
# ---------------------------------------------------------------------------


def test_03_a_second_challenge_becomes_a_credit(client, account, contract):
    before = _ledger(client, contract)
    locked_before = int(before["locked_total"])
    out_before = int(before["total_out"])

    first = _open_challenge(client, account, contract)

    # A second fee while the first challenge is still open is not a loss.
    _expect_ok(client, account, contract, "challenge", [RIVAL_URL], value=FEE)

    assert _read(client, contract, "get_open_challenge") == first
    assert _credit(client, contract, account) >= FEE

    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == locked_before + 2 * FEE
    assert int(ledger["total_out"]) == out_before
    assert _invariant_holds(client, contract)

    # Keep the id for the next case.
    _STATE["challenge"] = first


def test_04_a_stranger_cannot_take_the_fee_back(
    client, account, contract, stranger
):
    challenge_id = _STATE["challenge"]

    # The same client signs with the account passed per call, which is how the
    # Studio suite drives a second wallet. A second client would sign as its own
    # session account instead, and the test would prove nothing.
    touched = _attempt(client, stranger, contract, "withdraw_challenge", [challenge_id])
    assert not _succeeded(touched), "a stranger withdrew somebody else's fee"
    assert _read(client, contract, "get_challenge", [challenge_id])["status"] == "OPEN"


def test_05_the_open_challenge_and_the_credit_come_back(client, account, contract):
    challenge_id = _STATE["challenge"]

    locked_before = int(_ledger(client, contract)["locked_total"])
    out_before = int(_ledger(client, contract)["total_out"])
    balance_before = _balance(client, contract)
    credit_before = _credit(client, contract, account)
    assert credit_before >= FEE

    _expect_ok(client, account, contract, "withdraw_challenge", [challenge_id])

    challenge = _read(client, contract, "get_challenge", [challenge_id])
    assert challenge["status"] == "WITHDRAWN"
    assert _read(client, contract, "get_open_challenge") == ""

    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == locked_before - FEE
    assert int(ledger["total_out"]) == out_before + FEE

    _expect_ok(
        client,
        account,
        contract,
        "withdraw_credit",
        verify=lambda: _credit(client, contract, account) == 0,
    )

    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == locked_before - 2 * FEE
    assert int(ledger["total_out"]) == out_before + 2 * FEE
    assert _invariant_holds(client, contract)

    # Both payouts are external messages; the balance returns to where it began.
    assert _wait_balance(client, contract, balance_before) == balance_before


# ---------------------------------------------------------------------------
# the void guard: not refundable until it is earned
# ---------------------------------------------------------------------------


def test_06_void_is_refused_before_it_is_earned(client, account, contract):
    challenge_id = _open_challenge(client, account, contract)

    _expect_denied(client, account, contract, "void_challenge", [challenge_id])
    assert _read(client, contract, "get_challenge", [challenge_id])["status"] == "OPEN"

    balance_before = _balance(client, contract)
    _expect_ok(client, account, contract, "withdraw_challenge", [challenge_id])

    assert _read(client, contract, "get_challenge", [challenge_id])["status"] == "WITHDRAWN"
    assert _wait_balance(client, contract, balance_before - FEE) == balance_before - FEE
    assert _invariant_holds(client, contract)


def test_07_views_agree_with_the_ledger(client, contract):
    summary = _read(client, contract, "get_summary")
    challenges = _read(client, contract, "get_challenges")

    assert int(summary["challenges"]) == len(challenges)
    assert int(summary["crowned"]) >= 1
    assert _balance(client, contract) >= 0
    assert _invariant_holds(client, contract)
