# Privacy Policy — Aion

**Last updated:** February 18, 2026

Aion is an open-source calendar scheduling agent that connects to your Google Calendar.

## What data Aion accesses

Aion requests access to your Google Calendar through these scopes:

- **calendar.events** — read and create calendar events on your behalf
- **calendar.readonly** — read your calendar settings (used to detect your timezone)

## How your data is used

- All data stays **on your device**. Aion is a local CLI tool — it does not send your calendar data to any external server.
- Google Calendar data is fetched directly from Google's API to your machine.
- OAuth tokens are stored locally at `~/.aion/tokens.json` on your computer.
- No analytics, tracking, or telemetry is collected.

## What Aion does NOT do

- Does not store your calendar data on any remote server
- Does not share your data with third parties
- Does not collect usage analytics or personal information
- Does not access any Google services beyond Calendar

## Optional: Ollama LLM

If you enable the optional smart understanding feature, natural language commands are processed by a **locally-running** Ollama LLM on your machine. No data is sent to any cloud AI service.

## Data deletion

Run `aion logout` to delete your stored OAuth tokens. You can also revoke access at any time from your [Google Account permissions](https://myaccount.google.com/permissions).

## Open source

Aion's source code is publicly available at [github.com/sheikhmunim/Aion](https://github.com/sheikhmunim/Aion). You can verify exactly what data is accessed and how it is used.

## Contact

For questions or concerns, open an issue at [github.com/sheikhmunim/Aion/issues](https://github.com/sheikhmunim/Aion/issues).
