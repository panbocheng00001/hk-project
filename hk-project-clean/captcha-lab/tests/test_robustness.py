from defense.challenge import ChallengeConfig
from defense.robustness import PRESETS, evaluate


def _oracle(ch):
    return [ch.gap_cx]


def _blind(ch):
    return [0.0]


def test_oracle_attacker_always_wins():
    r = evaluate(ChallengeConfig(), samples=10, attacker=_oracle)
    assert r.attack_top1 == 1.0 and r.resistance == 0.0


def test_blind_attacker_always_loses():
    r = evaluate(ChallengeConfig(), samples=10, attacker=_blind)
    assert r.attack_top1 == 0.0 and r.resistance == 1.0


def test_hardening_beats_baseline_against_gap_locate():
    base = evaluate(PRESETS["baseline_square"], samples=20)
    hard = evaluate(PRESETS["hardened"], samples=20)
    assert base.attack_top1 > hard.attack_top1
