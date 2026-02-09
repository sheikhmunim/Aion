"""
Calendar App FastAPI Server
Provides calendar management and AI chat assistant using Ollama.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, AsyncGenerator
from enum import Enum
import uvicorn
import json
import os
import asyncio
import httpx
from pathlib import Path
from datetime import datetime, timedelta
import uuid
import re
import calendar

# Import the Clingo solver
from agent.solver import ScheduleSolver

app = FastAPI(title="Calendar App Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# Data Paths
# ============================================

def get_data_path() -> Path:
    """Get the path for storing event data."""
    return Path(__file__).parent / "data"


def get_events_file() -> Path:
    """Get the events JSON file path."""
    return get_data_path() / "events.json"


# ============================================
# Pydantic Models
# ============================================

class EventCategory(str, Enum):
    WORK = "work"
    PERSONAL = "personal"
    HEALTH = "health"
    MEETING = "meeting"
    REMINDER = "reminder"
    OTHER = "other"


class Event(BaseModel):
    id: str
    title: str
    date: str          # YYYY-MM-DD
    time: str          # HH:MM
    duration: int      # minutes
    description: str
    category: EventCategory


class EventCreate(BaseModel):
    title: str
    date: str
    time: str
    duration: int = 60
    description: str = ""
    category: EventCategory = EventCategory.OTHER


class EventUpdate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    duration: Optional[int] = None
    description: Optional[str] = None
    category: Optional[EventCategory] = None


class ChatRequest(BaseModel):
    message: str


class ConflictWarning(BaseModel):
    event_id: str
    title: str
    overlap_minutes: int
    message: str


class EventCreateResponse(BaseModel):
    success: bool
    event: Optional[Event] = None
    conflicts: List[ConflictWarning] = []
    error: Optional[str] = None


class SolveRequest(BaseModel):
    """Request to find available time slots using Clingo solver."""
    activity: str
    duration: int = 60  # minutes
    count: int = 1  # number of sessions
    date: Optional[str] = None  # specific date (YYYY-MM-DD)
    days: Optional[List[str]] = None  # allowed days
    prefer_morning: bool = False
    prefer_afternoon: bool = False
    prefer_evening: bool = False
    avoid_weekends: bool = False
    working_hours_only: bool = False


class FreeSlotsRequest(BaseModel):
    """Request to find free slots on a specific date."""
    date: str  # YYYY-MM-DD
    min_duration: int = 30  # minimum slot duration in minutes


# ============================================
# Global State
# ============================================

events: Dict[str, Event] = {}
chat_history: List[Dict[str, str]] = []
ollama_available: bool = False
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"  # Use larger model for better reasoning (0.5b, 1.5b, 3b, 7b)

# Initialize the Clingo solver
schedule_solver = ScheduleSolver()


# ============================================
# Persistence Functions
# ============================================

def load_events():
    """Load events from JSON file."""
    global events
    events_file = get_events_file()

    if events_file.exists():
        try:
            with open(events_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                events = {
                    e['id']: Event(**e) for e in data.get('events', [])
                }
                print(f"Loaded {len(events)} events from {events_file}")
        except Exception as e:
            print(f"Error loading events: {e}")
            events = {}
    else:
        events = {}
        # Ensure data directory exists
        get_data_path().mkdir(parents=True, exist_ok=True)
        save_events()


def save_events():
    """Save events to JSON file."""
    events_file = get_events_file()
    get_data_path().mkdir(parents=True, exist_ok=True)

    try:
        with open(events_file, 'w', encoding='utf-8') as f:
            json.dump({
                'events': [e.model_dump() for e in events.values()]
            }, f, indent=2)
    except Exception as e:
        print(f"Error saving events: {e}")


# ============================================
# Conflict Detection
# ============================================

def time_to_minutes(time_str: str) -> int:
    """Convert HH:MM to minutes from midnight."""
    parts = time_str.split(':')
    return int(parts[0]) * 60 + int(parts[1])


def check_conflicts(new_event: EventCreate, exclude_id: Optional[str] = None) -> List[ConflictWarning]:
    """Check for time conflicts with existing events."""
    conflicts = []

    new_start = time_to_minutes(new_event.time)
    new_end = new_start + new_event.duration

    for event_id, event in events.items():
        if exclude_id and event_id == exclude_id:
            continue

        if event.date != new_event.date:
            continue

        existing_start = time_to_minutes(event.time)
        existing_end = existing_start + event.duration

        # Check for overlap
        overlap_start = max(new_start, existing_start)
        overlap_end = min(new_end, existing_end)

        if overlap_start < overlap_end:
            overlap_minutes = overlap_end - overlap_start
            conflicts.append(ConflictWarning(
                event_id=event_id,
                title=event.title,
                overlap_minutes=overlap_minutes,
                message=f"Overlaps with '{event.title}' by {overlap_minutes} minutes"
            ))

    return conflicts


# ============================================
# Date Query Pre-Processing
# ============================================

MONTH_NAMES = {
    'january': 1, 'jan': 1,
    'february': 2, 'feb': 2,
    'march': 3, 'mar': 3,
    'april': 4, 'apr': 4,
    'may': 5,
    'june': 6, 'jun': 6,
    'july': 7, 'jul': 7,
    'august': 8, 'aug': 8,
    'september': 9, 'sep': 9, 'sept': 9,
    'october': 10, 'oct': 10,
    'november': 11, 'nov': 11,
    'december': 12, 'dec': 12,
}

WEEKDAY_NAMES = {
    'sunday': 6, 'sun': 6,
    'monday': 0, 'mon': 0,
    'tuesday': 1, 'tue': 1, 'tues': 1,
    'wednesday': 2, 'wed': 2,
    'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
    'friday': 4, 'fri': 4,
    'saturday': 5, 'sat': 5,
}


def parse_date_from_query(message: str) -> dict:
    """
    Parse date references from user message.
    Returns: {type: 'date'|'month'|'week'|'range'|None, dates: [...], label: str}
    """
    message_lower = message.lower()
    today = datetime.now()
    result = {'type': None, 'dates': [], 'label': ''}

    # Check for "today"
    if 'today' in message_lower:
        result['type'] = 'date'
        result['dates'] = [today.strftime('%Y-%m-%d')]
        result['label'] = f"today ({today.strftime('%B %d, %Y')})"
        return result

    # Check for "tomorrow"
    if 'tomorrow' in message_lower:
        tomorrow = today + timedelta(days=1)
        result['type'] = 'date'
        result['dates'] = [tomorrow.strftime('%Y-%m-%d')]
        result['label'] = f"tomorrow ({tomorrow.strftime('%B %d, %Y')})"
        return result

    # Check for "yesterday"
    if 'yesterday' in message_lower:
        yesterday = today - timedelta(days=1)
        result['type'] = 'date'
        result['dates'] = [yesterday.strftime('%Y-%m-%d')]
        result['label'] = f"yesterday ({yesterday.strftime('%B %d, %Y')})"
        return result

    # Check for "this week"
    if 'this week' in message_lower:
        start_of_week = today - timedelta(days=today.weekday())
        dates = [(start_of_week + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
        result['type'] = 'week'
        result['dates'] = dates
        result['label'] = f"this week ({start_of_week.strftime('%b %d')} - {(start_of_week + timedelta(days=6)).strftime('%b %d')})"
        return result

    # Check for "next week"
    if 'next week' in message_lower:
        start_of_next_week = today + timedelta(days=(7 - today.weekday()))
        dates = [(start_of_next_week + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
        result['type'] = 'week'
        result['dates'] = dates
        result['label'] = f"next week ({start_of_next_week.strftime('%b %d')} - {(start_of_next_week + timedelta(days=6)).strftime('%b %d')})"
        return result

    # Check for specific weekday (e.g., "Monday", "on Friday")
    for day_name, day_num in WEEKDAY_NAMES.items():
        if day_name in message_lower:
            # Find next occurrence of this weekday
            days_ahead = day_num - today.weekday()
            if days_ahead <= 0:  # Target day already happened this week
                days_ahead += 7
            target_date = today + timedelta(days=days_ahead)
            result['type'] = 'date'
            result['dates'] = [target_date.strftime('%Y-%m-%d')]
            result['label'] = f"{day_name.capitalize()} ({target_date.strftime('%B %d, %Y')})"
            return result

    # Check for month names (e.g., "March", "in April")
    for month_name, month_num in MONTH_NAMES.items():
        if month_name in message_lower:
            # Determine year (current year, or next year if month already passed)
            year = today.year
            if month_num < today.month:
                year += 1

            # Get all days in that month
            num_days = calendar.monthrange(year, month_num)[1]
            dates = [f"{year}-{month_num:02d}-{d:02d}" for d in range(1, num_days + 1)]
            result['type'] = 'month'
            result['dates'] = dates
            result['label'] = f"{month_name.capitalize()} {year}"
            return result

    # Check for specific date format (e.g., "February 5", "Feb 5th", "5th February")
    date_patterns = [
        r'(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?',  # "February 5" or "February 5, 2026"
        r'(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(\w+)(?:\s*,?\s*(\d{4}))?',  # "5th of February"
    ]

    for pattern in date_patterns:
        match = re.search(pattern, message_lower)
        if match:
            groups = match.groups()
            try:
                if groups[0].isdigit():
                    day = int(groups[0])
                    month_str = groups[1]
                else:
                    month_str = groups[0]
                    day = int(groups[1])

                if month_str in MONTH_NAMES:
                    month_num = MONTH_NAMES[month_str]
                    year = int(groups[2]) if groups[2] else today.year
                    if not groups[2] and month_num < today.month:
                        year += 1

                    target_date = datetime(year, month_num, day)
                    result['type'] = 'date'
                    result['dates'] = [target_date.strftime('%Y-%m-%d')]
                    result['label'] = target_date.strftime('%B %d, %Y')
                    return result
            except (ValueError, TypeError):
                pass

    return result


def get_events_for_dates(dates: list) -> list:
    """Get all events that fall on the given dates."""
    matching_events = []
    for event in events.values():
        if event.date in dates:
            matching_events.append(event)
    # Sort by date and time
    matching_events.sort(key=lambda e: (e.date, e.time))
    return matching_events


def format_events_list(events_list: list, label: str) -> str:
    """Format a list of events into a readable string."""
    if not events_list:
        return f"No events scheduled for {label}."

    lines = [f"Events for {label}:"]
    current_date = None

    for event in events_list:
        if event.date != current_date:
            current_date = event.date
            date_obj = datetime.strptime(event.date, '%Y-%m-%d')
            lines.append(f"\n{date_obj.strftime('%A, %B %d')}:")

        lines.append(f"  - {event.time} ({event.duration}min): {event.title} [{event.category.value}]")
        if event.description:
            lines.append(f"    {event.description}")

    return "\n".join(lines)


def preprocess_query(message: str) -> dict:
    """
    Pre-process user query to detect date-related questions.
    Returns: {handled: bool, response: str|None, context: str|None}
    """
    message_lower = message.lower()

    # Detect if this is a query about schedule/events
    query_keywords = ['what', 'any', 'do i have', 'am i', 'is there', 'are there',
                      'schedule', 'events', 'free', 'busy', 'available', 'calendar',
                      'show', 'list', 'tell me']

    is_query = any(kw in message_lower for kw in query_keywords)

    if not is_query:
        return {'handled': False, 'response': None, 'context': None}

    # Parse date from query
    date_info = parse_date_from_query(message)

    if date_info['type'] is None:
        return {'handled': False, 'response': None, 'context': None}

    # Get events for those dates
    matching_events = get_events_for_dates(date_info['dates'])
    formatted = format_events_list(matching_events, date_info['label'])

    # Check if asking about availability (free/busy)
    if any(word in message_lower for word in ['free', 'available', 'busy']):
        if not matching_events:
            response = f"Yes, you're free {date_info['label']}. No events scheduled."
        else:
            response = f"You have {len(matching_events)} event(s) {date_info['label']}:\n\n{formatted}"
            response += "\n\nYou may have some busy times. Check the specific times above."
        return {'handled': True, 'response': response, 'context': None}

    # For general queries, provide pre-filtered context to LLM for nicer response
    if matching_events:
        context = f"USER IS ASKING ABOUT: {date_info['label']}\n\nEVENTS FOUND:\n{formatted}"
        return {'handled': False, 'response': None, 'context': context}
    else:
        # No events - we can answer directly
        return {'handled': True, 'response': f"No events scheduled for {date_info['label']}.", 'context': None}


# ============================================
# Ollama Integration
# ============================================

async def check_ollama() -> bool:
    """Check if Ollama is available."""
    global ollama_available
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            if response.status_code == 200:
                ollama_available = True
                return True
    except Exception:
        pass
    ollama_available = False
    return False


def format_events_for_context() -> str:
    """Format current events for LLM context."""
    if not events:
        return "No events scheduled."

    # Group events by date
    events_by_date: Dict[str, List[Event]] = {}
    for event in events.values():
        if event.date not in events_by_date:
            events_by_date[event.date] = []
        events_by_date[event.date].append(event)

    # Sort dates and format
    lines = []
    for date in sorted(events_by_date.keys()):
        date_events = sorted(events_by_date[date], key=lambda e: e.time)
        lines.append(f"\n{date}:")
        for e in date_events:
            lines.append(f"  - {e.time} ({e.duration}min): {e.title} [{e.category.value}]")
            if e.description:
                lines.append(f"    {e.description}")

    return "\n".join(lines)


def format_events_for_context_with_ids() -> str:
    """Format current events for LLM context, including IDs for delete/update."""
    if not events:
        return "No events scheduled."

    # Group events by date
    events_by_date: Dict[str, List[Event]] = {}
    for event in events.values():
        if event.date not in events_by_date:
            events_by_date[event.date] = []
        events_by_date[event.date].append(event)

    # Sort dates and format with IDs
    lines = []
    for date in sorted(events_by_date.keys()):
        date_events = sorted(events_by_date[date], key=lambda e: e.time)
        lines.append(f"\n{date}:")
        for e in date_events:
            lines.append(f"  - [ID: {e.id}] {e.time} ({e.duration}min): {e.title} [{e.category.value}]")
            if e.description:
                lines.append(f"    {e.description}")

    return "\n".join(lines)


def get_system_prompt() -> str:
    """Generate system prompt with calendar context."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")
    current_month = now.strftime("%B")
    current_year = now.year

    events_context = format_events_for_context_with_ids()

    return f"""You are a calendar assistant. Today is {weekday}, {today} ({current_month} {current_year}).

CURRENT EVENTS:
{events_context}

RULES:
- If user asks about a date/month with NO events, say "No events scheduled for [date/month]."
- Only reference events that exist in the list above.
- Each event has an ID in [brackets] - use this ID for delete/update actions.

TO ADD AN EVENT:
ACTION: ADD_EVENT
{{"title": "name", "date": "YYYY-MM-DD", "time": "HH:MM", "duration": 60, "category": "work"}}

TO DELETE AN EVENT (use the event ID from the list above):
ACTION: DELETE_EVENT
{{"id": "event_id_here"}}

TO UPDATE AN EVENT:
ACTION: UPDATE_EVENT
{{"id": "event_id_here", "title": "new title", "time": "new time"}}

Categories: work, personal, health, meeting, reminder, other

Be brief and accurate."""


