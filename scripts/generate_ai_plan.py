"""Generate weekly gym plans from BTWB WODs using Anthropic Messages API.
Only WOD text and a generic training profile are sent to Anthropic.
No browser logs, weights, names, credentials or personal identifiers are sent.
"""
import json
import os
import re
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

INPUT = Path("data/wods.json")
OUTPUT = Path("data/ai-plan.json")
PREFERRED_MODELS = ("claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4-20250514", "claude-3-7-sonnet-20250219")
EXERCISES = {
    "Barbell bench press": ("🏋️", "Borst"),
    "Lat pulldown": ("↙️", "Brede rug"),
    "Chest-supported dumbbell row": ("🚣", "Rugdikte"),
    "Incline dumbbell press": ("💪", "Bovenborst"),
    "Dumbbell lateral raise": ("🙆", "Zijkant schouders"),
    "Reverse fly": ("🪽", "Achterste schouder"),
    "Hammer curl": ("💪", "Biceps"),
    "Biceps curl": ("💪", "Biceps"),
    "Cable triceps pushdown": ("💪", "Triceps"),
    "Pec deck": ("💪", "Borst"),
    "Cable row": ("🚣", "Rug"),
    "Face pull": ("🪽", "Achterste schouder"),
}

def resolve_model(key):
    """Ask Anthropic for models available to this API key instead of guessing IDs."""
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models?limit=100",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            models = json.load(response).get("data", [])
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Anthropic models listing HTTP {exc.code}") from None
    ids = [m.get("id", "") for m in models if m.get("id")]
    if not ids:
        raise RuntimeError("Anthropic returned no available models")
    for preferred in PREFERRED_MODELS:
        if preferred in ids:
            print(f"Selected available Anthropic model: {preferred}")
            return preferred
    sonnets = [m for m in ids if "sonnet" in m]
    if sonnets:
        chosen = sonnets[0]
        print(f"Selected available Anthropic model: {chosen}")
        return chosen
    raise RuntimeError("No Claude Sonnet model available; available model IDs: " + ", ".join(ids[:20]))

