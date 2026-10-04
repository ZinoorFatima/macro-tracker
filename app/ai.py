"""Claude integration: meal analysis, activity parsing, day rating.

Three flows, all built on the same shape: a forced tool call whose schema *is*
the response contract. Claude never returns free prose that the app has to
parse -- it either calls `ask_clarification` to get a missing portion size, or
it calls the submit tool with structured macros.

Two things are deliberate here and easy to break on a later edit:

* `tool_choice={"type": "any"}` forces a tool call. Forced tool choice is
  rejected with a 400 on some newer models (Opus 5.5, Sonnet 5.5, Fable 5.1),
  so `MODEL` cannot be bumped to one of those without also moving to
  `{"type": "auto"}` plus a prompt instruction naming the tool.
* A tool schema constrains shape, not sanity. Claude can return a schema-valid
  3,000g of protein, so every submitted analysis goes through
  `validate_meal_analysis` / `validate_activity_analysis` before the router
  sees it.
"""

import os

import anthropic

from .validation import (
    MAX_ACTIVITY_CALORIES,
    MAX_FEEDBACK_LEN,
    MAX_MEAL_CALORIES,
    MAX_MEAL_MACRO_G,
    ValidationProblem,
    coerce_number,
)

# Overridable so the eval harness can measure a different model without a code
# change. Must be a model that allows forced tool choice -- see module docstring.
MODEL = os.environ.get("MACRO_TRACKER_MODEL", "claude-sonnet-5")
MAX_TOKENS = 2000

_client = None


class AIUnavailableError(Exception):
    """The AI call could not be completed. The message is shown to the user."""


class AIOutputError(AIUnavailableError):
    """The AI responded, but with values the app will not store."""


def get_client() -> anthropic.Anthropic:
    global _client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AIUnavailableError(
            "No API key configured. Add ANTHROPIC_API_KEY to the .env file and restart."
        )
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


ASK_CLARIFICATION_TOOL = {
    "name": "ask_clarification",
    "description": "Ask the user one concise clarifying question when an ambiguity "
    "would significantly change the analysis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "One concise question for the user"}
        },
        "required": ["question"],
    },
}

SUBMIT_MEAL_TOOL = {
    "name": "submit_meal_analysis",
    "description": "Submit the final nutritional analysis of the meal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "portion": {"type": "string"},
                        "calories": {"type": "integer"},
                        "protein_g": {"type": "number"},
                        "carbs_g": {"type": "number"},
                        "fat_g": {"type": "number"},
                    },
                    "required": ["name", "calories", "protein_g", "carbs_g", "fat_g"],
                },
            },
            "total_calories": {"type": "integer"},
            "total_protein_g": {"type": "number"},
            "total_carbs_g": {"type": "number"},
            "total_fat_g": {"type": "number"},
            "score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "How well this meal fits the user's goal, 1-10",
            },
            "feedback": {
                "type": "string",
                "description": "2-3 concrete improvement suggestions relative to the "
                "user's goal. State any assumptions made.",
            },
        },
        "required": [
            "items",
            "total_calories",
            "total_protein_g",
            "total_carbs_g",
            "total_fat_g",
            "score",
            "feedback",
        ],
    },
}

SUBMIT_ACTIVITY_TOOL = {
    "name": "submit_activity_analysis",
    "description": "Submit the parsed workout and calorie estimate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "exercises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["strength", "cardio"]},
                        "sets": {"type": "integer"},
                        "reps": {"type": "integer"},
                        "weight_kg": {"type": "number"},
                        "duration_min": {"type": "number"},
                    },
                    "required": ["name", "kind"],
                },
            },
            "total_calories_burned": {"type": "integer"},
            "notes": {
                "type": "string",
                "description": "Brief comments and any assumptions made",
            },
        },
        "required": ["exercises", "total_calories_burned", "notes"],
    },
}

SUBMIT_DAY_RATING_TOOL = {
    "name": "submit_day_rating",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
            "summary": {
                "type": "string",
                "description": "Commentary on target adherence, food quality, and activity",
            },
            "progress_note": {
                "type": "string",
                "description": "Commentary on trajectory toward the goal and timeline",
            },
        },
        "required": ["score", "summary", "progress_note"],
    },
}


def profile_block(p) -> str:
    goal_str = p["goal"].replace("_", " ")
    timeline = f" over {p['timeline_weeks']} weeks" if p["timeline_weeks"] else ""
    target_w = f" (target weight {p['target_weight_kg']}kg)" if p["target_weight_kg"] else ""
    return (
        f"USER PROFILE: {p['age']}yo {p['gender']}, {p['height_cm']}cm, {p['weight_kg']}kg, "
        f"activity level: {p['activity_level']}.\n"
        f"GOAL: {goal_str}{timeline}{target_w}.\n"
        f"DAILY TARGETS: {p['calorie_target']} kcal, {p['protein_target_g']}g protein, "
        f"{p['carbs_target_g']}g carbs, {p['fat_target_g']}g fat."
    )


