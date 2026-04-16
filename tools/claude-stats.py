#!/usr/bin/env python3
"""
claude-stats — Aggregate Claude Code session data into a stats JSON and SVG card.

Reads from two data sources:
  1. ~/.claude/projects/ — full session JSONL with message and token counts
  2. ~/Library/Application Support/Claude/claude-code-sessions/ — desktop app
     session metadata (covers all accounts, used to fill gaps for sessions
     whose JSONL data has been purged)

Usage:
    python3 claude-stats.py                                    # defaults
    python3 claude-stats.py --dir ~/.claude --out ./graph
    python3 claude-stats.py --dir ~/.claude --dir /path/.claude --out ./graph
"""

import argparse
import json
import os
import glob
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path


DESKTOP_SESSIONS_DIR = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions"
)


def count_desktop_accounts() -> int:
    """Count distinct accounts from the desktop app session directory."""
    if not os.path.isdir(DESKTOP_SESSIONS_DIR):
        return 1
    accounts = [
        d for d in os.listdir(DESKTOP_SESSIONS_DIR)
        if os.path.isdir(os.path.join(DESKTOP_SESSIONS_DIR, d))
        and not d.startswith(".")
    ]
    return max(len(accounts), 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate Claude Code session stats")
    parser.add_argument("--dir", dest="dirs", action="append", default=[],
                        help="Claude config directory (repeatable)")
    parser.add_argument("--out", default=".", help="Output directory")
    args = parser.parse_args()
    if not args.dirs:
        args.dirs = [os.path.expanduser("~/.claude")]
    return args


# ---------------------------------------------------------------------------
# Session parsing — full JSONL data
# ---------------------------------------------------------------------------

def parse_session_jsonl(jsonl_path: str) -> dict | None:
    """Parse a session JSONL file for full metrics."""
    user_msgs = 0
    assistant_msgs = 0
    input_tokens = 0
    output_tokens = 0
    first_ts = None
    last_ts = None
    models: dict[str, int] = defaultdict(int)
    hours: dict[int, int] = defaultdict(int)

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

    return {
        "source": "jsonl",
        "user_msgs": user_msgs,
        "assistant_msgs": assistant_msgs,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_min": (last_ts - first_ts).total_seconds() / 60 if last_ts else 0,
        "day": first_ts.strftime("%Y-%m-%d"),
        "models": dict(models),
        "hours": dict(hours),
        "cli_session_id": Path(jsonl_path).stem,
    }


# ---------------------------------------------------------------------------
# Session parsing — desktop app metadata (supplementary)
# ---------------------------------------------------------------------------

def parse_desktop_sessions() -> list[dict]:
    """Parse desktop app session metadata for all accounts."""
    if not os.path.isdir(DESKTOP_SESSIONS_DIR):
        return []

    sessions = []
    seen_cli_ids: set[str] = set()

    for meta_path in glob.glob(os.path.join(DESKTOP_SESSIONS_DIR, "*", "*", "*.json")):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        cli_id = meta.get("cliSessionId", "")
        created = meta.get("createdAt", 0)
        last_activity = meta.get("lastActivityAt", created)
        model = meta.get("model", "")
        turns = meta.get("completedTurns", 0)

        if not created or not cli_id:
            continue
        if cli_id in seen_cli_ids:
            continue
        seen_cli_ids.add(cli_id)

        created_dt = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
        last_dt = datetime.fromtimestamp(last_activity / 1000, tz=timezone.utc)
        duration_min = (last_dt - created_dt).total_seconds() / 60

        # Estimate messages from completed turns (each turn ≈ 1 user + 1 assistant)
        estimated_msgs = turns * 2

        # Resolve project from originCwd (safer than cwd which may be a worktree)
        origin_cwd = meta.get("originCwd", meta.get("cwd", ""))
        proj = origin_cwd.rstrip("/").split("/")[-1] if origin_cwd else "misc"

        sessions.append({
            "source": "desktop_meta",
            "cli_session_id": cli_id,
            "user_msgs": turns,
            "assistant_msgs": turns,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_min": max(duration_min, 0),
            "day": created_dt.strftime("%Y-%m-%d"),
            "models": {model: turns} if model else {},
            "hours": {created_dt.hour: 1, last_dt.hour: 1} if created_dt.hour != last_dt.hour else {created_dt.hour: 1},
            "project": proj,
        })

    return sessions


# ---------------------------------------------------------------------------
# Aggregation — merge JSONL + desktop metadata, dedup by cli_session_id
# ---------------------------------------------------------------------------

def resolve_project_from_path(proj_dir_name: str) -> str:
    """Extract a clean project name from an encoded Claude project directory name."""
    name = proj_dir_name
    for prefix in [
        "-Users-coin-Documents-GitHub-",
        "-Users-coin-Desktop-DesktopFolders-",
        "-Users-coin-Library-Mobile-Documents-iCloud-md-obsidian-Documents-",
    ]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if "--" in name:
        name = name.split("--")[0]
    if "-" in name:
        parts = name.split("-")
        half = len(parts) // 2
        if half > 0 and parts[:half] == parts[half:]:
            name = "-".join(parts[:half])
    if name in ("-Users-coin", "Users-coin", "coin", ""):
        return "misc"
    return name


def aggregate(dirs: list[str]) -> dict:
    sessions_by_id: dict[str, dict] = {}

    # 1. Parse full JSONL sessions (these have the best data)
    for claude_dir in dirs:
        claude_dir = os.path.expanduser(claude_dir)
        for jsonl_path in glob.glob(os.path.join(claude_dir, "projects", "*", "*.jsonl")):
            if "/subagents/" in jsonl_path:
                continue
            session = parse_session_jsonl(jsonl_path)
            if session is None:
                continue
            # Resolve project from parent directory name
            proj_dir = os.path.basename(os.path.dirname(jsonl_path))
            session["project"] = resolve_project_from_path(proj_dir)
            sessions_by_id[session["cli_session_id"]] = session

    # 2. Fill in gaps from desktop metadata (only for sessions not already parsed)
    for desktop_session in parse_desktop_sessions():
        cli_id = desktop_session["cli_session_id"]
        if cli_id not in sessions_by_id:
            sessions_by_id[cli_id] = desktop_session

    # 3. Count distinct accounts from desktop app (all accounts, not just gap-fill)
    num_accounts = count_desktop_accounts()

    # 4. Aggregate into daily buckets + project buckets
    daily = defaultdict(lambda: {
        "sessions": 0, "user_msgs": 0, "assistant_msgs": 0,
        "input_tokens": 0, "output_tokens": 0, "minutes": 0,
    })
    models: dict[str, int] = defaultdict(int)
    hours: dict[int, int] = defaultdict(int)
    project_tokens: dict[str, int] = defaultdict(int)

    for session in sessions_by_id.values():
        day = session["day"]
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

        proj = session.get("project", "misc")
        project_tokens[proj] += session["input_tokens"] + session["output_tokens"]

    all_days = set(daily.keys())
    current_streak, longest_streak = compute_streaks(sorted(all_days))
    total_sessions = sum(d["sessions"] for d in daily.values())
    total_messages = sum(d["user_msgs"] + d["assistant_msgs"] for d in daily.values())
    total_tokens = sum(d["input_tokens"] + d["output_tokens"] for d in daily.values())

    peak_hour = max(hours, key=hours.get) if hours else 0
    favorite_model = max(models, key=models.get) if models else "n/a"

    # Top 3 projects by token spend (privacy-filtered in SVG rendering)
    top_projects = sorted(project_tokens.items(), key=lambda x: -x[1])[:3]

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "accounts": num_accounts,
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
        "top_projects": top_projects,
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
# SVG card
#
# WCAG AA on #0d1117:
#   Labels: #8b949e (4.7:1)  Values: #e6edf3 (13.5:1)
# ---------------------------------------------------------------------------

W = 480
H = 380
BG = "#0d1117"
BORDER = "#30363d"
LABEL = "#8b949e"
VALUE = "#e6edf3"
CELL_EMPTY = "#161b22"
GREENS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
FONT = "Geist Mono, monospace"


def get_public_repos() -> set[str]:
    """Fetch public repo names via gh CLI. Returns empty set on failure."""
    import subprocess
    try:
        result = subprocess.run(
            ["gh", "api", "users/vxcozy/repos", "--jq", ".[].name", "--paginate"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return set(result.stdout.strip().split("\n"))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return set()


def render_project_bars(top_projects: list[tuple[str, int]], public_repos: set[str]) -> str:
    """Render top 3 projects as horizontal bars. Private repos shown as [redacted]."""
    if not top_projects:
        return ""

    max_tokens = top_projects[0][1] if top_projects else 1
    y0 = 286
    lines = []

    lines.append(
        f'  <text x="20" y="{y0 - 6}" fill="{LABEL}" font-size="8" '
        f'font-family="{FONT}" letter-spacing="0.05em">TOP PROJECTS BY TOKENS</text>'
    )

    for i, (name, tokens) in enumerate(top_projects[:3]):
        y = y0 + i * 26 + 4
        bar_max_w = 220
        bar_w = max(12, int(tokens / max_tokens * bar_max_w))
        intensity = 0.6 - i * 0.12

        # Privacy: only show name if it matches a public repo
        display_name = name if name.lower() in {r.lower() for r in public_repos} else "[redacted]"

        # Bar
        lines.append(
            f'  <rect x="20" y="{y}" width="{bar_w}" height="14" '
            f'rx="4" fill="{GREENS[4]}" opacity="{intensity:.2f}"/>'
        )
        # Label
        lines.append(
            f'  <text x="{bar_w + 30}" y="{y + 11}" fill="{LABEL}" '
            f'font-size="10" font-family="{FONT}">{display_name}</text>'
        )
        # Token count (right-aligned)
        lines.append(
            f'  <text x="{W - 20}" y="{y + 11}" fill="{LABEL}" '
            f'font-size="9" font-family="{FONT}" text-anchor="end" '
            f'opacity="0.7">{fmt_num(tokens)} tkns</text>'
        )

    return "\n".join(lines)


def render_stat_boxes(summary: dict) -> str:
    box_w = 105
    box_h = 52
    gap = 8
    x0 = 20
    row1_y = 62
    row2_y = row1_y + box_h + gap
    r = 8

    rows = [
        [
            ("Sessions", fmt_num(summary["total_sessions"])),
            ("Messages", fmt_num(summary["total_messages"])),
            ("Total tokens", fmt_num(summary["total_tokens"])),
            ("Active days", str(summary["active_days"])),
        ],
        [
            ("Current streak", f'{summary["current_streak"]}d'),
            ("Longest streak", f'{summary["longest_streak"]}d'),
            ("Peak hour", fmt_hour(summary["peak_hour"])),
            ("Favorite model", summary["favorite_model"].replace("claude-", "")),
        ],
    ]

    lines = []
    for row_idx, (stats, y) in enumerate([(rows[0], row1_y), (rows[1], row2_y)]):
        for i, (label, value) in enumerate(stats):
            x = x0 + i * (box_w + gap)
            lines.append(
                f'  <rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" '
                f'rx="{r}" fill="{CELL_EMPTY}" stroke="{BORDER}" stroke-width="0.5"/>'
            )
            lines.append(
                f'  <text x="{x + 10}" y="{y + 18}" fill="{LABEL}" '
                f'font-size="8" font-family="{FONT}" letter-spacing="0.02em">{label}</text>'
            )
            font_size = "14" if len(value) > 8 else "16"
            lines.append(
                f'  <text x="{x + 10}" y="{y + 40}" fill="{VALUE}" '
                f'font-size="{font_size}" font-family="{FONT}" font-weight="600">{value}</text>'
            )

    return "\n".join(lines)


def render_contribution_grid(daily: dict) -> str:
    today = datetime.now()
    cell = 7
    gap = 2
    x0 = 20
    y0 = 196
    cols = 26

    msg_by_day = {}
    for day, data in daily.items():
        msg_by_day[day] = data["sessions"]  # color by session count, not messages

    max_val = max(msg_by_day.values(), default=1)
    lines = []

    days_since_sunday = today.weekday() + 1 if today.weekday() != 6 else 0
    grid_start = today - timedelta(days=cols * 7 - 1 + days_since_sunday)

    for week in range(cols):
        for dow in range(7):
            day = grid_start + timedelta(days=week * 7 + dow)
            if day > today:
                continue

            x = x0 + week * (cell + gap)
            y = y0 + dow * (cell + gap)
            val = msg_by_day.get(day.strftime("%Y-%m-%d"), 0)

            if val == 0:
                color = CELL_EMPTY
            else:
                pct = val / max_val
                if pct <= 0.25:
                    color = GREENS[1]
                elif pct <= 0.50:
                    color = GREENS[2]
                elif pct <= 0.75:
                    color = GREENS[3]
                else:
                    color = GREENS[4]

            lines.append(
                f'  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" '
                f'rx="2" fill="{color}"/>'
            )

    return "\n".join(lines)


def generate_svg(stats: dict) -> str:
    s = stats["summary"]
    stat_boxes = render_stat_boxes(s)
    grid = render_contribution_grid(stats["daily"])
    public_repos = get_public_repos()
    project_bars = render_project_bars(stats.get("top_projects", []), public_repos)
    acct = stats["accounts"]
    subtitle = f'{s["active_days"]} active days  ·  {acct} account{"s" if acct > 1 else ""}'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <style>@import url('https://fonts.googleapis.com/css2?family=Geist+Mono:wght@300;400;600&amp;display=swap');</style>
  </defs>

  <rect width="{W}" height="{H}" rx="12" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>

  <text x="20" y="34" fill="{VALUE}" font-size="14" font-family="{FONT}" font-weight="600">claude code</text>
  <text x="20" y="50" fill="{LABEL}" font-size="9" font-family="{FONT}">{subtitle}</text>

{stat_boxes}

  <text x="20" y="188" fill="{LABEL}" font-size="8" font-family="{FONT}" letter-spacing="0.05em">ACTIVITY</text>
{grid}

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
    print(f"\n{stats['accounts']} accounts | {s['total_sessions']} sessions | "
          f"{fmt_num(s['total_messages'])} messages | {fmt_num(s['total_tokens'])} tokens | "
          f"{s['active_days']} active days | {s['favorite_model']}")


if __name__ == "__main__":
    main()
