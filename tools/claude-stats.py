#!/usr/bin/env python3
"""
claude-stats — Aggregate Claude Code session data into a stats JSON and SVG card.

Supports multiple Claude config directories for multi-account aggregation.

Usage:
    python3 claude-stats.py                                    # ~/.claude → ./
    python3 claude-stats.py --dir ~/.claude --out ./graph
    python3 claude-stats.py --dir ~/.claude --dir /path/.claude --out ./graph
"""

import argparse
import json
import math
import os
import glob
from datetime import datetime, timezone, timedelta
from collections import defaultdict


DESKTOP_SESSIONS_DIR = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate Claude Code session stats")
    parser.add_argument("--dir", dest="dirs", action="append", default=[],
                        help="Claude config directory (repeatable for multi-account)")
    parser.add_argument("--out", default=".", help="Output directory")
    args = parser.parse_args()
    if not args.dirs:
        args.dirs = [os.path.expanduser("~/.claude")]
    return args


def count_accounts() -> int:
    """Count distinct Claude accounts from the desktop app session directory."""
    if not os.path.isdir(DESKTOP_SESSIONS_DIR):
        return 1
    accounts = [
        d for d in os.listdir(DESKTOP_SESSIONS_DIR)
        if os.path.isdir(os.path.join(DESKTOP_SESSIONS_DIR, d))
        and not d.startswith(".")
    ]
    return max(len(accounts), 1)


# ---------------------------------------------------------------------------
# Session parsing
# ---------------------------------------------------------------------------

def parse_session(jsonl_path: str) -> dict | None:
    """Parse a session JSONL, returning aggregate metrics."""
    user_msgs = 0
    assistant_msgs = 0
    input_tokens = 0
    output_tokens = 0
    first_ts = None
    last_ts = None
    models = defaultdict(int)
    hours = defaultdict(int)

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
                msg = entry.get("message", {})
                if isinstance(msg, dict):
                    usage = msg.get("usage", {})
                    input_tokens += usage.get("input_tokens", 0)
                    input_tokens += usage.get("cache_creation_input_tokens", 0)
                    input_tokens += usage.get("cache_read_input_tokens", 0)
                    output_tokens += usage.get("output_tokens", 0)
                    model = msg.get("model", "")
                    if model:
                        models[model] += 1

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
            hours[ts.hour] += 1

    if first_ts is None:
        return None

    duration_min = (last_ts - first_ts).total_seconds() / 60 if last_ts else 0

    return {
        "user_msgs": user_msgs,
        "assistant_msgs": assistant_msgs,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_min": duration_min,
        "day": first_ts.strftime("%Y-%m-%d"),
        "models": dict(models),
        "hours": dict(hours),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(dirs: list[str]) -> dict:
    daily = defaultdict(lambda: {
        "sessions": 0, "user_msgs": 0, "assistant_msgs": 0,
        "input_tokens": 0, "output_tokens": 0, "minutes": 0,
    })
    models = defaultdict(int)
    hours = defaultdict(int)
    all_days: set[str] = set()

    for claude_dir in dirs:
        claude_dir = os.path.expanduser(claude_dir)
        for jsonl_path in glob.glob(os.path.join(claude_dir, "projects", "*", "*.jsonl")):
            if "/subagents/" in jsonl_path:
                continue
            session = parse_session(jsonl_path)
            if session is None:
                continue

            day = session["day"]
            all_days.add(day)

            daily[day]["sessions"] += 1
            daily[day]["user_msgs"] += session["user_msgs"]
            daily[day]["assistant_msgs"] += session["assistant_msgs"]
            daily[day]["input_tokens"] += session["input_tokens"]
            daily[day]["output_tokens"] += session["output_tokens"]
            daily[day]["minutes"] += session["duration_min"]

            for model, count in session["models"].items():
                models[model] += count
            for hour, count in session["hours"].items():
                hours[hour] += count

    current_streak, longest_streak = compute_streaks(sorted(all_days))
    total_sessions = sum(d["sessions"] for d in daily.values())
    total_messages = sum(d["user_msgs"] + d["assistant_msgs"] for d in daily.values())
    total_input = sum(d["input_tokens"] for d in daily.values())
    total_output = sum(d["output_tokens"] for d in daily.values())
    total_tokens = total_input + total_output

    peak_hour = max(hours, key=hours.get) if hours else 0
    favorite_model = max(models, key=models.get) if models else "n/a"

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "accounts": count_accounts(),
        "summary": {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "active_days": len(all_days),
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "peak_hour": peak_hour,
            "favorite_model": favorite_model,
        },
        "daily": {day: daily[day] for day in sorted(daily)},
    }


def compute_streaks(sorted_days: list[str]) -> tuple[int, int]:
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

    today = datetime.now().strftime("%Y-%m-%d")
    gap = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(sorted_days[-1], "%Y-%m-%d")).days
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
# Formatting
# ---------------------------------------------------------------------------

