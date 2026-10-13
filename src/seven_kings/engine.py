"""Disk format, audit replay and CLI for the HELL-1.2 political engine.

Private campaign directories contain secrets.json. Never commit them to public Git.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets as secure_random
import sys

from . import rules

SOURCE_FILES = ("engine.py", "rules.py", "__init__.py")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _put_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    os.replace(temporary, path)


def _source_sha():
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8") + b"\0")
        h.update((root / name).read_bytes())
    return h.hexdigest()


def _audit_entry(index, kind, round_no, payload, prev_hash):
    fields = {
        "index": index, "kind": kind, "round": round_no,
        "payload": payload, "prev_hash": prev_hash,
    }
    return {**fields, "hash": rules.digest(fields)}


def _audit_path(root):
    return Path(root) / "audit.jsonl"


def _read_audit(root):
    return [
        json.loads(line)
        for line in _audit_path(root).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _put_audit(root, entries):
    text = "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True)
                     for e in entries) + "\n"
    path = _audit_path(root)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def init(root, *, seed=None, game_id="K7-NEW"):
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise ValueError("Campaign directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    secret_seed = seed if seed is not None else secure_random.token_hex(32)
    if not isinstance(secret_seed, str) or len(secret_seed) < 12:
        raise ValueError("Seed must be a string with at least 12 characters")
    private = {"seed": secret_seed, "goals": rules.secret_goals(secret_seed)}
    initial = rules.opening_state(secret_seed, game_id=game_id)
    seal = {
        "version": rules.VERSION, "game_id": game_id, "source_sha256": _source_sha(),
        "initial_state_sha256": rules.digest(initial),
        "seed_commit": hashlib.sha256(secret_seed.encode("utf-8")).hexdigest(),
        "secret_file_sha256": rules.digest(private),
    }
    seal["genesis_commit"] = rules.digest(seal)
    genesis_audit = _audit_entry(
        0, "genesis", 0,
        {"genesis_commit": seal["genesis_commit"],
         "initial_state_sha256": seal["initial_state_sha256"]},
        "0" * 64)
    _put_json(root / "genesis.json", seal)
    _put_json(root / "secrets.json", private)
    _put_json(root / "initial_state.json", initial)
    _put_json(root / "state.json", initial)
    _put_audit(root, [genesis_audit])
    docket = rules.make_docket(initial, secret_seed)
    _put_json(root / "docket_01.json", docket)
    return {"genesis_commit": seal["genesis_commit"],
            "ai_plan_commit": docket["ai_plan_commit"],
            "round": 0, "game_id": game_id, "version": rules.VERSION}


def verify(root):
    root = Path(root)
    seal = _read_json(root / "genesis.json")
    original_seal = {k: v for k, v in seal.items() if k != "genesis_commit"}
    if rules.digest(original_seal) != seal["genesis_commit"]:
        raise ValueError("Genesis commitment changed")
    if seal["version"] != rules.VERSION:
        raise ValueError("Wrong game version")
    if seal["source_sha256"] != _source_sha():
        raise ValueError("Frozen engine SHA mismatch")
    private = _read_json(root / "secrets.json")
    if rules.digest(private) != seal["secret_file_sha256"]:
        raise ValueError("Secret file commitment mismatch")
    if hashlib.sha256(private["seed"].encode("utf-8")).hexdigest() != seal["seed_commit"]:
        raise ValueError("Seed commitment mismatch")
    initial = _read_json(root / "initial_state.json")
    if rules.digest(initial) != seal["initial_state_sha256"]:
        raise ValueError("Initial state hash mismatch")
    entries = _read_audit(root)
    if not entries or entries[0]["kind"] != "genesis":
        raise ValueError("Missing genesis audit")
    state = deepcopy(initial)
    tip = "0" * 64
    for index, entry in enumerate(entries):
        data = {k: v for k, v in entry.items() if k != "hash"}
        if (entry["index"] != index or entry["prev_hash"] != tip or
                rules.digest(data) != entry["hash"]):
            raise ValueError("Audit chain tampered at record %d" % index)
        if index == 0:
            if (entry["payload"].get("genesis_commit") != seal["genesis_commit"] or
                    entry["payload"].get("initial_state_sha256") !=
                    seal["initial_state_sha256"]):
                raise ValueError("Audit genesis payload tampered")
        else:
            if entry["kind"] != "turn" or entry["round"] != index:
                raise ValueError("Invalid audit round sequence")
            payload = entry["payload"]
            docket = rules.make_docket(state, private["seed"])
            if rules.digest(docket) != payload["docket_sha256"]:
                raise ValueError("Public docket hash differs on replay")
            replayed, result = rules.execute_round(state, private, payload["command"])
            if (rules.digest(replayed) != payload["state_sha256"] or
                    rules.digest(result) != payload["result_sha256"] or
                    docket["ai_plan_commit"] != payload["ai_plan_commit"]):
                raise ValueError("Replay differs in round %d" % index)
            state = replayed
        tip = entry["hash"]
    stored = _read_json(root / "state.json")
    if rules.canonical(stored) != rules.canonical(state):
        raise ValueError("Latest state differs from audit replay")
    if state["round"] != len(entries) - 1:
        raise ValueError("Round count differs")
    if state["status"] == "ongoing" and state["round"] < 20:
        docket = rules.make_docket(state, private["seed"])
        docket_file = root / ("docket_%02d.json" % (state["round"] + 1))
        if docket_file.exists() and _read_json(docket_file) != docket:
            raise ValueError("Public docket file differs")
    return {"verified": True, "version": rules.VERSION,
            "game_id": state["game_id"], "round": state["round"],
            "status": state["status"], "audit_records": len(entries),
            "audit_tip": tip, "state_sha256": rules.digest(state),
            "genesis_commit": seal["genesis_commit"]}


def docket(root):
    verify(root)
    root = Path(root)
    state = _read_json(root / "state.json")
    private = _read_json(root / "secrets.json")
    return rules.make_docket(state, private["seed"])


def play(root, command):
    # Validation and independent replay precede any file modifications.
    status = verify(root)
    root = Path(root)
    state = _read_json(root / "state.json")
    private = _read_json(root / "secrets.json")
    current_docket = rules.make_docket(state, private["seed"])
    new_state, result = rules.execute_round(state, private, command)
    normalized = rules.validate_command(state, current_docket, command)
    entries = _read_audit(root)
    payload = {
        "command": normalized,
        "docket_sha256": rules.digest(current_docket),
        "ai_plan_commit": current_docket["ai_plan_commit"],
        "result_sha256": rules.digest(result),
        "state_sha256": rules.digest(new_state),
    }
    entry = _audit_entry(
        len(entries), "turn", new_state["round"], payload, entries[-1]["hash"])
    # Each round publishes immutable snapshots for inspection and replays.
    r = new_state["round"]
    snapshot = root / ("turn_%02d.json" % r)
    if snapshot.exists():
        raise ValueError("Round snapshot exists; refusing to overwrite")
    _put_json(root / ("command_%02d.json" % r), normalized)
    _put_json(root / ("result_%02d.json" % r), result)
    _put_json(snapshot, new_state)
    _put_json(root / "state.json", new_state)
    _put_audit(root, entries + [entry])
    if new_state["status"] == "ongoing" and new_state["round"] < 20:
        _put_json(root / ("docket_%02d.json" % (r + 1)),
                  rules.make_docket(new_state, private["seed"]))
    checked = verify(root)
    return {"round_outcome": result, "verification": checked,
            "previous_audit_tip": status["audit_tip"]}


def reveal(root):
    checked = verify(root)
    if checked["status"] == "ongoing":
        raise ValueError("Sealed GM data cannot be revealed before game end")
    state = _read_json(Path(root) / "state.json")
    private = _read_json(Path(root) / "secrets.json")
    return {"version": rules.VERSION, "seed": private["seed"],
            "hidden_goals": private["goals"], "scores": state["scores"],
            "goal_results": state.get("goal_results"),
            "final_status": state["status"],
            "verification": checked}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Seven Kings Council HELL-1.2 deterministic political engine")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("init", "docket", "play", "verify", "reveal"):
        sub.add_parser(name)
    ap.add_argument("--dir", default=None, help=argparse.SUPPRESS)
    # Subcommand options must follow their respective subcommands.
    for command in ("init", "docket", "play", "verify", "reveal"):
        child = sub.choices[command]
        child.add_argument("--dir", required=True)
    sub.choices["init"].add_argument("--game-id", default="K7-NEW")
    sub.choices["init"].add_argument(
        "--seed", help="Explicit synthetic seed; for testing only")
    sub.choices["play"].add_argument("--command-file", required=True)
    options = ap.parse_args(argv)
    try:
        if options.command == "init":
            value = init(options.dir, seed=options.seed, game_id=options.game_id)
        elif options.command == "docket":
            value = docket(options.dir)
        elif options.command == "play":
            value = play(options.dir, _read_json(options.command_file))
        elif options.command == "verify":
            value = verify(options.dir)
        else:
            value = reveal(options.dir)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
