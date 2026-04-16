#!/usr/bin/env python3
"""
claude-stats — Aggregate Claude Code session data into a stats JSON and SVG card.

Supports multiple Claude config directories for multi-account aggregation.

Usage:
    python3 claude-stats.py                           # defaults to ~/.claude, outputs to ./
    python3 claude-stats.py --dir ~/.claude --out ./graph
    python3 claude-stats.py --dir ~/.claude --dir /other/.claude --out ./graph
"""

import argparse
import json
import os
import glob
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Aggregate Claude Code session stats")
    parser.add_argument("--dir", dest="dirs", action="append", default=[],
                        help="Claude config directory (repeatable for multi-account)")
    parser.add_argument("--out", default=".", help="Output directory for stats JSON and SVG")
    args = parser.parse_args()
    if not args.dirs:
        args.dirs = [os.path.expanduser("~/.claude")]
    return args


# ---------------------------------------------------------------------------
# Project name resolution
# ---------------------------------------------------------------------------

def resolve_project_name(proj_dir_name: str) -> str:
    """Extract a clean project name from an encoded Claude project directory name.

    Claude encodes project paths as directory names by replacing '/' with '-'.
    Worktree paths contain '--' separating the parent repo from the worktree name.
    """
    name = proj_dir_name

    # Strip user-path prefixes (longest match first)
    prefixes = [
        "-Users-coin-Documents-GitHub-",
        "-Users-coin-Desktop-DesktopFolders-",
        "-Users-coin-Library-Mobile-Documents-iCloud-md-obsidian-Documents-",
    ]
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    # Collapse worktrees into parent project
    if "--" in name:
        name = name.split("--")[0]

    # Handle repeated segments: "tome-tome" → "tome", "cozy-brain-cozy-brain" → "cozy-brain"
    if "-" in name:
        parts = name.split("-")
        half = len(parts) // 2
        if half > 0 and parts[:half] == parts[half:]:
            name = "-".join(parts[:half])

    # Drop short path-prefix segments: "k2vp-K2-compass" → "K2-compass"
    if "-" in name:
        parts = name.split("-")
        if len(parts) >= 3 and len(parts[0]) <= 4:
            name = "-".join(parts[1:])

    # Map home-directory sessions to "misc"
    if name in ("-Users-coin", "Users-coin", "coin", ""):
        return "misc"

    return name


# ---------------------------------------------------------------------------
# Session parsing
# ---------------------------------------------------------------------------

