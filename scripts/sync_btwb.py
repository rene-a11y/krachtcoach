"""Fetch upcoming CrossFit WODs from a personal BTWB session.

Unofficial integration: BTWB can change its login flow or endpoint.
Only workout titles/descriptions are published; never credentials/cookies.
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = "https://app.btwb.com"
OUTPUT = Path("data/wods.json")


def extract_html(response_text):
    # BTWB responds with JavaScript assigning HTML to whiteboard-wods.
    match = re.search(r'document\.getElementById\("whiteboard-wods"\)\.innerHTML\s*=\s*("(?:\\.|[^"\\])*")\s*;', response_text, re.S)
    if match:
        return json.loads(match.group(1))
    if 'whiteboard-wods-panel' in response_text and '<' in response_text:
        return response_text
    raise RuntimeError("Unexpected BTWB response format")


def parse_crossfit(html):
    soup = BeautifulSoup(html, "html.parser")
    panel = soup.select_one("#whiteboard-wods-panel")
    if panel is None:
        raise RuntimeError("BTWB whiteboard panel not found")
    heading = next((h for h in panel.find_all("h3") if h.get_text(" ", strip=True).lower() == "crossfit"), None)
    if heading is None:
        return []
    workouts = []
    # Walk forward through DOM nodes; BTWB may nest rows in wrappers.
    for row in heading.find_all_next(class_="todo-row"):
        # Stop once the row belongs to a later program section.
        previous_heading = row.find_previous("h3")
        if previous_heading is not heading:
            break
        desc = row.select_one(".workout-description")
        if desc is None:
            continue
        title_el = row.select_one(".event-title") or row.select_one(".event-workout a")
        title = title_el.get_text(" ", strip=True) if title_el else "Workout"
        workouts.append({"title": title[:180], "description": desc.get_text("\\n", strip=True)[:5000]})
    return workouts


def main():
    email = os.environ.get("BTWB_EMAIL", "").strip()
    password = os.environ.get("BTWB_PASSWORD", "")
    member_id = os.environ.get("BTWB_MEMBER_ID", "").strip()
    if not email or not password or not member_id.isdecimal():
        raise RuntimeError("Set BTWB_EMAIL, BTWB_PASSWORD and numeric BTWB_MEMBER_ID as Actions secrets")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="nl-NL")
        page = context.new_page()
        page.goto(BASE + "/whiteboard", wait_until="domcontentloaded", timeout=60000)
        if "login" in page.url or page.locator('input[type="password"]').count():
            user_field = page.locator('input[type="email"], input[name="user[email]"], input[name="email"], input[name="username"]').first
            pass_field = page.locator('input[type="password"]').first
            if not user_field.count() or not pass_field.count():
                raise RuntimeError("BTWB login form changed; manual investigation required")
            user_field.fill(email)
            pass_field.fill(password)
            pass_field.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=45000)
        # Do not log cookies, page HTML, or credential-bearing request headers.
        today = datetime.now(ZoneInfo("Europe/Amsterdam")).date()
        days = []
        for offset in range(14):
            day = today + timedelta(days=offset)
            url = f"{BASE}/members/{member_id}/whiteboard/day?d={day.isoformat()}"
            response = context.request.get(url, timeout=30000)
            if not response.ok or "login" in response.url:
                raise RuntimeError(f"BTWB request failed for {day.isoformat()}: HTTP {response.status}")
            content = response.text()
            if "<html" in content[:500].lower() and ("sign in" in content.lower() or "log in" in content.lower()):
                raise RuntimeError("BTWB session was not authenticated")
            workouts = parse_crossfit(extract_html(content))
            days.append({"date": day.isoformat(), "workouts": workouts})
        browser.close()
    if not any(day["workouts"] for day in days):
        raise RuntimeError("No CrossFit workouts found in 14 days; refusing to publish an empty sync")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"updated_at": datetime.now(ZoneInfo("Europe/Amsterdam")).isoformat(), "days": days}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BTWB sync complete: {len(days)} days; {sum(len(d['workouts']) for d in days)} CrossFit workouts")


if __name__ == "__main__":
    main()
