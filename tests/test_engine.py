"""Synthetic, non-secret end-to-end tests for HELL-1.2."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from seven_kings import rules
from seven_kings import engine


SEED = "synthetic-seed-for-public-unit-tests-only-001"


def command(state, seed, *, action=None, court=None, vote="no", bid=None):
    docket = rules.make_docket(state, seed)
    if action is None:
        a = state["houses"]["A"]
        action = "reform" if a["gold"] >= 4 and (
            a["prestige"] <= 5 or state["stability"] <= 9) else "tax"
    choice = {
        "action": {"type": action}, "diplomacy": {"type": "none"},
        "court": court or {"type": "none"}, "reply": "decline",
        "vote": vote, "sway": 0, "bid": 0,
    }
    if docket["election"]:
        choice["candidate"] = "A"
        choice["bid"] = min(3, state["houses"]["A"]["influence"]) if bid is None else bid
    return choice


class DeterminismAndAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "campaign"
        engine.init(self.root, seed=SEED, game_id="SYNTHETIC")

    def tearDown(self):
        self.tmp.cleanup()

    def test_initial_audit_and_sealed_reveal(self):
        check = engine.verify(self.root)
        self.assertEqual(check["round"], 0)
        self.assertEqual(check["audit_records"], 1)
        with self.assertRaisesRegex(ValueError, "before game end"):
            engine.reveal(self.root)
        docket = engine.docket(self.root)
        self.assertIn("ai_plan_commit", docket)
        text = (self.root / "docket_01.json").read_text()
        self.assertNotIn(SEED, text)
        self.assertNotIn("goals", text)
        self.assertNotIn("ai_plans", text)

    def test_repeatable_genesis_and_plan(self):
        other = Path(self.tmp.name) / "repeat"
        engine.init(other, seed=SEED, game_id="SYNTHETIC")
        self.assertEqual((self.root / "genesis.json").read_bytes(),
                         (other / "genesis.json").read_bytes())
        self.assertEqual(engine.docket(self.root), engine.docket(other))
        self.assertEqual(engine.verify(self.root)["audit_tip"],
                         engine.verify(other)["audit_tip"])

    def test_one_round_and_independent_replay(self):
        before = engine.docket(self.root)["ai_plan_commit"]
        current = engine._read_json(self.root / "state.json")
        c = command(current, SEED)
        result = engine.play(self.root, c)
        self.assertEqual(result["round_outcome"]["ai_plan_commit"], before)
        self.assertEqual(engine.verify(self.root)["round"], 1)
        self.assertEqual(engine.verify(self.root)["audit_records"], 2)
        self.assertEqual(engine._read_json(self.root / "turn_01.json"),
                         engine._read_json(self.root / "state.json"))
        self.assertEqual(result["verification"]["status"], "ongoing")

    def test_invalid_command_never_changes_files(self):
        snapshot = {n: (self.root / n).read_bytes()
                    for n in ("state.json", "audit.jsonl", "genesis.json")}
        bad = command(engine._read_json(self.root / "state.json"), SEED)
        bad["court"] = {"type": "request_backing", "target": "B",
                        "offer_gold": 1, "offer_influence": 1}
        with self.assertRaisesRegex(ValueError, "allied"):
            engine.play(self.root, bad)
        for n, value in snapshot.items():
            self.assertEqual((self.root / n).read_bytes(), value)
        self.assertFalse((self.root / "turn_01.json").exists())

    def test_tampered_state_is_detected(self):
        original = (self.root / "state.json").read_bytes()
        wrong = engine._read_json(self.root / "state.json")
        wrong["houses"]["A"]["gold"] += 1
        engine._put_json(self.root / "state.json", wrong)
        with self.assertRaisesRegex(ValueError, "Latest state differs"):
            engine.verify(self.root)
        (self.root / "state.json").write_bytes(original)
        self.assertTrue(engine.verify(self.root)["verified"])

    def test_tampered_audit_is_detected(self):
        raw = (self.root / "audit.jsonl").read_text()
        wrong = raw.replace('"round": 0', '"round": 2', 1)
        (self.root / "audit.jsonl").write_text(wrong)
        with self.assertRaisesRegex(ValueError, "Audit chain"):
            engine.verify(self.root)
        (self.root / "audit.jsonl").write_text(raw)

    def test_identical_command_gives_identical_states(self):
        other = Path(self.tmp.name) / "repeat"
        engine.init(other, seed=SEED, game_id="SYNTHETIC")
        c = command(engine._read_json(self.root / "state.json"), SEED)
        engine.play(self.root, c)
        engine.play(other, c)
        for path in ("state.json", "audit.jsonl",
                     "result_01.json", "turn_01.json"):
            self.assertEqual((self.root / path).read_bytes(),
                             (other / path).read_bytes(), path)

    def test_complete_game_with_incremental_verification(self):
        for _ in range(20):
            s = engine._read_json(self.root / "state.json")
            if s["status"] != "ongoing":
                break
            engine.play(self.root, command(s, SEED))
            self.assertTrue(engine.verify(self.root)["verified"])
        state = engine._read_json(self.root / "state.json")
        self.assertNotEqual(state["status"], "ongoing")
        self.assertEqual(state["round"], engine.verify(self.root)["audit_records"] - 1)
        self.assertEqual(engine.reveal(self.root)["final_status"], state["status"])
        self.assertIn("hidden_goals", engine.reveal(self.root))

    def test_long_simulation_multiple_synthetic_seeds(self):
        for i in range(6):
            state = rules.opening_state("synthetic-%02d" % i)
            private = {"seed": "synthetic-%02d" % i,
                       "goals": rules.secret_goals("synthetic-%02d" % i)}
            for _ in range(20):
                if state["status"] != "ongoing":
                    break
                previous = deepcopy(state)
                c = command(state, private["seed"])
                new_state, out = rules.execute_round(state, private, c)
                self.assertEqual(state, previous, "Pure transition mutated its input")
                self.assertEqual(out["round"], state["round"] + 1)
                self.assertEqual(new_state["round"], out["round"])
                self.assertEqual(
                    out["ai_plan_commit"],
                    rules.make_docket(state, private["seed"])["ai_plan_commit"])
                state = new_state
            self.assertLessEqual(state["round"], 20)


class CooperativePolitics(unittest.TestCase):
    def setUp(self):
        self.state = rules.opening_state(SEED)
        self.private = {"seed": SEED, "goals": rules.secret_goals(SEED)}

    def election(self, state, *, candidate="A"):
        """Run a real election without unrelated NPC actions replacing promises."""
        state["round"] = 3
        for p in state["pacts"]:
            p["expires_after_round"] = 4
        plans = {
            h: {"action": "pass", "diplomacy_priorities": [],
                "court": {"type": "none"}}
            for h in rules.NPCS
        }
        with patch.object(rules, "npc_plans", return_value=plans):
            c = command(state, SEED, action="pass", bid=0)
            c["candidate"] = candidate
            return rules.execute_round(state, self.private, c)

    def third_party_coalition(self, *, tension=0):
        s = self.state
        rules.relationship(s, "B", "E").update(
            trust=5, capital=10, tension=tension)
        for h in ("B", "E"):
            # E scores D at 35 versus B at 46 (43 with tension 1).
            # The gap must be within capital + 2 before a coalition can form.
            rules.relationship(s, h, "D").update(trust=5, capital=9)
        out = {"events": []}
        rules.npc_court(s, 4, "B",
                        {"court": {"type": "coalition", "target": "E",
                                   "candidate": "D"}}, out)
        self.assertTrue(any(e["code"] == "npc_coalition" for e in out["events"]))
        return s

    def test_beyond_trust_cap_cooperation_accumulates_capital(self):
        s = self.state
        rel = rules.relationship(s, "A", "D")
        rel["trust"] = 5
        rel["capital"] = 1
        out = {"events": []}
        self.assertTrue(rules.joint_project(s, "A", "D", out))
        self.assertEqual(rel["trust"], 5)
        self.assertEqual(rel["capital"], 3)
        self.assertTrue(rules.joint_project(s, "A", "D", out))
        self.assertEqual(rel["capital"], 5)

    def test_cooperation_can_change_uncontracted_election_preference(self):
        s = self.state
        rules.relationship(s, "D", "A")["trust"] = 5
        rules.relationship(s, "D", "F")["trust"] = 5
        s["houses"]["A"]["crowns"] = 2
        s["houses"]["A"]["prestige"] = 6
        s["houses"]["F"]["prestige"] = 8
        self.assertGreater(rules.candidate_score(s, "D", "F"),
                           rules.candidate_score(s, "D", "A"))
        rules.relationship(s, "A", "D")["capital"] = 4
        self.assertGreater(rules.candidate_score(s, "D", "A"),
                           rules.candidate_score(s, "D", "F"))
        self.assertEqual(rules.favorite(s, "D"), "A")

    def test_capital_reduces_contract_price_tension_increases_it(self):
        s = self.state
        before = rules.backing_threshold(s, "D", "A")
        r = rules.relationship(s, "D", "A")
        r["capital"] = 4
        improved = rules.backing_threshold(s, "D", "A")
        self.assertLess(improved, before)
        r["tension"] = 2
        self.assertGreater(rules.backing_threshold(s, "D", "A"), improved)

    def test_first_refusal_is_not_betrayal_second_has_consequence(self):
        s = self.state
        s["houses"]["A"]["crowns"] = 2
        s["houses"]["F"]["prestige"] = 15
        rel = rules.relationship(s, "A", "D")
        out = {"events": [], "forced_council": {}}
        offer = {"type": "request_backing", "target": "D", "offer_gold": 1,
                 "offer_influence": 0, "use_favor": False}
        rules.player_court(s, 1, offer, "no", out)
        self.assertEqual(rel["refusals"], 1)
        self.assertEqual(rel["tension"], 0)
        self.assertEqual(s["houses"]["A"]["scandal"], 0)
        rules.player_court(s, 1, offer, "no", out)
        self.assertEqual(rel["refusals"], 2)
        self.assertEqual(rel["tension"], 1)

    def test_no_pact_does_not_grant_backing(self):
        s = self.state
        s["contracts"].append({
            "voter": "B", "candidate": "A", "election_round": 4,
            "origin": "fictional"})
        self.assertIsNone(rules.promised(s, "B", 4))

    def test_nonplayer_joint_project_is_a_real_action(self):
        s = self.state
        first = rules.relationship(s, "B", "E")["capital"]
        out = {"events": []}
        self.assertTrue(rules.joint_project(s, "B", "E", out))
        self.assertEqual(rules.relationship(s, "B", "E")["capital"], first + 2)
        self.assertEqual(out["events"][-1]["code"], "joint_project")

    def test_npc_coalition_is_binding_for_two_signers(self):
        for actor, target in (("B", "E"), ("E", "B")):
            with self.subTest(actor=actor):
                s = rules.opening_state(SEED)
                rules.relationship(s, actor, target).update(trust=5, capital=8)
                out = {"events": []}
                plan = {"court": {"type": "coalition", "target": target,
                                  "candidate": actor}}
                rules.npc_court(s, 4, actor, plan, out)
                self.assertTrue(any(e["code"] == "npc_coalition"
                                    for e in out["events"]))
                self.assertEqual(rules.promised(s, actor, 4), actor)
                self.assertEqual(rules.promised(s, target, 4), actor)
                _, result = self.election(s)
                for voter in (actor, target):
                    self.assertEqual(result["election_votes"][voter]["candidate"], actor)
                    self.assertTrue(result["election_votes"][voter]["contract"])

    def test_npc_coalition_can_nominate_receiver(self):
        s = self.state
        s["houses"]["B"]["scandal"] = 10
        s["houses"]["E"]["prestige"] = 16
        out = {"events": []}
        rules.npc_court(s, 4, "B",
                        {"court": {"type": "coalition", "target": "E",
                                   "candidate": "E"}}, out)
        self.assertEqual(rules.promised(s, "B", 4), "E")
        self.assertEqual(rules.promised(s, "E", 4), "E")

    def test_coalition_honor_reward_is_not_doubled(self):
        s = self.state
        rules.npc_court(s, 4, "B",
                        {"court": {"type": "coalition", "target": "E",
                                   "candidate": "B"}}, {"events": []})
        self.assertEqual(len(s["contracts"]), 2)
        self.assertEqual(rules.relationship(s, "B", "E")["capital"], 2)
        result, out = self.election(s)
        council_alignment = int(out["council_votes"]["B"]["vote"] ==
                                out["council_votes"]["E"]["vote"])
        # Formation + council alignment + one honor bonus + election alignment.
        self.assertEqual(rules.relationship(result, "B", "E")["capital"],
                         2 + council_alignment + 2 + 1)

    def test_coalition_to_third_party_keeps_both_voters_bound(self):
        s = self.third_party_coalition()
        self.assertFalse(rules.pact(s, "B", "D"))
        self.assertFalse(rules.pact(s, "E", "D"))
        self.assertEqual(rules.promised(s, "B", 4), "D")
        self.assertEqual(rules.promised(s, "E", 4), "D")
        self.assertIsNone(rules.promised(s, "D", 4))
        self.assertIsNone(rules.promised(s, "B", 8))

    def test_joint_third_party_vote_is_not_misclassified_as_betrayal(self):
        s = self.third_party_coalition(tension=1)
        self.assertEqual(rules.relationship(s, "B", "E")["tension"], 0)
        result, out = self.election(s)
        for voter in ("B", "E"):
            self.assertEqual(out["election_votes"][voter]["candidate"], "D")
            self.assertTrue(out["election_votes"][voter]["contract"])
        self.assertEqual(rules.relationship(result, "B", "E")["tension"], 0)
        self.assertEqual(result["contracts"], [])

    def test_uncoordinated_allied_votes_still_increase_tension(self):
        s = self.state
        rules.relationship(s, "B", "D").update(trust=5, capital=10)
        rules.relationship(s, "E", "G").update(trust=5, capital=10)
        result, out = self.election(s)
        self.assertEqual(out["election_votes"]["B"]["candidate"], "D")
        self.assertEqual(out["election_votes"]["E"]["candidate"], "G")
        self.assertEqual(rules.relationship(result, "B", "E")["tension"], 1)

    def test_unattractive_third_party_coalition_is_still_rejected(self):
        s = self.state
        rel = rules.relationship(s, "B", "E")
        rel.update(trust=5, capital=10, tension=1)
        gold = {h: s["houses"][h]["gold"] for h in ("B", "E")}
        out = {"events": []}
        rules.npc_court(s, 4, "B",
                        {"court": {"type": "coalition", "target": "E",
                                   "candidate": "D"}}, out)
        self.assertEqual(rel["tension"], 1)
        self.assertEqual(rel["refusals"], 1)
        self.assertEqual(s["contracts"], [])
        self.assertFalse(out["events"])
        self.assertEqual({h: s["houses"][h]["gold"] for h in gold}, gold)

    def test_replacement_coalition_has_one_promise_per_voter(self):
        s = self.third_party_coalition()
        rules.npc_court(s, 4, "B",
                        {"court": {"type": "coalition", "target": "E",
                                   "candidate": "B"}}, {"events": []})
        self.assertEqual(len(s["contracts"]), 2)
        for voter in ("B", "E"):
            self.assertEqual(rules.promised(s, voter, 4), "B")

    def test_coalition_requires_signers_pact_even_if_nominee_is_allied(self):
        s = self.third_party_coalition()
        s["pacts"] = [{"a": h, "b": "D", "expires_after_round": 4}
                      for h in ("B", "E")]
        for voter in ("B", "E"):
            self.assertIsNone(rules.promised(s, voter, 4))
        _, out = self.election(s)
        for voter in ("B", "E"):
            self.assertFalse(out["election_votes"][voter]["contract"])
        self.assertFalse(any(e["code"] == "contract_honored" for e in out["events"]))

    def test_player_coalition_settles_with_signer_not_nominee(self):
        for nominee in ("A", "D", "B"):
            for ballot in (nominee, "G"):
                with self.subTest(nominee=nominee, ballot=ballot):
                    s = rules.opening_state(SEED)
                    s["houses"][nominee]["prestige"] = 16
                    if nominee != "D":
                        rules.relationship(s, "D", nominee).update(trust=5, capital=10)
                    else:
                        s["houses"]["A"]["scandal"] = 5
                        s["houses"]["F"]["scandal"] = 5
                    out = {"events": []}
                    rules.player_court(s, 4, {"type": "coalition", "target": "D",
                                             "candidate": nominee}, "no", out)
                    self.assertTrue(any(e["code"] == "coalition_formed"
                                        for e in out["events"]))
                    for voter in ("A", "D"):
                        self.assertEqual(rules.promised(s, voter, 4), nominee)
                    before = deepcopy(rules.relationship(s, "A", "D"))
                    result, election = self.election(s, candidate=ballot)
                    self.assertEqual(election["election_votes"]["D"]["candidate"], nominee)
                    broken = [e for e in election["events"]
                              if e["code"] == "player_broke_coalition"]
                    self.assertEqual(len(broken), int(ballot != nominee))
                    self.assertEqual(rules.relationship(result, "A", "D")["trust"],
                                     before["trust"] - (2 if ballot != nominee else 0))

    def test_ordinary_backing_still_requires_voter_nominee_pact(self):
        s = self.state
        out = {"events": []}
        self.assertTrue(rules.sign_contract(s, "D", "A", 1, out, "player_offer"))
        self.assertEqual(rules.promised(s, "D", 4), "A")
        del s["contracts"][0]["partner"]  # Legacy ordinary backing stays valid.
        self.assertEqual(rules.promised(s, "D", 4), "A")
        self.assertTrue(rules.sign_contract(s, "B", "E", 1, out, "npc_offer"))
        self.assertFalse(rules.sign_contract(s, "B", "A", 1, out, "player_offer"))
        self.assertEqual(rules.promised(s, "B", 4), "E")
        s["pacts"] = []
        self.assertIsNone(rules.promised(s, "D", 4))

    def test_terminal_pact_expires_after_round_inclusively(self):
        s = self.state
        # A-F is introduced as second ally, with both pacts expiring after 20.
        s["pacts"].append({"a": "A", "b": "F", "expires_after_round": 20})
        for p in s["pacts"]:
            if "A" in (p["a"], p["b"]):
                p["expires_after_round"] = 20
        self.assertTrue(rules.goal_met(s, "A", "player_mission"))
        # Expiring after 19 would have been cleaned at the end of round 19.
        s["pacts"] = [p for p in s["pacts"]
                      if not ("A" in (p["a"], p["b"]) and
                              p["expires_after_round"] <= 19)]
        self.assertTrue(rules.goal_met(s, "A", "player_mission"))


if __name__ == "__main__":
    unittest.main()
