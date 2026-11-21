"""Submission validation, one-to-one matching, F1 and the terminal reward.

Select a maximum-cardinality matching. Among equal-size matchings choose the
lexicographically smallest sorted list of (issue_id, original finding index).
"""
ERROR_TYPES = ("duplicate_invoice", "payment_allocation", "fx_error", "period_error")
MAX_FINDINGS = 30
MAX_EVIDENCE = 10
MAX_EXPLANATION = 1000
STEP_COST = 0.002


def canon_ids(ids):
    """Canonical affected_ids: stripped, unique, sorted."""
    return tuple(sorted({str(i).strip() for i in ids}))


def validate_submission(sub, episode_id):
    """Return None if valid, else a short error string."""
    if not isinstance(sub, dict):
        return "submission must be an object"
    if sub.get("episode_id") != episode_id:
        return "episode_id mismatch"
    fs = sub.get("findings")
    if not isinstance(fs, list):
        return "findings must be a list"
    if len(fs) > MAX_FINDINGS:
        return "too many findings"
    for n, f in enumerate(fs):
        p = f"findings[{n}]"
        if not isinstance(f, dict):
            return f"{p} must be an object"
        if not isinstance(f.get("type"), str):
            return f"{p}.type invalid"
        ids = f.get("affected_ids")
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(i, str) and i.strip() for i in ids)):
            return f"{p}.affected_ids invalid"
        a = f.get("claimed_amount_minor")
        if not isinstance(a, int) or isinstance(a, bool):
            return f"{p}.claimed_amount_minor invalid"
        if not isinstance(f.get("currency"), str):
            return f"{p}.currency invalid"
        ev = f.get("evidence_ids")
        if (not isinstance(ev, list) or len(ev) > MAX_EVIDENCE
                or not all(isinstance(i, str) for i in ev)):
            return f"{p}.evidence_ids invalid"
        ex = f.get("explanation")
        if not isinstance(ex, str) or len(ex) > MAX_EXPLANATION:
            return f"{p}.explanation invalid"
    return None


def public_evidence_ids(episode):
    """IDs the policy can cite from the four public tables of this episode."""
    ids = {r["row_id"] for r in episode["ledger"]}
    for table in ("invoices", "payments"):
        ids.update(r["doc_id"] for r in episode[table])
    ids.update(f"{r['date']}|{r['source_currency']}|{r['target_currency']}" for r in episode["fx"])
    return ids


def scoring_issues(episode):
    """Apply the public amount tolerance unless the fixture specifies it per issue."""
    default = episode.get("policy", {}).get("amount_tolerance_minor", 0)
    return [{"tolerance_minor": default, **issue} for issue in episode["truth"]["issues"]]


def _matches(issue, f):
    if f["type"] != issue["type"] or f["currency"] != issue["currency"]:
        return False
    if canon_ids(f["affected_ids"]) != canon_ids(issue["affected_ids"]):
        return False
    if abs(f["claimed_amount_minor"] - issue["expected_amount_minor"]) > issue.get("tolerance_minor", 0):
        return False
    ev = set(f["evidence_ids"])
    return any(set(es) <= ev for es in issue["evidence_sets"])


def _maximum_size(edges, reserved):
    """Number of issues that can receive distinct, unreserved findings."""
    owner = {}

    def augment(issue, visited):
        for index in edges[issue]:
            if index in reserved or index in visited:
                continue
            visited.add(index)
            if index not in owner or augment(owner[index], visited):
                owner[index] = issue
                return True
        return False

    return sum(augment(issue, set()) for issue in range(len(edges)))


def _match(issues, findings, available_evidence_ids):
    if available_evidence_ids is None:
        # Standalone fixtures without public tables can establish only their named evidence.
        available_evidence_ids = {key for issue in issues for group in issue["evidence_sets"] for key in group}
    eligible = [index for index, finding in enumerate(findings)
                if available_evidence_ids.issuperset(finding["evidence_ids"])]
    ordered = sorted(issues, key=lambda issue: issue["issue_id"])
    edges = [[index for index in eligible if _matches(issue, findings[index])] for issue in ordered]
    remaining = _maximum_size(edges, set())
    used = {}
    for position, candidates in enumerate(edges):
        if not remaining:
            break
        for index in candidates:
            if index in used:
                continue
            # Commit the earliest pair only if all remaining matches still fit.
            if _maximum_size(edges[position + 1:], used.keys() | {index}) >= remaining - 1:
                used[index] = ordered[position]["issue_id"]
                remaining -= 1
                break
    return used, eligible


def evaluate(issues, findings, available_evidence_ids=None):
    """Score structurally valid findings. Pass the public ID set for a real episode."""
    used, _ = _match(issues, findings, available_evidence_ids)
    tp = len(used)
    fp = len(findings) - tp
    fn = len(issues) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 1.0 if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def explain(issues, findings, available_evidence_ids=None):
    """Evaluator-side verdict for each finding and the list of missed issues.

    Never shown to the agent during a run; used for demos, reports and open train/dev episodes.
    """
    used, eligible = _match(issues, findings, available_evidence_ids)
    matched = set(used.values())
    rows = []
    for idx, f in enumerate(findings):
        if idx in used:
            rows.append({"finding": f, "verdict": "TP", "issue_id": used[idx], "reason": "matched"})
            continue
        ids = canon_ids(f["affected_ids"])
        same = [i for i in issues if canon_ids(i["affected_ids"]) == ids]
        same_t = [i for i in same if i["type"] == f["type"]]
        if not same:
            reason = "false accusation: no discrepancy on these records"
        elif not same_t:
            reason = "wrong type for these records"
        elif all(f["currency"] != i["currency"] for i in same_t):
            reason = "wrong currency"
        elif not any(abs(f["claimed_amount_minor"] - i["expected_amount_minor"]) <= i.get("tolerance_minor", 0)
                     for i in same_t if i["currency"] == f["currency"]):
            reason = "amount outside tolerance"
        elif idx not in eligible:
            reason = "evidence does not prove it"
        elif any(i["issue_id"] in matched and _matches(i, f) for i in same_t):
            reason = "repeat of an already matched finding"
        else:
            reason = "evidence does not prove it"
        rows.append({"finding": f, "verdict": "FP", "issue_id": None, "reason": reason})
    missed = [{"issue_id": i["issue_id"], "type": i["type"], "affected_ids": list(i["affected_ids"])}
              for i in sorted(issues, key=lambda i: i["issue_id"]) if i["issue_id"] not in matched]
    return {"findings": rows, "missed": missed}


def terminal_reward(f1, inspection_steps):
    r = 2 * f1 - 1 - STEP_COST * inspection_steps
    return round(max(-1.0, min(1.0, r)), 12)


def score_submission(episode, submission, inspection_steps, available_evidence_ids=None):
    """Trusted evaluator result (seven official fields, private structural error or None)."""
    error = validate_submission(submission, episode["manifest"]["episode_id"])
    if error is not None:
        metrics = dict.fromkeys(("tp", "fp", "fn", "precision", "recall", "f1"))
        return {**metrics, "return": -1.0}, error
    if available_evidence_ids is None:
        available_evidence_ids = public_evidence_ids(episode)
    metrics = evaluate(scoring_issues(episode), submission["findings"], available_evidence_ids)
    return {**metrics, "return": terminal_reward(metrics["f1"], inspection_steps)}, None
