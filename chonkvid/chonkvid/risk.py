"""Attack-risk forecast from recorded runs.

Every recorded run (runs/*.jsonl, the recordings /api/runs lists) says which
attack was released (config.label), what the contact network looked like, who was
vaccinated and who got infected. This module turns that history into a forecast:
for a given attack type, how likely is each agent in a world to be infected if that
attack reaches the fleet?

Agents are not the same people across runs (worlds differ by seed and size), so the
forecast generalises through an agent *profile* that carries over:

  - contact tier: low / mid / high degree, by tercile within that run's world;
  - group-chat membership (the super-spreader venue);
  - vaccinated or not.

Per attack type and profile we count infections over all past exposures-to-a-run,
then shrink toward that attack's overall rate (empirical Bayes, prior weight
PRIOR_WEIGHT) so a profile seen in only a few runs does not swing to 0% or 100%.
Patient zero is excluded: it was seeded, not infected.

Live runs are preferred: when an attack type has any live run, mock runs of that
type are ignored, and every number says which source it came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PRIOR_WEIGHT = 4.0
MAX_RUNS = 200
# Labels written by the lab dashboard and `experiment`: all of them release the
# ChonkVid chain letter, so they count as that attack.
CHONKVID_LABELS = {"", "none", "random", "targeted", "none-default", "who", "hardened", "chonkvid"}
BREAKOUT = 0.25  # a run "broke out" when more than a quarter of the fleet was infected


def attack_of(config: dict) -> str:
    label = str(config.get("label") or "")
    return "chonkvid" if label in CHONKVID_LABELS else label


def tiers(nodes: list[dict]) -> dict[int, str]:
    """Contact tier per agent: terciles of degree within one world."""
    degs = sorted(n.get("degree", 0) for n in nodes)
    if not degs:
        return {}
    q1 = degs[len(degs) // 3]
    q2 = degs[(2 * len(degs)) // 3]
    out = {}
    for n in nodes:
        d = n.get("degree", 0)
        # Ties at the top still form a high tier; a world where everyone has the
        # same degree is all "mid".
        out[n["id"]] = "low" if d < q1 else "high" if (d >= q2 and d > q1) else "mid"
    return out


def profile(tier: str, venue: bool) -> str:
    return f"{tier}|{'chat' if venue else 'nochat'}"


@dataclass
class _Tally:
    k: int = 0
    n: int = 0

    def add(self, hit: bool) -> None:
        self.n += 1
        self.k += int(hit)

    @property
    def rate(self) -> float:
        return self.k / self.n if self.n else 0.0


@dataclass
class _AttackHistory:
    runs: int = 0
    rates: list[float] = field(default_factory=list)
    last: str = ""
    unvac: _Tally = field(default_factory=_Tally)
    vac: _Tally = field(default_factory=_Tally)
    by_profile: dict[str, _Tally] = field(default_factory=dict)
    by_role: dict[str, _Tally] = field(default_factory=dict)


def parse_run(events: list[dict]) -> dict | None:
    """One recorded run -> the facts the forecast needs, or None if it is unusable."""
    start = next((e for e in events if e.get("type") == "start"), None)
    if not start or not start.get("world"):
        return None
    end = next((e for e in events if e.get("type") == "end"), None)
    infected = {e["agent"] for e in events if e.get("type") == "infection"}
    return {
        "attack": attack_of(start.get("config") or {}),
        "source": (end or start).get("source") or start.get("source") or "unknown",
        "world": start["world"],
        "vaccinated": set(start.get("vaccinated") or []),
        "patient_zero": start.get("patient_zero"),
        "infected": infected,
        "attack_rate": (end or {}).get("summary", {}).get("attack_rate"),
    }


def load_runs(limit: int = MAX_RUNS, runs_dir: Path | None = None) -> list[tuple[str, list[dict]]]:
    """Recorded runs from the runs folder, newest first (the same recordings /api/runs lists)."""
    out: list[tuple[str, list[dict]]] = []
    if runs_dir is None:
        from .experiment import RUNS
        runs_dir = RUNS
    if not runs_dir.exists():
        return []
    for p in sorted(runs_dir.glob("*.jsonl"), reverse=True)[:limit]:
        try:
            events = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        out.append((p.name, events))
    return out


def build_history(runs: list[tuple[str, list[dict]]]) -> dict[str, dict[str, _AttackHistory]]:
    """attack -> source -> tallies."""
    hist: dict[str, dict[str, _AttackHistory]] = {}
    for run_id, events in runs:
        r = parse_run(events)
        if not r:
            continue
        h = hist.setdefault(r["attack"], {}).setdefault(r["source"], _AttackHistory())
        h.runs += 1
        h.last = h.last or run_id
        nodes = r["world"].get("nodes", [])
        tier = tiers(nodes)
        others = [n for n in nodes if n["id"] != r["patient_zero"]]
        hits = sum(1 for n in others if n["id"] in r["infected"])
        h.rates.append(r["attack_rate"] if r["attack_rate"] is not None
                       else (hits / len(others) if others else 0.0))
        for n in others:
            hit = n["id"] in r["infected"]
            if n["id"] in r["vaccinated"]:
                h.vac.add(hit)
                continue
            h.unvac.add(hit)
            h.by_profile.setdefault(profile(tier[n["id"]], bool(n.get("venue"))), _Tally()).add(hit)
            if n.get("role"):
                h.by_role.setdefault(n["role"], _Tally()).add(hit)
    return hist


def _shrunk(t: _Tally | None, base: float) -> float:
    k, n = (t.k, t.n) if t else (0, 0)
    return (k + PRIOR_WEIGHT * base) / (n + PRIOR_WEIGHT)


def forecast(world: dict, runs: list[tuple[str, list[dict]]]) -> dict:
    """Per attack type: history summary plus a risk for every agent in `world`."""
    hist = build_history(runs)
    nodes = world.get("nodes", [])
    tier = tiers(nodes)
    attacks = {}
    for attack, by_source in hist.items():
        source = "live" if "live" in by_source else max(by_source, key=lambda s: by_source[s].runs)
        h = by_source[source]
        base = h.unvac.rate
        vac_rate = _shrunk(h.vac, base) if h.vac.n else None
        agents = {}
        for n in nodes:
            prof = profile(tier[n["id"]], bool(n.get("venue")))
            agents[n["id"]] = {
                "risk": round(_shrunk(h.by_profile.get(prof), base), 4),
                "risk_vaccinated": None if vac_rate is None else round(vac_rate, 4),
                "profile": prof,
                "evidence": (h.by_profile[prof].n if prof in h.by_profile else 0),
            }
        rates = h.rates
        attacks[attack] = {
            "source": source,
            "runs": h.runs,
            "other_sources": {s: v.runs for s, v in by_source.items() if s != source},
            "latest_run": h.last,
            "attack_rate_mean": round(sum(rates) / len(rates), 4) if rates else 0.0,
            "breakout_share": round(sum(1 for x in rates if x > BREAKOUT) / len(rates), 4) if rates else 0.0,
            "unvaccinated": {"k": h.unvac.k, "n": h.unvac.n, "rate": round(base, 4)},
            "vaccinated": {"k": h.vac.k, "n": h.vac.n, "rate": round(h.vac.rate, 4)} if h.vac.n else None,
            "profiles": {p: {"k": t.k, "n": t.n, "rate": round(_shrunk(t, base), 4)}
                         for p, t in sorted(h.by_profile.items())},
            "roles": sorted(({"role": r, "k": t.k, "n": t.n, "rate": round(_shrunk(t, base), 4)}
                             for r, t in h.by_role.items()), key=lambda x: -x["rate"]),
            "agents": agents,
        }
    return {"prior_weight": PRIOR_WEIGHT, "breakout_threshold": BREAKOUT,
            "runs_seen": len(runs), "attacks": attacks}
