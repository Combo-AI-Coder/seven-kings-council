"""Synthetic archive corruption, transaction failure and concurrent writer tests."""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from seven_kings import engine, rules

SEED = "synthetic-storage-tests-never-a-private-campaign"


def _lock_process(root, ready, release):
    with engine._locked(root):
        ready.set()
        release.wait(15)


def _crash_process(root, command):
    original = engine._put_bytes

    def crash(path, data):
        original(path, data)
        if Path(path) == Path(root) / "state.json":
            os._exit(17)

    with patch.object(engine, "_put_bytes", side_effect=crash):
        engine.play(root, command)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "campaign"
        engine.init(self.root, seed=SEED, game_id="SYNTHETIC-STORAGE")

    def tearDown(self):
        self.tmp.cleanup()

    def play(self, root=None):
        root = root or self.root
        # A cheap defensive command avoids a premature low-stability loss and
        # works for the opening rounds used throughout these fixtures.
        command = engine.template(root)
        state = engine._read_json(root / "state.json")
        command["action"] = {"type": "reform" if state["houses"]["A"]["gold"] >= 2 else "tax"}
        return engine.play(root, command)

    def snapshot(self, root=None):
        root = root or self.root
        return {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}

    def test_each_artifact_tamper_delete_swap_and_resigned_manifest(self):
        for _ in range(3):
            self.play()
        saved = self.snapshot()
        names = ["%s_%02d.json" % (prefix, r)
                 for prefix in ("command", "result", "turn", "docket")
                 for r in (1, 2, 3)] + ["docket_04.json"]
        for name in names:
            for damage in ("tamper", "delete", "swap", "resign"):
                if damage == "swap" and name.startswith("command"):
                    # Identical normalized commands are valid equal snapshots;
                    # swap with a structurally different round artifact instead.
                    replacement = "result_01.json"
                else:
                    replacement = (name[:-7] + "02.json" if name.endswith("01.json")
                                   else name[:-7] + "01.json")
                with self.subTest(file=name, damage=damage):
                    path = self.root / name
                    if damage == "delete":
                        path.unlink()
                    elif damage == "swap":
                        path.write_bytes(saved[replacement])
                    else:
                        value = json.loads(saved[name])
                        value["synthetic_corruption"] = True
                        engine._put_json(path, value)
                        if damage == "resign":
                            engine._write_manifest(self.root)
                    with self.assertRaises(ValueError):
                        engine.verify(self.root)
                    for stored, raw in saved.items():
                        (self.root / stored).write_bytes(raw)
        self.assertTrue(engine.verify(self.root)["verified"])

    def test_manifest_inventory_and_byte_hashes(self):
        self.play()
        original = self.snapshot()
        self.root.joinpath("command_99.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "inventory"):
            engine.verify(self.root)
        self.root.joinpath("command_99.json").unlink()
        self.root.joinpath("manifest.json").unlink()
        with self.assertRaisesRegex(ValueError, "manifest"):
            engine.verify(self.root)
        self.root.joinpath("manifest.json").write_bytes(original["manifest.json"])
        manifest = engine._read_json(self.root / "manifest.json")
        del manifest["files"]["command_01.json"]
        engine._put_json(self.root / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "manifest"):
            engine.verify(self.root)
        self.root.joinpath("manifest.json").write_bytes(original["manifest.json"])
        # Semantic equality does not override the manifest's exact-byte policy.
        self.root.joinpath("command_01.json").write_bytes(original["command_01.json"] + b"\n")
        with self.assertRaisesRegex(ValueError, "manifest"):
            engine.verify(self.root)

    def test_external_anchor_detects_replacement_and_old_revision(self):
        anchor = engine.verify(self.root)["archive_sha256"]
        self.assertTrue(engine.verify(self.root, expected_archive_sha256=anchor)["verified"])
        self.play()
        with self.assertRaisesRegex(ValueError, "External archive"):
            engine.verify(self.root, expected_archive_sha256=anchor)
        with self.assertRaisesRegex(ValueError, "External archive"):
            engine.verify(self.root, expected_archive_sha256="0" * 64)

    def test_frozen_source_hash_never_bypassed(self):
        original = self.snapshot()
        with patch.object(engine, "_source_sha", return_value="0" * 64):
            for call in (engine.verify, engine.export_commands, engine.recover):
                with self.assertRaisesRegex(ValueError, "Frozen engine SHA"):
                    call(self.root)
        self.assertEqual(original, self.snapshot())

    def test_legacy_is_explicit_not_in_place_migration(self):
        original = self.snapshot()
        seal = engine._read_json(self.root / "genesis.json")
        del seal["archive_format"]
        seal["genesis_commit"] = rules.digest({k: v for k, v in seal.items() if k != "genesis_commit"})
        engine._put_json(self.root / "genesis.json", seal)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Legacy archive"):
            engine.verify(self.root)
        self.assertEqual(before, self.snapshot())
        self.assertNotEqual(original["genesis.json"], before["genesis.json"])

    def test_idempotent_retry_and_stale_different_command(self):
        command = engine.template(self.root)
        first = engine.play(self.root, command)
        saved = self.snapshot()
        same = engine.play(self.root, deepcopy(command))
        self.assertTrue(same["already_committed"])
        self.assertEqual(first["round_outcome"], same["round_outcome"])
        self.assertEqual(saved, self.snapshot())
        other = deepcopy(command)
        other["vote"] = "yes"
        with self.assertRaisesRegex(ValueError, "Stale audit tip"):
            engine.play(self.root, other)
        self.assertEqual(saved, self.snapshot())
        self.play()
        # Older successful templates remain idempotent after later turns too.
        self.assertEqual(engine.play(self.root, command)["round_outcome"]["round"], 1)
        self.assertEqual(engine.verify(self.root)["round"], 2)

    def test_fail_before_and_after_every_transaction_write(self):
        observed = []
        original = engine._put_bytes

        def record(path, data):
            observed.append(str(path))
            return original(path, data)

        with patch.object(engine, "_put_bytes", side_effect=record):
            self.play()
        for after in (False, True):
            for fail_at in range(1, len(observed) + 1):
                with self.subTest(after=after, write=fail_at):
                    root = Path(self.tmp.name) / ("fault-%s-%d" % (after, fail_at))
                    engine.init(root, seed=SEED)
                    command = engine.template(root)
                    old = self.snapshot(root)
                    calls = 0

                    def fail(path, data):
                        nonlocal calls
                        calls += 1
                        if calls == fail_at and not after:
                            raise OSError("synthetic injected write failure")
                        result = original(path, data)
                        if calls == fail_at and after:
                            raise OSError("synthetic injected write failure")
                        return result

                    with patch.object(engine, "_put_bytes", side_effect=fail):
                        with self.assertRaisesRegex(OSError, "synthetic"):
                            engine.play(root, command)
                    restored = engine.recover(root)
                    check = restored["verification"]
                    self.assertIn(check["round"], (0, 1))
                    if check["round"] == 0:
                        self.assertEqual(old, self.snapshot(root))
                    result = engine.play(root, command)
                    self.assertEqual(result["round_outcome"]["round"], 1)
                    self.assertEqual(engine.verify(root)["round"], 1)
                    self.assertEqual(engine.verify(root)["audit_records"], 2)
                    self.assertFalse((root / engine.TRANSACTION).exists())

    def test_fail_before_and_after_every_atomic_rename(self):
        observed = []
        original = os.replace

        def record(source, destination):
            observed.append((str(source), str(destination)))
            return original(source, destination)

        with patch.object(os, "replace", side_effect=record):
            self.play()
        for after in (False, True):
            for fail_at in range(1, len(observed) + 1):
                with self.subTest(after=after, rename=fail_at):
                    root = Path(self.tmp.name) / ("atomic-%s-%d" % (after, fail_at))
                    engine.init(root, seed=SEED)
                    command = engine.template(root)
                    calls = 0

                    def fail(source, destination):
                        nonlocal calls
                        calls += 1
                        if calls == fail_at and not after:
                            raise OSError("synthetic atomic rename failure")
                        result = original(source, destination)
                        if calls == fail_at and after:
                            raise OSError("synthetic atomic rename failure")
                        return result

                    with patch.object(os, "replace", side_effect=fail):
                        with self.assertRaisesRegex(OSError, "synthetic"):
                            engine.play(root, command)
                    self.assertIn(engine.recover(root)["verification"]["round"], (0, 1))
                    self.assertEqual(engine.play(root, command)["round_outcome"]["round"], 1)
                    self.assertEqual(engine.verify(root)["audit_records"], 2)
                    self.assertFalse(list(root.glob("*.tmp")))

    def test_fail_before_and_after_commit_cleanup_rename(self):
        original = os.replace
        for after in (False, True):
            root = Path(self.tmp.name) / ("rename-%s" % after)
            engine.init(root, seed=SEED)
            command = engine.template(root)

            def fail(source, destination):
                is_cleanup = Path(source).name == engine.TRANSACTION
                if is_cleanup and not after:
                    raise OSError("synthetic cleanup boundary")
                result = original(source, destination)
                if is_cleanup and after:
                    raise OSError("synthetic cleanup boundary")
                return result

            with patch.object(os, "replace", side_effect=fail):
                with self.assertRaisesRegex(OSError, "cleanup boundary"):
                    engine.play(root, command)
            engine.recover(root)
            self.assertTrue(engine.play(root, command)["already_committed"])
            self.assertEqual(engine.verify(root)["round"], 1)

    def test_naked_retry_cannot_advance_during_automatic_recovery(self):
        command = engine.template(self.root)
        del command["expected_audit_tip"]
        original = os.replace

        def fail(source, destination):
            if Path(source).name == engine.TRANSACTION:
                raise OSError("synthetic full publication interruption")
            return original(source, destination)

        with patch.object(os, "replace", side_effect=fail):
            with self.assertRaises(OSError):
                engine.play(self.root, command)
        result = engine.play(self.root, command)
        self.assertTrue(result["already_committed"])
        self.assertEqual(engine.verify(self.root)["round"], 1)

    def test_corrupt_pending_transaction_is_never_silently_repaired(self):
        command = engine.template(self.root)
        original = engine._put_bytes

        def fail(path, data):
            if Path(path) == self.root / "state.json":
                raise OSError("synthetic partial commit")
            return original(path, data)

        with patch.object(engine, "_put_bytes", side_effect=fail):
            with self.assertRaises(OSError):
                engine.play(self.root, command)
        with self.assertRaisesRegex(ValueError, "Pending transaction"):
            engine.verify(self.root)
        (self.root / "initial_state.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "outside transaction"):
            engine.recover(self.root)
        self.assertEqual((self.root / "initial_state.json").read_text(), "{}")

    def test_readers_and_same_different_writers_are_excluded(self):
        for same in (False, True):
            root = Path(self.tmp.name) / ("threads-%s" % same)
            engine.init(root, seed=SEED)
            command = engine.template(root)
            entered, release = threading.Event(), threading.Event()
            original = engine._commit
            failures, results = [], []

            def paused(*args):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test release timed out")
                return original(*args)

            def first_writer():
                try:
                    results.append(engine.play(root, command))
                except BaseException as exc:
                    failures.append(exc)

            with patch.object(engine, "_commit", side_effect=paused):
                thread = threading.Thread(target=first_writer)
                thread.start()
                self.assertTrue(entered.wait(10))
                try:
                    second = deepcopy(command)
                    if not same:
                        second["vote"] = "yes"
                    for reader in (engine.verify, engine.docket, engine.template,
                                   engine.recover, lambda p: engine.talk(p, "B", "hello")):
                        with self.assertRaises(engine.CampaignBusyError):
                            reader(root)
                    with self.assertRaises(engine.CampaignBusyError):
                        engine.play(root, second)
                    naked = {k: v for k, v in second.items() if k != "expected_audit_tip"}
                    with self.assertRaises(engine.CampaignBusyError):
                        engine.play(root, naked)
                finally:
                    release.set()
                    thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(engine.verify(root)["round"], 1)
            if same:
                self.assertTrue(engine.play(root, second)["already_committed"])
            else:
                with self.assertRaisesRegex(ValueError, "Stale audit tip"):
                    engine.play(root, second)

    def test_process_lock_and_abrupt_process_crash(self):
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        owner = context.Process(target=_lock_process, args=(str(self.root), ready, release))
        owner.start()
        try:
            self.assertTrue(ready.wait(15))
            with self.assertRaises(engine.CampaignBusyError):
                engine.verify(self.root)
        finally:
            release.set()
            owner.join(15)
            if owner.is_alive():
                owner.terminate()
                owner.join()
        self.assertEqual(owner.exitcode, 0)
        command = engine.template(self.root)
        process = context.Process(target=_crash_process, args=(str(self.root), command))
        process.start()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join()
        self.assertEqual(process.exitcode, 17)
        self.assertEqual(engine.recover(self.root)["verification"]["round"], 1)
        self.assertTrue(engine.play(self.root, command)["already_committed"])
        self.assertEqual(engine.verify(self.root)["audit_records"], 2)

    def test_template_and_talk_are_read_only_cli_entry_points(self):
        command = engine.template(self.root)
        saved = self.snapshot()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(engine.main(["template", "--dir", str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue()), command)
        self.assertEqual(saved, self.snapshot())
        with patch.object(rules, "talk", return_value={"synthetic": True}) as talk:
            self.assertEqual(engine.talk(self.root, "B", "议案如何？"), {"synthetic": True})
            talk.assert_called_once_with(engine._read_json(self.root / "state.json"), "B", "议案如何？")
        self.assertEqual(saved, self.snapshot())
        self.assertEqual(engine.play(self.root, command)["verification"]["round"], 1)
        for _ in range(2):
            self.play()
        self.assertIn("candidate", engine.template(self.root))

    def test_checked_command_export_replays_only_into_new_directory(self):
        for _ in range(3):
            self.play()
        before = self.snapshot()
        exported = engine.export_commands(self.root)
        self.assertNotIn(SEED, json.dumps(exported))
        self.assertNotIn("goals", json.dumps(exported))
        new = Path(self.tmp.name) / "imported"
        result = engine.replay(new, exported, seed=SEED, game_id="SYNTHETIC-STORAGE")
        self.assertEqual(result["verification"]["round"], 3)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(before, self.snapshot(new))
        with self.assertRaisesRegex(ValueError, "NEW directory"):
            engine.replay(self.root, exported, seed=SEED)
        invalid = deepcopy(exported)
        invalid["commands"][0]["action"]["type"] = "illegal-old-action"
        rejected = Path(self.tmp.name) / "rejected"
        with self.assertRaises(ValueError):
            engine.replay(rejected, invalid, seed=SEED)
        self.assertFalse(rejected.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlinks unavailable")
    def test_archive_symlinks_rejected(self):
        source = self.root / "state.json"
        original = source.read_bytes()
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_bytes(original)
        source.unlink()
        source.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symbolic"):
            engine.verify(self.root)


if __name__ == "__main__":
    unittest.main()