def main():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY GitHub Actions secret")
    model = resolve_model(key)
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    today = datetime.now(ZoneInfo("Europe/Amsterdam")).date()
    monday = today - timedelta(days=today.weekday())
    # Include next week if the current week's training days have passed.
    weeks = [monday, monday + timedelta(days=7)]
    all_plans = []
    for start in weeks:
        end = start + timedelta(days=6)
        selected = [
            {"date": d["date"], "workouts": d["workouts"]}
            for d in data["days"]
            if start <= date.fromisoformat(d["date"]) <= end
            and date.fromisoformat(d["date"]).weekday() in (1, 4)
        ]
        if not selected or not any(d["workouts"] for d in selected):
            continue
        friday = next((d for d in selected if date.fromisoformat(d["date"]).weekday() == 4), None)
        friday_available = bool(friday and friday["workouts"])
        friday_titles = [w["title"] for w in friday["workouts"]] if friday_available else []
        prompt = {
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "wods": selected,
            "friday_wod_available": friday_available,
            "friday_wod_titles": friday_titles,
            "available_exercises": list(EXERCISES),
            "goal": "Upper body hypertrophy (chest, back width/thickness, shoulders, arms)",
            "schedule": "Gym Monday (A), CrossFit Tuesday, Gym Thursday (B), CrossFit Friday, running on weekend",
            "session_minutes": "45-60",
            "training_level": "CrossFit beginner, recreational running, progressive resistance training",
        }
        instruction = (
            "You are an evidence-informed strength coach. Design two upper-body gym workouts "
            "for the specified week, prioritizing hypertrophy while managing fatigue around the "
            "actual Tuesday and Friday CrossFit WODs. Consider that published BTWB workouts may "
            "include alternatives for different levels: do not assume the athlete performs all "
            "alternatives. If Friday WOD is missing, say uncertainty explicitly and keep Thursday "
            "conservative. Never invent WOD content. No max-effort sets or pain-provoking movements. "
            "Use 2-3 reps in reserve and include sensible progression cues. "
            "CRITICAL: friday_wod_available is an authoritative boolean. If true, NEVER say the Friday WOD "
            "is missing, unpublished, unknown or unavailable. Refer explicitly to the provided Friday "
            "WOD exercise demands when explaining Thursday programming. If false, do not invent it. "
            "Choose ONLY exercise names from available_exercises. "
            "Return ONLY valid JSON, no markdown, in this exact shape: "
            '{"A":{"summary":"Dutch 1-3 sentences","exercises":[{"name":"exercise from list",'
            '"sets":3,"reps":"8-12","note":"short Dutch cue"}]},'
            '"B":{"summary":"Dutch 1-3 sentences","exercises":[{"name":"exercise from list",'
            '"sets":2,"reps":"10-12","note":"short Dutch cue"}]}}. '
            "Each day 5-7 exercises, 2-4 sets each. Write summaries and notes in Dutch. "
            "Only output the JSON object."
        )
        payload = json.dumps({
            "model": model, "max_tokens": 2300, "temperature": 0.2,
            "system": instruction,
            "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            # Show only HTTP status and safe Anthropic error type, never credentials or request data.
            try:
                error_type = json.loads(exc.read().decode("utf-8")).get("error", {}).get("type", "unknown")
            except (ValueError, UnicodeError):
                error_type = "unknown"
            raise RuntimeError(f"Anthropic API HTTP {exc.code}, error type: {error_type}") from None
        except Exception as exc:
            raise RuntimeError(f"Anthropic API request failed: {type(exc).__name__}") from None
        raw = "".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
        raw = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", raw.strip())
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("Claude returned invalid JSON") from None
        for day in ("A", "B"):
            if day not in plan or not isinstance(plan[day].get("summary"), str):
                raise RuntimeError(f"Claude plan missing {day} summary")
            exercises = plan[day].get("exercises")
            if not isinstance(exercises, list) or not 5 <= len(exercises) <= 7:
                raise RuntimeError(f"Claude plan has invalid {day} exercises")
            for ex in exercises:
                if ex.get("name") not in EXERCISES or type(ex.get("sets")) is not int or not 1 <= ex["sets"] <= 4:
                    raise RuntimeError("Claude returned unsupported exercise or set count")
                if not isinstance(ex.get("reps"), str) or not re.fullmatch(r"[0-9]{1,2}(?:-[0-9]{1,2})?", ex["reps"]):
                    raise RuntimeError("Claude returned invalid rep range")
                if not isinstance(ex.get("note"), str):
                    raise RuntimeError("Claude returned missing exercise note")
                ex["icon"], ex["focus"] = EXERCISES[ex["name"]]
        if friday_available:
            # Prevent a fabricated missing-WOD claim even when the model ignores its context.
            summary = plan["B"]["summary"]
            if re.search(r"(vrijdag.{0,55}(niet beschikbaar|ontbreek|niet gepubliceerd|onbekend)|(?:niet beschikbaar|ontbreek|niet gepubliceerd|onbekend).{0,55}vrijdag)", summary, re.I):
                plan["B"]["summary"] = (
                    "Vrijdag staat de CrossFit-WOD " + ", ".join(friday_titles) +
                    " gepland. Donderdag wordt het trainingsvolume afgestemd op de daarbij "
                    "genoemde oefeningen, met extra aandacht voor herstel van rug, grip en schouders. "
                    "Houd 2-3 herhalingen in reserve."
                )
                print(f"Corrected false missing-Friday-WOD claim for week {start}")
        all_plans.append({"week_start": start.isoformat(), "week_end": end.isoformat(), "A": plan["A"], "B": plan["B"]})
    if not all_plans:
        raise RuntimeError("No upcoming Tuesday/Friday WODs available for AI planning")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "generated_at": datetime.now(ZoneInfo("Europe/Amsterdam")).isoformat(),
        "model": model, "weeks": all_plans,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(all_plans)} weekly gym plans with Claude")

if __name__ == "__main__":
    main()
