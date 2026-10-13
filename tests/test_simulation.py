"""Fast, reproducible subset of the public balance evaluation."""
from copy import deepcopy
import unittest

from seven_kings import rules, simulation


class BalanceEvaluation(unittest.TestCase):
    def test_all_policies_reproducible_and_bounded(self):
        for policy in simulation.POLICIES:
            with self.subTest(policy=policy):
                seed = simulation.SEED_PREFIX + "0000"
                first = simulation.simulate(seed, policy)
                self.assertEqual(first, simulation.simulate(seed, policy))
                self.assertGreater(first["rounds"], 0)
                self.assertLessEqual(first["rounds"], 20)
                self.assertEqual(first["contracts_honored"], first["contracts_due"])
                self.assertLessEqual(first["max_capital"], 10)
                self.assertLessEqual(first["max_tension"], 8)

    def test_policy_uses_only_public_information_and_is_pure(self):
        seed = simulation.SEED_PREFIX + "0001"
        state = rules.opening_state(seed)
        docket = rules.make_docket(state, seed)
        before = deepcopy((state, docket))
        for policy in simulation.POLICIES:
            command = simulation.choose_command(state, docket, policy)
            self.assertEqual(command, rules.validate_command(state, docket, command))
        self.assertEqual((state, docket), before)

    def test_adversarial_policies_actually_attempt_their_strategy(self):
        projects = simulation.simulate(simulation.SEED_PREFIX + "0000", "project_spam")
        contracts = simulation.simulate(simulation.SEED_PREFIX + "0000", "cheap_contract")
        self.assertGreater(projects["project_attempts"], 0)
        self.assertGreater(contracts["cheap_offer_attempts"], 0)
        negotiated = simulation.simulate(simulation.SEED_PREFIX + "0000", "negotiator")
        self.assertGreater(negotiated["npc_player_offers_accepted"], 0)

    def test_summary_definitions_and_empty_denominators(self):
        row = simulation.simulate(simulation.SEED_PREFIX + "0001", "cautious")
        row.update(contracts_due=0, contracts_honored=0, crown_hhi=None)
        summary = simulation.summarize([row])["cautious"]
        self.assertIsNone(summary["npc_contract_honor_rate"])
        self.assertIsNone(summary["mean_crown_hhi"])
        self.assertEqual(summary["games_without_crowns"], 1)
        self.assertEqual(summary["npc_secret_goal_rate"], row["npc_goals_met"] / 6)


if __name__ == "__main__":
    unittest.main()
