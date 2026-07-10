"""Mint test JWTs for the Modbus PEP.

Example:
  python tools/mint_token.py --role maintenance_engineer --risk-score 20 --ttl 300
"""

from __future__ import annotations

import argparse

from src.auth import mint_token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", default="device_01")
    parser.add_argument("--role", default="maintenance_engineer")
    parser.add_argument("--zone", default="cell_A")
    parser.add_argument("--attestation-state", default="valid", choices=["valid", "stale", "failed"])
    parser.add_argument("--risk-score", type=int, default=20)
    parser.add_argument("--ttl", type=int, default=300)
    args = parser.parse_args()

    print(
        mint_token(
            subject=args.subject,
            role=args.role,
            zone=args.zone,
            attestation_state=args.attestation_state,
            risk_score=args.risk_score,
            ttl_seconds=args.ttl,
        )
    )


if __name__ == "__main__":
    main()