def parse_session(jsonl_path: str) -> dict | None:
    """Parse a single session JSONL file, returning aggregate metrics."""
    user_msgs = 0
    assistant_msgs = 0
    first_ts = None
    last_ts = None

    with open(jsonl_path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = entry.get("type", "")
            if msg_type == "user":
                user_msgs += 1
            elif msg_type == "assistant":
                assistant_msgs += 1

            ts_raw = entry.get("timestamp")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue

            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts

    if first_ts is None:
        return None

    duration_min = (last_ts - first_ts).total_seconds() / 60 if last_ts else 0

    return {
        "user_msgs": user_msgs,
        "assistant_msgs": assistant_msgs,
        "duration_min": duration_min,
        "day": first_ts.strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------

def aggregate(dirs: list[str]) -> dict:
    """Scan all Claude config directories and aggregate session data."""
    daily = defaultdict(lambda: {"sessions": 0, "user_msgs": 0, "assistant_msgs": 0, "minutes": 0})
    projects = defaultdict(lambda: {"sessions": 0, "messages": 0, "minutes": 0})
    all_days = set()

    for claude_dir in dirs:
        claude_dir = os.path.expanduser(claude_dir)
        project_dirs = glob.glob(os.path.join(claude_dir, "projects", "*"))

        for proj_dir in project_dirs:
            proj_name = resolve_project_name(os.path.basename(proj_dir))

            for jsonl_path in glob.glob(os.path.join(proj_dir, "*.jsonl")):
                session = parse_session(jsonl_path)
                if session is None:
                    continue

                day = session["day"]
                all_days.add(day)
                total_msgs = session["user_msgs"] + session["assistant_msgs"]

                daily[day]["sessions"] += 1
                daily[day]["user_msgs"] += session["user_msgs"]
                daily[day]["assistant_msgs"] += session["assistant_msgs"]
                daily[day]["minutes"] += session["duration_min"]

                projects[proj_name]["sessions"] += 1
                projects[proj_name]["messages"] += total_msgs
                projects[proj_name]["minutes"] += session["duration_min"]

    current_streak, longest_streak = compute_streaks(sorted(all_days))
    total_sessions = sum(d["sessions"] for d in daily.values())
    total_user = sum(d["user_msgs"] for d in daily.values())
    total_assistant = sum(d["assistant_msgs"] for d in daily.values())
    total_minutes = sum(d["minutes"] for d in daily.values())
    top_projects = dict(sorted(projects.items(), key=lambda x: -x[1]["messages"])[:8])

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "accounts": len(dirs),
        "summary": {
            "total_sessions": total_sessions,
            "total_messages": total_user + total_assistant,
            "total_user_messages": total_user,
            "total_assistant_messages": total_assistant,
            "total_minutes": round(total_minutes),
            "total_hours": round(total_minutes / 60, 1),
            "active_days": len(all_days),
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        },
        "daily": {day: daily[day] for day in sorted(daily)},
        "projects": top_projects,
    }


def compute_streaks(sorted_days: list[str]) -> tuple[int, int]:
    """Compute current and longest streaks from sorted date strings."""
    if not sorted_days:
        return 0, 0

    longest = streak = 1
    for i in range(1, len(sorted_days)):
        prev = datetime.strptime(sorted_days[i - 1], "%Y-%m-%d")
        curr = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            streak += 1
        else:
            longest = max(longest, streak)
            streak = 1
    longest = max(longest, streak)

    # Current streak: count backwards from most recent day
    today = datetime.now().strftime("%Y-%m-%d")
    last_day = sorted_days[-1]
    gap = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last_day, "%Y-%m-%d")).days
    if gap > 1:
        return 0, longest

    current = 1
    for i in range(len(sorted_days) - 2, -1, -1):
        prev = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        curr = datetime.strptime(sorted_days[i + 1], "%Y-%m-%d")
        if (curr - prev).days == 1:
            current += 1
        else:
            break

    return current, longest


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_num(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def fmt_time(minutes: float) -> str:
    hours = minutes / 60
    if hours >= 24:
        return f"{hours / 24:.1f}d"
    if hours >= 1:
        return f"{hours:.1f}h"
    return f"{int(minutes)}m"


# ---------------------------------------------------------------------------
# SVG card generation
# ---------------------------------------------------------------------------

CARD_WIDTH = 420
CARD_HEIGHT = 280


def render_activity_bars(daily: dict, max_bars: int = 30) -> str:
    """Render recent daily activity as vertical bars."""
    sorted_days = sorted(daily.keys())[-max_bars:]
    if not sorted_days:
        return ""

    max_msgs = max(
        (daily[d]["user_msgs"] + daily[d]["assistant_msgs"] for d in sorted_days),
        default=1,
    )

    bar_w = 8
    gap = 3
    base_y = 145
    lines = []

    for i, day in enumerate(sorted_days):
        msgs = daily[day]["user_msgs"] + daily[day]["assistant_msgs"]
        height = max(2, int(msgs / max_msgs * 40))
        x = 20 + i * (bar_w + gap)
        y = base_y - height
        opacity = 0.4 + (height / 42) * 0.6
        lines.append(
            f'    <rect x="{x}" y="{y}" width="{bar_w}" height="{height}" '
            f'rx="2" fill="#39d353" opacity="{opacity:.2f}"/>'
        )

    return "\n".join(lines)


def render_project_bars(projects: dict, max_projects: int = 5) -> str:
    """Render top projects as horizontal bars with labels."""
    top = list(projects.items())[:max_projects]
    if not top:
        return ""

    max_msgs = max((d["messages"] for _, d in top), default=1)
    lines = []

    for i, (name, data) in enumerate(top):
        y = 170 + i * 20
        width = max(10, int(data["messages"] / max_msgs * 180))
        opacity = 0.3 + i * 0.12
        lines.append(
            f'    <rect x="20" y="{y}" width="{width}" height="12" '
            f'rx="3" fill="#39d353" opacity="{opacity:.2f}"/>'
        )
        lines.append(
            f'    <text x="{width + 28}" y="{y + 10}" fill="#c9d1d9" '
            f'font-size="10" font-family="monospace">{name} ({fmt_num(data["messages"])})</text>'
        )

    return "\n".join(lines)


def generate_svg(stats: dict) -> str:
    s = stats["summary"]
    activity_bars = render_activity_bars(stats["daily"])
    project_bars = render_project_bars(stats["projects"])

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" height="{CARD_HEIGHT}" viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0d1117"/>
      <stop offset="100%" stop-color="#161b22"/>
    </linearGradient>
  </defs>
  <rect width="{CARD_WIDTH}" height="{CARD_HEIGHT}" rx="12" fill="url(#bg)" stroke="#30363d" stroke-width="1"/>

  <!-- Title -->
  <text x="20" y="32" fill="#c9d1d9" font-size="14" font-family="monospace" font-weight="600">claude code</text>
  <text x="20" y="48" fill="#6e7681" font-size="10" font-family="monospace">{s["active_days"]} active days across {stats["accounts"]} account{"s" if stats["accounts"] > 1 else ""}</text>

  <!-- Stats -->
  <text x="20" y="75" fill="#6e7681" font-size="9" font-family="monospace">sessions</text>
  <text x="20" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{fmt_num(s["total_sessions"])}</text>

  <text x="110" y="75" fill="#6e7681" font-size="9" font-family="monospace">messages</text>
  <text x="110" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{fmt_num(s["total_messages"])}</text>

  <text x="210" y="75" fill="#6e7681" font-size="9" font-family="monospace">active time</text>
  <text x="210" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{fmt_time(s["total_minutes"])}</text>

  <text x="320" y="75" fill="#6e7681" font-size="9" font-family="monospace">streak</text>
  <text x="320" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{s["current_streak"]}d</text>

  <!-- Activity bars -->
  <text x="20" y="115" fill="#6e7681" font-size="9" font-family="monospace">recent activity</text>
{activity_bars}

  <!-- Projects -->
{project_bars}
</svg>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    os.makedirs(args.out, exist_ok=True)

    stats = aggregate(args.dirs)

    json_path = os.path.join(args.out, "claude-stats.json")
    with open(json_path, "w") as f:
        json.dump(stats, f, indent=2)

    svg_path = os.path.join(args.out, "claude-card.svg")
    with open(svg_path, "w") as f:
        f.write(generate_svg(stats))

    s = stats["summary"]
    print(f"Wrote {json_path}")
    print(f"Wrote {svg_path}")
    print(f"\n{s['total_sessions']} sessions, {fmt_num(s['total_messages'])} messages, "
          f"{fmt_time(s['total_minutes'])} active, {s['active_days']} days")


if __name__ == "__main__":
    main()
