"""Challenges: the fee, the bar, and every path the money can take.

The champion's stored score is the bar. A challenger wins only by passing
strictly more criteria; a tie defends the belt. Whatever the committee cannot
settle is refunded, never confiscated.
"""

import json

BRIEF = "Document an open-source project well enough that a stranger can run it."
CRITERIA = (
    "1. The first screen says what the project does. "
    "2. The page shows how to install or run it with real commands. "
    "3. The page says where the source code lives."
)
CRITERIA_COUNT = 3

ENTRY_URL = "https://entries.example.com/project"
RIVAL_URL = "https://entries.example.com/rival"
ENTRY_BODY = (
    "Gauntlet is a contest contract for GenLayer. Install it with pip install "
    "gauntlet, then run gauntlet start. The source lives at "
    "https://github.com/example/gauntlet."
)
QUOTE = "Install it with pip install gauntlet, then run gauntlet start."

FEE = 500


def _mock_page(direct_vm, body=ENTRY_BODY):
    direct_vm.mock_web(r".*entries\.example\.com.*", {"status": 200, "body": body})


def _mock_judge(direct_vm, passed=(1, 0, 1), quote=QUOTE):
    direct_vm.mock_llm(
        r".*\[\[GAUNTLET_JUDGEMENT\]\].*",
        json.dumps(
            {"passed": list(passed), "quote": quote, "reason": "scored against the rubric"}
        ),
    )


def _throned(direct_vm, direct_deploy, direct_owner, passed=(1, 0, 1)):
    contract = direct_deploy("contracts/Gauntlet.py", BRIEF, CRITERIA, CRITERIA_COUNT)
    _mock_page(direct_vm)
    _mock_judge(direct_vm, passed)
    direct_vm.sender = direct_owner
    contract.claim_throne(ENTRY_URL)
    direct_vm.clear_mocks()
    return contract


def _rescore(direct_vm, passed, quote=QUOTE):
    direct_vm.clear_mocks()
    _mock_page(direct_vm)
    _mock_judge(direct_vm, passed, quote)


def test_a_challenge_without_a_champion_becomes_a_credit(
    direct_vm, direct_deploy, direct_alice
):
    contract = direct_deploy("contracts/Gauntlet.py", BRIEF, CRITERIA, CRITERIA_COUNT)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    assert contract.challenge(RIVAL_URL) == "credit"
    direct_vm.value = 0

    assert int(contract.get_credit(direct_alice)) == FEE
    assert int(contract.get_ledger()["locked_total"]) == FEE


def test_a_challenge_holds_the_fee(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    assert challenge_id == "ga-1"
    assert contract.get_open_challenge() == challenge_id

    challenge = contract.get_challenge(challenge_id)
    assert challenge["status"] == "OPEN"
    assert int(challenge["fee"]) == FEE
    assert int(challenge["attempts"]) == 0

    ledger = contract.get_ledger()
    assert int(ledger["total_in"]) == FEE
    assert int(ledger["locked_total"]) == FEE
    assert int(ledger["total_out"]) == 0


def test_a_zero_fee_is_refused(direct_vm, direct_deploy, direct_owner, direct_alice):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED]:zero_fee"):
        contract.challenge(RIVAL_URL)


def test_one_challenge_at_a_time(direct_vm, direct_deploy, direct_owner, direct_alice):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    contract.challenge(RIVAL_URL)
    # A second fee while the first is open is not lost: it becomes a credit.
    assert contract.challenge(RIVAL_URL) == "credit"
    direct_vm.value = 0

    assert int(contract.get_credit(direct_alice)) == FEE
    assert int(contract.get_ledger()["locked_total"]) == 2 * FEE