def meal_system_prompt(p, meal_type: str) -> str:
    return (
        "You are a nutrition analyst for a personal macro-tracking app. The user describes a "
        f"meal they ate ({meal_type}). Estimate its calories and macros per item.\n"
        "Ask a clarifying question (ask_clarification) ONLY if an ambiguity would change the "
        "estimate by more than roughly 20% (e.g. unknown portion size, fried vs grilled). "
        "Ask at most 2 questions total across the conversation, one at a time. After that, use "
        "reasonable assumptions and state them in the feedback field.\n"
        "Score the meal 1-10 for how well it fits the user's goal and daily targets, and give "
        "2-3 concrete, actionable improvement suggestions.\n\n" + profile_block(p)
    )


def activity_system_prompt(p) -> str:
    return (
        "You are a fitness analyst for a personal macro-tracking app. The user describes a "
        "workout in free text. Parse it into structured exercises (strength: name/sets/reps/"
        "weight; cardio: name/duration) and estimate total calories burned using MET-based "
        f"reasoning for a person weighing about {p['weight_kg']}kg.\n"
        "Ask a clarifying question (ask_clarification) at most ONCE, and only if duration or "
        "intensity is truly unknown and would change the estimate substantially. Otherwise use "
        "reasonable assumptions and state them in the notes field.\n\n" + profile_block(p)
    )


def day_rating_system_prompt(p) -> str:
    return (
        "You are a coach for a personal macro-tracking app. Rate the user's day 1-10 "
        "considering adherence to calorie/macro targets, food quality, and activity. In "
        "progress_note, comment on their trajectory toward the goal and timeline given the "
        "recent weight trend. Be encouraging but honest and concrete.\n\n" + profile_block(p)
    )


def _call(system: str, messages: list[dict], tools: list[dict], tool_choice: dict):
    client = get_client()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
    except anthropic.AuthenticationError as e:
        raise AIUnavailableError("Invalid API key. Check ANTHROPIC_API_KEY in .env.") from e
    except anthropic.RateLimitError as e:
        raise AIUnavailableError(
            "Rate limited by the AI service. Try again in a minute."
        ) from e
    except anthropic.APIConnectionError as e:
        raise AIUnavailableError(
            "Could not reach the AI service. Check your internet connection."
        ) from e
    except anthropic.APIStatusError as e:
        raise AIUnavailableError(
            f"AI service error ({e.status_code}). Try again shortly."
        ) from e
    for block in response.content:
        if block.type == "tool_use":
            return block.name, block.input
    raise AIUnavailableError("Unexpected AI response format. Try again.")


# A clarifying question is rendered as a chat bubble; keep it to a sentence or two.
MAX_QUESTION_LEN = 500


def validate_meal_analysis(data: dict) -> dict:
    """Check and normalise a submit_meal_analysis tool call.

    Returns a cleaned copy. Raises AIOutputError when the numbers are not
    storable, which the router turns into a 503 so the user can retry or fall
    back to manual entry -- never a silent bad row.

    Totals are always recomputed from the item breakdown: the per-item numbers
    are what the user sees in the review table, so the saved totals have to be
    the ones that add up to it.
    """
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise AIOutputError("The analysis came back with no food items. Try rephrasing.")

    clean_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AIOutputError("The analysis contained a malformed food item.")
        name = str(item.get("name") or "").strip()
        if not name:
            raise AIOutputError("A food item in the analysis had no name.")
        where = f"item {index + 1} ({name})"
        try:
            clean = {
                "name": name[:200],
                "calories": round(
                    coerce_number(
                        item.get("calories"),
                        field=f"{where} calories",
                        lo=0,
                        hi=MAX_MEAL_CALORIES,
                    )
                ),
                "protein_g": round(
                    coerce_number(
                        item.get("protein_g"),
                        field=f"{where} protein",
                        lo=0,
                        hi=MAX_MEAL_MACRO_G,
                    ),
                    1,
                ),
                "carbs_g": round(
                    coerce_number(
                        item.get("carbs_g"), field=f"{where} carbs", lo=0, hi=MAX_MEAL_MACRO_G
                    ),
                    1,
                ),
                "fat_g": round(
                    coerce_number(
                        item.get("fat_g"), field=f"{where} fat", lo=0, hi=MAX_MEAL_MACRO_G
                    ),
                    1,
                ),
            }
        except ValidationProblem as e:
            raise AIOutputError(f"The analysis returned an impossible value: {e}") from None
        portion = item.get("portion")
        if isinstance(portion, str) and portion.strip():
            clean["portion"] = portion.strip()[:200]
        clean_items.append(clean)

    score = data.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
        raise AIOutputError(f"The analysis returned an out-of-range score ({score!r}).")

    feedback = data.get("feedback")
    if not isinstance(feedback, str) or not feedback.strip():
        raise AIOutputError("The analysis came back without feedback.")

    totals = {
        "total_calories": round(sum(i["calories"] for i in clean_items)),
        "total_protein_g": round(sum(i["protein_g"] for i in clean_items), 1),
        "total_carbs_g": round(sum(i["carbs_g"] for i in clean_items), 1),
        "total_fat_g": round(sum(i["fat_g"] for i in clean_items), 1),
    }
    if totals["total_calories"] > MAX_MEAL_CALORIES:
        raise AIOutputError(
            f"The analysis totalled {totals['total_calories']} kcal for one meal, "
            "which is beyond what this app will store."
        )
    return {
        "items": clean_items,
        **totals,
        "score": score,
        "feedback": feedback.strip()[:MAX_FEEDBACK_LEN],
    }