def parse_llm_response(response: str) -> Dict[str, Any]:
    """Parse LLM response for actions."""
    result = {"text": response, "action": None, "action_data": None}

    # Check for different action types
    action_types = ["ADD_EVENT", "DELETE_EVENT", "UPDATE_EVENT"]

    for action_type in action_types:
        action_marker = f"ACTION: {action_type}"
        if action_marker in response:
            try:
                # Find JSON after ACTION marker
                action_idx = response.find(action_marker)
                json_start = response.find("{", action_idx)
                json_end = response.find("}", json_start) + 1

                if json_start != -1 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    action_data = json.loads(json_str)
                    result["action"] = action_type
                    result["action_data"] = action_data
                    # Remove action from text
                    result["text"] = response[:action_idx].strip()
                    break
            except json.JSONDecodeError:
                pass

    return result


async def chat_with_ollama(message: str, extra_context: str = None) -> AsyncGenerator[str, None]:
    """Stream chat response from Ollama."""
    global chat_history

    system_prompt = get_system_prompt()

    # Add extra context if provided (from pre-processing)
    if extra_context:
        system_prompt += f"\n\n--- RELEVANT CONTEXT ---\n{extra_context}\n--- END CONTEXT ---"

    # Build messages
    messages = [{"role": "system", "content": system_prompt}]

    # Add recent chat history (last 6 messages)
    for msg in chat_history[-6:]:
        messages.append(msg)

    messages.append({"role": "user", "content": message})

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": True
                }
            ) as response:
                full_response = ""
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "message" in data and "content" in data["message"]:
                                chunk = data["message"]["content"]
                                full_response += chunk
                                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
                        except json.JSONDecodeError:
                            pass

                # Update chat history
                chat_history.append({"role": "user", "content": message})
                chat_history.append({"role": "assistant", "content": full_response})

                # Keep only last 20 messages
                if len(chat_history) > 20:
                    chat_history = chat_history[-20:]

                # Parse for actions
                parsed = parse_llm_response(full_response)

                yield f"data: {json.dumps({'type': 'done', 'action': parsed['action'], 'action_data': parsed['action_data']})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"


