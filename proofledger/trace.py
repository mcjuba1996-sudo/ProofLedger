"""Turn a finished run into a compact, evaluator-side trace (for the demo, reports and the viewer)."""
from .matcher import explain


def short_action(a):
    if not isinstance(a, dict):
        return "invalid action"
    n = a.get("action")
    if n == "list_records":
        return f"list_records {a.get('table')} @{a.get('cursor') or 0}"
    if n == "get_record":
        return f"get_record {a.get('table')} {a.get('id')}"
    if n == "get_document":
        return f"get_document {a.get('doc_id')}"
    if n == "calculator":
        return f"calculator {a.get('expression')}"
    if n == "submit":
        f = a.get("findings")
        k = len(f.get("findings", [])) if isinstance(f, dict) and isinstance(f.get("findings"), list) else "?"
        return f"submit {k} findings"
    return f"unknown {n}"


def short_obs(o):
    if o.get("error"):
        return "error: " + o["error"].get("code", "?")
    r = o.get("result")
    if not r:
        return "first observation" if "task" in o else "ok"
    if "records" in r:
        return f"{len(r['records'])} records" + ("" if r.get("next_cursor") is None else ", more")
    if "record" in r:
        return "1 record"
    if "document" in r:
        return f"{r['doc_type']} document"
    if "value" in r:
        return "= " + r["value"]
    if r.get("submitted"):
        return "submitted"
    return "ok"


def build(env, policy_name, level=None, seed=None):
    steps = [{"step": t["step"], "action": short_action(t["action"]) if t["action"] is not None else "reset",
              "kind": (t["action"] or {}).get("action", "reset") if isinstance(t["action"], dict) or t["action"] is None else "invalid",
              "obs": short_obs(t["observation"])} for t in env.trajectory]
    last = env.trajectory[-1]["action"]
    findings = []
    if isinstance(last, dict) and last.get("action") == "submit":
        sub = last.get("findings")
        if isinstance(sub, dict) and isinstance(sub.get("findings"), list):
            findings = sub["findings"]
    rep = env.final_report()
    if rep["error"]:
        findings = []
    verdicts = explain(env._issues, findings, env._evidence_ids)
    m = env._ep["manifest"]
    return {"policy": policy_name, "level": level, "seed": seed, "episode_id": m["episode_id"],
            "period": f"{m['period_start']} .. {m['period_end']}", "timezone": m["timezone"],
            "counts": m["counts"], "issues": len(env._issues),
            "report": {k: rep[k] for k in ("reward", "f1", "precision", "recall", "tp", "fp", "fn", "steps", "error")
                       if k in rep},
            "steps": steps,
            "verdicts": [{"verdict": v["verdict"], "reason": v["reason"], "type": v["finding"]["type"],
                          "affected_ids": v["finding"]["affected_ids"],
                          "amount": v["finding"]["claimed_amount_minor"], "currency": v["finding"]["currency"]}
                         for v in verdicts["findings"]],
            "missed": verdicts["missed"]}
