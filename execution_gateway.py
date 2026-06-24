"""Future live execution boundary.

This module intentionally cannot place orders. It exists so the paper engine
does not need to be redesigned later and so live trading cannot be enabled by
an accidental UI click or a committed configuration file.
"""

from dataclasses import dataclass
from typing import Dict

from polymarket_api import check_geoblock


class LiveTradingDisabled(RuntimeError):
    pass


@dataclass
class LiveReadiness:
    eligible_region: bool
    country: str
    private_key_configured: bool
    api_credentials_configured: bool
    explicit_operator_unlock: bool
    implementation_enabled: bool = False

    @property
    def ready(self) -> bool:
        return all([
            self.eligible_region,
            self.private_key_configured,
            self.api_credentials_configured,
            self.explicit_operator_unlock,
            self.implementation_enabled,
        ])


def readiness_from_environment(environment: Dict[str, str]) -> LiveReadiness:
    geo = check_geoblock()
    return LiveReadiness(
        eligible_region=not geo["blocked"],
        country=geo["country"],
        private_key_configured=bool(environment.get("POLYMARKET_PRIVATE_KEY")),
        api_credentials_configured=all(
            environment.get(key)
            for key in (
                "POLYMARKET_API_KEY",
                "POLYMARKET_API_SECRET",
                "POLYMARKET_API_PASSPHRASE",
            )
        ),
        explicit_operator_unlock=(
            environment.get("POLYMARKET_LIVE_UNLOCK") == "I_ACCEPT_REAL_LOSS"
        ),
    )


def place_live_order(*_args, **_kwargs):
    raise LiveTradingDisabled(
        "Live execution nie jest zaimplementowane. Najpierw wymagany jest "
        "udokumentowany paper trading, kwalifikacja regionu i osobny audyt."
    )