# ============================================
# API Endpoints
# ============================================

@app.on_event("startup")
async def startup():
    """Initialize on server start."""
    load_events()
    await check_ollama()
    print(f"Ollama available: {ollama_available}")


@app.get("/")
async def root():
    return {"status": "online", "service": "Calendar App Server"}


@app.get("/status")
async def status():
    """Get server status including Ollama availability."""
    await check_ollama()
    return {
        "status": "online",
        "ollama_available": ollama_available,
        "ollama_model": OLLAMA_MODEL,
        "event_count": len(events),
        "chat_history_length": len(chat_history)
    }


# ============================================
# Event CRUD Endpoints
# ============================================

@app.get("/events")
async def list_events(date: Optional[str] = None) -> Dict[str, Any]:
    """List all events, optionally filtered by date."""
    if date:
        filtered = [e.model_dump() for e in events.values() if e.date == date]
        return {"success": True, "events": filtered, "count": len(filtered)}

    return {
        "success": True,
        "events": [e.model_dump() for e in events.values()],
        "count": len(events)
    }


@app.post("/events")
async def create_event(event_data: EventCreate) -> EventCreateResponse:
    """Create a new event with conflict detection."""
    # Check for conflicts
    conflicts = check_conflicts(event_data)

    # Create event regardless of conflicts (user can decide)
    event_id = str(uuid.uuid4())[:8]
    event = Event(
        id=event_id,
        title=event_data.title,
        date=event_data.date,
        time=event_data.time,
        duration=event_data.duration,
        description=event_data.description,
        category=event_data.category
    )

    events[event_id] = event
    save_events()

    return EventCreateResponse(
        success=True,
        event=event,
        conflicts=conflicts
    )


