from buitre.models import StrategyDecision, TargetInfo
from buitre.strategy.rules import FetchStrategy


def select_initial_strategy(
    target: TargetInfo,
) -> StrategyDecision:
    """
    Select the lightest suitable acquisition strategy.
    """

    if target.is_onion:
        return StrategyDecision(
            strategy=FetchStrategy.TOR_HTTP.value,
            reason=(
                "Target is an onion service; "
                "starting with Tor HTTP acquisition."
            ),
            fallback_allowed=True,
        )

    return StrategyDecision(
        strategy=FetchStrategy.HTTP.value,
        reason=(
            "Standard HTTP/HTTPS target; "
            "starting with direct HTTP acquisition."
        ),
        fallback_allowed=True,
    )
