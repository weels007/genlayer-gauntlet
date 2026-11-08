"""Consensus: the vector is the whole decision.

Two models word their reasoning differently and can disagree on a borderline
criterion, but a score is only accepted when both produce the same pass/fail
vector. That vector is what the belt is decided on.
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


def _mock_page(direct_vm):
    direct_vm.mock_web(r".*entries\.example\.com.*", {"status": 200, "body": ENTRY_BODY})


def _mock_judge(direct_vm, passed=(1, 0, 1)):
    direct_vm.mock_llm(
        r".*\[\[GAUNTLET_JUDGEMENT\]\].*",
        json.dumps({"passed": list(passed), "quote": QUOTE, "reason": "scored"}),
    )


def _judged(direct_vm, direct_deploy, direct_owner, direct_alice, passed=(1, 0, 1)):
    contract = direct_deploy("contracts/Gauntlet.py", BRIEF, CRITERIA, CRITERIA_COUNT)
    _mock_page(direct_vm)
    _mock_judge(direct_vm)
    direct_vm.sender = direct_owner
    contract.claim_throne(ENTRY_URL)

    direct_vm.clear_mocks()
    _mock_page(direct_vm)
    _mock_judge(direct_vm)

    direct_vm.sender = direct_alice
    direct_vm.value = FEE
    challenge_id = contract.challenge(RIVAL_URL)
    direct_vm.value = 0

    contract.judge(challenge_id)
    return contract, challenge_id


def test_validator_accepts_the_same_vector(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    _judged(direct_vm, direct_deploy, direct_owner, direct_alice)

    assert direct_vm.run_validator() is True


def test_validator_rejects_a_different_vector(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    _judged(direct_vm, direct_deploy, direct_owner, direct_alice)

    direct_vm.clear_mocks()
    _mock_page(direct_vm)
    _mock_judge(direct_vm, (1, 1, 1))

    assert direct_vm.run_validator() is False


def test_validator_rejects_a_partial_match(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    # Same score, different criteria passed: the vector differs, so it is not
    # the same judgement.
    _judged(direct_vm, direct_deploy, direct_owner, direct_alice, (1, 0, 1))

    direct_vm.clear_mocks()
    _mock_page(direct_vm)
    _mock_judge(direct_vm, (1, 1, 0))

    assert direct_vm.run_validator() is False


def test_validator_refuses_when_it_cannot_verify(
    direct_vm, direct_deploy, direct_owner, direct_alice
):
    _judged(direct_vm, direct_deploy, direct_owner, direct_alice)

    # No page and no model: the validator cannot reproduce the score.
    direct_vm.clear_mocks()

    assert direct_vm.run_validator() is False
