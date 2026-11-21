"""Export the open train/dev episodes and baseline trajectories.

python -m proofledger.export [out_dir]   (default: ./episodes)

Writes <out>/{train,dev}/<episode_id>/ (open files; truth.json and provenance.json are included
because these seeds are public), <out>/configs.json and <out>/trajectories.jsonl.
Trajectories come from non-oracle policies in train mode, so no closed truth appears in them.
"""
import json
import sys
from pathlib import Path

from .configs import CONFIG_VERSION, DEV, TRAIN
from .generator import VERSION, generate_episode, write_episode
from .policies import run_policy

TRAJ_POLICIES = ("reconciler", "random_explorer", "flag_all", "silence")


def main(argv=None):
    out = Path((argv or sys.argv[1:] or ["episodes"])[0])
    out.mkdir(parents=True, exist_ok=True)
    index = {"generator_version": VERSION, "config_version": CONFIG_VERSION, "train": [], "dev": []}
    for split, items in (("train", TRAIN), ("dev", DEV)):
        for seed, cfg in items:
            ep = generate_episode(seed, cfg)
            eid = ep["manifest"]["episode_id"]
            write_episode(ep, out / split / eid, include_private=True)
            index[split].append({"episode_id": eid, "seed": seed, "config": cfg,
                                 "issues": len(ep["truth"]["issues"])})
    (out / "configs.json").write_text(json.dumps(index, indent=2))
    n = 0
    with open(out / "trajectories.jsonl", "w") as f:
        for seed, cfg in TRAIN:
            for p in TRAJ_POLICIES:
                for row in run_policy(p, seed, cfg)["trajectory"]:
                    f.write(json.dumps({"policy": p, **row}) + "\n")
                    n += 1
    print(f"wrote {len(TRAIN)} train + {len(DEV)} dev episodes and {n} trajectory steps to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
