"""Disk format, transactional storage and independent replay for HELL-1.2.

Campaigns contain sealed secrets. A manifest detects local damage, not a wholesale
rewrite: retain its archive_sha256 in a separately trusted location to anchor it.
Locks use POSIX flock or Windows msvcrt, are fail-fast, and require a local
filesystem with working advisory locks, atomic rename and fsync. Do not use an
NFS/cloud-sync directory or bypass the engine while a campaign is open.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets as secure_random
import shutil
import sys
import tempfile

from . import rules

SOURCE_FILES = ("engine.py", "rules.py", "__init__.py")
FORMAT = 2
BASE_FILES = {"genesis.json", "secrets.json", "initial_state.json", "state.json",
              "audit.jsonl"}
TRANSACTION = ".transaction"
CLEANUP = ".transaction-cleanup"


class CampaignBusyError(ValueError):
    """Another reader/writer owns the campaign; retry with the same audit tip."""


def _read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Archive files must not be symbolic links: " + path.name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("Missing required archive file: " + path.name) from exc


def _fsync_dir(path):
    # Windows has no portable directory fsync. File fsync and atomic replace are
    # still used there; full power-loss durability is only claimed on POSIX.
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _put_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("Refusing symbolic link in archive write")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _put_json(path, data):
    _put_bytes(path, (json.dumps(data, ensure_ascii=False, sort_keys=True,
                                indent=2) + "\n").encode("utf-8"))


@contextmanager
def _locked(root):
    """Nonblocking process/thread lock; the persistent lock file is not data."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError("Campaign directory does not exist")
    path = root / ".campaign.lock"
    if path.is_symlink():
        raise ValueError("Campaign lock must not be a symbolic link")
    with path.open("a+b") as handle:
        acquired = False
        try:
            if os.name == "posix":
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise CampaignBusyError("Campaign busy; retry the same command and audit tip") from exc
            elif os.name == "nt":
                import msvcrt
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise CampaignBusyError("Campaign busy; retry the same command and audit tip") from exc
            else:
                raise ValueError("No supported campaign locking on this platform")
            acquired = True
            yield
        finally:
            if acquired:
                if os.name == "posix":
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                else:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _source_sha():
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8") + b"\0")
        h.update((root / name).read_bytes())
    return h.hexdigest()


def _audit_entry(index, kind, round_no, payload, prev_hash):
    fields = {"index": index, "kind": kind, "round": round_no,
              "payload": payload, "prev_hash": prev_hash}
    return {**fields, "hash": rules.digest(fields)}


def _audit_path(root):
    return Path(root) / "audit.jsonl"


def _read_audit(root):
    path = _audit_path(root)
    if path.is_symlink():
        raise ValueError("Audit file must not be a symbolic link")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError("Missing required archive file: audit.jsonl") from exc
    return [json.loads(line) for line in lines if line.strip()]


def _put_audit(root, entries):
    text = "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True)
                     for e in entries) + "\n"
    _put_bytes(_audit_path(root), text.encode("utf-8"))


