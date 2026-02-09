# Calendar App with AI Assistant

An Outlook-style calendar application with month/week views, event management, and an AI chat assistant powered by Ollama.

![Calendar App](CalendarWindow.png)

---

## Features

- **Month View**: Grid calendar with event pills, click to select, double-click to add
- **Week View**: Time-based view (6AM-10PM) with positioned events
- **Event Management**: Create, edit, delete events with categories
- **Conflict Detection**: Warns when events overlap
- **AI Chat Assistant**: Natural language scheduling via Ollama LLM
- **Smart Query Processing**: Date queries handled without LLM for speed and accuracy
- **Persistent Storage**: Events saved to JSON file

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Calendar UI    │────▶│  calendar_server │────▶│   Ollama    │
│  (React/TSX)    │◀────│  (FastAPI)       │◀────│  (qwen2.5)  │
└─────────────────┘     └──────────────────┘     └─────────────┘
                               │
                               ▼
                        ┌──────────────┐
                        │ events.json  │
                        └──────────────┘
```

---

## Prerequisites

### 1. Python 3.10+

Download from [python.org](https://www.python.org/downloads/)

Verify installation:
```bash
python --version
```

### 2. Ollama (for AI features)

Download from [ollama.com](https://ollama.com/download)

**Windows:**
```bash
winget install Ollama.Ollama
```

**macOS:**
```bash
brew install ollama
```

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

---

## Installation

### Step 1: Clone/Navigate to the App

```bash
cd C:\Users\Munim\ContextUI\default\workflows\examples\CalendarApp
```

### Step 2: Create Python Virtual Environment

```bash
# Create venv
python -m venv venv

# Activate venv (Windows Command Prompt)
venv\Scripts\activate.bat

# Activate venv (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Activate venv (Linux/macOS)
source venv/bin/activate
```

**PowerShell Execution Policy Error?**
Run as Administrator:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Step 3: Install Python Dependencies

```bash
pip install -r requirements.txt
```

Or manually:
```bash
pip install fastapi uvicorn httpx pydantic
```

### Step 4: Pull the LLM Model

```bash
# Start Ollama service (if not running)
ollama serve

# Pull the model (in another terminal)
ollama pull qwen2.5:3b
```

---

## LLM Model Options

| Model | Size | RAM Needed | Quality | Command |
|-------|------|------------|---------|---------|
| `qwen2.5:0.5b` | ~400MB | 2GB | Basic | `ollama pull qwen2.5:0.5b` |
| `qwen2.5:1.5b` | ~1GB | 4GB | Good | `ollama pull qwen2.5:1.5b` |
| **`qwen2.5:3b`** | ~2GB | 6GB | **Recommended** | `ollama pull qwen2.5:3b` |
| `qwen2.5:7b` | ~4.5GB | 10GB | Best | `ollama pull qwen2.5:7b` |

**Default model:** `qwen2.5:3b`

To change the model, edit `calendar_server.py` line ~47:
```python
OLLAMA_MODEL = "qwen2.5:3b"  # Change this
```

---

## Running the App

### Option 1: Through ContextUI (Recommended)

1. Open ContextUI application
2. Find "CalendarApp" in workflows/examples
3. Click to open
4. Select your Python venv from dropdown
5. Click "Start Server"
6. Use the calendar!

### Option 2: Standalone (Command Line)

**Terminal 1 - Start Ollama:**
```bash
ollama serve
```

**Terminal 2 - Start Calendar Server:**
```bash
cd C:\Users\Munim\ContextUI\default\workflows\examples\CalendarApp

# Activate venv (Windows)
venv\Scripts\activate.bat

# Start server
python calendar_server.py 8767
```

You should see:
```
Loaded 0 events from ...\data\events.json
Ollama available: True
Starting Calendar App server on port 8767...
```

---

## Usage

### Calendar Navigation

| Action | How |
|--------|-----|
| Previous month/week | Click `<` button |
| Next month/week | Click `>` button |
| Go to today | Click `Today` button |
| Switch view | Click `Month` or `Week` |

### Event Management

| Action | How |
|--------|-----|
| Create event | Click `+ Event` or double-click a day |
| Edit event | Click on an event pill |
| Delete event | Open event → click `Delete` |
| View conflicts | Shown in red when saving overlapping events |

### Event Categories

| Category | Color |
|----------|-------|
| Work | Blue |
| Personal | Green |
| Health | Red |
| Meeting | Purple |
| Reminder | Amber |
| Other | Gray |

### AI Chat Assistant

The chat panel on the right lets you manage your calendar with natural language.

**Query Examples:**
```
"What's on my calendar today?"
"Any events tomorrow?"
"Am I free on Friday?"
"What do I have this week?"
"Show me March"
```

**Add Events:**
```
"Add a meeting with John tomorrow at 2pm"
"Schedule dentist appointment on Friday at 10am for 45 minutes"
"Create a gym session Monday at 6pm"
```

**Delete Events:**
```
"Delete the meeting tomorrow"
"Remove the gym event"
"Cancel my dentist appointment"
```

**Update Events:**
```
"Change the meeting time to 3pm"
"Move my dentist appointment to Tuesday"
```

---

## API Reference

Base URL: `http://127.0.0.1:8767`

### Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/status` | Server status + Ollama availability |

### Events

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/events` | List all events |
| GET | `/events?date=YYYY-MM-DD` | List events for specific date |
| POST | `/events` | Create event |
| GET | `/events/{id}` | Get single event |
| PUT | `/events/{id}` | Update event |
| DELETE | `/events/{id}` | Delete event |

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat` | Send message (non-streaming) |
| POST | `/chat/stream` | Send message (streaming) |
| POST | `/chat/clear` | Clear chat history |
| POST | `/chat/parse` | Debug: test date parsing |

### Example API Calls

```bash
# Check status
curl http://127.0.0.1:8767/status

# Create event
curl -X POST http://127.0.0.1:8767/events \
  -H "Content-Type: application/json" \
  -d '{"title": "Team Meeting", "date": "2026-02-05", "time": "14:00", "duration": 60, "category": "meeting"}'

# List events
curl http://127.0.0.1:8767/events

# Chat with AI
curl -X POST http://127.0.0.1:8767/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What events do I have tomorrow?"}'

# Delete event
curl -X DELETE http://127.0.0.1:8767/events/EVENT_ID
```

---

## File Structure

```
CalendarApp/
├── CalendarWindow.tsx      # React frontend component
├── CalendarWindow.meta.json # Icon/color metadata
├── CalendarWindow.png      # Preview image
├── calendar_server.py      # FastAPI backend server
├── requirements.txt        # Python dependencies
├── description.txt         # App description
├── README.md              # This file
└── data/
    └── events.json        # Persistent event storage
```

---

## How the AI Works

### Smart Pre-Processing

Date-related queries are handled **without calling the LLM** for speed and accuracy:

```
User: "Any events in March?"
         │
         ▼
┌─────────────────────────────────────────┐
│ PRE-PROCESSOR (No LLM needed!)          │
│ 1. Detects "March" → month query        │
│ 2. Calculates dates: 2026-03-01 to 31   │
│ 3. Searches events.json                 │
│ 4. Returns: "No events for March 2026"  │
└─────────────────────────────────────────┘
```

**Supported without LLM:**
- today, tomorrow, yesterday
- Weekdays (Monday, Tuesday, etc.)
- this week, next week
- Month names (January, February, etc.)
- Specific dates (February 5, 5th of March)

### LLM Actions

The LLM is used for:
- Adding events from natural language
- Deleting events
- Updating events
- Complex conversational queries

**Action Format:**
```
ACTION: ADD_EVENT
{"title": "Meeting", "date": "2026-02-05", "time": "14:00", "duration": 60, "category": "meeting"}

ACTION: DELETE_EVENT
{"id": "abc123"}

ACTION: UPDATE_EVENT
{"id": "abc123", "title": "New Title", "time": "15:00"}
```

---

## Troubleshooting

### "Ollama not available"

1. Make sure Ollama is installed
2. Start Ollama: `ollama serve`
3. Pull the model: `ollama pull qwen2.5:3b`

### "Cannot activate virtual environment" (PowerShell)

Run as Administrator:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### "Module not found" errors

Make sure venv is activated and dependencies installed:
```bash
venv\Scripts\activate.bat
pip install -r requirements.txt
```

### Events not persisting

Check that `data/events.json` exists and is writable:
```bash
dir data\events.json
```

### LLM giving wrong answers

- Use a larger model (`qwen2.5:3b` or `qwen2.5:7b`)
- The pre-processor handles most date queries accurately without LLM

### Port already in use

Change the port:
```bash
python calendar_server.py 8768
```

---

## Dependencies

### Python Packages

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | ≥0.104.0 | Web framework |
| uvicorn | ≥0.24.0 | ASGI server |
| httpx | ≥0.25.0 | HTTP client for Ollama |
| pydantic | ≥2.0.0 | Data validation |

### External Services

| Service | Purpose | Required |
|---------|---------|----------|
| Ollama | Local LLM inference | For AI chat features |

---

## License

This project is part of ContextUI examples.

---

## Quick Start Summary

```bash
# 1. Install Ollama and pull model
ollama pull qwen2.5:3b

# 2. Setup Python environment
cd CalendarApp
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt

# 3. Start Ollama (Terminal 1)
ollama serve

# 4. Start Calendar Server (Terminal 2)
python calendar_server.py 8767

# 5. Open in browser or use via ContextUI
# API available at http://127.0.0.1:8767
```