def test_a_bad_entry_url_becomes_a_credit(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    assert contract.challenge("https://127.0.0.1/rival") == "credit"
    direct_vm.value = 0

    assert contract.get_open_challenge() == ""
    assert int(contract.get_credit(direct_alice)) == FEE


def test_the_challenger_can_take_the_fee_back(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    contract.withdraw_challenge(challenge_id)

    assert contract.get_challenge(challenge_id)["status"] == "WITHDRAWN"
    assert contract.get_open_challenge() == ""
    ledger = contract.get_ledger()
    assert int(ledger["total_out"]) == FEE
    assert int(ledger["locked_total"]) == 0
    assert contract.get_summary()["withdrawn"] == 1


def test_a_stranger_cannot_withdraw_the_fee(
    direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED]:not_challenger"):
        contract.withdraw_challenge(challenge_id)


def test_passing_more_criteria_takes_the_belt(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    # The champion scored 2 of 3.
    contract = _throned(direct_vm, direct_deploy, direct_owner, passed=(1, 0, 1))

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    # The rival scores 3 of 3: strictly more, so the belt changes hands.
    _rescore(direct_vm, (1, 1, 1))
    assert contract.judge(challenge_id) == "WON"

    challenge = contract.get_challenge(challenge_id)
    assert challenge["status"] == "WON"
    assert challenge["vector"] == "111"
    assert challenge["score"] == 3
    assert challenge["quote"].lower() == QUOTE.lower()
    assert contract.get_open_challenge() == ""

    champion = contract.get_champion()
    assert champion["url"] == RIVAL_URL
    assert champion["score"] == 3
    assert champion["owner"].lower().replace("0x", "") == direct_alice.hex().lower().replace(
        "0x", ""
    )

    # The fee came back to the winner, so only the champion's score moved money.
    ledger = contract.get_ledger()
    assert int(ledger["total_out"]) == FEE
    assert int(ledger["locked_total"]) == 0
    assert contract.get_summary()["crowned"] == 2


def test_a_tie_defends_the_belt(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner, passed=(1, 0, 1))

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    # Same score as the champion: the challenger has to beat the bar, not match it.
    _rescore(direct_vm, (1, 1, 0))
    assert contract.judge(challenge_id) == "LOST"

    assert contract.get_challenge(challenge_id)["status"] == "LOST"
    assert contract.get_champion()["url"] == ENTRY_URL
    assert contract.get_champion()["score"] == 2

    ledger = contract.get_ledger()
    assert int(ledger["total_out"]) == FEE
    assert int(ledger["locked_total"]) == 0
    assert contract.get_summary()["defended"] == 1


def test_an_ungrounded_score_keeps_the_challenge_open(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    _rescore(direct_vm, (1, 1, 1), quote="a sentence that is not on the page at all")
    assert contract.judge(challenge_id) == "OPEN"

    challenge = contract.get_challenge(challenge_id)
    assert challenge["status"] == "OPEN"
    assert challenge["vector"] == ""
    assert int(challenge["attempts"]) == 1
    assert contract.get_champion()["url"] == ENTRY_URL

    ledger = contract.get_ledger()
    assert int(ledger["locked_total"]) == FEE
    assert int(ledger["total_out"]) == 0


def test_an_unsettled_challenge_is_refunded(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    for _ in range(3):
        _rescore(direct_vm, (1, 1, 1), quote="still not on the page")
        contract.judge(challenge_id)

    assert int(contract.get_challenge(challenge_id)["attempts"]) == 3

    with direct_vm.expect_revert("[EXPECTED]:too_many_attempts"):
        contract.judge(challenge_id)

    contract.void_challenge(challenge_id)

    assert contract.get_challenge(challenge_id)["status"] == "VOID"
    ledger = contract.get_ledger()
    assert int(ledger["total_out"]) == FEE
    assert int(ledger["locked_total"]) == 0
    assert contract.get_summary()["void"] == 1


def test_void_is_refused_before_it_is_earned(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    with direct_vm.expect_revert("[EXPECTED]:not_voidable"):
        contract.void_challenge(challenge_id)


def test_a_closed_challenge_cannot_be_judged_twice(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    _rescore(direct_vm, (1, 1, 1))
    contract.judge(challenge_id)

    with direct_vm.expect_revert("[EXPECTED]:challenge_closed"):
        contract.judge(challenge_id)


def test_the_ledger_stays_balanced_through_a_challenge(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _throned(direct_vm, direct_deploy, direct_owner)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    _rescore(direct_vm, (1, 1, 1))
    contract.judge(challenge_id)

    ledger = contract.get_ledger()
    assert int(ledger["total_in"]) == int(ledger["total_out"]) + int(ledger["locked_total"])
