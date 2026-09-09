#!/usr/bin/env python3
"""Daily closed-loop learning job for the Threads growth system."""

from generate_and_schedule import NICHE, generate_text, load_history
from growth_intelligence import refresh_daily_intelligence
from threads_insights import reconcile_history


def main() -> None:
    reconcile_history()
    history = load_history()
    state = refresh_daily_intelligence(generate_text, NICHE, history, force=True)
    print(
        f"[ Daily Learning ] Ready for {state.get('date_ist')}: "
        f"{state.get('signal_count', 0)} signals, {len(state.get('candidates', []))} candidates."
    )


if __name__ == "__main__":
    main()
