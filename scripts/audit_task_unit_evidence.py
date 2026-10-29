"""Independently re-derive the documented claims from archived task-unit runs.

Reads only archived result files. It does not open a game session, so it cannot
interfere with a live run. Every claim recorded in the documentation is listed
here as data and checked against the raw action logs and run reports; a claim
that cannot be reproduced from the archive is reported as unsupported.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "pure_network"
QUEST_ITEM = "4862"

# Claims transcribed from docs/53_有界任务单元与契约执行器_2026-10-28.md.
CLAIMS = [
    {"run": "run99_task_unit", "status": "failure", "note": "nav usage error"},
    {"run": "run100_task_unit", "status": "success", "item_before": 3, "item_after": 4, "health": 89},
    {"run": "run102_task_unit_repeat", "status": "failure", "note": "path length rejected"},
    {"run": "run103_task_unit_repeat", "status": "unknown", "note": "combat precondition"},
    {"run": "run104_task_unit_repeat", "status": "failure", "note": "side effect verdict order"},
    {"run": "run105_task_unit_repeat", "status": "success", "item_before": 4, "item_after": 5, "health": 89},
    {"run": "run106_task_unit_repeat", "status": "unknown", "note": "path z drop"},
    {"run": "run107_task_unit_repeat", "status": "success", "item_before": 5, "item_after": 6, "health": 63},
    {"run": "run108_collection_verify", "status": "PASS", "item_after": 6, "health": 89},
]


def load_run(run_id):
    report_path = RESULTS / f"agent_wow_task_{run_id}.json"
    log_path = RESULTS / f"agent_wow_task_{run_id}.actions.jsonl"
    if not report_path.exists():
        return None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return {"report": report, "records": records}


def derive(run_id, bundle):
    report = bundle["report"]
    records = bundle["records"]
    run = report.get("run", {})
    derived = {"run": run_id, "status": run.get("status"), "actions": {},
               "final_health": None, "final_in_combat": None, "final_ghost": None,
               "item_before": None, "item_after": None, "logout_status": None,
               "error": report.get("error")}
    for record in records:
        derived["actions"][record["action_id"]] = record["status"]
        if record["action_id"] == "loot":
            before = record.get("observation_before", {}).get("item_totals", {})
            after = record.get("observation_after", {}).get("item_totals", {})
            derived["item_before"] = int(before.get(QUEST_ITEM, 0))
            derived["item_after"] = int(after.get(QUEST_ITEM, 0))
        if record["action_id"] == "logout":
            derived["logout_status"] = record.get("evidence", {}).get("status")
    final = report.get("final_observation") or {}
    derived["final_health"] = final.get("health")
    derived["final_in_combat"] = final.get("in_combat")
    derived["final_ghost"] = final.get("ghost")
    derived["items_final"] = final.get("item_totals", {}).get(QUEST_ITEM)
    derived["elapsed_s"] = report.get("elapsed_s")
    for record in records:
        evidence = record.get("evidence", {}) or {}
        if record["action_id"] == "fight":
            derived["fight_outcome"] = (evidence.get("phase") or {}).get("outcome")
            derived["attack_sent"] = (evidence.get("phase") or {}).get("start_sent_once")
        if record["action_id"] == "loot":
            derived["loot_received"] = evidence.get("received")
        if record["action_id"] == "approach":
            derived["approach_attempts"] = evidence.get("attempts")
            derived["approach_reached"] = evidence.get("reached")
    return derived


def deep_check(claim, derived):
    """Substantive checks for successful and unknown runs."""
    issues = []
    if derived["status"] == "success":
        if derived.get("fight_outcome") != "target_dead":
            issues.append(f"success without confirmed target death: {derived.get('fight_outcome')!r}")
        if not derived.get("loot_received"):
            issues.append("success without recorded loot receipt")
        if derived.get("item_after") is not None and derived.get("item_before") is not None:
            if derived["item_after"] != derived["item_before"] + 1:
                issues.append(f"quest item count {derived['item_before']}->{derived['item_after']} is not +1")
        if derived.get("final_in_combat") is not False:
            issues.append(f"success ended in combat: {derived.get('final_in_combat')!r}")
        if derived.get("final_ghost") is not False:
            issues.append(f"success ended as ghost: {derived.get('final_ghost')!r}")
        if derived.get("logout_status") != "closed":
            issues.append(f"success without closed logout: {derived.get('logout_status')!r}")
    if derived["status"] == "unknown":
        unknown_actions = [name for name, status in derived["actions"].items() if status == "unknown"]
        for name in unknown_actions:
            count = sum(1 for name_ in derived["actions"] if name_ == name)
            if count != 1:
                issues.append(f"unknown action {name} appears {count} times; possible blind resend")
        for name, status in derived["actions"].items():
            if name in unknown_actions:
                continue
            if status == "success" and name in ("fight", "loot", "logout"):
                issues.append(f"action {name} reported success after an unknown earlier step")
    return issues


def check(claim, derived):
    issues = []
    if "status" in claim and claim["status"] != derived["status"]:
        issues.append(f"status claim {claim['status']!r} != derived {derived['status']!r}")
    if "item_before" in claim and derived["item_before"] is not None and claim["item_before"] != derived["item_before"]:
        issues.append(f"item_before claim {claim['item_before']} != derived {derived['item_before']}")
    if "item_after" in claim:
        actual = derived["item_after"] if derived["item_after"] is not None else derived["items_final"]
        if actual is not None and claim["item_after"] != actual:
            issues.append(f"item_after claim {claim['item_after']} != derived {actual}")
    if "health" in claim and derived["final_health"] is not None and claim["health"] != derived["final_health"]:
        issues.append(f"health claim {claim['health']} != derived {derived['final_health']}")
    return issues


def main():
    rows = []
    for claim in CLAIMS:
        bundle = load_run(claim["run"])
        if bundle is None:
            bridge = RESULTS / f"agent_wow_bridge_{claim['run']}.json"
            if bridge.exists():
                data = json.loads(bridge.read_text(encoding="utf-8"))
                final = data.get("final_self") or {}
                derived = {"run": claim["run"], "status": data.get("status"), "actions": {},
                           "final_health": final.get("health"), "final_in_combat": final.get("in_combat"),
                           "final_ghost": final.get("ghost"), "item_before": None, "item_after": None,
                           "items_final": (data.get("owned_item_totals") or {}).get(QUEST_ITEM),
                           "logout_status": (data.get("logout") or {}).get("result", {}).get("status"),
                           "error": data.get("error"), "source": "bridge report"}
            else:
                rows.append({"run": claim["run"], "status": "MISSING", "issues": ["no archived file"]})
                continue
        else:
            derived = derive(claim["run"], bundle)
            derived["source"] = "task report"
        issues = check(claim, derived)
        issues += deep_check(claim, derived)
        derived["issues"] = issues
        derived["supported"] = not issues
        rows.append(derived)
    unsupported = [r for r in rows if not r.get("supported")]
    output = {"claims": rows, "unsupported_count": len(unsupported),
              "quest_item_entry": QUEST_ITEM,
              "scope": "read-only re-derivation from archived files; no live session; "
                       "does not re-run the game"}
    path = RESULTS / "task_unit_evidence_audit_2026-10-28.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(row["run"], row.get("status"), "supported=" if row.get("supported") else "ISSUES=",
              row.get("issues") or "")
    print("unsupported claims:", len(unsupported))
    print(path)


if __name__ == "__main__":
    main()
