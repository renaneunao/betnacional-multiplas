import logging
import math
from itertools import combinations, product
from typing import List, Optional

from multiplas.models import MatchOdds, Combination, AnalysisResult

logger = logging.getLogger("multiplas.engine")

OUTCOME_LABELS = {"1": "casa", "2": "empate", "3": "fora"}


class MultiplesEngine:
    def __init__(self, num_legs: int = 3):
        self.num_legs = num_legs

    def analyze(
        self,
        matches: List[MatchOdds],
        stake: float = 1.0,
        require_draw: bool = False,
        max_odd: Optional[float] = None,
        mixed_count: Optional[int] = None,
    ) -> AnalysisResult:
        if len(matches) < self.num_legs:
            return AnalysisResult(
                num_matches=len(matches), num_legs=self.num_legs,
                total_combinations=0, stake_per_bet=stake, total_stake=0, combinations=[]
            )

        all_combos: List[Combination] = []

        for match_combo in combinations(matches, self.num_legs):
            outcome_lists = [[(m, o) for o in m.outcomes] for m in match_combo]

            for outcome_combo in product(*outcome_lists):
                if require_draw:
                    has_draw = any(o.id == "2" for _, o in outcome_combo)
                    if not has_draw:
                        continue

                if max_odd is not None:
                    if any(o.odd > max_odd for _, o in outcome_combo):
                        continue

                entries = []
                combined_prob = 1.0
                total_odd = 1.0

                for match, outcome in outcome_combo:
                    entries.append({
                        "match_id": match.match_id,
                        "choice": OUTCOME_LABELS.get(outcome.id, outcome.id),
                        "team": outcome.name,
                        "odd": outcome.odd,
                        "implied_prob": outcome.implied_prob,
                        "match": f"{match.home_team} vs {match.away_team}",
                        "start_time": match.start_time,
                    })
                    combined_prob *= outcome.implied_prob
                    total_odd *= outcome.odd

                all_combos.append(Combination(
                    rank=0,
                    combined_prob=round(combined_prob, 6),
                    total_odd=round(total_odd, 2),
                    expected_return=round(total_odd * stake, 2),
                    entries=entries,
                    matches_summary=" | ".join(f"{e['team']} (@{e['odd']})" for e in entries),
                ))

        all_combos.sort(key=lambda c: c.combined_prob, reverse=True)
        for i, c in enumerate(all_combos):
            c.rank = i + 1

        total_count = len(all_combos)

        if mixed_count and mixed_count > 0 and len(all_combos) > mixed_count:
            selected = self._select_mixed(all_combos, mixed_count)
            all_combos = selected

        logger.info(
            "Analysis: %d matches, %d legs = %d combos (filtered from %d)",
            len(matches), self.num_legs, len(all_combos), total_count
        )

        return AnalysisResult(
            num_matches=len(matches), num_legs=self.num_legs,
            total_combinations=len(all_combos), stake_per_bet=stake,
            total_stake=len(all_combos) * stake, combinations=all_combos
        )

    def _select_mixed(self, sorted_combos: List[Combination], count: int) -> List[Combination]:
        """Diverse selection: mostly top picks + samples from lower tiers so failure isn't correlated."""
        n = len(sorted_combos)
        selected = []
        seen_ids = set()

        tier1_size = int(count * 0.50)
        tier2_size = int(count * 0.25)
        tier3_size = int(count * 0.15)
        tier4_size = count - tier1_size - tier2_size - tier3_size

        tier1_end = max(1, int(n * 0.03))
        tier2_end = max(tier1_end + 1, int(n * 0.15))
        tier3_end = max(tier2_end + 1, int(n * 0.50))

        selected.extend(sorted_combos[:tier1_size])

        t2_pool = sorted_combos[tier1_end:tier2_end]
        step = max(1, len(t2_pool) // max(tier2_size, 1))
        for i in range(min(tier2_size, len(t2_pool))):
            idx = min(i * step, len(t2_pool) - 1)
            selected.append(t2_pool[idx])

        t3_pool = sorted_combos[tier2_end:tier3_end]
        step = max(1, len(t3_pool) // max(tier3_size, 1))
        for i in range(min(tier3_size, len(t3_pool))):
            idx = min(i * step, len(t3_pool) - 1)
            selected.append(t3_pool[idx])

        t4_pool = sorted_combos[tier3_end:]
        step = max(1, len(t4_pool) // max(tier4_size, 1))
        for i in range(min(tier4_size, len(t4_pool))):
            idx = min(i * step, len(t4_pool) - 1)
            selected.append(t4_pool[idx])

        selected.sort(key=lambda c: c.combined_prob, reverse=True)
        for i, c in enumerate(selected):
            c.rank = i + 1

        return selected
