"""Run a disposable, explicitly synthetic campaign using public-information policy."""
import argparse
import json
from pathlib import Path

from . import engine, simulation

DEMO_SEED = "public-synthetic-demo-hell-1.2-only"


def run(root):
    engine.init(root, seed=DEMO_SEED, game_id="PUBLIC-SYNTHETIC-DEMO")
    for _ in range(20):
        check = engine.verify(root)
        if check["status"] != "ongoing":
            break
        state = engine._read_json(Path(root) / "state.json")
        command = simulation.choose_command(state, engine.docket(root), "project_spam")
        engine.play(root, command)
    check = engine.verify(root)
    if check["status"] == "ongoing":
        raise RuntimeError("Demo did not finish")
    revealed = engine.reveal(root)
    return {"synthetic": True, "verification": check,
            "final_status": revealed["final_status"], "scores": revealed["scores"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="New empty disposable directory")
    args = parser.parse_args(argv)
    print(json.dumps(run(args.dir), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
