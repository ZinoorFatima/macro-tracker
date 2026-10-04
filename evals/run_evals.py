#!/usr/bin/env python
"""Eval runner for the three Claude-backed flows.

    # graders and harness only, no API calls, no spend -- this is what CI runs
    python -m evals.run_evals --offline

    # the real thing, against whatever provider .env configures
    python -m evals.run_evals --flow meal_macros
    python -m evals.run_evals --reps 3            # all flows, 3 samples each

    # compare two providers on the same cases
    python -m evals.run_evals --variant baseline
    python -m evals.run_evals --provider groq --model <id> --variant v1

Two modes:

**offline** replays the hand-written stand-in responses in `fixtures/` instead
of calling the API. It proves the harness, the graders and the output contract
work; it tells you nothing about the model, because the fixtures were written by
hand rather than recorded from a real run. CI runs this so a broken grader fails
the build without the repo needing a billed API key.

**live** calls the configured model once per case per rep. Every call is billed.
`--reps` above 1 is worth it when comparing two models or prompts, since a
single sample per case cannot separate a real difference from sampling noise.

Output follows the per-variant layout the Anthropic eval tooling reads:

    evals/results/<flow>/<variant>/results.jsonl   one row per (case, rep)
    evals/results/<flow>/<variant>/traces/<id>_rep<k>.json
    evals/results/<flow>/<variant>/errors.jsonl    attempts that never scored

Rows are written as each case finishes, and a rerun skips `(case, rep)` pairs
already present, so an interrupted run resumes instead of re-billing.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app import ai, providers  # noqa: E402
from app.targets import compute_targets  # noqa: E402
from evals.graders import GRADERS, METRICS  # noqa: E402

CASES_PATH = Path(__file__).parent / "cases.jsonl"
FIXTURES_PATH = Path(__file__).parent / "fixtures"
RESULTS_ROOT = Path(__file__).parent / "results"

# The eval's stand-in user. Held fixed so a prompt change is the only thing that
# moves between runs -- the profile feeds every system prompt.
EVAL_PROFILE = {
    "age": 28,
    "gender": "female",
    "height_cm": 165.0,
    "weight_kg": 70.0,
    "activity_level": "moderate",
    "goal": "lose_fat",
    "timeline_weeks": 12,
    "target_weight_kg": 62.0,
}
EVAL_PROFILE.update(
    compute_targets(
        EVAL_PROFILE["age"],
        EVAL_PROFILE["gender"],
        EVAL_PROFILE["height_cm"],
        EVAL_PROFILE["weight_kg"],
        EVAL_PROFILE["activity_level"],
        EVAL_PROFILE["goal"],
    )
)

# Per-case wall-clock ceiling. A hung connection can emit keepalives forever, so
# only a total-time ceiling reliably reclaims the slot.
CASE_TIMEOUT_S = 120
MAX_ATTEMPTS = 3


class CaseTimeout(Exception):
    pass


def load_cases(flow: str | None, only: str | None) -> list[dict]:
    cases = [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if flow:
        cases = [c for c in cases if c["flow"] == flow]
    if only:
        wanted = {s.strip() for s in only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def day_context(case: dict) -> str:
    """Render a day_rating case into the same text the app sends.

    Deliberately duplicates the shape of `routers.days.rate_day` rather than
    importing it: that function reads from SQLite, and an eval that needs a
    populated database to measure a prompt is an eval nobody runs.
    """
    day = case["day"]
    lines = ["DAY: 2026-01-15", "", "MEALS:"]
    if day["meals"]:
        for meal in day["meals"]:
            score = f", meal score {meal['score']}/10" if meal.get("score") else ""
            lines.append(
                f"- [{meal['meal_type']}] {meal['description']}: {meal['calories']} kcal, "
                f"{meal['protein_g']}g P, {meal['carbs_g']}g C, {meal['fat_g']}g F{score}"
            )
    else:
        lines.append("- (no meals logged)")

    lines += ["", "ACTIVITIES:"]
    if day["activities"]:
        for act in day["activities"]:
            if act["activity_type"] == "steps":
                lines.append(f"- {act['steps']} steps (~{act['calories_burned']} kcal)")
            elif act["activity_type"] == "rest":
                lines.append("- Rest day (no training)")
            else:
                lines.append(
                    f"- Workout: {act['description']} (~{act['calories_burned']} kcal)"
                )
    else:
        lines.append("- (no activity logged)")

    totals_kcal = sum(m["calories"] for m in day["meals"])
    totals_p = sum(m["protein_g"] for m in day["meals"])
    totals_c = sum(m["carbs_g"] for m in day["meals"])
    totals_f = sum(m["fat_g"] for m in day["meals"])
    burned = sum(a["calories_burned"] for a in day["activities"])
    lines += [
        "",
        f"DAY TOTALS: {totals_kcal} kcal eaten, {totals_p}g protein, "
        f"{totals_c}g carbs, {totals_f}g fat, "
        f"~{burned} kcal burned through activity.",
    ]
    if day["weights"]:
        lines += ["", "RECENT WEIGHT LOGS (newest first):"]
        lines += [f"- {w['date']}: {w['weight_kg']}kg" for w in day["weights"]]
    return "\n".join(lines)


def build_request(case: dict) -> tuple[str, list[dict], dict | None]:
    """Return (system_prompt, messages, submit_tool) for a case."""
    if case["flow"] == "meal_macros":
        return (
            ai.meal_system_prompt(EVAL_PROFILE, case["meal_type"]),
            [dict(m) for m in case["messages"]],
            ai.SUBMIT_MEAL_TOOL,
        )
    if case["flow"] == "activity_parse":
        return (
            ai.activity_system_prompt(EVAL_PROFILE),
            [dict(m) for m in case["messages"]],
            ai.SUBMIT_ACTIVITY_TOOL,
        )
    if case["flow"] == "day_rating":
        return (
            ai.day_rating_system_prompt(EVAL_PROFILE),
            [{"role": "user", "content": day_context(case)}],
            None,
        )
    raise ValueError(f"Unknown flow {case['flow']!r}")


def run_offline(case: dict) -> dict:
    """Replay a hand-written fixture through the real validators."""
    path = FIXTURES_PATH / f"{case['id']}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No offline fixture for {case['id']}. Add {path.name} to evals/fixtures/ "
            "or run without --offline."
        )
    fixture = json.loads(path.read_text(encoding="utf-8"))
    tool_name, tool_input = fixture["tool_name"], fixture["tool_input"]
    if tool_name == "ask_clarification":
        return {
            "status": "question",
            "question": ai.validate_question(tool_input),
            "model": "offline-fixture",
            "usage": {},
        }
    validated = ai.VALIDATORS[tool_name](tool_input)
    return {
        "status": "analysis",
        "analysis": validated,
        "model": "offline-fixture",
        "usage": {},
    }


def run_live(case: dict, model: str | None) -> dict:
    """One real model call for a case, returning the validated outcome.

    `model` of None means "whatever the environment is configured for", so
    `--model` stays optional when MACRO_TRACKER_MODEL is already set.
    """
    system, messages, submit_tool = build_request(case)

    if case["flow"] == "day_rating":
        result = ai.call_with_meta(
            system,
            messages,
            [ai.SUBMIT_DAY_RATING_TOOL],
            {"type": "tool", "name": "submit_day_rating"},
        )
        return {
            "status": "analysis",
            "analysis": ai.validate_day_rating(result.arguments),
            **_meta(result),
        }

    result = ai.call_with_meta(
        system, messages, [ai.ASK_CLARIFICATION_TOOL, submit_tool], {"type": "any"}
    )
    if result.name == "ask_clarification":
        return {
            "status": "question",
            "question": ai.validate_question(result.arguments),
            **_meta(result),
        }
    if result.name != submit_tool["name"]:
        raise ai.AIOutputError(f"Unexpected tool {result.name!r}")
    return {
        "status": "analysis",
        "analysis": ai.VALIDATORS[result.name](result.arguments),
        **_meta(result),
    }


def _meta(result) -> dict:
    return {
        "model": result.model,
        "stop_reason": result.stop_reason,
        "usage": result.usage,
    }


def attempt_case(case: dict, rep: int, model: str | None, offline: bool) -> dict:
    """Run and grade one (case, rep), with retries on transient failures.

    Scoring is strict per attempt: a case that only succeeds on retry is still
    recorded with its retry count, so a run that needed three tries is visible
    in the data rather than only on the bill.
    """
    started = time.monotonic()
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        if time.monotonic() - started > CASE_TIMEOUT_S:
            last_error = CaseTimeout(f"exceeded {CASE_TIMEOUT_S}s")
            break
        try:
            outcome = run_offline(case) if offline else run_live(case, model)
        except ai.AIOutputError as e:
            # A schema-valid-but-unusable response is a model failure, not a
            # plumbing failure: score it rather than retrying into the bill.
            grade = {metric: 0.0 for metric, _ in METRICS[case["flow"]]}
            return {
                "prompt_id": case["id"],
                "rep": rep,
                "flow": case["flow"],
                "tags": case.get("tags", []),
                "prompt": _prompt_text(case),
                "status": "rejected",
                "grade": grade,
                "explanation": {"rejected": str(e)},
                "retries": attempt,
                "latency_s": round(time.monotonic() - started, 2),
                "model": model or "unknown",
                "usage": {},
            }
        except Exception as e:  # transient: rate limit, connection, 5xx
            last_error = e
            if attempt == MAX_ATTEMPTS - 1:
                break
            # Jittered backoff: a tight retry loop turns one 429 into a
            # torn-down batch and multiplies spend invisibly.
            time.sleep(min(2**attempt + random.uniform(0, 1), 20))
            continue

        grader = GRADERS[case["flow"]]
        grade, explanation = grader(case, outcome)
        served = outcome.get("model", model)
        row = {
            "prompt_id": case["id"],
            "rep": rep,
            "flow": case["flow"],
            "tags": case.get("tags", []),
            "prompt": _prompt_text(case),
            "status": "ok" if outcome.get("stop_reason") != "max_tokens" else "truncated",
            "stop_reason": outcome.get("stop_reason"),
            "outcome": outcome["status"],
            "grade": grade,
            "explanation": explanation,
            "retries": attempt,
            "latency_s": round(time.monotonic() - started, 2),
            "model": served,
            "usage": outcome.get("usage", {}),
        }
        if not offline and model and served != model and not served.startswith(model):
            # A substituted model invalidates the comparison the eval exists for.
            # Providers vary in how they echo the id (some append a revision),
            # so a prefix match is the strictest check that does not false-alarm.
            row["served_model_mismatch"] = True
        return row

    raise RuntimeError(
        f"{case['id']} rep{rep} failed after {MAX_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def _prompt_text(case: dict) -> str:
    if case["flow"] == "day_rating":
        return day_context(case)
    return "\n".join(f"{m['role']}: {m['content']}" for m in case["messages"])


def write_trace(out_dir: Path, case: dict, row: dict) -> None:
    system, messages, _ = build_request(case)
    turns = [{"role": "system", "content": system}]
    turns += [{"role": m["role"], "content": m["content"]} for m in messages]
    if row.get("outcome") == "question":
        turns.append(
            {
                "role": "tool_call",
                "name": "ask_clarification",
                "content": json.dumps(
                    {"question": row["explanation"].get("detail", "")}, indent=2
                ),
            }
        )
    else:
        turns.append(
            {
                "role": "tool_call",
                "name": "submit_analysis",
                "content": json.dumps(row.get("grade", {}), indent=2),
            }
        )
    turns.append(
        {"role": "assistant", "content": json.dumps(row.get("explanation", {}), indent=2)}
    )
    traces = out_dir / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    (traces / f"{case['id']}_rep{row['rep']}.json").write_text(
        json.dumps(turns, indent=2), encoding="utf-8"
    )


def already_done(results_path: Path) -> set[tuple[str, int]]:
    if not results_path.exists():
        return set()
    done = set()
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        done.add((row["prompt_id"], row["rep"]))
    return done


def summarise(rows: list[dict], flow: str) -> None:
    metrics = METRICS[flow]
    scored = [r for r in rows if r["status"] != "truncated"]
    print(f"\n  {flow}  ({len(scored)} scored row(s))")
    if not scored:
        return
    for index, (metric, description) in enumerate(metrics):
        values = [r["grade"][metric] for r in scored if metric in r["grade"]]
        if not values:
            print(
                f"    {'*' if index == 0 else ' '} {metric:<18} --      "
                f"(not applicable to these cases)"
            )
            continue
        mean = statistics.mean(values)
        # Wald interval; with a handful of cases it is wide, which is the point.
        half = 1.96 * (mean * (1 - mean) / len(values)) ** 0.5 if len(values) > 1 else 0.0
        marker = "*" if index == 0 else " "
        print(
            f"    {marker} {metric:<18} {mean:>5.0%}  +/-{half:>4.0%}  "
            f"n={len(values):<3} {description}"
        )
    latencies = [r["latency_s"] for r in scored if r.get("latency_s")]
    if latencies:
        print(
            f"      latency           {statistics.mean(latencies):.1f}s mean, "
            f"{max(latencies):.1f}s max"
        )
    tokens = sum(r.get("usage", {}).get("output_tokens", 0) for r in scored)
    if tokens:
        inp = sum(r.get("usage", {}).get("input_tokens", 0) for r in scored)
        print(f"      tokens            {inp} in / {tokens} out")
    failures = [r for r in scored if r["grade"].get(metrics[0][0]) == 0.0]
    if failures:
        print(f"      headline failures: {', '.join(sorted(r['prompt_id'] for r in failures))}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--flow", choices=sorted(METRICS), help="only run one flow (default: all)"
    )
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument(
        "--variant", default="baseline", help="output directory name: baseline, v1, v2, ..."
    )
    parser.add_argument(
        "--model", default=None, help="model id to evaluate (default: MACRO_TRACKER_MODEL)"
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="anthropic, groq, gemini, openrouter, ollama (default: MACRO_TRACKER_PROVIDER)",
    )
    parser.add_argument(
        "--reps", type=int, default=1, help="samples per case; >1 separates signal from noise"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="replay fixtures instead of calling the API (free)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="in-flight requests; keep below your rate limit",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="exit non-zero if any flow's headline metric is below this",
    )
    args = parser.parse_args()

    cases = load_cases(args.flow, args.only)
    if not cases:
        print("No cases matched.", file=sys.stderr)
        return 2

    if not args.offline:
        if args.model:
            os.environ["MACRO_TRACKER_MODEL"] = args.model
        if args.provider:
            os.environ["MACRO_TRACKER_PROVIDER"] = args.provider
        providers.reset_provider()
        ok, detail = providers.is_configured()
        if not ok:
            print(
                f"{detail}\n\nPass --offline to run the graders against the bundled "
                "fixtures without calling any API.",
                file=sys.stderr,
            )
            return 2

    total_calls = len(cases) * args.reps
    if args.offline:
        mode = "offline (no API calls)"
    else:
        mode = f"live against {providers.get_provider().describe()}"
    print(f"Running {len(cases)} case(s) x {args.reps} rep(s) = {total_calls} call(s), {mode}")

    rows_by_flow: dict[str, list[dict]] = {}
    for flow in sorted({c["flow"] for c in cases}):
        flow_cases = [c for c in cases if c["flow"] == flow]
        out_dir = RESULTS_ROOT / flow / args.variant
        out_dir.mkdir(parents=True, exist_ok=True)
        results_path = out_dir / "results.jsonl"
        errors_path = out_dir / "errors.jsonl"
        done = already_done(results_path)

        work = [
            (case, rep)
            for case in flow_cases
            for rep in range(args.reps)
            if (case["id"], rep) not in done
        ]
        if len(work) < len(flow_cases) * args.reps:
            print(
                f"  resuming {flow}: skipping "
                f"{len(flow_cases) * args.reps - len(work)} completed row(s)"
            )

        rows = (
            [
                json.loads(line)
                for line in results_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if results_path.exists()
            else []
        )

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(attempt_case, case, rep, args.model, args.offline): (case, rep)
                for case, rep in work
            }
            for future in as_completed(futures):
                case, rep = futures[future]
                try:
                    row = future.result()
                except Exception as e:
                    # Harness or serving failure: keep it out of results.jsonl so
                    # a resume retries it instead of scoring plumbing as a model
                    # failure.
                    with errors_path.open("a", encoding="utf-8") as fh:
                        fh.write(
                            json.dumps(
                                {
                                    "prompt_id": case["id"],
                                    "rep": rep,
                                    "failure_class": "timeout"
                                    if isinstance(e, CaseTimeout)
                                    else "harness_or_serving",
                                    "error": str(e),
                                }
                            )
                            + "\n"
                        )
                    print(f"  ERROR {case['id']} rep{rep}: {e}", file=sys.stderr)
                    continue
                # Written as each case finishes: a crash mid-run must not cost
                # the cases that already completed.
                with results_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
                write_trace(out_dir, case, row)
                rows.append(row)
                headline = METRICS[flow][0][0]
                mark = {1.0: "PASS", 0.0: "FAIL"}.get(row["grade"].get(headline), "--  ")
                print(f"  {mark}  {case['id']}")

        rows_by_flow[flow] = rows

    print("\n" + "=" * 72)
    print("RESULTS" + ("  (* = headline metric)" if rows_by_flow else ""))
    print("=" * 72)
    for flow, rows in rows_by_flow.items():
        summarise(rows, flow)
    print(f"\nPer-case rows and transcripts: {RESULTS_ROOT}")

    if args.threshold is not None:
        for flow, rows in rows_by_flow.items():
            headline = METRICS[flow][0][0]
            values = [r["grade"][headline] for r in rows if headline in r["grade"]]
            if values and statistics.mean(values) < args.threshold:
                print(
                    f"\nFAIL: {flow} {headline} = {statistics.mean(values):.0%}, "
                    f"below the {args.threshold:.0%} threshold",
                    file=sys.stderr,
                )
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
