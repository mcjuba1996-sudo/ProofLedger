"""Harmless transform of an episode on disk: shuffle rows in every file and rename every id.

python -m proofledger.transform SRC_DIR DST_DIR [--seed N]

Ids of invoices, documents, payments, ledger rows, entities and issues are replaced consistently in
all files (including truth.json and provenance.json). Content, amounts, dates and fx keys stay the
same, so the oracle answer and its reward must be unchanged up to the new ids.
"""
import argparse
import random
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"\b(INV-\d{5}|A-\d{6}|PAY-\d{5}|P-\d{6}|L\d{5}|E-\d{4}|ISS-\d{5})\b")
FILES = ("manifest.json", "policy.json", "ledger.csv", "fx.csv", "invoices.jsonl", "payments.jsonl",
         "truth.json", "provenance.json")


def transform(src, dst, seed=0):
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"transform|{seed}")
    texts = {f: (src / f).read_text(encoding="utf-8") for f in FILES if (src / f).exists()}
    found = sorted({m for t in texts.values() for m in ID_RE.findall(t)})
    mapping, used = {}, set()
    for old in found:
        prefix = re.match(r"[A-Z]+-?", old).group(0)
        while True:
            new = f"{prefix}{rng.randint(100000, 999999)}"  # longer than any original id: no collisions
            if new not in used:
                used.add(new)
                mapping[old] = new
                break
    for f, t in texts.items():
        t = ID_RE.sub(lambda m: mapping[m.group(0)], t)
        if f.endswith(".csv"):
            head, *rows = t.splitlines()
            rng.shuffle(rows)
            t = "\n".join([head] + rows) + "\n"
        elif f.endswith(".jsonl"):
            rows = [x for x in t.splitlines() if x.strip()]
            rng.shuffle(rows)
            t = "\n".join(rows) + "\n"
        (dst / f).write_text(t, encoding="utf-8")
    return mapping


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    m = transform(a.src, a.dst, a.seed)
    print(f"renamed {len(m)} ids and shuffled rows: {a.src} -> {a.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
