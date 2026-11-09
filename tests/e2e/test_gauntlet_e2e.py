"""Gauntlet end to end on the deployed contest: the paths the Studio suite leaves out.

The Studio suite takes the contest through crown → challenge → judge → payout.
This one covers the money paths around it, against the same pinned contract:

- a second challenge while one is open becomes a **credit**, not a loss;
- the challenger can withdraw the open challenge and get the fee back;
- the credit is withdrawable too, and the ledger returns to where it started;
- ``void_challenge`` is refused while it is not yet earned, and a stranger cannot
  take somebody else's fee back;
- a challenger that does not finish **strictly above** the champion's stored score
  leaves the belt where it was, whatever the committee scored;
- a judgement nobody can ground is driven to the attempt cap and then voided by
  the *other* account, with the fee going back to the challenger.

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
from wallet import load_account, named_env_present  # noqa: E402

DEPLOYMENT_PATH = ROOT / "deployment.json"
ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")

POLL_INTERVAL = 10_000
POLL_RETRIES = 60
RETRY_PAUSE = 20

FEE = 100
CRITERIA_COUNT = 3
JUDGEMENT_ATTEMPTS = 3

CHAMPION_URL = "https://raw.githubusercontent.com/weels007/genlayer-vow/main/README.md"
RIVAL_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-gauntlet/main/README.md"
)

# A page whose whole text folds to 19 letters: one short of the 20 the contract
# requires before a quote can ground. Every quote taken from it is a substring of
# that fold, so no judgement against it can ever settle — on any run, for any
# model. That is an undecided judgement on demand, instead of waiting out the
# seven-day timeout.
UNGROUNDABLE_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-gauntlet/main"
    "/testdata/ungroundable.txt"
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
    """A genuinely different signer, or None when the repository has only one.

    The repository holds one deployer secret, and a second name resolves to the
    same keystore unless its own pair is configured. Proving nothing with the
    deployer's own key would be worse than not proving it, so this returns None
    and each caller decides: the cases where *only the caller* is allowed to act
    skip, and the cases that are permissionless by design fall back to the
    deployer and stay covered.
    """
    try:
        other = load_account("tester2")
    except SystemExit as error:
        _STATE["stranger_skip"] = str(error)
        return None
    if _hex(other.address) == _hex(account.address):
        _STATE["stranger_skip"] = (
            "no keystore is filed under tester2 (GAUNTLET_TESTER2_KEYSTORE or "
            "GAUNTLET_TESTER_KEYSTORE2 is empty), so tester2 falls back to the "
            "deployer's keystore"
            if not named_env_present("tester2")
            else "the keystore filed under tester2 decrypts to the deployer's "
            "own account"
        )
        return None
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


def _rollback_text(result) -> str:
    """Whatever the receipt says about why execution failed."""
    if not isinstance(result, dict):
        return str(result)

    bits = []
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    if isinstance(leader, dict):
        for key in ("message", "error_message", "error", "result"):
            value = leader.get(key)
            if isinstance(value, (str, bytes)):
                bits.append(str(value))
    for key in ("error_message", "message"):
        value = result.get(key)
        if isinstance(value, (str, bytes)):
            bits.append(str(value))
    return " ".join(part for part in bits if part)


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


def _judge_until_settled(client, account, contract, challenge_id, cap=JUDGEMENT_ATTEMPTS):
    """Judge until the committee settles it, or the attempts are spent.

    A judgement costs a fetch and a full round of consensus: one that comes back
    undecided books an attempt, while a run whose own work fails books nothing. So
    this keeps asking until the challenge changes or the cap is reached, instead
    of assuming one call books one attempt.
    """
    challenge = _read(client, contract, "get_challenge", [challenge_id])
    for _ in range(cap + 5):
        if challenge["status"] != "OPEN" or int(challenge["attempts"]) >= cap:
            return challenge
        _attempt(client, account, contract, "judge", [challenge_id])
        challenge = _read(client, contract, "get_challenge", [challenge_id])
        if challenge["status"] == "OPEN":
            print(
                f"  judgement {challenge['attempts']}/{cap} came back undecided: "
                f"{challenge['reason']}"
            )
    return challenge


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


def _settle_our_tail(client, account, contract):
    """Clear our own open challenge and credit before a delta is measured."""
    open_id = _read(client, contract, "get_open_challenge")
    if open_id:
        challenge = _read(client, contract, "get_challenge", [open_id])
        if challenge["status"] == "OPEN":
            if _hex(challenge["challenger"]) != _hex(account.address):
                pytest.skip("another account holds an open challenge")
            _expect_ok(client, account, contract, "withdraw_challenge", [open_id])

    if _credit(client, contract, account) > 0:
        _expect_ok(client, account, contract, "withdraw_credit")


def test_03_a_second_challenge_becomes_a_credit(client, account, contract):
    _settle_our_tail(client, account, contract)

    before = _ledger(client, contract)
    locked_before = int(before["locked_total"])
    out_before = int(before["total_out"])

    first = _open_challenge(client, account, contract)
    _STATE["challenge"] = first

    # A second fee while the first challenge is still open is not a loss.
    _expect_ok(client, account, contract, "challenge", [RIVAL_URL], value=FEE)

    assert _read(client, contract, "get_open_challenge") == first
    assert _credit(client, contract, account) >= FEE

    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == locked_before + 2 * FEE
    assert int(ledger["total_out"]) == out_before
    assert _invariant_holds(client, contract)

    # Keep the id for the next case; set right away so a failed assertion here
    # does not cascade into the cases that follow.
    _STATE["challenge"] = first


def test_04_a_stranger_cannot_take_the_fee_back(
    client, account, contract, stranger
):
    if stranger is None:
        pytest.skip(
            "a stranger here would be the deployer itself: "
            + _STATE.get("stranger_skip", "no second wallet is configured")
        )
    challenge_id = _STATE["challenge"]

    # The same client signs with the account passed per call, which is how the
    # Studio suite drives a second wallet. A second client would sign as its own
    # session account instead, and the test would prove nothing.
    touched = _attempt(client, stranger, contract, "withdraw_challenge", [challenge_id])
    assert not _succeeded(touched), "a stranger withdrew somebody else's fee"

    # A refusal only proves custody if the *contract* is the one refusing: an
    # unfunded wallet or a network fault would look identical from out here.
    text = _rollback_text(touched).lower()
    if text:
        assert "challenger" in text, (
            "the stranger was stopped, but not by the contract's own "
            f"refusal: {text}"
        )
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
    _settle_our_tail(client, account, contract)
    challenge_id = _open_challenge(client, account, contract)

    _expect_denied(client, account, contract, "void_challenge", [challenge_id])
    assert _read(client, contract, "get_challenge", [challenge_id])["status"] == "OPEN"

    balance_before = _balance(client, contract)
    _expect_ok(client, account, contract, "withdraw_challenge", [challenge_id])

    assert _read(client, contract, "get_challenge", [challenge_id])["status"] == "WITHDRAWN"
    assert _wait_balance(client, contract, balance_before - FEE) == balance_before - FEE
    assert _invariant_holds(client, contract)


# ---------------------------------------------------------------------------
# the bar: a challenger that does not pass it cannot take the belt
# ---------------------------------------------------------------------------


def test_07_the_bar_cannot_be_cleared_by_equalling_it(client, account, contract):
    """The champion's stored score is a floor, never a target to match.

    A live committee cannot be ordered to return one exact score, so the case
    follows the law instead of a hoped-for number: only a score strictly above
    the bar moves the belt, and anything at or below it leaves the holder and the
    money where they were. When the bar is the rubric's maximum the case is exact
    arithmetic — no page can satisfy more criteria than all of them, so every
    settled judgement must lose.

    What a test cannot order is a settled judgement at all: the committee has to
    ground a quote in the page first. When it does not, the challenge is undecided
    by design, and this proves the exit and reports the case as inconclusive
    instead of guessing at a verdict nobody produced.
    """
    _settle_our_tail(client, account, contract)

    champion = _champion(client, contract)
    bar = int(champion["score"])
    url = champion["url"]
    since = int(champion["since"])

    # When the bar is already the maximum any page has to lose, so probe one the
    # committee has grounded a quote in before. Otherwise probe the champion's own
    # page: that is the entry most likely to reproduce the bar and make the tie
    # literal.
    if bar == CRITERIA_COUNT:
        probe = RIVAL_URL if url != RIVAL_URL else CHAMPION_URL
    else:
        probe = url

    summary_before = _read(client, contract, "get_summary")
    ledger_before = _ledger(client, contract)
    balance_before = _balance(client, contract)

    challenge_id = _open_challenge(client, account, contract, url=probe)
    challenge = _judge_until_settled(client, account, contract, challenge_id)

    if challenge["status"] == "OPEN":
        # No score to hold against the bar on this run. Prove the exit, leave
        # nothing behind, and say plainly that the case was inconclusive.
        _expect_ok(
            client,
            account,
            contract,
            "void_challenge",
            [challenge_id],
            verify=lambda: _read(client, contract, "get_challenge", [challenge_id])["status"]
            == "VOID",
        )
        assert int(_ledger(client, contract)["locked_total"]) == int(
            ledger_before["locked_total"]
        )
        assert _invariant_holds(client, contract)
        pytest.skip(
            "the committee left the bar undecided after "
            f"{int(challenge['attempts'])} attempts: {challenge['reason']}"
        )

    assert challenge["status"] in ("WON", "LOST"), challenge
    assert len(challenge["vector"]) == CRITERIA_COUNT
    assert int(challenge["score"]) == challenge["vector"].count("1")

    champion_after = _champion(client, contract)
    summary = _read(client, contract, "get_summary")

    if challenge["status"] == "WON":
        # Only strictly more criteria take the belt, and the fee comes back.
        assert int(challenge["score"]) > bar
        assert champion_after["url"] == probe
        assert _hex(champion_after["owner"]) == _hex(account.address)
        assert int(summary["crowned"]) == int(summary_before["crowned"]) + 1
    else:
        assert int(challenge["score"]) <= bar
        assert champion_after["url"] == url
        assert champion_after["vector"] == champion["vector"]
        assert int(champion_after["since"]) == since
        assert int(summary["defended"]) == int(summary_before["defended"]) + 1
        if int(challenge["score"]) == bar:
            print(f"  the challenger matched the bar at {bar}; the belt was defended")

    if bar == CRITERIA_COUNT:
        # No page can satisfy more than every criterion, so no challenger can
        # ever pass the champion's score: this is the tie, and it must lose.
        assert challenge["status"] == "LOST"

    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == int(ledger_before["total_out"]) + FEE
    assert int(ledger["locked_total"]) == int(ledger_before["locked_total"])
    assert _invariant_holds(client, contract)
    assert _wait_balance(client, contract, balance_before) == balance_before


# ---------------------------------------------------------------------------
# the void path: a judgement nobody can ground, driven to the attempt cap
# ---------------------------------------------------------------------------


def test_08_an_ungroundable_challenge_is_voided_after_the_cap(
    client, account, contract, stranger
):
    """Three attempts the committee cannot settle, then anyone refunds the fee.

    ``test_06`` proves void is refused while it has not been earned. This drives
    the earned case. The entry's whole text folds to nineteen letters, one short
    of the twenty a quote needs to ground, so every judgement comes back
    undecided: the attempts run out and the fee goes back to the challenger —
    voided by the *other* account, because voiding is permissionless by design and
    paying the wrong party must still be impossible.
    """
    _settle_our_tail(client, account, contract)

    cap = int(_read(client, contract, "get_rules")["max_judgement_attempts"])
    summary_before = _read(client, contract, "get_summary")
    ledger_before = _ledger(client, contract)
    balance_before = _balance(client, contract)

    challenge_id = _open_challenge(client, account, contract, url=UNGROUNDABLE_URL)
    challenge = _judge_until_settled(client, account, contract, challenge_id, cap=cap)

    assert challenge["status"] == "OPEN", (
        "the fixture entry produced a grounded judgement, so nothing was left "
        f"undecided: {challenge}"
    )
    assert int(challenge["attempts"]) == cap, (
        f"only {challenge['attempts']} of {cap} attempts were booked: {challenge}"
    )
    assert challenge["vector"] == ""
    assert _read(client, contract, "get_open_challenge") == challenge_id

    # Nothing moved while the judgement was undecided.
    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == int(ledger_before["locked_total"]) + FEE
    assert int(ledger["total_out"]) == int(ledger_before["total_out"])

    # The attempts are spent: no further judgement is possible.
    _expect_denied(client, account, contract, "judge", [challenge_id])

    actor = stranger if stranger is not None else account
    _expect_ok(
        client,
        actor,
        contract,
        "void_challenge",
        [challenge_id],
        verify=lambda: _read(client, contract, "get_challenge", [challenge_id])["status"]
        == "VOID",
    )

    challenge = _read(client, contract, "get_challenge", [challenge_id])
    assert challenge["status"] == "VOID"
    assert _read(client, contract, "get_open_challenge") == ""

    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == int(ledger_before["total_out"]) + FEE
    assert int(ledger["locked_total"]) == int(ledger_before["locked_total"])
    assert int(_read(client, contract, "get_summary")["void"]) == int(
        summary_before["void"]
    ) + 1
    assert _invariant_holds(client, contract)

    # The refund went to the challenger, so the contract holds what it held.
    assert _wait_balance(client, contract, balance_before) == balance_before


def test_09_views_agree_with_the_ledger(client, contract):
    summary = _read(client, contract, "get_summary")
    challenges = _read(client, contract, "get_challenges")

    assert int(summary["challenges"]) == len(challenges)
    assert int(summary["crowned"]) >= 1
    assert _balance(client, contract) >= 0
    assert _invariant_holds(client, contract)
