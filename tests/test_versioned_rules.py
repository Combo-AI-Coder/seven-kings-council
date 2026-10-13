"""Executable expectations for documented intentional HELL-1.2 differences."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from seven_kings import rules

SEED = "public-synthetic-compatibility-1.2"


class VersionedRules(unittest.TestCase):
    def setUp(self):
        self.state = rules.opening_state(SEED)
        self.secret = {"seed": SEED, "goals": rules.secret_goals(SEED)}

    def test_retained_main_actions(self):
        cases = {
            "tax": {"gold": 5, "prestige": -1},
            "reform": {"gold": -2, "prestige": 2},
            "campaign": {"gold": -3, "prestige": 1, "influence": 2},
            "lobby": {"gold": -3, "influence": 3},
            "muster": {"gold": -3, "guards": 3},
            "spy": {"gold": -1, "intel": 3},
            "pass": {},
        }
        for action, deltas in cases.items():
            state = deepcopy(self.state)
            before = deepcopy(state["houses"]["A"])
            rules.do_action(state, "A", action, {"events": []})
            self.assertEqual(state["houses"]["A"],
                             {k: v + deltas.get(k, 0) for k, v in before.items()})

    def test_resource_caps_and_public_score_formula(self):
        self.assertEqual(rules.CAPS, {"gold": 30, "guards": 18, "influence": 20,
                                     "prestige": 16, "intel": 12, "scandal": 10, "crowns": 5})
        a = self.state["houses"]["A"]
        expected = a["gold"] + a["guards"] + a["influence"] + 2 * a["prestige"] + a["intel"]
        # A has one starting pact, so its goal bonus is zero.
        self.assertEqual(rules.standings(self.state, self.secret["goals"])["A"], expected)

    def test_each_motion_retained_or_versioned(self):
        expected_types = {"relief", "conscription", "academy", "embargo", "amnesty",
                          "festival", "fortify", "common_tax", "reform_bill", "impeachment"}
        self.assertEqual(set(rules.MOTIONS), expected_types)
        self.assertNotIn("patronage", rules.MOTIONS)
        for motion in expected_types:
            with self.subTest(motion=motion):
                state = deepcopy(self.state)
                state["stability"] = 6
                state["houses"]["B"]["scandal"] = 5
                before = deepcopy(state)
                out = {"events": []}
                rules._apply_motion(state, motion, out)
                self.assertEqual(out["events"][-1]["motion"], motion)
                if motion == "amnesty":
                    self.assertEqual(state["houses"]["B"]["scandal"], 3)
                    self.assertEqual(state["houses"]["A"]["scandal"], 0)
                if motion == "common_tax":
                    richest = rules.select_max(before, "gold")
                    poorest = rules.select_min(before, "gold")[0]
                    self.assertEqual(state["houses"][richest]["gold"], before["houses"][richest]["gold"] - 2)
                    self.assertEqual(state["houses"][poorest]["gold"], before["houses"][poorest]["gold"] + 2)
                    self.assertEqual(state["stability"], 6)
                if motion == "reform_bill":
                    for h in rules.HOUSES:
                        self.assertEqual(state["houses"][h]["gold"], before["houses"][h]["gold"] - 2)
                        self.assertEqual(state["houses"][h]["prestige"], before["houses"][h]["prestige"] + 1)

    def test_diplomacy_versioned_effects(self):
        state = deepcopy(self.state)
        before = rules.trust(state, "A", "B")
        rules.do_diplomacy(state, 1, {"type": "bribe", "target": "B", "amount": 4}, {"events": []})
        self.assertEqual(rules.trust(state, "A", "B"), min(5, before + 2))
        state = deepcopy(self.state)
        state["houses"]["A"]["guards"] = 18
        before_a, before_b = deepcopy(state["houses"]["A"]), deepcopy(state["houses"]["B"])
        rules.do_diplomacy(state, 1, {"type": "threaten", "target": "B"}, {"events": []})
        self.assertEqual(state["houses"]["A"]["influence"], before_a["influence"] + 2)
        self.assertEqual(state["houses"]["A"]["gold"], before_a["gold"])
        self.assertEqual(state["houses"]["B"]["gold"], before_b["gold"])
        self.assertEqual(state["houses"]["A"]["scandal"], before_a["scandal"])
        state = deepcopy(self.state)
        before = deepcopy(state["houses"]["A"])
        rules.do_diplomacy(state, 1, {"type": "betray", "target": "D"}, {"events": []})
        self.assertFalse(rules.pact(state, "A", "D"))
        self.assertEqual(state["houses"]["A"]["gold"], before["gold"])
        self.assertEqual(state["houses"]["A"]["influence"], before["influence"])
        self.assertEqual(state["houses"]["A"]["prestige"], before["prestige"] - 2)
        self.assertEqual(state["houses"]["A"]["scandal"], before["scandal"] + 3)

    def _quiet_round(self, state):
        plans = {h: {"action": "pass", "court": {"type": "none"},
                     "diplomacy_priorities": []} for h in rules.NPCS}
        command = {"action": {"type": "pass"}, "vote": "no", "sway": 0, "bid": 0}
        if (state["round"] + 1) % 4 == 0:
            command["candidate"] = "A"
        with patch.object(rules, "npc_plans", return_value=plans), \
             patch.object(rules, "_apply_motion"), patch.object(rules, "_apply_event"):
            return rules.execute_round(state, self.secret, command)

    def test_upkeep_and_hard_failure_are_versioned(self):
        state = deepcopy(self.state)
        state["houses"]["A"]["gold"] = 0
        state["houses"]["A"]["prestige"] = 1
        before_guards = state["houses"]["A"]["guards"]
        after, _ = self._quiet_round(state)
        self.assertEqual(after["houses"]["A"]["prestige"], 0)
        self.assertEqual(after["houses"]["A"]["guards"], before_guards)
        self.assertEqual(after["status"], "ongoing")  # 1.2 checks zero prestige at final only.
        state["houses"]["A"]["scandal"] = 8
        self.assertEqual(self._quiet_round(state)[0]["status"], "hard_failure")
        state["round"] = 19
        state["houses"]["A"]["scandal"] = 0
        state["houses"]["A"]["prestige"] = 0
        rules._finish(state, self.secret["goals"], {"events": []})
        self.assertEqual(state["status"], "hard_failure")

    def test_final_tie_break_and_perfect_victory_requirements(self):
        state = deepcopy(self.state)
        for house in state["houses"].values():
            house.update(gold=10, guards=10, influence=10, prestige=10, intel=0, scandal=0, crowns=0)
        goals = {h: "unmet" for h in rules.HOUSES}
        self.assertEqual(list(rules.standings(state, goals)), list(rules.HOUSES))
        out = {"events": []}
        rules._finish(state, goals, out)
        self.assertEqual(state["status"], "victory")
        self.assertNotEqual(state["status"], "perfect_victory")

    def test_refusal_saturation_is_explicit(self):
        relation = rules.relationship(self.state, "A", "D")
        for n in range(1, 13):
            rules._record_refusal(self.state, "A", "D")
            self.assertEqual(relation["refusals"], min(8, n))
            self.assertEqual(relation["tension"], min(8, n // 2 if n <= 8 else 4 + n - 8))


if __name__ == "__main__":
    unittest.main()
