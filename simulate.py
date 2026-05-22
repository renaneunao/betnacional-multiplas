"""
Simulação com dados reais da rodada atual do Brasileirão.
Usa Cartola como referência oficial e Betnacional para odds.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from multiplas.client import BetnacionalAPIClient
from multiplas.engine import MultiplesEngine
from multiplas.config import Config
from multiplas.cartola import CartolaRound, cartola_to_betnacional_name


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Simulador de múltiplas do Brasileirão")
    parser.add_argument("--legs", type=int, default=None, help="Jogos por múltipla (default: 3)")
    parser.add_argument("--stake", type=float, default=None, help="Valor por entrada (default: 1.00)")
    parser.add_argument("--top", type=int, default=15, help="Quantas combinações exibir")
    parser.add_argument("--api-url", type=str, default=None, help="URL da API do cliente Betnacional")
    args = parser.parse_args()

    num_legs = args.legs or Config.NUM_LEGS
    stake = args.stake or Config.STAKE
    api_url = args.api_url or Config.BETNACIONAL_API_URL

    print("=" * 80)
    print("  SIMULADOR DE MÚLTIPLAS - BRASILEIRÃO SÉRIE A")
    print("=" * 80)
    print(f"  API Betnacional: {api_url}")
    print(f"  Jogos por múltipla: {num_legs}")
    print(f"  Stake por entrada: R$ {stake:.2f}")
    print()

    print("1. Consultando Cartola (referência oficial da rodada)...")
    cartola = CartolaRound.fetch()
    print(f"   Rodada {cartola.rodada} - {len(cartola.partidas)} jogos válidos:")
    for i, p in enumerate(cartola.partidas):
        bn_casa = cartola_to_betnacional_name(p["casa"])
        bn_visitante = cartola_to_betnacional_name(p["visitante"])
        print(f"   [{i+1:2d}] {p['data']}  {bn_casa} vs {bn_visitante} ({p['local']})")

    print()
    print("2. Obtendo odds da Betnacional...")
    client = BetnacionalAPIClient(base_url=api_url)
    matches = client.get_round_matches()

    if not matches:
        print("   [ERRO] Nenhum jogo encontrado na Betnacional!")
        return

    print(f"   {len(matches)} jogos com odds disponíveis:")
    for i, m in enumerate(matches):
        outcomes_str = " | ".join(
            f"{o.name}={o.odd}({o.implied_prob*100:.1f}%)"
            for o in m.outcomes
        )
        print(f"   [{i+1:2d}] {m.match_id}  {m.home_team} vs {m.away_team}")
        print(f"        {m.start_time}  (margem: {m.margin*100:.2f}%)")
        print(f"        {outcomes_str}")

    print()
    print(f"3. Calculando combinações de {num_legs} jogos...")
    engine = MultiplesEngine(num_legs=num_legs)
    result = engine.analyze(matches, stake=stake)

    print(f"   Total de combinações: {result.total_combinations}")
    print(f"   Stake total (se apostar todas): R$ {result.total_stake:.2f}")
    print()

    if not result.combinations:
        print("   Nenhuma combinação possível.")
        return

    top_n = min(args.top, len(result.combinations))
    print(f"   TOP {top_n} MAIS SEGURAS (maior probabilidade combinada):")
    print()
    header = f"   {'#':<4} {'Prob.%':<8} {'Odd':<8} {'Retorno':<10} {'Seleções'}"
    print(header)
    print(f"   {'-'*4} {'-'*8} {'-'*8} {'-'*10} {'-'*50}")

    for c in result.combinations[:top_n]:
        prob_pct = c.combined_prob * 100
        retorno = stake * c.total_odd
        games = " + ".join(
            f"{e['team']} (@{e['odd']})"
            for e in c.entries
        )
        print(f"   {c.rank:<4} {prob_pct:<8.2f} {c.total_odd:<8.2f} R${retorno:<9.2f} {games}")

    print()
    print("   DISTRIBUIÇÃO DE PROBABILIDADES:")
    bins = [0, 5, 10, 15, 20, 30, 40, 50, 100]
    counts = [0] * (len(bins) - 1)
    for c in result.combinations:
        pct = c.combined_prob * 100
        for i in range(len(bins) - 1):
            if bins[i] <= pct < bins[i + 1]:
                counts[i] += 1
                break

    max_count = max(counts) if max(counts) > 0 else 1
    for i in range(len(bins) - 1):
        bar = "#" * max(1, counts[i] // max(1, max_count // 30))
        print(f"   {bins[i]:>5.0f}%-{bins[i+1]:>5.0f}%: {counts[i]:>5d}  {bar}")

    print()
    print("=" * 80)
    print("  Simulação concluída.")
    print(f"  Para apostar: POST /execute com {{combinations: [1,2,3...]}}")
    print("=" * 80)


if __name__ == "__main__":
    main()
