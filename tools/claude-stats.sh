#!/usr/bin/env bash
# claude-stats — aggregate Claude Code session data into stats JSON + SVG card
# Supports multiple Claude config directories for multi-account aggregation
#
# Usage:
#   ./claude-stats.sh [--dir ~/.claude] [--dir /path/to/other/.claude] [--out ./graph]
#
# Output:
#   <out>/claude-stats.json   — raw aggregated stats
#   <out>/claude-card.svg     — embeddable SVG card for GitHub README

set -euo pipefail

DIRS=()
OUT_DIR="."

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIRS+=("$2"); shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Default to ~/.claude if no dirs specified
if [[ ${#DIRS[@]} -eq 0 ]]; then
  DIRS=("$HOME/.claude")
fi

mkdir -p "$OUT_DIR"

# Aggregate all session data with Python
python3 - "${DIRS[@]}" "$OUT_DIR" << 'PYEOF'
import json, sys, os, glob
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

dirs = sys.argv[1:-1]
out_dir = sys.argv[-1]

sessions_per_day = defaultdict(lambda: {"sessions": 0, "user_msgs": 0, "assistant_msgs": 0, "minutes": 0})
projects = defaultdict(lambda: {"sessions": 0, "messages": 0, "minutes": 0})
total_sessions = 0
all_days = set()

for claude_dir in dirs:
    claude_dir = os.path.expanduser(claude_dir)

    # Parse history.jsonl for session/project mapping
    history_file = os.path.join(claude_dir, "history.jsonl")
    session_projects = {}
    if os.path.exists(history_file):
        with open(history_file) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    sid = d.get("sessionId", "")
                    proj = d.get("project", "")
                    proj_name = proj.rstrip("/").split("/")[-1] if proj else "unknown"
                    if sid and proj_name:
                        session_projects[sid] = proj_name
                except:
                    pass

    # Parse all session JSONL files
    project_dirs = glob.glob(os.path.join(claude_dir, "projects", "*"))
    for proj_dir in project_dirs:
        for jsonl_path in glob.glob(os.path.join(proj_dir, "*.jsonl")):
            sid = os.path.basename(jsonl_path).replace(".jsonl", "")
            msgs = {"user": 0, "assistant": 0}
            first_ts = last_ts = None

            try:
                with open(jsonl_path) as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                            t = d.get("type", "")
                            if t in msgs:
                                msgs[t] += 1
                            ts_str = d.get("timestamp")
                            if ts_str and isinstance(ts_str, str):
                                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                if first_ts is None or ts < first_ts:
                                    first_ts = ts
                                if last_ts is None or ts > last_ts:
                                    last_ts = ts
                        except:
                            pass
            except:
                continue

            if first_ts is None:
                continue

            total_sessions += 1
            duration = (last_ts - first_ts).total_seconds() / 60 if last_ts else 0
            day = first_ts.strftime("%Y-%m-%d")
            all_days.add(day)

            sessions_per_day[day]["sessions"] += 1
            sessions_per_day[day]["user_msgs"] += msgs["user"]
            sessions_per_day[day]["assistant_msgs"] += msgs["assistant"]
            sessions_per_day[day]["minutes"] += duration

            # Resolve project name from directory path
            raw = os.path.basename(proj_dir)
            # Strip common prefixes and collapse worktrees into parent project
            proj_name = raw
            for prefix in ["-Users-coin-Documents-GitHub-", "-Users-coin-Desktop-DesktopFolders-",
                           "-Users-coin-Library-Mobile-Documents-iCloud-md-obsidian-Documents-"]:
                if proj_name.startswith(prefix):
                    proj_name = proj_name[len(prefix):]
                    break
            # Remove worktree suffixes (--claude-worktrees-*, --stoneforge--worktrees-*)
            if "--" in proj_name:
                proj_name = proj_name.split("--")[0]
            # Nested dirs like "tome-tome", "cozy-brain-cozy-brain", "k2vp-K2-compass"
            # Split on path separator encoded as "-" and take meaningful segment
            if "-" in proj_name:
                parts = proj_name.split("-")
                half = len(parts) // 2
                # Repeated halves: "tome-tome" → "tome"
                if parts[:half] == parts[half:]:
                    proj_name = "-".join(parts[:half])
                # Pattern "parent-child" where parent is a short prefix: take child
                elif len(parts) >= 2 and len(parts[0]) <= 4:
                    proj_name = "-".join(parts[1:])
            # Skip bare home directory sessions
            if proj_name in ("-Users-coin", "Users-coin", "coin", ""):
                proj_name = "misc"
            # Use history.jsonl name only if directory-based name is generic
            hist_name = session_projects.get(sid)
            if hist_name and hist_name not in ("coin", "unknown") and proj_name == "misc":
                proj_name = hist_name

            projects[proj_name]["sessions"] += 1
            projects[proj_name]["messages"] += msgs["user"] + msgs["assistant"]
            projects[proj_name]["minutes"] += duration

# Calculate streak
sorted_days = sorted(all_days)
current_streak = 0
max_streak = 0
if sorted_days:
    streak = 1
    for i in range(1, len(sorted_days)):
        d1 = datetime.strptime(sorted_days[i-1], "%Y-%m-%d")
        d2 = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        if (d2 - d1).days == 1:
            streak += 1
        else:
            max_streak = max(max_streak, streak)
            streak = 1
    max_streak = max(max_streak, streak)

    # Current streak (from most recent day backwards)
    today = datetime.now().strftime("%Y-%m-%d")
    if sorted_days[-1] == today or (datetime.now() - datetime.strptime(sorted_days[-1], "%Y-%m-%d")).days <= 1:
        current_streak = 1
        for i in range(len(sorted_days) - 2, -1, -1):
            d1 = datetime.strptime(sorted_days[i], "%Y-%m-%d")
            d2 = datetime.strptime(sorted_days[i+1], "%Y-%m-%d")
            if (d2 - d1).days == 1:
                current_streak += 1
            else:
                break

# Totals
total_user_msgs = sum(d["user_msgs"] for d in sessions_per_day.values())
total_assistant_msgs = sum(d["assistant_msgs"] for d in sessions_per_day.values())
total_minutes = sum(d["minutes"] for d in sessions_per_day.values())
total_messages = total_user_msgs + total_assistant_msgs

# Top projects
top_projects = sorted(projects.items(), key=lambda x: -x[1]["messages"])[:8]

# Build stats object
stats = {
    "generated": datetime.now(timezone.utc).isoformat(),
    "accounts": len(dirs),
    "summary": {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_user_messages": total_user_msgs,
        "total_assistant_messages": total_assistant_msgs,
        "total_minutes": round(total_minutes),
        "total_hours": round(total_minutes / 60, 1),
        "active_days": len(all_days),
        "current_streak": current_streak,
        "longest_streak": max_streak,
    },
    "daily": {day: sessions_per_day[day] for day in sorted(sessions_per_day.keys())},
    "projects": {name: data for name, data in top_projects},
}

# Write JSON
json_path = os.path.join(out_dir, "claude-stats.json")
with open(json_path, "w") as f:
    json.dump(stats, f, indent=2)
print(f"Wrote {json_path}")

# Generate SVG card
def fmt_num(n):
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)

def fmt_time(minutes):
    hours = minutes / 60
    if hours >= 24:
        return f"{hours/24:.1f}d"
    if hours >= 1:
        return f"{hours:.1f}h"
    return f"{int(minutes)}m"

# Build daily activity bars (last 30 days)
bar_data = []
sorted_all = sorted(sessions_per_day.keys())
recent_days = sorted_all[-30:] if len(sorted_all) >= 30 else sorted_all
max_msgs_day = max((sessions_per_day[d]["user_msgs"] + sessions_per_day[d]["assistant_msgs"] for d in recent_days), default=1)

for day in recent_days:
    d = sessions_per_day[day]
    msgs = d["user_msgs"] + d["assistant_msgs"]
    bar_data.append({"day": day, "height": max(2, int(msgs / max_msgs_day * 40)), "msgs": msgs})

# Project breakdown for bars
proj_bars = []
max_proj_msgs = max((d["messages"] for _, d in top_projects), default=1) if top_projects else 1
for name, data in top_projects[:5]:
    proj_bars.append({"name": name, "width": max(10, int(data["messages"] / max_proj_msgs * 180)), "msgs": data["messages"]})

card_width = 420
card_height = 280
bar_width = 8
bar_gap = 3
bars_start_x = 20
bars_y = 145

activity_bars_svg = ""
for i, bar in enumerate(bar_data):
    x = bars_start_x + i * (bar_width + bar_gap)
    h = bar["height"]
    y = bars_y - h
    opacity = 0.4 + (h / 42) * 0.6
    activity_bars_svg += f'    <rect x="{x}" y="{y}" width="{bar_width}" height="{h}" rx="2" fill="#39d353" opacity="{opacity:.2f}"/>\n'

project_rows_svg = ""
for i, proj in enumerate(proj_bars):
    y = 170 + i * 20
    project_rows_svg += f'    <rect x="20" y="{y}" width="{proj["width"]}" height="12" rx="3" fill="#39d353" opacity="{0.3 + i * 0.12:.2f}"/>\n'
    project_rows_svg += f'    <text x="{proj["width"] + 28}" y="{y + 10}" fill="#c9d1d9" font-size="10" font-family="monospace">{proj["name"]} ({fmt_num(proj["msgs"])})</text>\n'

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{card_width}" height="{card_height}" viewBox="0 0 {card_width} {card_height}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0d1117"/>
      <stop offset="100%" stop-color="#161b22"/>
    </linearGradient>
  </defs>
  <rect width="{card_width}" height="{card_height}" rx="12" fill="url(#bg)" stroke="#30363d" stroke-width="1"/>

  <!-- Title -->
  <text x="20" y="32" fill="#c9d1d9" font-size="14" font-family="monospace" font-weight="600">claude code</text>
  <text x="20" y="48" fill="#6e7681" font-size="10" font-family="monospace">{stats["summary"]["active_days"]} active days across {stats["accounts"]} account{"s" if stats["accounts"] > 1 else ""}</text>

  <!-- Stats row -->
  <text x="20" y="75" fill="#6e7681" font-size="9" font-family="monospace" text-transform="uppercase" letter-spacing="1">sessions</text>
  <text x="20" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{fmt_num(total_sessions)}</text>

  <text x="110" y="75" fill="#6e7681" font-size="9" font-family="monospace">messages</text>
  <text x="110" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{fmt_num(total_messages)}</text>

  <text x="210" y="75" fill="#6e7681" font-size="9" font-family="monospace">active time</text>
  <text x="210" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{fmt_time(total_minutes)}</text>

  <text x="320" y="75" fill="#6e7681" font-size="9" font-family="monospace">streak</text>
  <text x="320" y="92" fill="#ffffff" font-size="18" font-family="monospace" font-weight="600">{current_streak}d</text>

  <!-- Activity bars -->
  <text x="20" y="115" fill="#6e7681" font-size="9" font-family="monospace">recent activity</text>
{activity_bars_svg}
  <!-- Projects -->
{project_rows_svg}
</svg>'''

svg_path = os.path.join(out_dir, "claude-card.svg")
with open(svg_path, "w") as f:
    f.write(svg)
print(f"Wrote {svg_path}")
print(f"\nStats: {total_sessions} sessions, {fmt_num(total_messages)} messages, {fmt_time(total_minutes)} active, {len(all_days)} days")
PYEOF

echo "Done."