def fmt_num(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_hour(h: int) -> str:
    if h == 0:
        return "12 AM"
    if h < 12:
        return f"{h} AM"
    if h == 12:
        return "12 PM"
    return f"{h - 12} PM"


# ---------------------------------------------------------------------------
# SVG card — matches native Claude Code activity tracker layout
#
# WCAG AA contrast on #0d1117:
#   Labels:  #8b949e (4.7:1) — PASS AA
#   Values:  #e6edf3 (13.5:1) — PASS AAA
#   Muted:   #7d8590 (3.8:1) — PASS AA large text
#   Grid empty: #161b22
#   Grid active: 4-level blue scale
# ---------------------------------------------------------------------------

W = 480
H = 340

# Colors
BG = "#0d1117"
BORDER = "#30363d"
LABEL = "#8b949e"       # 4.7:1 on BG — AA compliant
VALUE = "#e6edf3"       # 13.5:1 on BG — AAA compliant
MUTED = "#7d8590"       # AA large text
CELL_EMPTY = "#161b22"
BLUES = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]  # green scale
FONT = "Geist Mono, monospace"


def render_stat_boxes(summary: dict) -> str:
    """Render 2 rows of 4 stat boxes matching native tracker layout."""
    box_w = 105
    box_h = 52
    gap = 8
    start_x = 20
    row1_y = 62
    row2_y = row1_y + box_h + gap
    corner_r = 8

    stats_row1 = [
        ("Sessions", fmt_num(summary["total_sessions"])),
        ("Messages", fmt_num(summary["total_messages"])),
        ("Total tokens", fmt_num(summary["total_tokens"])),
        ("Active days", str(summary["active_days"])),
    ]
    stats_row2 = [
        ("Current streak", f'{summary["current_streak"]}d'),
        ("Longest streak", f'{summary["longest_streak"]}d'),
        ("Peak hour", fmt_hour(summary["peak_hour"])),
        ("Favorite model", summary["favorite_model"].replace("claude-", "")),
    ]

    lines = []
    for row_idx, (stats, y) in enumerate([(stats_row1, row1_y), (stats_row2, row2_y)]):
        for i, (label, value) in enumerate(stats):
            x = start_x + i * (box_w + gap)
            # Box background
            lines.append(
                f'  <rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" '
                f'rx="{corner_r}" fill="{CELL_EMPTY}" stroke="{BORDER}" stroke-width="0.5"/>'
            )
            # Label
            lines.append(
                f'  <text x="{x + 10}" y="{y + 18}" fill="{LABEL}" '
                f'font-size="8" font-family="{FONT}" letter-spacing="0.02em">{label}</text>'
            )
            # Value
            font_size = "14" if len(value) > 8 else "16"
            lines.append(
                f'  <text x="{x + 10}" y="{y + 40}" fill="{VALUE}" '
                f'font-size="{font_size}" font-family="{FONT}" font-weight="600">{value}</text>'
            )

    return "\n".join(lines)


def render_contribution_grid(daily: dict) -> str:
    """Render a GitHub-style contribution grid (last 52 weeks)."""
    today = datetime.now()
    cell_size = 7
    cell_gap = 2
    start_x = 20
    start_y = 196
    cols = 26  # 26 weeks (half year, fits the card width)

    # Build a date→count map
    msg_by_day = {}
    for day, data in daily.items():
        msg_by_day[day] = data["user_msgs"] + data["assistant_msgs"]

    max_msgs = max(msg_by_day.values(), default=1)
    lines = []

    # Work backwards from today
    # Find the Sunday of the current week
    days_since_sunday = today.weekday() + 1 if today.weekday() != 6 else 0
    grid_end = today
    grid_start = grid_end - timedelta(days=cols * 7 - 1 + days_since_sunday)

    for week in range(cols):
        for dow in range(7):
            day_offset = week * 7 + dow
            day = grid_start + timedelta(days=day_offset)
            day_str = day.strftime("%Y-%m-%d")

            if day > today:
                continue

            x = start_x + week * (cell_size + cell_gap)
            y = start_y + dow * (cell_size + cell_gap)

            msgs = msg_by_day.get(day_str, 0)
            if msgs == 0:
                color = CELL_EMPTY
            else:
                pct = msgs / max_msgs
                if pct <= 0.25:
                    color = BLUES[1]
                elif pct <= 0.50:
                    color = BLUES[2]
                elif pct <= 0.75:
                    color = BLUES[3]
                else:
                    color = BLUES[4]

            lines.append(
                f'  <rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" '
                f'rx="2" fill="{color}"/>'
            )

    return "\n".join(lines)


def generate_svg(stats: dict) -> str:
    s = stats["summary"]
    stat_boxes = render_stat_boxes(s)
    grid = render_contribution_grid(stats["daily"])
    account_label = f'{stats["accounts"]} account{"s" if stats["accounts"] > 1 else ""}'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Geist+Mono:wght@300;400;600&amp;display=swap');
    </style>
  </defs>

  <!-- Background -->
  <rect width="{W}" height="{H}" rx="12" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>

  <!-- Header -->
  <text x="20" y="34" fill="{VALUE}" font-size="14" font-family="{FONT}" font-weight="600">claude code</text>
  <text x="20" y="50" fill="{LABEL}" font-size="9" font-family="{FONT}">{account_label}</text>

  <!-- Stat boxes -->
{stat_boxes}

  <!-- Contribution grid -->
  <text x="20" y="188" fill="{LABEL}" font-size="8" font-family="{FONT}" letter-spacing="0.05em">ACTIVITY</text>
{grid}
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
    print(f"\n{s['total_sessions']} sessions | {fmt_num(s['total_messages'])} messages | "
          f"{fmt_num(s['total_tokens'])} tokens | {s['active_days']} days | "
          f"{s['favorite_model']}")


if __name__ == "__main__":
    main()
