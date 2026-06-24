import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from snapshot_store import SnapshotStore, build_full_snapshot


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATUS_PATH = DATA_DIR / "snapshot_refresh_status.json"
PID_PATH = DATA_DIR / "snapshot_refresh.pid"


def write_status(state: str, **values):
    payload = {
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        **values,
    }
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, STATUS_PATH)


def load_wallets():
    result = []
    for line in (ROOT / "wallets.txt").read_text(encoding="utf-8-sig").splitlines():
        wallet = line.split("#", 1)[0].strip().lower()
        if wallet:
            result.append(wallet)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=500)
    args = parser.parse_args()
    DATA_DIR.mkdir(exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    wallets = load_wallets()
    try:
        write_status("running", wallets=len(wallets), records=args.records)
        result = build_full_snapshot(wallets, trades_per_wallet=args.records)
        if result["trades"].empty:
            raise RuntimeError("Nie pobrano zadnych transakcji.")
        failed = result["status"][
            result["status"]["error"].fillna("").astype(str).str.len() > 0
        ]
        if not failed.empty:
            details = "; ".join(
                f"{row.wallet}: {row.error}"
                for row in failed.itertuples(index=False)
            )
            raise RuntimeError(
                "Niepelna migawka nie zostala zapisana. " + details
            )
        SnapshotStore(DATA_DIR).save(
            result["trades"],
            result["open_positions"],
            result["closed_positions"],
            {
                "wallets": wallets,
                "trades_per_wallet": args.records,
                "status": result["status"].to_dict(orient="records"),
            },
        )
        write_status(
            "complete",
            trades=len(result["trades"]),
            open_positions=len(result["open_positions"]),
            closed_positions=len(result["closed_positions"]),
            status=result["status"].to_dict(orient="records"),
        )
    except Exception as exc:
        write_status("error", error=str(exc))
        raise
    finally:
        PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
