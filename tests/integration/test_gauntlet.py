"""Gauntlet on studionet: the belt, the fee and the refusals, for real.

The contract has no owner and no reset, so every assertion is a delta — a count,
a balance, a ledger total compared before and after — and the suite tolerates a
belt that already exists from an earlier run. A challenge is scored by the live
committee against the frozen rubric, and the fee has to land where the rubric
says it lands.

    pytest tests/integration -v -s
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

FEE = 200
CRITERIA_COUNT = 3

# Both pages document a real repository: install commands, a source link and a
# first screen that says what it is. The rubric is written in that vocabulary on
# purpose, so the live committee has something concrete to mark.
CHAMPION_URL = "https://raw.githubusercontent.com/weels007/genlayer-vow/main/README.md"
RIVAL_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-gauntlet/main/README.md"
)


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
def deployment() -> dict:
    return _deployment()


@pytest.fixture(scope="module")
def contract(deployment) -> str:
    print(f"\n[gauntlet] contract {deployment['address']}")
    return deployment["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def rival():
    return load_account("tester1")


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


def _champion(client, contract):
    return _read(client, contract, "get_champion")


def _invariant_holds(client, contract) -> bool:
    ledger = _ledger(client, contract)
    return int(ledger["total_in"]) == int(ledger["total_out"]) + int(ledger["locked_total"])


def _wait_balance(client, contract, expected, attempts=30, pause=10):
    for _ in range(attempts):
        if _balance(client, contract) == expected:
            return expected
        time.sleep(pause)
    return _balance(client, contract)


# ---------------------------------------------------------------------------
# the contest is live and frozen
# ---------------------------------------------------------------------------


def test_01_the_contest_is_live_and_frozen(client, contract, deployment):
    contest = _read(client, contract, "get_contest")
    assert contest["brief"] == deployment["brief"]
    assert int(contest["criteria_count"]) == CRITERIA_COUNT
    assert contest["criteria"]

    rules = _read(client, contract, "get_rules")
    assert int(rules["max_criteria_count"]) == 5
    assert int(rules["max_judgement_attempts"]) == 3
    assert int(rules["challenge_timeout_seconds"]) == 7 * 86400

    assert _invariant_holds(client, contract)


def test_02_the_belt_has_an_owner(client, account, contract):
    champion = _champion(client, contract)
    if not champion["defended"]:
        # A fresh contract: the first scored entry takes the throne.
        _expect_ok(
            client,
            account,
            contract,
            "claim_throne",
            [CHAMPION_URL],
            verify=lambda: _champion(client, contract)["defended"],
        )
        champion = _champion(client, contract)

    assert champion["defended"] is True
    assert champion["url"].startswith("https://")
    assert len(champion["vector"]) == CRITERIA_COUNT
    assert int(champion["score"]) == champion["vector"].count("1")
    assert champion["quote"], "a crowned entry must carry a grounded quote"
    assert _invariant_holds(client, contract)


# ---------------------------------------------------------------------------
# the fee: a challenge is scored and the money follows the rubric
# ---------------------------------------------------------------------------


def test_03_a_challenge_is_scored_and_the_fee_moves(client, account, contract):
    champion = _champion(client, contract)
    url = RIVAL_URL if champion["url"] != RIVAL_URL else CHAMPION_URL

    before = _ledger(client, contract)
    locked_before = int(before["locked_total"])
    out_before = int(before["total_out"])
    balance_before = _balance(client, contract)

    _expect_ok(
        client,
        account,
        contract,
        "challenge",
        [url],
        value=FEE,
        verify=lambda: _read(client, contract, "get_open_challenge") != "",
    )

    challenge_id = _read(client, contract, "get_open_challenge")
    assert challenge_id, "the challenge was not opened"

    challenge = _read(client, contract, "get_challenge", [challenge_id])
    assert challenge["status"] == "OPEN"
    assert int(challenge["fee"]) == FEE

    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == locked_before + FEE
    assert int(ledger["total_out"]) == out_before

    # Anyone may run the judgement; the rubric decides, not the caller.
    _expect_ok(
        client,
        account,
        contract,
        "judge",
        [challenge_id],
        verify=lambda: _read(client, contract, "get_challenge", [challenge_id])["status"]
        != "OPEN",
    )

    challenge = _read(client, contract, "get_challenge", [challenge_id])
    assert challenge["status"] in ("WON", "LOST"), challenge
    assert int(challenge["attempts"]) == 1
    assert len(challenge["vector"]) == CRITERIA_COUNT
    assert int(challenge["score"]) == challenge["vector"].count("1")
    assert challenge["quote"], "a settled challenge must carry a grounded quote"

    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == out_before + FEE
    assert int(ledger["locked_total"]) == locked_before
    assert _invariant_holds(client, contract)

    # The payout is an external message: the balance returns to where it was
    # once it finalises.
    assert _wait_balance(client, contract, balance_before) == balance_before

    champion_after = _champion(client, contract)
    if challenge["status"] == "WON":
        assert champion_after["url"] == url
        assert int(champion_after["score"]) == int(challenge["score"])
    else:
        assert leave_belt_unchanged(champion, champion_after)


def leave_belt_unchanged(before, after) -> bool:
    return before["url"] == after["url"] and int(before["score"]) == int(after["score"])


# ---------------------------------------------------------------------------
# adversarial: the refusals that protect the fee and the belt
# ---------------------------------------------------------------------------


def test_04_refusals(client, account, contract):
    # The throne is taken: no second first entry.
    _expect_denied(client, account, contract, "claim_throne", [CHAMPION_URL])

    # A challenge with no fee is a plain revert, never a stranded deposit.
    _expect_denied(client, account, contract, "challenge", [RIVAL_URL])

    # Evidence has to be https and public: the fee becomes a credit, not a loss.
    _expect_ok(client, account, contract, "challenge", ["https://127.0.0.1/x"], value=FEE)
    credit = int(_read(client, contract, "get_credit", [account.address]))
    assert credit >= FEE

    balance_before = _balance(client, contract)
    _expect_ok(
        client,
        account,
        contract,
        "withdraw_credit",
        verify=lambda: int(_read(client, contract, "get_credit", [account.address])) == 0,
    )
    assert _wait_balance(client, contract, balance_before - FEE) == balance_before - FEE


def test_05_a_stranger_cannot_take_the_fee_back(client, account, contract):
    # Open a challenge, then try to withdraw it from another account.
    _expect_ok(
        client,
        account,
        contract,
        "challenge",
        [RIVAL_URL],
        value=FEE,
        verify=lambda: _read(client, contract, "get_open_challenge") != "",
    )
    challenge_id = _read(client, contract, "get_open_challenge")
    assert challenge_id

    other = load_account("tester2")
    if _hex(other.address) == _hex(account.address):
        # The repository holds one deployer secret; a second name resolves to the
        # same keystore unless its own pair is configured. Proving nothing with
        # the deployer's own key would be worse than skipping.
        pytest.skip("the second wallet resolves to the deployer's own account")
    other_client = create_client(studionet, account=other)
    touched = _attempt(other_client, other, contract, "withdraw_challenge", [challenge_id])
    assert not _succeeded(touched), "a stranger withdrew somebody else's fee"
    assert _read(client, contract, "get_challenge", [challenge_id])["status"] == "OPEN"

    # The challenger can always take it back.
    balance_before = _balance(client, contract)
    _expect_ok(client, account, contract, "withdraw_challenge", [challenge_id])

    challenge = _read(client, contract, "get_challenge", [challenge_id])
    assert challenge["status"] == "WITHDRAWN"
    assert _read(client, contract, "get_open_challenge") == ""
    assert _wait_balance(client, contract, balance_before - FEE) == balance_before - FEE
    assert _invariant_holds(client, contract)


def test_06_views_agree_with_the_ledger(client, contract):
    summary = _read(client, contract, "get_summary")
    challenges = _read(client, contract, "get_challenges")

    assert int(summary["challenges"]) == len(challenges)
    assert int(summary["crowned"]) >= 1
    assert _balance(client, contract) >= 0
    assert _invariant_holds(client, contract)
