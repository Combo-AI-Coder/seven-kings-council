"""Reproducible public-seed evaluation. Policies only inspect public state/docket."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import time

from . import rules

POLICIES = ("cautious", "project_spam", "cheap_contract", "negotiator")
SEED_PREFIX = "public-synthetic-balance-v1-"


def choose_command(state, docket, policy="cautious"):
    """Bounded heuristics, not an optimal player or a hidden-goal oracle."""
    if policy not in POLICIES:
        raise ValueError("Unknown evaluation policy")
    a = state["houses"]["A"]
    action = ("tax" if a["gold"] < 5 else
              "reform" if a["prestige"] <= 7 or state["stability"] <= 8 else
              "campaign" if a["influence"] < 9 else "reform")
    vote = "no" if docket["motion"] in ("conscription", "fortify") else "yes"
    command = {"action": {"type": action}, "diplomacy": {"type": "none"},
               "court": {"type": "none"}, "reply": "decline", "vote": vote,
               "sway": 0, "bid": 0}
    if docket["election"]:
        command.update(candidate="A", bid=min(3, a["influence"]))
    if policy == "cautious":
        return rules.validate_command(state, docket, command)
    allies = sorted(h for h in rules.NPCS if rules.pact(state, "A", h))
    if allies:
        target = min(allies, key=lambda h: (-rules.relationship(state, "A", h)["capital"], h))
        agreement = next(p for p in state["pacts"] if {p["a"], p["b"]} == {"A", target})
        if agreement["expires_after_round"] <= docket["round"] + 1:
            command["court"] = {"type": "renew", "target": target}
        elif policy in ("project_spam", "negotiator"):
            command["court"] = {"type": "joint_project", "target": target}
        elif a["influence"] >= command["bid"] + 1:
            command["court"] = {"type": "request_backing", "target": target,
                                "offer_gold": 1, "offer_influence": 0,
                                "use_favor": False}
    elif rules.pact_count(state, "A") < 2:
        candidates = [h for h in rules.NPCS if rules.pact_count(state, h) < 2]
        if candidates:
            target = max(candidates, key=lambda h: (rules.trust(state, "A", h), -ord(h)))
            command["diplomacy"] = ({"type": "propose", "target": target}
                                    if rules.trust(state, "A", target) >= 1 else
                                    {"type": "bribe", "target": target, "amount": 2})
    if policy == "negotiator":
        if len(allies) < 2:
            candidates = [h for h in rules.NPCS if h not in allies and rules.pact_count(state, h) < 2]
            if candidates:
                target = max(candidates, key=lambda h: (rules.trust(state, "A", h), -ord(h)))
                command["diplomacy"] = ({"type": "propose", "target": target}
                                        if rules.trust(state, "A", target) >= 1 else
                                        {"type": "bribe", "target": target, "amount": 2})
        # Honour any live promise, and accept one legal, presealed invitation.
        # Selection uses public proposal content only, never a hidden goal/plan.
        if docket["election"]:
            command["candidate"] = rules.promised(state, "A", docket["round"]) or "A"
        for offer in sorted(docket.get("offers", []), key=lambda o: (
                o["candidate"] != "A", o["gold_from_player"], o["sender"])):
            proposed = deepcopy(command)
            proposed["offer_reply"] = {"offer_id": offer["offer_id"], "response": "accept"}
            if docket["election"]:
                proposed["candidate"] = offer["candidate"]
            try:
                return rules.validate_command(state, docket, proposed)
            except ValueError as exc:
                if "Insufficient" not in str(exc):
                    raise
    # The adversarial choice still pays legal costs. Fall back to its main action
    # only when the combined public-start budget is insufficient.
    try:
        return rules.validate_command(state, docket, command)
    except ValueError as exc:
        if "Insufficient" not in str(exc):
            raise
        command["court"] = {"type": "none"}
        command["diplomacy"] = {"type": "none"}
        return rules.validate_command(state, docket, command)


def simulate(seed, policy):
    state = rules.opening_state(seed, game_id="PUBLIC-EVALUATION")
    private = {"seed": seed, "goals": rules.secret_goals(seed)}
    counters = {"project_attempts": 0, "projects_completed": 0,
                "cheap_offer_attempts": 0, "cheap_offers_accepted": 0,
                "rejection_events": 0, "npc_coalitions": 0,
                "npc_player_offers": 0, "npc_player_offers_accepted": 0,
                "contracts_due": 0,
                "contracts_honored": 0, "refusal_increments": 0}
    max_capital = max_tension = 0
    trace = []
    for _ in range(20):
        if state["status"] != "ongoing":
            break
        docket = rules.make_docket(state, seed)
        command = choose_command(state, docket, policy)
        counters["project_attempts"] += command["court"]["type"] == "joint_project"
        counters["cheap_offer_attempts"] += command["court"]["type"] == "request_backing"
        original = deepcopy(state)
        input_state = state
        before_refusals = sum(v["refusals"] for v in state["relations"].values())
        state, outcome = rules.execute_round(state, private, command)
        if input_state != original:
            raise AssertionError("Pure transition mutated input state")
        if not 0 <= state["round"] <= 20 or state["round"] != original["round"] + 1:
            raise AssertionError("Invalid round progression")
        for house in state["houses"].values():
            for resource, cap in rules.CAPS.items():
                if not 0 <= house[resource] <= cap:
                    raise AssertionError("Resource escaped cap")
        if outcome["ai_plan_commit"] != docket["ai_plan_commit"]:
            raise AssertionError("Precommitted NPC plans changed")
        events = outcome["events"]
        counters["projects_completed"] += sum(e["code"] == "joint_project" and e.get("sender") == "A" for e in events)
        counters["cheap_offers_accepted"] += sum(e["code"] == "politics_offer_accepted" and e.get("type") == "request_backing" for e in events)
        counters["rejection_events"] += sum("reject" in e["code"] or "declin" in e["code"] for e in events)
        counters["npc_coalitions"] += sum(e["code"] == "npc_coalition" for e in events)
        counters["npc_player_offers"] += len(docket.get("offers", []))
        counters["npc_player_offers_accepted"] += sum(e["code"] == "npc_offer_accepted" for e in events)
        counters["refusal_increments"] += max(0, sum(v["refusals"] for v in state["relations"].values()) - before_refusals)
        # Exact denominator: NPC ballots flagged as governed by a live promise.
        # Expired/replaced contracts are not considered due, and A is excluded.
        due = [v for h, v in outcome["election_votes"].items() if h != "A" and v["contract"]]
        counters["contracts_due"] += len(due)
        counters["contracts_honored"] += sum(e["code"] == "contract_honored" and e.get("voter") != "A" for e in events)
        max_capital = max(max_capital, *(v["capital"] for v in state["relations"].values()))
        max_tension = max(max_tension, *(v["tension"] for v in state["relations"].values()))
        trace.append(rules.digest(outcome))
    if state["status"] == "ongoing":
        raise AssertionError("Evaluation failed to reach terminal state")
    crowns = [state["houses"][h]["crowns"] for h in rules.HOUSES]
    total = sum(crowns)
    row = {"seed": seed, "policy": policy, "rounds": state["round"],
           "status": state["status"], "victory": int(state["status"] in ("victory", "perfect_victory")),
           "perfect_victory": int(state["status"] == "perfect_victory"),
           "hard_failure": int(state["status"] == "hard_failure"),
           "player_mission_met": int(rules.goal_met(state, "A", "player_mission")),
           "npc_goals_met": sum(rules.goal_met(state, h, private["goals"][h]) for h in rules.NPCS),
           "crown_hhi": sum((n / total) ** 2 for n in crowns) if total else None,
           "player_crowns": crowns[0], "total_crowns": total,
           "final_stability": state["stability"],
           "player_score": state["scores"]["A"],
           "max_capital": max_capital, "max_tension": max_tension,
           "final_total_tension": sum(v["tension"] for v in state["relations"].values()),
           **counters, "trace_sha256": rules.digest(trace),
           "state_sha256": rules.digest(state)}
    if counters["contracts_honored"] > counters["contracts_due"]:
        raise AssertionError("Contract fulfillment numerator exceeds denominator")
    return row


def summarize(rows):
    output = {}
    for policy in POLICIES:
        group = [r for r in rows if r["policy"] == policy]
        if not group:
            continue
        n = len(group)
        avg = lambda key: sum(r[key] for r in group) / n
        due = sum(r["contracts_due"] for r in group)
        crowns = [r["crown_hhi"] for r in group if r["crown_hhi"] is not None]
        output[policy] = {"games": n, "victory_rate": avg("victory"),
                          "perfect_victory_rate": avg("perfect_victory"),
                          "hard_failure_rate": avg("hard_failure"),
                          "player_mission_rate": avg("player_mission_met"),
                          "npc_secret_goal_rate": avg("npc_goals_met") / 6,
                          "mean_crown_hhi": sum(crowns) / len(crowns) if crowns else None,
                          "games_without_crowns": n - len(crowns),
                          "npc_contracts_due": due,
                          "npc_contract_honor_rate": sum(r["contracts_honored"] for r in group) / due if due else None,
                          "mean_rounds": avg("rounds"), "mean_player_score": avg("player_score"),
                          "mean_final_tension": avg("final_total_tension"),
                          "mean_rejection_events": avg("rejection_events"),
                          "mean_refusal_increments": avg("refusal_increments"),
                          "npc_coalitions_per_game": avg("npc_coalitions"),
                          "npc_player_offers_per_game": avg("npc_player_offers"),
                          "npc_player_offers_accepted": sum(r["npc_player_offers_accepted"] for r in group),
                          "project_attempts": sum(r["project_attempts"] for r in group),
                          "projects_completed": sum(r["projects_completed"] for r in group),
                          "cheap_offer_attempts": sum(r["cheap_offer_attempts"] for r in group),
                          "cheap_offers_accepted": sum(r["cheap_offers_accepted"] for r in group)}
    return output


def evaluate(count=100):
    if type(count) is not int or count < 1:
        raise ValueError("Seed count must be positive")
    rows = [simulate(SEED_PREFIX + "%04d" % i, policy)
            for policy in POLICIES for i in range(count)]
    source = Path(rules.__file__).read_bytes()
    return {"schema": 1, "seed_prefix": SEED_PREFIX, "seed_count": count,
            "max_rounds": 20, "policies": list(POLICIES),
            "rules_sha256": hashlib.sha256(source).hexdigest(),
            "simulation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "summary": summarize(rows), "rows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("balance-results"))
    args = parser.parse_args(argv)
    start = time.perf_counter()
    report = evaluate(args.seeds)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "balance.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(report["rows"][0]))
    writer.writeheader()
    writer.writerows(report["rows"])
    (args.output / "balance.csv").write_text(stream.getvalue(), encoding="utf-8")
    print(json.dumps({"games": len(report["rows"]), "seconds": round(time.perf_counter() - start, 3),
                      "output": str(args.output), "summary": report["summary"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
