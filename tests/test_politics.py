"""Public synthetic regression coverage for money, negotiation and narrative."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from seven_kings import rules

SEED = "synthetic-public-politics-acceptance-v1"


def idle_plans(state=None, *, offers=False, round_no=1):
    plans = {h: {"action": "pass", "diplomacy_priorities": [],
                 "court": {"type": "none"}} for h in rules.NPCS}
    if offers:
        for h in rules.NPCS:
            offer = rules.player_offer(state, SEED, round_no, h)
            plans[h]["player_offer"] = offer
            if offer:
                plans[h]["court"] = {"type": "player_offer", "target": "A"}
    return plans


def command(state, **parts):
    value = {"action": {"type": "pass"}, "vote": "no"}
    if (state["round"] + 1) % 4 == 0:
        value["candidate"] = "A"
    value.update(parts)
    return value


class BribeAtomicity(unittest.TestCase):
    def test_failed_actual_payment_cannot_create_gold_or_trust(self):
        state = rules.opening_state(SEED)
        state["houses"]["A"]["gold"] = 4
        state["houses"]["B"]["guards"] = state["houses"]["A"]["guards"] + 3
        plans = idle_plans()
        plans["B"].update(action="raid", raid_target="A")
        private = {"seed": SEED, "goals": rules.secret_goals(SEED)}
        attempt = command(state, diplomacy={"type": "bribe", "target": "C", "amount": 4})
        with patch.object(rules, "npc_plans", return_value=plans):
            # The complete command is valid before the NPC steals three gold.
            rules.validate_command(state, rules.make_docket(state, SEED), attempt)
            result, out = rules.execute_round(state, private, attempt)
            baseline, _ = rules.execute_round(state, private, command(state))
        self.assertEqual(result, baseline)
        self.assertTrue(any(e["code"] == "raid" and e["target"] == "A" for e in out["events"]))
        rejected = [e for e in out["events"] if e["code"] == "bribe_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "insufficient_gold")
        self.assertFalse(any(e["code"] == "bribe" for e in out["events"]))

    def test_affordable_bribe_keeps_exact_transfer_and_trust(self):
        state = rules.opening_state(SEED)
        rules.relationship(state, "A", "B")["trust"] = 0
        total = sum(x["gold"] for x in state["houses"].values())
        before_a, before_b = (state["houses"][h]["gold"] for h in ("A", "B"))
        out = {"events": []}
        rules.do_diplomacy(state, 1, {"type": "bribe", "target": "B", "amount": 3}, out)
        self.assertEqual(state["houses"]["A"]["gold"], before_a - 3)
        self.assertEqual(state["houses"]["B"]["gold"], before_b + 3)
        self.assertEqual(sum(x["gold"] for x in state["houses"].values()), total)
        self.assertEqual(rules.trust(state, "A", "B"), 2)
        self.assertEqual(out["events"][-1]["code"], "bribe")


class OptimizationParity(unittest.TestCase):
    def test_optimized_python_charges_the_same_escrow(self):
        script = """
from seven_kings import rules
seed = 'synthetic-independent-review-001'
s = rules.opening_state(seed)
state, out = rules.execute_round(s, {'seed': seed, 'goals': rules.secret_goals(seed)},
                                {'action': {'type': 'pass'}, 'vote': 'no', 'sway': 2})