def _file_sha(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or unsafe archive file: " + path.name)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_names(root):
    # Non-engine notes are outside the archive. All reserved names are checked,
    # including malformed/out-of-range names, so orphan turns cannot hide.
    return {p.name for p in Path(root).iterdir()
            if p.name in BASE_FILES | {"manifest.json"} or
            (p.name.startswith(("command_", "result_", "turn_", "docket_"))
             and p.name.endswith(".json"))}


def _manifest(root, names, *, seal, entries, state):
    data = {"format": FORMAT, "genesis_commit": seal["genesis_commit"],
            "round": state["round"], "audit_tip": entries[-1]["hash"],
            "files": {name: _file_sha(Path(root) / name) for name in sorted(names)}}
    return {**data, "archive_sha256": rules.digest(data)}


def _write_manifest(root):
    root = Path(root)
    value = _manifest(root, _archive_names(root) - {"manifest.json"},
                      seal=_read_json(root / "genesis.json"),
                      entries=_read_audit(root), state=_read_json(root / "state.json"))
    _put_json(root / "manifest.json", value)


def _expect_json(root, name, expected, message=None):
    if rules.canonical(_read_json(Path(root) / name)) != rules.canonical(expected):
        raise ValueError(message or ("Archive replay differs: " + name))


def _verify(root, *, expected_archive_sha256=None):
    """Pure verification with the caller holding the campaign lock."""
    root = Path(root)
    if (root / TRANSACTION).exists() or (root / TRANSACTION).is_symlink():
        raise ValueError("Pending transaction; run recover before reading or playing")
    seal = _read_json(root / "genesis.json")
    original_seal = {k: v for k, v in seal.items() if k != "genesis_commit"}
    if rules.digest(original_seal) != seal["genesis_commit"]:
        raise ValueError("Genesis commitment changed")
    if seal["version"] != rules.VERSION:
        raise ValueError("Wrong game version")
    if seal["source_sha256"] != _source_sha():
        raise ValueError("Frozen engine SHA mismatch; use the original engine, never reseal history")
    if seal.get("archive_format") != FORMAT:
        raise ValueError("Legacy archive: verify with its original engine, export commands and replay into a NEW directory")
    private = _read_json(root / "secrets.json")
    if rules.digest(private) != seal["secret_file_sha256"]:
        raise ValueError("Secret file commitment mismatch")
    seed = private["seed"]
    if hashlib.sha256(seed.encode("utf-8")).hexdigest() != seal["seed_commit"]:
        raise ValueError("Seed commitment mismatch")
    if private != {"seed": seed, "goals": rules.secret_goals(seed)}:
        raise ValueError("Secret goals differ from deterministic genesis")
    initial = _read_json(root / "initial_state.json")
    if rules.digest(initial) != seal["initial_state_sha256"]:
        raise ValueError("Initial state hash mismatch")
    if initial != rules.opening_state(seed, game_id=seal["game_id"]):
        raise ValueError("Initial state differs from deterministic genesis")
    entries = _read_audit(root)
    if not entries or entries[0]["kind"] != "genesis":
        raise ValueError("Missing genesis audit")
    expected_names = set(BASE_FILES)
    state = deepcopy(initial)
    tip = "0" * 64
    for index, entry in enumerate(entries):
        data = {k: v for k, v in entry.items() if k != "hash"}
        if (entry["index"] != index or entry["prev_hash"] != tip or
                rules.digest(data) != entry["hash"]):
            raise ValueError("Audit chain tampered at record %d" % index)
        if index == 0:
            expected = _audit_entry(0, "genesis", 0,
                                   {"genesis_commit": seal["genesis_commit"],
                                    "initial_state_sha256": seal["initial_state_sha256"]},
                                   "0" * 64)
            if entry != expected:
                raise ValueError("Audit genesis payload tampered")
        else:
            if entry["kind"] != "turn" or entry["round"] != index:
                raise ValueError("Invalid audit round sequence")
            payload = entry["payload"]
            public = rules.make_docket(state, seed)
            normalized = rules.validate_command(state, public, payload["command"])
            if rules.canonical(normalized) != rules.canonical(payload["command"]):
                raise ValueError("Audit command is not normalized")
            if rules.digest(public) != payload["docket_sha256"]:
                raise ValueError("Public docket hash differs on replay")
            replayed, result = rules.execute_round(state, private, normalized)
            if (rules.digest(replayed) != payload["state_sha256"] or
                    rules.digest(result) != payload["result_sha256"] or
                    public["ai_plan_commit"] != payload["ai_plan_commit"]):
                raise ValueError("Replay differs in round %d" % index)
            for prefix, value in (("docket", public), ("command", normalized),
                                  ("result", result), ("turn", replayed)):
                name = "%s_%02d.json" % (prefix, index)
                _expect_json(root, name, value)
                expected_names.add(name)
            state = replayed
        tip = entry["hash"]
    _expect_json(root, "state.json", state, "Latest state differs from audit replay")
    if state["round"] != len(entries) - 1:
        raise ValueError("Round count differs")
    if state["status"] == "ongoing" and state["round"] < 20:
        name = "docket_%02d.json" % (state["round"] + 1)
        _expect_json(root, name, rules.make_docket(state, seed))
        expected_names.add(name)
    if _archive_names(root) != expected_names | {"manifest.json"}:
        raise ValueError("Archive inventory differs: missing manifest or unexpected round files")
    expected = _manifest(root, expected_names, seal=seal, entries=entries, state=state)
    if _read_json(root / "manifest.json") != expected:
        raise ValueError("Archive manifest differs")
    if expected_archive_sha256 is not None and expected_archive_sha256 != expected["archive_sha256"]:
        raise ValueError("External archive SHA anchor differs")
    return {"verified": True, "version": rules.VERSION, "archive_format": FORMAT,
            "game_id": state["game_id"], "round": state["round"],
            "status": state["status"], "audit_records": len(entries),
            "audit_tip": tip, "state_sha256": rules.digest(state),
            "genesis_commit": seal["genesis_commit"],
            "archive_sha256": expected["archive_sha256"]}


def verify(root, *, expected_archive_sha256=None):
    with _locked(root):
        return _verify(root, expected_archive_sha256=expected_archive_sha256)


def _remove_transaction(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Unsafe transaction directory")
    shutil.rmtree(path)
    _fsync_dir(path.parent)


def _finish_transaction(root):
    """Roll forward only a fully verified, durable committed workspace."""
    txn = root / TRANSACTION
    if txn.is_symlink() or not txn.is_dir():
        raise ValueError("Unsafe transaction directory")
    stage = txn / "staged"
    marker = _read_json(txn / "commit.json")
    marker_data = {k: v for k, v in marker.items() if k != "hash"}
    if (marker.get("format") != 1 or marker.get("hash") != rules.digest(marker_data)
            or stage.is_symlink() or not stage.is_dir()):
        raise ValueError("Invalid transaction commit marker")
    checked = _verify(stage, expected_archive_sha256=marker["after_archive_sha256"])
    after_names = _archive_names(stage)
    before = marker["before_files"]
    if not isinstance(before, dict) or not set(before) <= after_names:
        raise ValueError("Invalid transaction base inventory")
    # Missing or altered old data is corruption, not a recoverable write. Each
    # visible file must be the recorded old or new bytes before anything changes.
    if not _archive_names(root) <= after_names:
        raise ValueError("Unexpected archive files during recovery")
    for name in after_names:
        path = root / name
        if path.exists() or path.is_symlink():
            if _file_sha(path) not in {before.get(name), _file_sha(stage / name)}:
                raise ValueError("Archive changed outside transaction: " + name)
        elif name in before:
            raise ValueError("Archive file removed during transaction: " + name)
    # Manifest is published last, but readers still always require the lock.
    for name in sorted(after_names - {"manifest.json"}) + ["manifest.json"]:
        source = stage / name
        target = root / name
        if not target.exists() or _file_sha(target) != _file_sha(source):
            _put_bytes(target, source.read_bytes())
    # Rename makes cleanup itself crash-safe: a partially removed workspace is
    # never mistaken for a still-pending commit on the next recovery.
    os.replace(txn, root / CLEANUP)
    _fsync_dir(root)
    result = _verify(root)
    if result != checked:
        raise ValueError("Published transaction differs from staged verification")
    _remove_transaction(root / CLEANUP)
    return result


def _recover_locked(root):
    root = Path(root)
    cleanup = root / CLEANUP
    cleaned_up = False
    if cleanup.exists() or cleanup.is_symlink():
        # Cleanup can only follow publication. Verify before discarding it.
        _verify(root)
        _remove_transaction(cleanup)
        cleaned_up = True
    txn = root / TRANSACTION
    if not txn.exists() and not txn.is_symlink():
        return "cleaned_up" if cleaned_up else "none"
    if txn.is_symlink() or not txn.is_dir():
        raise ValueError("Unsafe transaction directory")
    if not (txn / "commit.json").exists():
        # No commit decision was persisted: canonical archive is unchanged.
        _remove_transaction(txn)
        return "rolled_back"
    _finish_transaction(root)
    return "committed"


def recover(root):
    with _locked(root):
        action = _recover_locked(Path(root))
        return {"recovery": action, "verification": _verify(root)}


def _commit(root, stage, before):
    _write_manifest(stage)
    checked = _verify(stage)
    data = {"format": 1, "before_files": before,
            "after_archive_sha256": checked["archive_sha256"]}
    # This atomic, fsynced marker is the sole commit boundary. No visible
    # campaign bytes have changed before it; recovery rolls forward after it.
    _put_json(root / TRANSACTION / "commit.json", {**data, "hash": rules.digest(data)})
    _finish_transaction(root)


def _new_stage(root):
    txn = root / TRANSACTION
    txn.mkdir()
    stage = txn / "staged"
    stage.mkdir()
    _fsync_dir(root)
    _fsync_dir(txn)
    return stage


def init(root, *, seed=None, game_id="K7-NEW"):
    root = Path(root)
    secret_seed = seed if seed is not None else secure_random.token_hex(32)
    if not isinstance(secret_seed, str) or len(secret_seed) < 12:
        raise ValueError("Seed must be a string with at least 12 characters")
    root.mkdir(parents=True, exist_ok=True)
    with _locked(root):
        if any(p.name != ".campaign.lock" for p in root.iterdir()):
            raise ValueError("Campaign directory must be empty")
        private = {"seed": secret_seed, "goals": rules.secret_goals(secret_seed)}
        initial = rules.opening_state(secret_seed, game_id=game_id)
        seal = {"version": rules.VERSION, "archive_format": FORMAT,
                "game_id": game_id, "source_sha256": _source_sha(),
                "initial_state_sha256": rules.digest(initial),
                "seed_commit": hashlib.sha256(secret_seed.encode("utf-8")).hexdigest(),
                "secret_file_sha256": rules.digest(private)}
        seal["genesis_commit"] = rules.digest(seal)
        genesis_audit = _audit_entry(0, "genesis", 0,
                                    {"genesis_commit": seal["genesis_commit"],
                                     "initial_state_sha256": seal["initial_state_sha256"]},
                                    "0" * 64)
        stage = _new_stage(root)
        for name, data in (("genesis.json", seal), ("secrets.json", private),
                           ("initial_state.json", initial), ("state.json", initial)):
            _put_json(stage / name, data)
        _put_audit(stage, [genesis_audit])
        public = rules.make_docket(initial, secret_seed)
        _put_json(stage / "docket_01.json", public)
        _commit(root, stage, {})
        return {"genesis_commit": seal["genesis_commit"],
                "ai_plan_commit": public["ai_plan_commit"],
                "archive_sha256": _verify(root)["archive_sha256"],
                "round": 0, "game_id": game_id, "version": rules.VERSION}


def docket(root):
    with _locked(root):
        _verify(root)
        root = Path(root)
        return rules.make_docket(_read_json(root / "state.json"),
                                 _read_json(root / "secrets.json")["seed"])


def template(root):
    with _locked(root):
        checked = _verify(root)
        root = Path(root)
        state = _read_json(root / "state.json")
        if state["status"] != "ongoing":
            raise ValueError("Campaign has ended")
        public = rules.make_docket(state, _read_json(root / "secrets.json")["seed"])
        command = {"action": {"type": "pass"}, "diplomacy": {"type": "none"},
                   "court": {"type": "none"}, "reply": "decline", "vote": "no",
                   "sway": 0, "bid": 0}
        if public["election"]:
            command["candidate"] = "A"
        return {**rules.validate_command(state, public, command),
                "expected_audit_tip": checked["audit_tip"]}


def talk(root, target, text):
    with _locked(root):
        _verify(root)
        return rules.talk(_read_json(Path(root) / "state.json"), target, text)


def _already_played(root, command, expected, checked):
    """Return an idempotent receipt only for the same normalized prior command."""
    entries = _read_audit(root)
    for index, entry in enumerate(entries[1:], 1):
        if entry["prev_hash"] != expected:
            continue
        before = _read_json(root / ("initial_state.json" if index == 1 else
                                   "turn_%02d.json" % (index - 1)))
        public = _read_json(root / ("docket_%02d.json" % index))
        normalized = rules.validate_command(before, public, command)
        if normalized != entry["payload"]["command"]:
            break
        return {"round_outcome": _read_json(root / ("result_%02d.json" % index)),
                "verification": checked, "previous_audit_tip": expected,
                "already_committed": True}
    raise ValueError("Stale audit tip; command was not applied. Read the current docket/template")


def play(root, command):
    root = Path(root)
    if type(command) is not dict:
        raise ValueError("Command must be a JSON object")
    command = deepcopy(command)
    has_expected = "expected_audit_tip" in command
    expected = command.pop("expected_audit_tip", None)
    if has_expected and (not isinstance(expected, str) or
                         re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        raise ValueError("expected_audit_tip must be a SHA-256 hex string")
    # A legacy naked command is tied to the manifest seen BEFORE lock acquisition.
    # It remains convenient for sequential callers, but cannot wait and silently
    # become a command for a different round. Persisted templates are safer.
    observed = _read_json(root / "manifest.json")["audit_tip"]
    with _locked(root):
        recovery = _recover_locked(root)
        status = _verify(root)
        if expected is not None and expected != status["audit_tip"]:
            return _already_played(root, command, expected, status)
        if expected is None and recovery in ("committed", "cleaned_up") and status["round"]:
            # A legacy retry after a partially/fully published commit cannot
            # silently advance another round during automatic recovery.
            return _already_played(root, command, _read_audit(root)[-1]["prev_hash"], status)
        if expected is None and observed != status["audit_tip"]:
            raise ValueError("Campaign advanced during submission; use a fresh template")
        state = _read_json(root / "state.json")
        private = _read_json(root / "secrets.json")
        public = rules.make_docket(state, private["seed"])
        normalized = rules.validate_command(state, public, command)
        new_state, result = rules.execute_round(state, private, normalized)
        entries = _read_audit(root)
        payload = {"command": normalized, "docket_sha256": rules.digest(public),
                   "ai_plan_commit": public["ai_plan_commit"],
                   "result_sha256": rules.digest(result),
                   "state_sha256": rules.digest(new_state)}
        entry = _audit_entry(len(entries), "turn", new_state["round"],
                             payload, entries[-1]["hash"])
        before = {name: _file_sha(root / name) for name in _archive_names(root)}
        stage = _new_stage(root)
        for name in sorted(before):
            _put_bytes(stage / name, (root / name).read_bytes())
        r = new_state["round"]
        for name, data in (("command_%02d.json" % r, normalized),
                           ("result_%02d.json" % r, result),
                           ("turn_%02d.json" % r, new_state), ("state.json", new_state)):
            _put_json(stage / name, data)
        _put_audit(stage, entries + [entry])
        if new_state["status"] == "ongoing" and r < 20:
            _put_json(stage / ("docket_%02d.json" % (r + 1)),
                      rules.make_docket(new_state, private["seed"]))
        _commit(root, stage, before)
        return {"round_outcome": result, "verification": _verify(root),
                "previous_audit_tip": status["audit_tip"], "already_committed": False}


def reveal(root):
    with _locked(root):
        checked = _verify(root)
        if checked["status"] == "ongoing":
            raise ValueError("Sealed GM data cannot be revealed before game end")
        state = _read_json(Path(root) / "state.json")
        private = _read_json(Path(root) / "secrets.json")
        return {"version": rules.VERSION, "seed": private["seed"],
                "hidden_goals": private["goals"], "scores": state["scores"],
                "goal_results": state.get("goal_results"),
                "final_status": state["status"], "verification": checked}


def export_commands(root):
    """Seed-free command export; source campaign is verified and never changed."""
    with _locked(root):
        checked = _verify(root)
        return {"format": "seven-kings-command-replay-v1", "source": checked,
                "commands": [e["payload"]["command"] for e in _read_audit(root)[1:]]}


def replay(root, exported, *, seed, game_id="K7-REPLAY"):
    """Replay explicit synthetic commands as a NEW campaign, not old-history repair."""
    root = Path(root)
    if root.exists():
        raise ValueError("Replay destination must be a NEW directory")
    if (not isinstance(exported, dict) or
            exported.get("format") != "seven-kings-command-replay-v1" or
            not isinstance(exported.get("commands"), list)):
        raise ValueError("Invalid command replay export")
    # Validate all commands before creating any destination, including transitions
    # that are no longer legal under the current rules.
    state = rules.opening_state(seed, game_id=game_id)
    private = {"seed": seed, "goals": rules.secret_goals(seed)}
    for command in exported["commands"]:
        state, _ = rules.execute_round(state, private, command)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".replay-", dir=root.parent) as temporary:
        stage = Path(temporary) / "campaign"
        init(stage, seed=seed, game_id=game_id)
        for command in exported["commands"]:
            play(stage, command)
        checked = verify(stage)
        # mkdir is an exclusive reservation and prevents replacing an existing
        # campaign even if another process created it during our replay.
        root.mkdir()
        try:
            os.replace(stage, root)
        except BaseException:
            root.rmdir()
            raise
        _fsync_dir(root.parent)
    return {"verification": checked, "source": exported.get("source"),
            "source_provenance_verified": False,
            "notice": "New synthetic campaign; original history and outcomes are not migrated"}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Seven Kings Council HELL-1.2 deterministic political engine")
    sub = ap.add_subparsers(dest="command", required=True)
    names = ("init", "docket", "play", "verify", "reveal", "recover", "template",
             "talk", "export", "replay")
    for name in names:
        sub.add_parser(name).add_argument("--dir", required=True)
    sub.choices["init"].add_argument("--game-id", default="K7-NEW")
    sub.choices["init"].add_argument("--seed", help="Explicit synthetic seed; testing only")
    sub.choices["play"].add_argument("--command-file", required=True)
    sub.choices["verify"].add_argument("--expected-archive-sha256")
    sub.choices["talk"].add_argument("--target", required=True)
    sub.choices["talk"].add_argument("--text", required=True)
    sub.choices["replay"].add_argument("--commands-file", required=True)
    sub.choices["replay"].add_argument("--seed", required=True, help="Explicit synthetic seed only")
    sub.choices["replay"].add_argument("--game-id", default="K7-REPLAY")
    options = ap.parse_args(argv)
    try:
        if options.command == "init":
            value = init(options.dir, seed=options.seed, game_id=options.game_id)
        elif options.command == "play":
            value = play(options.dir, _read_json(options.command_file))
        elif options.command == "verify":
            value = verify(options.dir, expected_archive_sha256=options.expected_archive_sha256)
        elif options.command == "talk":
            value = talk(options.dir, options.target, options.text)
        elif options.command == "export":
            value = export_commands(options.dir)
        elif options.command == "replay":
            value = replay(options.dir, _read_json(options.commands_file),
                           seed=options.seed, game_id=options.game_id)
        else:
            value = {"docket": docket, "reveal": reveal, "recover": recover,
                     "template": template}[options.command](options.dir)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
