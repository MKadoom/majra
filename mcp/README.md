# majra MCP — your data, inside the AI chat

Ask questions about your own marketing numbers in Claude, ChatGPT or any
MCP-capable client, and have the model query the live warehouse instead of you
exporting a CSV and pasting it in.

This is majra's answer to Windsor.ai's MCP, with one difference that matters:
the data never leaves your instance. The server runs on your machine and talks
to your own majra over HTTPS.

## What it can do

| Tool | Answers |
|---|---|
| `get_ad_metrics` | Paid performance blended across Meta, Google Ads, TikTok, Snapchat — spend, impressions, clicks, conversions, value. Group by platform, campaign or date. |
| `get_web_metrics` | GA4 sessions/users/conversions by source, medium, campaign. |
| `get_social_metrics` | Organic Facebook Pages + Instagram reach and engagement. |
| `list_accounts` | Which accounts and platforms are actually connected. |
| `get_tag_snapshot` | Latest GTM containers, tags, triggers, variables. |

**It is read-only by construction.** Every tool maps to a majra endpoint that
accepts an API key, and those endpoints cannot create connections, start syncs,
or touch an ad platform. An AI chat wired to this can report on your spend; it
cannot spend.

## Setup

**1. Create an API key** in majra → **Settings → API keys**. Copy it (`mk_…`),
it is shown once.

**2. Install:**

```bash
pip install -r mcp/requirements.txt
```

**3. Point your client at it.** For Claude Desktop, edit
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "majra": {
      "command": "python",
      "args": ["/absolute/path/to/majra/mcp/majra_mcp.py"],
      "env": {
        "MAJRA_URL": "https://your-majra.up.railway.app",
        "MAJRA_API_KEY": "mk_..."
      }
    }
  }
}
```

Restart the client. You should see the `majra` tools appear.

## Questions worth asking

- "What did we spend on each platform last month, and which had the best ROAS?"
- "Compare Meta and Snapchat cost per conversion over the last 30 days."
- "Which campaigns spent money but produced no conversions?"
- "Did our GA4 sessions move with ad spend last week?"
- "Show me spend by country for Snapchat this month."

The model picks the right tool and grouping. Asking it to *group by platform*
rather than pull every row keeps answers fast and accurate.

## How it reaches your instance

The browser dashboard talks to the backend through `/api/proxy` (session
cookie). This server uses the other door: `/v1/*` on your public domain, which
passes `Authorization: Bearer <key>` straight through to the backend. Same
surface a Looker Studio or Sheets connector would use.

If `MAJRA_URL` or `MAJRA_API_KEY` is missing the server exits immediately with a
message, rather than failing on your first question.