@app.get("/events/{event_id}")
async def get_event(event_id: str) -> Dict[str, Any]:
    """Get a single event by ID."""
    if event_id not in events:
        raise HTTPException(status_code=404, detail="Event not found")

    return {"success": True, "event": events[event_id].model_dump()}


@app.put("/events/{event_id}")
async def update_event(event_id: str, update_data: EventUpdate) -> Dict[str, Any]:
    """Update an existing event."""
    if event_id not in events:
        raise HTTPException(status_code=404, detail="Event not found")

    event = events[event_id]
    update_dict = update_data.model_dump(exclude_none=True)

    # Check for conflicts if date/time/duration changed
    if any(k in update_dict for k in ['date', 'time', 'duration']):
        temp_event = EventCreate(
            title=update_dict.get('title', event.title),
            date=update_dict.get('date', event.date),
            time=update_dict.get('time', event.time),
            duration=update_dict.get('duration', event.duration),
            description=update_dict.get('description', event.description),
            category=update_dict.get('category', event.category)
        )
        conflicts = check_conflicts(temp_event, exclude_id=event_id)
    else:
        conflicts = []

    # Update event
    updated_event = Event(
        id=event_id,
        title=update_dict.get('title', event.title),
        date=update_dict.get('date', event.date),
        time=update_dict.get('time', event.time),
        duration=update_dict.get('duration', event.duration),
        description=update_dict.get('description', event.description),
        category=update_dict.get('category', event.category)
    )

    events[event_id] = updated_event
    save_events()

    return {
        "success": True,
        "event": updated_event.model_dump(),
        "conflicts": [c.model_dump() for c in conflicts]
    }


