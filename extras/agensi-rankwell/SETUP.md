# Set up the Rankwell MCP server

Rankwell runs locally on macOS or Linux. It needs [uv](https://docs.astral.sh/uv/) (`brew install uv`, or
`curl -LsSf https://astral.sh/uv/install.sh | sh`) and, for
iOS builds and uploads, the Xcode command line tools (`xcode-select --install`).

## Claude Code (recommended)
Install it as a plugin; this also adds the daily collection catch-up and session-start nudges:

    claude plugin marketplace add AkashShahriar55/rankwell
    claude plugin install rankwell@rankwell

Restart Claude Code and say "Set up Rankwell for my app". You can skip the rest of this file.

## Cursor, Claude Desktop, Windsurf and other MCP clients
1. Install the server as a command:

       uv tool install "git+https://github.com/AkashShahriar55/rankwell@v0.2.2"

   This adds `rankwell` to `~/.local/bin`. Check it with `rankwell doctor`.

2. Add the server to your client's MCP config. `config/mcp.json` in this folder has the block to copy:
   - Cursor: `~/.cursor/mcp.json`
   - Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windsurf: `~/.codeium/windsurf/mcp_config.json`

   If your client cannot find `rankwell`, use the full path: run `which rankwell` and put it in `command`.

3. Restart the client, then ask your agent: "Set up Rankwell for my app" (it follows
   `playbooks/aso-setup/SKILL.md`).

## App Store Connect key (optional, recommended)
Public data (ranks, competitors, keywords) works without a key. Analytics, reviews, listing changes and
releases need one with the App Manager role: App Store Connect → Users and Access → Integrations → App Store
Connect API → Team Keys → "+". Then either tell your agent the path of the `.p8` file and your issuer id (it
calls `asc_key_save`, which reads the file itself), or run:

    rankwell init --asc-key ~/Downloads/AuthKey_XXXXXXXXXX.p8 --issuer-id <issuer id>

The key is stored in the system keyring (Keychain on macOS, GNOME Keyring or KWallet on Linux). Never paste its content into a chat.

## Daily collection
    rankwell schedule install --at 11:00

Installs a daily job (launchd on macOS, a systemd timer or cron on Linux) that tracks ranks, listings, reviews, charts and analytics every day.

## Update
    uv tool upgrade rankwell

Or reinstall with a newer tag from https://github.com/AkashShahriar55/rankwell/releases
