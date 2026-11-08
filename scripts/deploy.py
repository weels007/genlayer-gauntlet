"""Deploy Gauntlet once, with the contest frozen at deployment.

    python scripts/deploy.py            # deploy only when the source changed
    python scripts/deploy.py --force    # redeploy and replace the address

The brief and the rubric are constructor arguments and there is no setter, so a
new contest means a new deployment. The address is recorded in deployment.json.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from genlayer_py import create_client, studionet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wallet import load_account  # noqa: E402

CONTRACT_PATH = ROOT / "contracts" / "Gauntlet.py"
DEPLOYMENT_PATH = ROOT / "deployment.json"

POLL_INTERVAL = 10_000
POLL_RETRIES = 60

ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")

# The seeded contest: judged from one public page, no interpretation needed.
BRIEF = "Document an open-source project well enough that a stranger can run it."
CRITERIA = (
    "1. The first screen says what the project does. "
    "2. The page shows how to install or run it with real commands. "
    "3. The page says where the source code lives."
)
CRITERIA_COUNT = 3


def recorded() -> dict:
    if not DEPLOYMENT_PATH.exists():
        return {}
    try:
        payload = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_deployment(address: str, revision: str) -> None:
    DEPLOYMENT_PATH.write_text(
        json.dumps(
            {
                "contract": "Gauntlet",
                "network": "GenLayer Studio (61999)",
                "address": address,
                "revision": revision,
                "explorer": f"https://explorer-studio.genlayer.com/address/{address}",
                "brief": BRIEF,
                "criteria_count": CRITERIA_COUNT,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def find_address(receipt) -> str:
    if not isinstance(receipt, dict):
        return ""
    candidates = [receipt.get("to_address"), receipt.get("recipient")]
    for key in ("data", "tx_data_decoded"):
        block = receipt.get(key)
        if isinstance(block, dict):
            candidates.append(block.get("contract_address"))
            candidates.append(block.get("to_address"))
    for value in candidates:
        if isinstance(value, str) and ADDRESS_VALUE.fullmatch(value):
            return value
    return ""


def execution_succeeded(receipt) -> bool:
    if not isinstance(receipt, dict):
        return False
    if receipt.get("status_name") not in ("ACCEPTED", "FINALIZED"):
        return False
    leader = (receipt.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    if isinstance(leader, dict):
        return leader.get("execution_result") == "SUCCESS"
    return str(receipt.get("tx_execution_result_name", "")) == "FINISHED_WITH_RETURN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="redeploy even when this revision is recorded"
    )
    args = parser.parse_args()

    code = CONTRACT_PATH.read_text(encoding="utf-8")
    revision = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
    previous = recorded()

    if not args.force and previous.get("address") and previous.get("revision") == revision:
        print(
            f"[deploy] deployment.json already points at {previous['address']} "
            f"at revision {revision}; reusing it"
        )
        return 0

    account = load_account("tester1")
    client = create_client(studionet, account=account)

    print(f"[deploy] deploying Gauntlet from {account.address} (revision {revision})")
    tx_hash = client.deploy_contract(
        code=code, args=[BRIEF, CRITERIA, CRITERIA_COUNT], account=account
    )
    print(f"[deploy] transaction {tx_hash}")

    receipt = client.wait_for_transaction_receipt(
        tx_hash, retries=POLL_RETRIES, interval=POLL_INTERVAL
    )
    if not execution_succeeded(receipt):
        raise SystemExit(f"[deploy] the deployment did not succeed: {receipt}")

    address = find_address(receipt)
    if not address:
        raise SystemExit(f"[deploy] no contract address in the receipt: {receipt}")

    # An address in a receipt is not proof of the contest we meant to create: read
    # the frozen rubric back before recording anything.
    try:
        contest = client.read_contract(address, "get_contest")
    except Exception as error:
        raise SystemExit(f"[deploy] the deployed contest does not answer: {error}")

    if contest.get("brief") != BRIEF or int(contest.get("criteria_count", 0)) != CRITERIA_COUNT:
        raise SystemExit(
            f"[deploy] the contest at {address} has the wrong rubric: {contest}"
        )

    write_deployment(address, revision)
    print(f"[deploy] Gauntlet is at {address}")
    print(f"[deploy] explorer: https://explorer-studio.genlayer.com/address/{address}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