def validate_activity_analysis(data: dict) -> dict:
    """Check and normalise a submit_activity_analysis tool call."""
    exercises = data.get("exercises")
    if not isinstance(exercises, list):
        raise AIOutputError("The analysis came back with no exercises.")

    clean_exercises = []
    for ex in exercises:
        if not isinstance(ex, dict):
            raise AIOutputError("The analysis contained a malformed exercise.")
        name = str(ex.get("name") or "").strip()
        if not name:
            raise AIOutputError("An exercise in the analysis had no name.")
        kind = ex.get("kind") if ex.get("kind") in ("strength", "cardio") else "cardio"
        clean = {"name": name[:200], "kind": kind}
        # Numeric detail fields are optional; drop anything unusable rather than
        # failing the whole analysis over a stray set count.
        for field, hi in (
            ("sets", 100),
            ("reps", 1000),
            ("weight_kg", 1000),
            ("duration_min", 1440),
        ):
            if ex.get(field) is None:
                continue
            try:
                value = coerce_number(ex[field], field=f"{name} {field}", lo=0, hi=hi)
            except ValidationProblem:
                continue
            clean[field] = round(value, 1) if field == "weight_kg" else round(value)
        clean_exercises.append(clean)

    try:
        burned = round(
            coerce_number(
                data.get("total_calories_burned"),
                field="calories burned",
                lo=0,
                hi=MAX_ACTIVITY_CALORIES,
            )
        )
    except ValidationProblem as e:
        raise AIOutputError(f"The analysis returned an impossible value: {e}") from None

    notes = data.get("notes")
    return {
        "exercises": clean_exercises,
        "total_calories_burned": burned,
        "notes": (notes.strip()[:MAX_FEEDBACK_LEN] if isinstance(notes, str) else ""),
    }


def validate_day_rating(data: dict) -> dict:
    """Check and normalise a submit_day_rating tool call."""
    score = data.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
        raise AIOutputError(f"The day rating came back out of range ({score!r}).")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise AIOutputError("The day rating came back without a summary.")
    progress = data.get("progress_note")
    return {
        "score": score,
        "summary": summary.strip()[:MAX_FEEDBACK_LEN],
        "progress_note": (
            progress.strip()[:MAX_FEEDBACK_LEN]
            if isinstance(progress, str) and progress.strip()
            else None
        ),
    }


def validate_question(data: dict) -> str:
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise AIOutputError("The AI asked an empty question. Try again.")
    return question.strip()[:MAX_QUESTION_LEN]


VALIDATORS = {
    "submit_meal_analysis": validate_meal_analysis,
    "submit_activity_analysis": validate_activity_analysis,
    "submit_day_rating": validate_day_rating,
}


def run_analysis(system: str, messages: list[dict], submit_tool: dict):
    """Run one analysis turn.

    Returns ("question", text) when Claude needs a clarification, or
    ("analysis", dict) with a validated, normalised analysis.
    """
    name, data = _call(
        system,
        messages,
        tools=[ASK_CLARIFICATION_TOOL, submit_tool],
        tool_choice={"type": "any"},
    )
    if name == "ask_clarification":
        return "question", validate_question(data)
    if name != submit_tool["name"]:
        raise AIOutputError(f"The AI called an unexpected tool ({name}). Try again.")
    return "analysis", VALIDATORS[name](data)


def run_day_rating(system: str, day_context: str) -> dict:
    """Rate one logged day. Returns a validated rating."""
    _, data = _call(
        system,
        [{"role": "user", "content": day_context}],
        tools=[SUBMIT_DAY_RATING_TOOL],
        tool_choice={"type": "tool", "name": "submit_day_rating"},
    )
    return validate_day_rating(data)
