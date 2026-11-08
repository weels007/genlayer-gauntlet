"""The belt: the frozen rubric, the first champion, and the guards around it.

The contest is created once. The brief and the rubric are stored in the
constructor and there is no setter and no owner, so the only way to move the belt
is a challenge that the committee scores against that same rubric.
"""

import json

import pytest

BRIEF = "Document an open-source project well enough that a stranger can run it."
CRITERIA = (
    "1. The first screen says what the project does. "
    "2. The page shows how to install or run it with real commands. "
    "3. The page says where the source code lives."
)
CRITERIA_COUNT = 3

ENTRY_URL = "https://entries.example.com/project"
ENTRY_BODY = (
    "Gauntlet is a contest contract for GenLayer. Install it with pip install "
    "gauntlet, then run gauntlet start. The source lives at "
    "https://github.com/example/gauntlet."
)
QUOTE = "Install it with pip install gauntlet, then run gauntlet start."


def _mock_entry(direct_vm, body=ENTRY_BODY):
    direct_vm.mock_web(r".*entries\.example\.com.*", {"status": 200, "body": body})


def _mock_judge(direct_vm, passed=(1, 0, 1), quote=QUOTE, reason="two of three"):
    direct_vm.mock_llm(
        r".*\[\[GAUNTLET_JUDGEMENT\]\].*",
        json.dumps({"passed": list(passed), "quote": quote, "reason": reason}),
    )


def _deployed(direct_deploy):
    return direct_deploy("contracts/Gauntlet.py", BRIEF, CRITERIA, CRITERIA_COUNT)


def test_the_contest_is_frozen_at_deployment(direct_deploy):
    contract = direct_deploy("contracts/Gauntlet.py", BRIEF, CRITERIA, CRITERIA_COUNT)

    contest = contract.get_contest()
    assert contest["brief"] == BRIEF
    assert contest["criteria"] == CRITERIA
    assert contest["criteria_count"] == CRITERIA_COUNT

    # No owner, no champion, no money, and no way to change the rubric.
    assert contract.get_champion() == {"defended": False}
    assert contract.get_open_challenge() == ""
    assert contract.get_ledger() == {"total_in": 0, "total_out": 0, "locked_total": 0}

    summary = contract.get_summary()
    assert summary["champion"] is False
    assert summary["challenges"] == 0


def test_a_bad_contest_is_refused_at_deployment(direct_deploy):
    with pytest.raises(Exception):
        direct_deploy("contracts/Gauntlet.py", "short", CRITERIA, CRITERIA_COUNT)

    with pytest.raises(Exception):
        direct_deploy("contracts/Gauntlet.py", BRIEF, "tiny", CRITERIA_COUNT)

    with pytest.raises(Exception):
        direct_deploy("contracts/Gauntlet.py", BRIEF, CRITERIA, 0)

    with pytest.raises(Exception):
        direct_deploy("contracts/Gauntlet.py", BRIEF, CRITERIA, 6)


def test_the_first_entry_takes_the_throne(direct_vm, direct_deploy, direct_owner):
    contract = _deployed(direct_deploy)
    _mock_entry(direct_vm)
    _mock_judge(direct_vm, (1, 0, 1))

    direct_vm.sender = direct_owner
    assert contract.claim_throne(ENTRY_URL) == "101"

    champion = contract.get_champion()
    assert champion["defended"] is True
    assert champion["owner"].lower().replace("0x", "") == direct_owner.hex().lower().replace(
        "0x", ""
    )
    assert champion["url"] == ENTRY_URL
    assert champion["vector"] == "101"
    assert champion["score"] == 2
    assert champion["quote"].lower() == QUOTE.lower()
    assert champion["entry_digest"] != ""

    assert contract.get_summary()["crowned"] == 1


def test_the_throne_can_only_be_claimed_once(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    contract = _deployed(direct_deploy)
    _mock_entry(direct_vm)
    _mock_judge(direct_vm)

    direct_vm.sender = direct_owner
    contract.claim_throne(ENTRY_URL)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED]:throne_taken"):
        contract.claim_throne(ENTRY_URL)


def test_an_unscorable_entry_does_not_crown_anyone(
    direct_vm, direct_deploy, direct_owner
):
    contract = _deployed(direct_deploy)
    _mock_entry(direct_vm)
    # A quote that is not on the page cannot score the entry.
    _mock_judge(direct_vm, (1, 1, 1), quote="a sentence that is not on the page at all")

    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[LLM_ERROR]:unresolved_entry"):
        contract.claim_throne(ENTRY_URL)

    assert contract.get_champion() == {"defended": False}


def test_the_entry_url_must_be_public(direct_vm, direct_deploy, direct_owner):
    contract = _deployed(direct_deploy)
    _mock_entry(direct_vm)
    _mock_judge(direct_vm)

    direct_vm.sender = direct_owner
    for url in (
        "http://entries.example.com/project",
        "https://localhost/project",
        "https://127.0.0.1/project",
        "https://[::1]/project",
        "https://intranet/project",
    ):
        with direct_vm.expect_revert("[EXPECTED]:invalid_entry_url"):
            contract.claim_throne(url)


def test_an_empty_page_is_an_external_error(direct_vm, direct_deploy, direct_owner):
    contract = _deployed(direct_deploy)
    _mock_entry(direct_vm, body="")
    _mock_judge(direct_vm)

    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[EXTERNAL]:empty_entry_page"):
        contract.claim_throne(ENTRY_URL)