@app.delete("/events/{event_id}")
async def delete_event(event_id: str) -> Dict[str, Any]:
    """Delete an event."""
    if event_id not in events:
        raise HTTPException(status_code=404, detail="Event not found")

    deleted = events.pop(event_id)
    save_events()

    return {"success": True, "deleted": deleted.model_dump()}


# ============================================
# Chat Endpoints
# ============================================

@app.post("/chat")
async def chat(request: ChatRequest) -> Dict[str, Any]:
    """Non-streaming chat endpoint."""

    # Pre-process the query for date-related questions
    preprocessed = preprocess_query(request.message)

    # If we can answer directly without LLM
    if preprocessed['handled']:
        return {
            "success": True,
            "response": preprocessed['response'],
            "action": None,
            "action_data": None,
            "preprocessed": True
        }

    # Need LLM - check if Ollama is available
    if not ollama_available:
        return {
            "success": False,
            "error": "Ollama is not available. Please install and start Ollama with the model."
        }

    try:
        full_response = ""
        action = None
        action_data = None

        # Pass extra context if we have pre-filtered events
        async for chunk in chat_with_ollama(request.message, preprocessed.get('context')):
            if chunk.startswith("data: "):
                data = json.loads(chunk[6:])
                if data["type"] == "token":
                    full_response += data["content"]
                elif data["type"] == "done":
                    action = data.get("action")
                    action_data = data.get("action_data")
                elif data["type"] == "error":
                    return {"success": False, "error": data["error"]}

        return {
            "success": True,
            "response": full_response,
            "action": action,
            "action_data": action_data
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat endpoint."""

    # Pre-process the query for date-related questions
    preprocessed = preprocess_query(request.message)

    # If we can answer directly without LLM
    if preprocessed['handled']:
        async def direct_response():
            # Simulate streaming for consistency
            response = preprocessed['response']
            yield f"data: {json.dumps({'type': 'token', 'content': response})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'action': None, 'action_data': None, 'preprocessed': True})}\n\n"
        return StreamingResponse(direct_response(), media_type="text/event-stream")

    # Need LLM - check if Ollama is available
    if not ollama_available:
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'error': 'Ollama not available. Install and run: ollama pull qwen2.5:3b'})}\n\n"
        return StreamingResponse(error_gen(), media_type="text/event-stream")

    return StreamingResponse(
        chat_with_ollama(request.message, preprocessed.get('context')),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/chat/clear")
async def clear_chat() -> Dict[str, Any]:
    """Clear chat history."""
    global chat_history
    chat_history = []
    return {"success": True}


@app.post("/chat/parse")
async def parse_query(request: ChatRequest) -> Dict[str, Any]:
    """Debug endpoint to test date parsing without calling LLM."""
    date_info = parse_date_from_query(request.message)
    preprocessed = preprocess_query(request.message)

    matching_events = []
    if date_info['dates']:
        matching_events = [e.model_dump() for e in get_events_for_dates(date_info['dates'])]

    return {
        "success": True,
        "message": request.message,
        "date_info": date_info,
        "matching_events": matching_events,
        "preprocessed": preprocessed
    }


# ============================================
# Solver Endpoints (Clingo ASP)
# ============================================

@app.post("/solve")
async def solve_scheduling(request: SolveRequest) -> Dict[str, Any]:
    """
    Find available time slots using Clingo constraint solver.

    This endpoint uses Answer Set Programming to find optimal time slots
    that satisfy the given constraints (duration, preferences, etc.).
    """
    # Convert events to list of dicts for solver
    events_list = [
        {
            "id": e.id,
            "title": e.title,
            "date": e.date,
            "time": e.time,
            "duration": e.duration,
            "category": e.category.value
        }
        for e in events.values()
    ]

    # Build request dict for solver
    solve_request = {
        "activity": request.activity,
        "duration": request.duration,
        "count": request.count,
        "prefer_morning": request.prefer_morning,
        "prefer_afternoon": request.prefer_afternoon,
        "prefer_evening": request.prefer_evening,
        "avoid_weekends": request.avoid_weekends,
        "working_hours_only": request.working_hours_only,
    }

    if request.date:
        solve_request["date"] = request.date
    if request.days:
        solve_request["days"] = request.days

    # Run solver
    try:
        solutions = schedule_solver.find_available_slots(events_list, solve_request)

        if not solutions:
            return {
                "success": True,
                "found": False,
                "message": "No available time slots found with the given constraints.",
                "solutions": []
            }

        return {
            "success": True,
            "found": True,
            "message": f"Found {len(solutions)} solution(s)",
            "solutions": solutions,
            "best": solutions[0] if solutions else None
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/solve/free")
async def find_free_slots(request: FreeSlotsRequest) -> Dict[str, Any]:
    """
    Find all free time slots on a specific date.

    Returns contiguous free time blocks that are at least min_duration long.
    """
    # Convert events to list of dicts for solver
    events_list = [
        {
            "id": e.id,
            "title": e.title,
            "date": e.date,
            "time": e.time,
            "duration": e.duration,
            "category": e.category.value
        }
        for e in events.values()
    ]

    try:
        free_slots = schedule_solver.find_free_slots(
            events_list,
            request.date,
            request.min_duration
        )

        # Calculate total free time
        total_free_mins = sum(slot["duration_mins"] for slot in free_slots)

        return {
            "success": True,
            "date": request.date,
            "free_slots": free_slots,
            "total_free_minutes": total_free_mins,
            "slot_count": len(free_slots)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ============================================
# Shutdown
# ============================================

@app.post("/shutdown")
async def shutdown():
    """Gracefully shutdown the server."""
    import signal

    save_events()

    def force_shutdown():
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.get_event_loop().call_later(1.0, force_shutdown)
    return {"success": True, "message": "Server shutting down"}


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
    print(f"Starting Calendar App server on port {port}...")
    uvicorn.run(app, host="127.0.0.1", port=port)
