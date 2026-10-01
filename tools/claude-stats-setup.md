# claude-stats aggregator setup

One-time setup for keeping `graph/claude-card.svg` fresh using session data
from all three Macs.

## Layout

- **Aggregator**: Mac Studio. Runs the script daily via launchd.
- **Sources**: work MacBook Pro, personal MacBook Pro. Push their session
  data to the aggregator over Syncthing.

Both `~/.claude/projects/` (CLI session JSONL) and
`~/Library/Application Support/Claude/claude-code-sessions/` (desktop app
metadata) need to be synced from each source.

## 1. Syncthing (on all three Macs)

Install Syncthing (`brew install --cask syncthing`) on every machine and
launch it. On **work laptop** and **personal laptop**, create a send-only
folder for each path below, with the **Mac Studio** as the only peer:

| Folder ID (suggestion)         | Path on source                                                     |
| ------------------------------ | ------------------------------------------------------------------ |
| `claude-cli-<hostname>`        | `~/.claude`                                                        |
| `claude-desktop-<hostname>`    | `~/Library/Application Support/Claude/claude-code-sessions`        |

On the **Mac Studio**, accept each folder as receive-only and pin them to:

```
~/synced/work-laptop/.claude
~/synced/work-laptop/claude-code-sessions
~/synced/personal-laptop/.claude
~/synced/personal-laptop/claude-code-sessions
```

Those are the paths baked into the plist's default `CLAUDE_DIRS` /
`DESKTOP_DIRS`. If you use different paths, edit the plist before
installing.

Tip: in Syncthing, add `ignore` patterns for anything you don't need
(e.g. `shell-snapshots/`, `statsig/`, `todos/`) to keep the sync small.
The script only reads `projects/*.jsonl` and `*.json.backup.*`.

## 2. Install the launchd agent (Mac Studio only)

```sh
REPO_DIR="$HOME/Documents/GitHub/vxcozy"   # adjust if yours differs

# Substitute placeholders and install
sed \
  -e "s|__REPO_DIR__|$REPO_DIR|g" \
  -e "s|__HOME__|$HOME|g" \
  "$REPO_DIR/tools/com.vxcozy.claude-stats.plist" \
  > ~/Library/LaunchAgents/com.vxcozy.claude-stats.plist

launchctl unload ~/Library/LaunchAgents/com.vxcozy.claude-stats.plist 2>/dev/null || true
launchctl load   ~/Library/LaunchAgents/com.vxcozy.claude-stats.plist
```

Test it immediately without waiting for 05:30:

```sh
launchctl start com.vxcozy.claude-stats
tail -f ~/Library/Logs/claude-stats.log
```

## 3. Verify

- `graph/claude-stats.json` and `graph/claude-card.svg` should have a
  fresh `git log` entry on `main`.
- The README "claude code" card at the top of your GitHub profile should
  reflect the latest activity within ~1 day.

## Adjusting

- **Different sync layout?** Edit `CLAUDE_DIRS` / `DESKTOP_DIRS` in the
  installed plist (colon-separated list of paths) and reload:
  `launchctl unload && launchctl load`.
- **Run manually anytime**: `bash tools/update-claude-stats.sh`
- **Stop the daily run**: `launchctl unload ~/Library/LaunchAgents/com.vxcozy.claude-stats.plist`
