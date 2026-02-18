# Aion

**AI-powered calendar agent for Google Calendar.**

Schedule, list, reschedule, and find free time — all from natural language in your terminal.

```
  aion > schedule gym tomorrow morning
  Finding optimal slot for 'gym'...
  Schedule 'gym' on February 19, 2026 at 07:00 for 60 min? [y/n]: y
  ✔ Created! 'gym' on 2026-02-19 at 07:00
```

Part of [A.U.R.A](https://github.com/munimdev) (Autonomous Unified Reasoning Assistant).

---

## Features

- **Natural language** — "schedule dentist friday at 2pm for 45 min", "what's on tomorrow?"
- **Smart scheduling** — ASP/Clingo constraint solver finds optimal slots avoiding conflicts
- **Google Calendar sync** — reads and writes real events via Calendar API v3
- **Conflict detection** — warns on overlaps, offers alternatives
- **User preferences** — block time slots (lunch, sleep), set default morning/afternoon/evening
- **Timezone-aware** — auto-detects your timezone from Google Calendar on login
- **Ollama NLU** (optional) — local LLM fallback for complex commands, auto-installs on first run

---

## Architecture

```
User Input
    │
    ▼
┌──────────────┐    ┌──────────────┐
│ Regex NLU    │───▶│ Ollama LLM   │  (optional fallback)
│ (intent.py)  │    │ (ollama.py)  │
└──────┬───────┘    └──────────────┘
       │
       ▼
┌──────────────┐    ┌──────────────┐
│ ASP Solver   │───▶│    Clingo    │  (constraint solving)
│ (solver.py)  │    │              │
└──────┬───────┘    └──────────────┘
       │
       ▼
┌──────────────┐
│ Google Cal   │  (httpx async)
│ (google_cal) │
└──────────────┘
```

---

## Prerequisites

- **Python 3.10+**
- **Google Cloud project** with Calendar API enabled and OAuth 2.0 credentials (Desktop app type)

---

## Installation

```bash
# Clone the repo
git clone https://github.com/munimdev/Aion.git
cd Aion

# Install (editable mode)
pip install -e .

# Or with dev tools (pytest, ruff)
pip install -e ".[dev]"
```

This installs the `aion` command globally.

---

## Setup

### 1. Google Calendar credentials

Create OAuth 2.0 credentials in [Google Cloud Console](https://console.cloud.google.com/apis/credentials) (application type: Desktop app).

Set them as environment variables or in `~/.aion/config.json`:

```bash
# Environment variables
export AION_GOOGLE_CLIENT_ID="your-client-id"
export AION_GOOGLE_CLIENT_SECRET="your-client-secret"
```

Or create `~/.aion/config.json`:

```json
{
  "google_client_id": "your-client-id",
  "google_client_secret": "your-client-secret"
}
```

### 2. Login

```bash
aion login
```

Opens your browser for Google OAuth. After login, Aion automatically detects your timezone from Google Calendar and saves it to config.

### 3. Smart understanding (optional)

On first interactive run, Aion offers to install [Ollama](https://ollama.com) for better natural language understanding. This is optional — basic regex parsing works without it.

```bash
# Or set up manually
aion setup
```

---

## Usage

Start the interactive CLI:

```bash
aion
```

### Commands

| Action | Examples |
|--------|----------|
| **Schedule** | `schedule gym tomorrow morning`, `add meeting at 3pm for 90 min` |
| **List** | `what's on today?`, `show my calendar this week`, `what tomorrow?` |
| **Delete** | `cancel gym tomorrow`, `delete meeting` |
| **Update** | `move gym to 3pm`, `reschedule meeting to friday` |
| **Free slots** | `when am I free tomorrow?`, `free slots this week` |
| **Best time** | `best time for a 2h study session` |
| **Preferences** | `preferences` — manage blocked times and defaults |
| **Login/Logout** | `login`, `logout` |
| **Help** | `help` |
| **Quit** | `quit` or `exit` |

### Preferences

Block recurring time slots and set defaults:

```
aion > preferences
  ┌─────────────────────────────────────────────┐
  │ 1. Add a blocked time slot                  │
  │ 2. Remove a blocked slot                    │
  │ 3. Change default time preference           │
  │ 4. Back                                     │
  └─────────────────────────────────────────────┘
```

Blocked slots (e.g. lunch 12:00-13:00 on weekdays) are respected by the scheduler — it won't suggest times during those windows.

---

## Configuration

Config lives at `~/.aion/config.json`. All options can also be set via environment variables with `AION_` prefix.

| Key | Env var | Default | Description |
|-----|---------|---------|-------------|
| `google_client_id` | `AION_GOOGLE_CLIENT_ID` | — | OAuth client ID |
| `google_client_secret` | `AION_GOOGLE_CLIENT_SECRET` | — | OAuth client secret |
| `timezone` | `AION_TIMEZONE` | `UTC` | IANA timezone (auto-detected on login) |
| `default_duration` | `AION_DEFAULT_DURATION` | `60` | Default event duration in minutes |
| `ollama_url` | `AION_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `ollama_model` | `AION_OLLAMA_MODEL` | `qwen2.5:0.5b` | Ollama model for NLU |

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check aion/
```

---

## How it works

1. **Intent classification** — Regex patterns match commands (schedule, list, delete, etc.) with confidence scores. Falls back to Ollama LLM for ambiguous input.

2. **Date parsing** — Handles "today", "tomorrow", weekday names, "this/next week", specific dates like "March 5th", and common typos.

3. **Constraint solving** — The ASP/Clingo solver models the day as 30-minute slots (6AM-10PM), marks busy times from existing events and user preferences, then finds optimal placements with time-of-day preferences.

4. **Google Calendar API** — All reads/writes go through Calendar API v3 via httpx async. Token refresh is automatic.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| [clingo](https://potassco.org/clingo/) | ASP constraint solver |
| [httpx](https://www.python-httpx.org/) | Async HTTP client (Google Calendar + Ollama) |
| [rich](https://rich.readthedocs.io/) | Terminal UI |

---

## License

MIT License. See [LICENSE](LICENSE).