print(rules.digest([state, out]))
"""
        env = dict(os.environ, PYTHONPATH=str(Path(rules.__file__).resolve().parent.parent))
        plain = subprocess.check_output([sys.executable, "-c", script], env=env)
        optimized = subprocess.check_output([sys.executable, "-O", "-c", script], env=env)
        self.assertEqual(plain, optimized)


class HostileActions(unittest.TestCase):
    def setUp(self):
        self.state = rules.opening_state(SEED)

    def test_raid_matches_legacy_cost_and_consequences(self):
        s = self.state
        s["houses"]["A"].update(gold=2, guards=10)
        s["houses"]["D"].update(gold=2, guards=7)
        rules.relationship(s, "A", "D")["trust"] = 3
        out = {"round": 1, "events": []}
        rules.validate_command(s, rules.make_docket(s, SEED),
                               command(s, action={"type": "raid", "target": "D"}))
        rules.do_action(s, "A", "raid", out, target="D")
        self.assertEqual(s["houses"]["A"]["gold"], 2)
        self.assertEqual(s["houses"]["D"]["gold"], 0)
        self.assertEqual(s["houses"]["A"]["scandal"], 3)
        self.assertEqual(s["stability"], 11)
        self.assertEqual(rules.trust(s, "A", "D"), -1)
        self.assertEqual(rules.relationship(s, "A", "D")["tension"], 2)
        self.assertTrue(rules.pact(s, "A", "D"))
        self.assertEqual(out["events"][-1]["stolen"], 2)

    def test_raid_floor_and_three_gold_limit(self):
        for gold, stolen in ((0, 0), (1, 1), (20, 3)):
            s = rules.opening_state(SEED)
            s["houses"]["A"]["guards"] = 18
            s["houses"]["B"]["gold"] = gold
            out = {"events": []}
            rules.do_action(s, "A", "raid", out, target="B")
            self.assertEqual(out["events"][-1]["stolen"], stolen)

    def test_raid_requires_guard_advantage_and_nonplayer_target(self):
        for target in (None, "A", "H", 1):
            with self.subTest(target=target), self.assertRaises(ValueError):
                rules.validate_command(self.state, rules.make_docket(self.state, SEED),
                                       command(self.state, action={"type": "raid", "target": target}))
        with self.assertRaisesRegex(ValueError, "guards"):
            rules.validate_command(self.state, rules.make_docket(self.state, SEED),
                                   command(self.state, action={"type": "raid", "target": "B"}))

    def test_player_resolution_rejection_never_falls_back_to_tax(self):
        s = self.state
        s["houses"]["A"]["gold"] = 1
        before = deepcopy(s)
        out = {"events": []}
        rules.do_action(s, "A", "raid", out, target="B")
        self.assertEqual(s, before)
        self.assertEqual([e["code"] for e in out["events"]], ["raid_rejected"])

    def test_sabotage_matches_legacy_and_clamps_zero(self):
        s = self.state
        s["houses"]["A"].update(gold=0, intel=3)
        s["houses"]["D"]["influence"] = 2
        before_guards = s["houses"]["D"]["guards"]
        out = {"events": []}
        rules.validate_command(s, rules.make_docket(s, SEED),
                               command(s, action={"type": "sabotage", "target": "D"}))
        rules.do_action(s, "A", "sabotage", out, target="D")
        self.assertEqual(s["houses"]["A"]["intel"], 0)
        self.assertEqual(s["houses"]["A"]["gold"], 0)
        self.assertEqual(s["houses"]["A"]["scandal"], 2)
        self.assertEqual(s["houses"]["D"]["influence"], 0)
        self.assertEqual(s["houses"]["D"]["guards"], before_guards)
        self.assertEqual(s["stability"], 12)
        self.assertEqual(rules.trust(s, "A", "D"), 0)
        self.assertTrue(rules.pact(s, "A", "D"))
        self.assertEqual(out["events"][-1]["influence_lost"], 2)

    def test_sabotage_cannot_spend_missing_intel(self):
        with self.assertRaisesRegex(ValueError, "3 intel"):
            rules.validate_command(self.state, rules.make_docket(self.state, SEED),
                                   command(self.state, action={"type": "sabotage", "target": "B"}))
        before = deepcopy(self.state)
        out = {"events": []}
        rules.do_action(self.state, "A", "sabotage", out, target="B")
        self.assertEqual(self.state, before)
        self.assertEqual(out["events"][-1]["code"], "sabotage_rejected")


class PresealedOffers(unittest.TestCase):
    def setUp(self):
        self.state = rules.opening_state(SEED)
        for h in rules.HOUSES:
            self.state["houses"][h]["gold"] = 20
        self.private = {"seed": SEED, "goals": rules.secret_goals(SEED)}

    def invitation(self, state=None, *, r=1):
        s = state or self.state
        offer = rules.player_offer(s, SEED, r, "D")
        self.assertIsNotNone(offer)
        return {"round": r, "offers": [offer]}, offer

    def test_npc_initiates_without_player_court_and_commits_terms(self):
        before = deepcopy(self.state)
        plans = rules.npc_plans(self.state, SEED, 1)
        docket = rules.make_docket(self.state, SEED)
        self.assertEqual(docket["ai_plan_commit"], rules.digest(plans))
        self.assertEqual(plans["D"]["court"], {"type": "player_offer", "target": "A"})
        self.assertEqual(docket["offers"], [plans["D"]["player_offer"]])
        self.assertEqual(set(docket["offers"][0]["counter_terms"]), set(rules.HOUSES))
        self.assertEqual(self.state, before)
        self.assertEqual(docket, rules.make_docket(self.state, SEED))

    def test_sender_can_nominate_player_self_and_third_house(self):
        for nominee in ("A", "D", "B"):
            s = deepcopy(self.state)
            for h in rules.HOUSES:
                s["houses"][h]["scandal"] = 8 if h != nominee else 0
            if nominee == "B":
                for voter in ("A", "D"):
                    rules.relationship(s, voter, nominee).update(trust=5, capital=10)
            _, offer = self.invitation(s)
            self.assertEqual(offer["candidate"], nominee)
            out = {"events": []}
            rules.apply_offer_reply(s, {"round": 1, "offers": [offer]},
                                    {"offer_id": offer["offer_id"], "response": "accept"}, out)
            self.assertTrue(any(e["code"] == "npc_offer_accepted" for e in out["events"]))
            for voter in ("A", "D"):
                self.assertEqual(rules.promised(s, voter, 4), nominee)

    def test_acceptance_exact_transfers_fees_and_pact_extension(self):
        docket, offer = self.invitation()
        before = deepcopy(self.state)
        out = {"events": []}
        rules.apply_offer_reply(self.state, docket,
                                {"offer_id": offer["offer_id"], "response": "accept"}, out)
        self.assertEqual(self.state["houses"]["A"]["gold"],
                         before["houses"]["A"]["gold"] - 1 - offer["gold_from_player"] + offer["gold_to_player"])
        self.assertEqual(self.state["houses"]["D"]["gold"],
                         before["houses"]["D"]["gold"] - 1 + offer["gold_from_player"] - offer["gold_to_player"])
        self.assertEqual(sum(h["gold"] for h in before["houses"].values()) -
                         sum(h["gold"] for h in self.state["houses"].values()), 2)
        self.assertTrue(any(p["expires_after_round"] == 4 and p["a"] == "A" for p in self.state["pacts"]))
        self.assertEqual(len(self.state["contracts"]), 2)

    def test_decline_is_audited_without_transfers_second_refusal_adds_tension(self):
        docket, offer = self.invitation()
        before = deepcopy(self.state["houses"])
        out = {"events": []}
        for _ in range(2):
            rules.apply_offer_reply(self.state, docket,
                                    {"offer_id": offer["offer_id"], "response": "decline"}, out)
        self.assertEqual(self.state["houses"], before)
        self.assertEqual(rules.relationship(self.state, "A", "D")["refusals"], 2)
        self.assertEqual(rules.relationship(self.state, "A", "D")["tension"], 1)
        self.assertFalse(self.state["contracts"])
        self.assertEqual(sum(e["code"] == "npc_offer_declined" for e in out["events"]), 2)

    def test_counteroffer_uses_sealed_candidate_threshold_not_changed_state(self):
        docket, offer = self.invitation()
        candidate = min(offer["counter_terms"], key=lambda c: offer["counter_terms"][c]["required_value"])
        required = offer["counter_terms"][candidate]["required_value"]
        gold = (required + 2) // 3
        # Later capital, tension and prestige changes cannot silently reprice.
        rules.relationship(self.state, "A", "D").update(capital=0, tension=8)
        self.state["houses"][candidate]["scandal"] = 8
        out = {"events": []}
        rules.apply_offer_reply(self.state, docket,
                                {"offer_id": offer["offer_id"], "response": "counter",
                                 "candidate": candidate, "offer_gold": gold, "offer_influence": 0}, out)
        accepted = next(e for e in out["events"] if e["code"] == "npc_offer_accepted")
        self.assertEqual(accepted["required"], required)
        self.assertEqual(accepted["response"], "counter")
        self.assertEqual(rules.promised(self.state, "D", 4), candidate)

    def test_underpriced_counteroffer_is_rejected_without_transfer(self):
        docket, offer = self.invitation()
        candidate = max(offer["counter_terms"], key=lambda c: offer["counter_terms"][c]["required_value"])
        self.assertGreater(offer["counter_terms"][candidate]["required_value"], 0)
        before = deepcopy(self.state["houses"])
        out = {"events": []}
        rules.apply_offer_reply(self.state, docket,
                                {"offer_id": offer["offer_id"], "response": "counter",
                                 "candidate": candidate, "offer_gold": 0, "offer_influence": 0}, out)
        self.assertEqual(self.state["houses"], before)
        self.assertEqual(out["events"][-1]["reason"], "below_sealed_threshold")
        self.assertFalse(self.state["contracts"])

    def test_late_or_lost_pact_offer_cannot_commit(self):
        for invalid in ("late", "lost_pact", "expired_pact"):
            s = deepcopy(self.state)
            docket, offer = self.invitation(s)
            if invalid == "late":
                docket["round"] = 2
            elif invalid == "lost_pact":
                s["pacts"] = []
            else:
                for p in s["pacts"]:
                    p["expires_after_round"] = 0
            before = deepcopy(s["houses"])
            out = {"events": []}
            rules.apply_offer_reply(s, docket,
                                    {"offer_id": offer["offer_id"], "response": "accept"}, out)
            self.assertEqual(s["houses"], before)
            self.assertFalse(s["contracts"])
            self.assertIn(out["events"][-1]["code"], ("npc_offer_expired", "npc_offer_rejected"))

    def test_post_action_resource_shortfall_is_atomic_for_both_sides(self):
        for debtor in ("A", "D"):
            s = deepcopy(self.state)
            docket, offer = self.invitation(s)
            s["houses"][debtor]["gold"] = 0
            before = deepcopy(s["houses"])
            out = {"events": []}
            rules.apply_offer_reply(s, docket,
                                    {"offer_id": offer["offer_id"], "response": "accept"}, out)
            self.assertEqual(s["houses"], before)
            self.assertFalse(s["contracts"])
            self.assertEqual(out["events"][-1]["reason"], "resources")

    def test_offer_influence_and_gold_are_in_combined_budget(self):
        docket = rules.make_docket(self.state, SEED)
        offer = docket["offers"][0]
        reply = {"offer_id": offer["offer_id"], "response": "counter",
                 "candidate": "A", "offer_gold": 4, "offer_influence": 2}
        self.state["houses"]["A"]["gold"] = 4
        with self.assertRaisesRegex(ValueError, "gold"):
            rules.validate_command(self.state, docket, command(self.state, offer_reply=reply))
        self.state["houses"]["A"]["gold"] = 20
        self.state["houses"]["A"]["influence"] = 1
        with self.assertRaisesRegex(ValueError, "influence"):
            rules.validate_command(self.state, docket, command(self.state, offer_reply=reply))

    def test_reply_validation_rejects_stale_ids_and_client_repricing(self):
        docket = rules.make_docket(self.state, SEED)
        offer = docket["offers"][0]
        invalid = [
            {"offer_id": "r00-D-A", "response": "accept"},
            {"offer_id": offer["offer_id"], "response": "accept", "gold_to_player": 30},
            {"offer_id": offer["offer_id"], "response": "counter", "candidate": "A",
             "offer_gold": -1, "offer_influence": 0},
            {"offer_id": offer["offer_id"], "response": "counter", "candidate": "A",
             "offer_gold": True, "offer_influence": 0},
        ]
        before = deepcopy(self.state)
        for reply in invalid:
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                rules.validate_command(self.state, docket, command(self.state, offer_reply=reply))
        self.assertEqual(self.state, before)

    def test_pact_expiration_removes_future_contract_with_audited_reason(self):
        s = self.state
        s["round"] = 2
        rules.sign_contract(s, "D", "A", 3, {"events": []}, "player_offer")
        with patch.object(rules, "npc_plans", return_value=idle_plans()):
            after, out = rules.execute_round(s, self.private, command(s))
        self.assertFalse(after["contracts"])
        expired = [e for e in out["events"] if e["code"] == "contract_expired"]
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["reason"], "pact_expired")
        self.assertEqual(expired[0]["election_round"], 4)

    def test_prices_are_symmetric_and_react_to_capital_tension(self):
        s = self.state
        forward = rules.coalition_terms(s, "D", "A", "B")
        reverse = rules.coalition_terms(s, "A", "D", "B")
        self.assertEqual(forward["thresholds"], reverse["thresholds"])
        low = rules.coalition_terms(s, "D", "A", "A")["required_value"]
        rules.relationship(s, "A", "D")["capital"] = 4
        high_capital = rules.coalition_terms(s, "D", "A", "A")["required_value"]
        self.assertLess(high_capital, low)
        rules.relationship(s, "A", "D")["tension"] = 2
        high_tension = rules.coalition_terms(s, "D", "A", "A")["required_value"]
        self.assertGreater(high_tension, high_capital)

    def test_election_honor_and_player_breach_are_real_and_replayable(self):
        for honor in (True, False):
            s = deepcopy(self.state)
            s["round"] = 3
            for p in s["pacts"]:
                p["expires_after_round"] = 4
            plans = idle_plans(s, offers=True, round_no=4)
            offer = plans["D"]["player_offer"]
            nominee = offer["candidate"]
            ballot = nominee if honor else next(h for h in rules.HOUSES if h != nominee)
            cmd = command(s, candidate=ballot, offer_reply={"offer_id": offer["offer_id"], "response": "accept"})
            with patch.object(rules, "npc_plans", return_value=plans):
                result, out = rules.execute_round(s, self.private, cmd)
                self.assertEqual((result, out), rules.execute_round(s, self.private, cmd))
            self.assertEqual(out["election_votes"]["D"]["candidate"], nominee)
            self.assertTrue(out["election_votes"]["D"]["contract"])
            self.assertTrue(out["election_votes"]["A"]["contract"])
            self.assertEqual(sum(e["code"] == "player_broke_coalition" for e in out["events"]), int(not honor))
            self.assertEqual(sum(e["code"] == "contract_honored" and e["voter"] in ("A", "D") for e in out["events"]), 2 if honor else 1)
            self.assertFalse(result["contracts"])

    def test_signed_offer_cannot_be_overwritten_and_betrayal_cancels_it(self):
        docket, offer = self.invitation()
        out = {"events": []}
        rules.apply_offer_reply(self.state, docket,
                                {"offer_id": offer["offer_id"], "response": "accept"}, out)
        before = deepcopy(self.state["contracts"])
        self.assertIsNone(rules.player_offer(self.state, SEED, 2, "D"))
        self.assertFalse(rules.sign_contract(self.state, "D", "F", 1, out, "npc_coalition", partner="F"))
        self.assertEqual(self.state["contracts"], before)
        rules.do_diplomacy(self.state, 2, {"type": "betray", "target": "D"}, out)
        self.assertFalse(self.state["contracts"])
        self.assertEqual(sum(e["code"] == "contract_expired" for e in out["events"]), 2)


class TraceableDialogue(unittest.TestCase):
    def test_npc_pact_and_coalition_never_claim_player_as_actor(self):
        state = rules.opening_state(SEED)
        state["pacts"] = []
        rules.relationship(state, "B", "E").update(trust=5, capital=10)
        out = {"round": 5, "events": []}
        self.assertTrue(rules.form_pact(state, "B", "E", 5, out, "B"))
        rules.npc_court(state, 5, "B", {"court": {"type": "coalition", "target": "E", "candidate": "B"}}, out)
        lines = [rules.event_dialogue(e) for e in out["events"]]
        for line, event in zip(lines, out["events"]):
            self.assertEqual(line["actor"], event["actor"])
            self.assertEqual(line["speaker"], rules.REPRESENTATIVES[event["actor"]])
            self.assertEqual(line["event_id"], event["event_id"])
            self.assertNotEqual(line["actor"], "A")
        self.assertEqual(len({e["event_id"] for e in out["events"]}), len(out["events"]))

    def test_talk_is_public_only_pure_and_nonbinding(self):
        state = rules.opening_state(SEED)
        state["goals"] = {"D": "THIS-IS-A-SECRET-SENTINEL"}
        state["secret"] = "THIS-IS-A-SECRET-SENTINEL"
        before = deepcopy(state)
        result = rules.talk(state, "D", "盟约与信任，还有选票和秘密目标？")
        self.assertEqual(state, before)
        self.assertFalse(result["binding"])
        self.assertFalse(result["advances_round"])
        self.assertIn("第3轮", result["text"])
        self.assertIn("尚未", result["text"])
        self.assertNotIn("THIS-IS-A-SECRET-SENTINEL", json.dumps(result))
        self.assertEqual(result, rules.talk(state, "D", result["query"]))

    def test_talk_tracks_real_contract_and_world_event(self):
        state = rules.opening_state(SEED)
        rules.sign_contract(state, "D", "A", 1, {"events": []}, "player_offer")
        state["last_outcome"] = {"motion": "relief", "event": "harvest"}
        reply = rules.talk(state, "D", "支持选票、议案和世界事件")
        self.assertIn("第4轮支持A", reply["text"])
        self.assertIn("赈济", reply["text"])
        self.assertIn("丰收", reply["text"])
        self.assertNotEqual(rules.talk(state, "B", "你好")["text"],
                            rules.talk(state, "C", "你好")["text"])

    def test_narration_reports_actual_capped_effects(self):
        s = rules.opening_state(SEED)
        rules.relationship(s, "A", "D")["capital"] = 10
        out = {"events": []}
        rules.joint_project(s, "A", "D", out)
        self.assertEqual(out["events"][-1]["capital_gained"], 0)
        self.assertIn("增加0", rules.event_dialogue(out["events"][-1])["text"])
        s["houses"]["A"].update(intel=3, scandal=9)
        rules.do_sabotage(s, "A", "D", out)
        self.assertEqual(out["events"][-1]["scandal_gained"], 1)
        self.assertIn("丑闻增加1", rules.event_dialogue(out["events"][-1])["text"])

    def test_twenty_round_story_traces_to_same_seed_events(self):
        state = rules.opening_state(SEED)
        private = {"seed": SEED, "goals": rules.secret_goals(SEED)}
        all_ids = set()
        for _ in range(20):
            self.assertEqual(state["status"], "ongoing")
            docket = rules.make_docket(state, SEED)
            a = state["houses"]["A"]
            action = ("tax" if a["gold"] < 5 else
                      "reform" if a["prestige"] <= 7 or state["stability"] <= 8 else
                      "campaign" if a["influence"] < 9 else "reform")
            cmd = command(state, action={"type": action},
                          vote="no" if docket["motion"] in ("conscription", "fortify") else "yes")
            if docket["election"]:
                cmd["bid"] = min(3, a["influence"])
            next_state, out = rules.execute_round(state, private, cmd)
            self.assertEqual((next_state, out), rules.execute_round(state, private, cmd))
            events = {e["event_id"]: e for e in out["events"]}
            self.assertFalse(set(events) & all_ids)
            all_ids.update(events)
            for line in out["scene"]["exchange"]:
                self.assertEqual(line["actor"], events[line["event_id"]]["actor"])
                self.assertEqual(line["event_code"], events[line["event_id"]]["code"])
            state = next_state
        self.assertEqual(state["round"], 20)
        self.assertNotEqual(state["status"], "ongoing")

    def test_talk_rejects_missing_or_unbounded_input(self):
        state = rules.opening_state(SEED)
        for target, text in (("A", "hi"), (None, "hi"), ("B", ""), ("B", "x" * 1001), ("B", 3)):
            with self.subTest(target=target, text=str(text)[:10]), self.assertRaises(ValueError):
                rules.talk(state, target, text)

    def test_live_scores_have_no_secret_goal_bonus_and_story_is_stable(self):
        state = rules.opening_state(SEED)
        private = {"seed": SEED, "goals": rules.secret_goals(SEED)}
        plans = idle_plans()
        with patch.object(rules, "npc_plans", return_value=plans):
            after, out = rules.execute_round(state, private, command(state))
            self.assertEqual((after, out), rules.execute_round(state, private, command(state)))
        self.assertEqual(after["status"], "ongoing")
        self.assertEqual(after["scores"], rules.standings(after, {h: "unrevealed" for h in rules.HOUSES}))
        ids = {e["event_id"]: e for e in out["events"]}
        for line in out["scene"]["exchange"]:
            self.assertEqual(line["actor"], ids[line["event_id"]]["actor"])
            self.assertEqual(line["event_code"], ids[line["event_id"]]["code"])
        encoded = json.dumps(out["scene"], ensure_ascii=False)
        for goal in rules.GOALS:
            self.assertNotIn(goal, encoded)
        self.assertNotIn(SEED, encoded)


if __name__ == "__main__":
    unittest.main()
