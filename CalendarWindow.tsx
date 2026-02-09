// NO IMPORTS - This is a dynamic window!
// All dependencies are provided globally by the app

// ============================================
// Interfaces
// ============================================

interface CalendarEvent {
  id: string;
  title: string;
  date: string;      // YYYY-MM-DD
  time: string;      // HH:MM
  duration: number;  // minutes
  description: string;
  category: EventCategory;
}

type EventCategory = 'work' | 'personal' | 'health' | 'meeting' | 'reminder' | 'other';

interface ConflictWarning {
  event_id: string;
  title: string;
  overlap_minutes: number;
  message: string;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  action?: string;
  actionData?: any;
}

interface ServerStatus {
  status: string;
  ollama_available: boolean;
  ollama_model: string;
  event_count: number;
  chat_history_length: number;
}

// ============================================
// Constants
// ============================================

const CATEGORY_COLORS: Record<EventCategory, string> = {
  work: '#3b82f6',      // blue
  personal: '#10b981',  // green
  health: '#ef4444',    // red
  meeting: '#8b5cf6',   // purple
  reminder: '#f59e0b',  // amber
  other: '#6b7280',     // gray
};

const CATEGORY_LABELS: Record<EventCategory, string> = {
  work: 'Work',
  personal: 'Personal',
  health: 'Health',
  meeting: 'Meeting',
  reminder: 'Reminder',
  other: 'Other',
};

const DAYS_OF_WEEK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

// ============================================
// Helper Functions
// ============================================

const generateId = (): string => {
  return `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
};

const formatDate = (date: Date): string => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const parseDate = (dateStr: string): Date => {
  const [year, month, day] = dateStr.split('-').map(Number);
  return new Date(year, month - 1, day);
};

const getMonthDays = (year: number, month: number): Date[] => {
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);

  const days: Date[] = [];

  // Add days from previous month to fill first week
  const startDayOfWeek = firstDay.getDay();
  for (let i = startDayOfWeek - 1; i >= 0; i--) {
    const d = new Date(year, month, -i);
    days.push(d);
  }

  // Add all days of current month
  for (let d = 1; d <= lastDay.getDate(); d++) {
    days.push(new Date(year, month, d));
  }

  // Add days from next month to fill last week
  const endDayOfWeek = lastDay.getDay();
  for (let i = 1; i < 7 - endDayOfWeek; i++) {
    days.push(new Date(year, month + 1, i));
  }

  return days;
};

const getWeekDays = (date: Date): Date[] => {
  const days: Date[] = [];
  const dayOfWeek = date.getDay();
  const sunday = new Date(date);
  sunday.setDate(date.getDate() - dayOfWeek);

  for (let i = 0; i < 7; i++) {
    const d = new Date(sunday);
    d.setDate(sunday.getDate() + i);
    days.push(d);
  }

  return days;
};

const timeToMinutes = (time: string): number => {
  const [hours, minutes] = time.split(':').map(Number);
  return hours * 60 + minutes;
};

const minutesToTime = (minutes: number): string => {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
};

// ============================================
// Main Component
// ============================================

export const CalendarWindow: React.FC = () => {
  // Server state
  const [serverPort, setServerPort] = useState(8767);
  const [serverRunning, setServerRunning] = useState(false);
  const [serverStatus, setServerStatus] = useState<ServerStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [availableVenvs, setAvailableVenvs] = useState<string[]>([]);
  const [selectedVenv, setSelectedVenv] = useState<string>('');

  // Calendar state
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [currentDate, setCurrentDate] = useState(new Date());
  const [view, setView] = useState<'month' | 'week'>('month');
  const [selectedDate, setSelectedDate] = useState<string>(formatDate(new Date()));

  // Event modal state
  const [showEventModal, setShowEventModal] = useState(false);
  const [editingEvent, setEditingEvent] = useState<CalendarEvent | null>(null);
  const [eventForm, setEventForm] = useState({
    title: '',
    date: formatDate(new Date()),
    time: '09:00',
    duration: 60,
    description: '',
    category: 'other' as EventCategory,
  });
  const [conflicts, setConflicts] = useState<ConflictWarning[]>([]);
  const [savingEvent, setSavingEvent] = useState(false);

  // Chat state
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [chatExpanded, setChatExpanded] = useState(true);

  // Refs
  const chatEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const ipcRenderer = (window as any).require?.('electron')?.ipcRenderer;

  // ============================================
  // Server Functions
  // ============================================

  const getServerUrl = () => `http://127.0.0.1:${serverPort}`;

  const checkServerStatus = useCallback(async () => {
    try {
      const res = await fetch(`${getServerUrl()}/status`);
      if (res.ok) {
        const status = await res.json();
        setServerStatus(status);
        setServerRunning(true);
        return true;
      }
    } catch {
      setServerRunning(false);
      setServerStatus(null);
    }
    return false;
  }, [serverPort]);

  const fetchEvents = useCallback(async () => {
    if (!serverRunning) return;

    try {
      const res = await fetch(`${getServerUrl()}/events`);
      const data = await res.json();
      if (data.success) {
        setEvents(data.events);
      }
    } catch (e) {
      console.error('Failed to fetch events:', e);
    }
  }, [serverRunning, serverPort]);

  // Load available venvs
  useEffect(() => {
    const loadVenvs = async () => {
      if (!ipcRenderer) return;
      const result = await ipcRenderer.invoke('python-list-venvs');
      if (result.success && result.venvs.length > 0) {
        const names = result.venvs.map((v: any) => v.name);
        setAvailableVenvs(names);
        if (!selectedVenv) {
          setSelectedVenv(names[0]);
        }
      }
    };
    loadVenvs();
  }, [ipcRenderer, selectedVenv]);

  // Poll server status
  useEffect(() => {
    const interval = setInterval(() => {
      if (serverRunning) {
        checkServerStatus();
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [serverRunning, checkServerStatus]);

  // Fetch events when server connects
  useEffect(() => {
    if (serverRunning) {
      fetchEvents();
    }
  }, [serverRunning, fetchEvents]);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  const startServer = async () => {
    if (!ipcRenderer) {
      console.error('Not running in Electron');
      return;
    }

    setConnecting(true);

    const alreadyRunning = await checkServerStatus();
    if (alreadyRunning) {
      setConnecting(false);
      return;
    }

    if (!selectedVenv) {
      alert('Please select a Python virtual environment');
      setConnecting(false);
      return;
    }

    // Get the script path
    const scriptResult = await ipcRenderer.invoke('resolve-workflow-script', {
      workflowFolder: 'CalendarApp',
      scriptName: 'calendar_server.py'
    });

    if (!scriptResult.success) {
      alert(`Could not find calendar_server.py: ${scriptResult.error}`);
      setConnecting(false);
      return;
    }

    const result = await ipcRenderer.invoke('python-start-script-server', {
      venvName: selectedVenv,
      scriptPath: scriptResult.path,
      port: serverPort,
      serverName: 'calendar_app',
    });

    if (result.success) {
      let attempts = 0;
      const maxAttempts = 30;
      const pollInterval = setInterval(async () => {
        attempts++;
        const isReady = await checkServerStatus();
        if (isReady) {
          clearInterval(pollInterval);
          setConnecting(false);
          fetchEvents();
        } else if (attempts >= maxAttempts) {
          clearInterval(pollInterval);
          setConnecting(false);
          alert('Server failed to start within timeout');
        }
      }, 1000);
    } else {
      alert(`Failed to start server: ${result.error}`);
      setConnecting(false);
    }
  };

  const stopServer = async () => {
    if (!ipcRenderer) return;

    const result = await ipcRenderer.invoke('python-stop-script-server', 'calendar_app');
    if (result.success) {
      setServerRunning(false);
      setServerStatus(null);
    } else {
      try {
        await fetch(`${getServerUrl()}/shutdown`, { method: 'POST' });
      } catch {
        // Server already stopped
      }
      setServerRunning(false);
      setServerStatus(null);
    }
  };

  // ============================================
  // Event CRUD
  // ============================================

  const openNewEventModal = (date?: string) => {
    setEditingEvent(null);
    setEventForm({
      title: '',
      date: date || selectedDate,
      time: '09:00',
      duration: 60,
      description: '',
      category: 'other',
    });
    setConflicts([]);
    setShowEventModal(true);
  };

  const openEditEventModal = (event: CalendarEvent) => {
    setEditingEvent(event);
    setEventForm({
      title: event.title,
      date: event.date,
      time: event.time,
      duration: event.duration,
      description: event.description,
      category: event.category,
    });
    setConflicts([]);
    setShowEventModal(true);
  };

  const saveEvent = async () => {
    if (!serverRunning || !eventForm.title.trim()) return;

    setSavingEvent(true);

    try {
      if (editingEvent) {
        // Update existing event
        const res = await fetch(`${getServerUrl()}/events/${editingEvent.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(eventForm),
        });
        const data = await res.json();
        if (data.success) {
          setConflicts(data.conflicts || []);
          await fetchEvents();
          if (!data.conflicts?.length) {
            setShowEventModal(false);
          }
        }
      } else {
        // Create new event
        const res = await fetch(`${getServerUrl()}/events`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(eventForm),
        });
        const data = await res.json();
        if (data.success) {
          setConflicts(data.conflicts || []);
          await fetchEvents();
          if (!data.conflicts?.length) {
            setShowEventModal(false);
          }
        }
      }
    } catch (e) {
      console.error('Failed to save event:', e);
    } finally {
      setSavingEvent(false);
    }
  };

  const deleteEvent = async (eventId: string) => {
    if (!serverRunning) return;
    if (!confirm('Delete this event?')) return;

    try {
      const res = await fetch(`${getServerUrl()}/events/${eventId}`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (data.success) {
        await fetchEvents();
        setShowEventModal(false);
      }
    } catch (e) {
      console.error('Failed to delete event:', e);
    }
  };

  // ============================================
  // Chat Functions
  // ============================================

  const sendChatMessage = async () => {
    if (!serverRunning || !chatInput.trim() || chatLoading) return;

    const userMessage: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: chatInput,
      timestamp: new Date().toLocaleTimeString(),
    };

    setChatMessages(prev => [...prev, userMessage]);
    setChatInput('');
    setChatLoading(true);

    const assistantMessage: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: new Date().toLocaleTimeString(),
    };
    setChatMessages(prev => [...prev, assistantMessage]);

    try {
      abortControllerRef.current = new AbortController();

      const res = await fetch(`${getServerUrl()}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMessage.content }),
        signal: abortControllerRef.current.signal,
      });

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let fullResponse = '';
      let action: string | undefined;
      let actionData: any;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));

              if (data.type === 'token') {
                fullResponse += data.content;
                setChatMessages(prev => prev.map(m =>
                  m.id === assistantMessage.id ? { ...m, content: fullResponse } : m
                ));
              } else if (data.type === 'done') {
                action = data.action;
                actionData = data.action_data;
              } else if (data.type === 'error') {
                setChatMessages(prev => prev.map(m =>
                  m.id === assistantMessage.id ? { ...m, content: `Error: ${data.error}` } : m
                ));
              }
            } catch {
              // Ignore parse errors
            }
          }
        }
      }

      // Handle action from LLM
      if (action === 'ADD_EVENT' && actionData) {
        try {
          const createRes = await fetch(`${getServerUrl()}/events`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(actionData),
          });
          const createData = await createRes.json();
          if (createData.success) {
            await fetchEvents();
            const eventInfo = `Created: "${actionData.title}" on ${actionData.date} at ${actionData.time}`;
            setChatMessages(prev => prev.map(m =>
              m.id === assistantMessage.id
                ? { ...m, action: 'ADD_EVENT', actionData, content: fullResponse + `\n\n[${eventInfo}]` }
                : m
            ));
          }
        } catch (e) {
          console.error('Failed to create event from chat:', e);
        }
      }

      // Handle DELETE_EVENT action
      if (action === 'DELETE_EVENT' && actionData?.id) {
        try {
          const deleteRes = await fetch(`${getServerUrl()}/events/${actionData.id}`, {
            method: 'DELETE',
          });
          const deleteData = await deleteRes.json();
          if (deleteData.success) {
            await fetchEvents();
            const eventInfo = `Deleted: "${deleteData.deleted?.title || actionData.id}"`;
            setChatMessages(prev => prev.map(m =>
              m.id === assistantMessage.id
                ? { ...m, action: 'DELETE_EVENT', actionData, content: fullResponse + `\n\n[${eventInfo}]` }
                : m
            ));
          } else {
            setChatMessages(prev => prev.map(m =>
              m.id === assistantMessage.id
                ? { ...m, content: fullResponse + `\n\n[Error: Could not delete event - ${deleteData.detail || 'not found'}]` }
                : m
            ));
          }
        } catch (e) {
          console.error('Failed to delete event from chat:', e);
        }
      }

      // Handle UPDATE_EVENT action
      if (action === 'UPDATE_EVENT' && actionData?.id) {
        try {
          const { id, ...updateFields } = actionData;
          const updateRes = await fetch(`${getServerUrl()}/events/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updateFields),
          });
          const updateData = await updateRes.json();
          if (updateData.success) {
            await fetchEvents();
            const eventInfo = `Updated: "${updateData.event?.title || id}"`;
            setChatMessages(prev => prev.map(m =>
              m.id === assistantMessage.id
                ? { ...m, action: 'UPDATE_EVENT', actionData, content: fullResponse + `\n\n[${eventInfo}]` }
                : m
            ));
          } else {
            setChatMessages(prev => prev.map(m =>
              m.id === assistantMessage.id
                ? { ...m, content: fullResponse + `\n\n[Error: Could not update event - ${updateData.detail || 'not found'}]` }
                : m
            ));
          }
        } catch (e) {
          console.error('Failed to update event from chat:', e);
        }
      }

    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setChatMessages(prev => prev.map(m =>
          m.id === assistantMessage.id ? { ...m, content: `Error: ${e.message}` } : m
        ));
      }
    } finally {
      abortControllerRef.current = null;
      setChatLoading(false);
    }
  };

  const clearChat = async () => {
    if (!serverRunning) return;
    try {
      await fetch(`${getServerUrl()}/chat/clear`, { method: 'POST' });
      setChatMessages([]);
    } catch {
      // Ignore
    }
  };

  // ============================================
  // Calendar Navigation
  // ============================================

  const goToPrevious = () => {
    if (view === 'month') {
      setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() - 1, 1));
    } else {
      const newDate = new Date(currentDate);
      newDate.setDate(currentDate.getDate() - 7);
      setCurrentDate(newDate);
    }
  };

  const goToNext = () => {
    if (view === 'month') {
      setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 1));
    } else {
      const newDate = new Date(currentDate);
      newDate.setDate(currentDate.getDate() + 7);
      setCurrentDate(newDate);
    }
  };

  const goToToday = () => {
    setCurrentDate(new Date());
    setSelectedDate(formatDate(new Date()));
  };

  // ============================================
  // Get events for a specific date
  // ============================================

  const getEventsForDate = (dateStr: string): CalendarEvent[] => {
    return events.filter(e => e.date === dateStr).sort((a, b) => a.time.localeCompare(b.time));
  };

  // ============================================
  // Render
  // ============================================

  const buttonStyle: React.CSSProperties = {
    background: '#374151',
    border: '1px solid #4b5563',
    color: '#e5e7eb',
    padding: '6px 12px',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '13px',
  };

  const buttonPrimaryStyle: React.CSSProperties = {
    ...buttonStyle,
    background: '#3b82f6',
    borderColor: '#3b82f6',
  };

  const inputStyle: React.CSSProperties = {
    background: '#1f2937',
    border: '1px solid #374151',
    color: '#e5e7eb',
    padding: '8px 12px',
    borderRadius: '6px',
    fontSize: '14px',
    width: '100%',
  };

  const selectStyle: React.CSSProperties = {
    ...inputStyle,
    cursor: 'pointer',
  };

  // Month view
  const renderMonthView = () => {
    const days = getMonthDays(currentDate.getFullYear(), currentDate.getMonth());
    const today = formatDate(new Date());

    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '1px', background: '#374151', flex: 1 }}>
        {/* Day headers */}
        {DAYS_OF_WEEK.map(day => (
          <div key={day} style={{
            background: '#1f2937',
            padding: '8px',
            textAlign: 'center',
            fontWeight: 'bold',
            fontSize: '12px',
            color: '#9ca3af',
          }}>
            {day}
          </div>
        ))}

        {/* Day cells */}
        {days.map((date, idx) => {
          const dateStr = formatDate(date);
          const isCurrentMonth = date.getMonth() === currentDate.getMonth();
          const isToday = dateStr === today;
          const isSelected = dateStr === selectedDate;
          const dayEvents = getEventsForDate(dateStr);

          return (
            <div
              key={idx}
              onClick={() => setSelectedDate(dateStr)}
              onDoubleClick={() => openNewEventModal(dateStr)}
              style={{
                background: isSelected ? '#1e3a5f' : '#111827',
                padding: '4px',
                minHeight: '80px',
                cursor: 'pointer',
                opacity: isCurrentMonth ? 1 : 0.4,
                borderTop: isToday ? '2px solid #3b82f6' : 'none',
              }}
            >
              <div style={{
                fontSize: '12px',
                fontWeight: isToday ? 'bold' : 'normal',
                color: isToday ? '#3b82f6' : '#9ca3af',
                marginBottom: '4px',
              }}>
                {date.getDate()}
              </div>

              {/* Event pills */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {dayEvents.slice(0, 3).map(event => (
                  <div
                    key={event.id}
                    onClick={(e) => { e.stopPropagation(); openEditEventModal(event); }}
                    style={{
                      background: CATEGORY_COLORS[event.category],
                      color: 'white',
                      fontSize: '10px',
                      padding: '2px 4px',
                      borderRadius: '3px',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {event.time.slice(0, 5)} {event.title}
                  </div>
                ))}
                {dayEvents.length > 3 && (
                  <div style={{ fontSize: '10px', color: '#6b7280' }}>
                    +{dayEvents.length - 3} more
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  // Week view
  const renderWeekView = () => {
    const weekDays = getWeekDays(currentDate);
    const today = formatDate(new Date());
    const hours = Array.from({ length: 17 }, (_, i) => i + 6); // 6AM to 10PM

    return (
      <div style={{ display: 'flex', flex: 1, overflow: 'auto' }}>
        {/* Time column */}
        <div style={{ width: '60px', flexShrink: 0 }}>
          <div style={{ height: '40px', background: '#1f2937', borderBottom: '1px solid #374151' }} />
          {hours.map(hour => (
            <div key={hour} style={{
              height: '50px',
              borderBottom: '1px solid #374151',
              padding: '4px',
              fontSize: '11px',
              color: '#6b7280',
              background: '#1f2937',
            }}>
              {hour === 0 ? '12 AM' : hour < 12 ? `${hour} AM` : hour === 12 ? '12 PM' : `${hour - 12} PM`}
            </div>
          ))}
        </div>

        {/* Day columns */}
        {weekDays.map((date, dayIdx) => {
          const dateStr = formatDate(date);
          const isToday = dateStr === today;
          const dayEvents = getEventsForDate(dateStr);

          return (
            <div key={dayIdx} style={{ flex: 1, minWidth: '100px', position: 'relative' }}>
              {/* Day header */}
              <div style={{
                height: '40px',
                background: isToday ? '#1e3a5f' : '#1f2937',
                borderBottom: '1px solid #374151',
                borderLeft: '1px solid #374151',
                padding: '4px',
                textAlign: 'center',
              }}>
                <div style={{ fontSize: '11px', color: '#6b7280' }}>{DAYS_OF_WEEK[date.getDay()]}</div>
                <div style={{ fontSize: '14px', fontWeight: isToday ? 'bold' : 'normal', color: isToday ? '#3b82f6' : '#e5e7eb' }}>
                  {date.getDate()}
                </div>
              </div>

              {/* Hour slots */}
              {hours.map(hour => (
                <div
                  key={hour}
                  onClick={() => openNewEventModal(dateStr)}
                  style={{
                    height: '50px',
                    borderBottom: '1px solid #374151',
                    borderLeft: '1px solid #374151',
                    background: '#111827',
                    cursor: 'pointer',
                  }}
                />
              ))}

              {/* Events overlay */}
              {dayEvents.map(event => {
                const startMinutes = timeToMinutes(event.time);
                const top = ((startMinutes - 360) / 60) * 50 + 40; // 360 = 6AM in minutes
                const height = (event.duration / 60) * 50;

                if (top < 40 || top > 40 + 17 * 50) return null; // Outside visible range

                return (
                  <div
                    key={event.id}
                    onClick={(e) => { e.stopPropagation(); openEditEventModal(event); }}
                    style={{
                      position: 'absolute',
                      top: `${Math.max(40, top)}px`,
                      left: '2px',
                      right: '2px',
                      height: `${Math.min(height, 17 * 50 + 40 - top)}px`,
                      background: CATEGORY_COLORS[event.category],
                      borderRadius: '4px',
                      padding: '4px',
                      overflow: 'hidden',
                      cursor: 'pointer',
                      fontSize: '11px',
                      color: 'white',
                    }}
                  >
                    <div style={{ fontWeight: 'bold' }}>{event.title}</div>
                    <div>{event.time} ({event.duration}min)</div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    );
  };

  // Event modal
  const renderEventModal = () => {
    if (!showEventModal) return null;

    return (
      <div style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: 'rgba(0,0,0,0.7)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}>
        <div style={{
          background: '#1f2937',
          borderRadius: '12px',
          padding: '24px',
          width: '400px',
          maxHeight: '90vh',
          overflow: 'auto',
        }}>
          <h2 style={{ margin: '0 0 16px 0', color: '#e5e7eb' }}>
            {editingEvent ? 'Edit Event' : 'New Event'}
          </h2>

          {/* Conflict warnings */}
          {conflicts.length > 0 && (
            <div style={{
              background: '#7f1d1d',
              border: '1px solid #ef4444',
              borderRadius: '6px',
              padding: '12px',
              marginBottom: '16px',
            }}>
              <div style={{ fontWeight: 'bold', marginBottom: '8px', color: '#fca5a5' }}>Scheduling Conflicts</div>
              {conflicts.map((c, idx) => (
                <div key={idx} style={{ fontSize: '13px', color: '#fca5a5' }}>{c.message}</div>
              ))}
              <div style={{ fontSize: '12px', color: '#f87171', marginTop: '8px' }}>
                Event saved despite conflicts. You may want to adjust the time.
              </div>
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: '#9ca3af' }}>Title</label>
              <input
                type="text"
                value={eventForm.title}
                onChange={(e) => setEventForm(prev => ({ ...prev, title: e.target.value }))}
                style={inputStyle}
                placeholder="Event title"
                autoFocus
              />
            </div>

            <div style={{ display: 'flex', gap: '12px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: '#9ca3af' }}>Date</label>
                <input
                  type="date"
                  value={eventForm.date}
                  onChange={(e) => setEventForm(prev => ({ ...prev, date: e.target.value }))}
                  style={inputStyle}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: '#9ca3af' }}>Time</label>
                <input
                  type="time"
                  value={eventForm.time}
                  onChange={(e) => setEventForm(prev => ({ ...prev, time: e.target.value }))}
                  style={inputStyle}
                />
              </div>
            </div>

            <div style={{ display: 'flex', gap: '12px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: '#9ca3af' }}>Duration (minutes)</label>
                <input
                  type="number"
                  value={eventForm.duration}
                  onChange={(e) => setEventForm(prev => ({ ...prev, duration: parseInt(e.target.value) || 60 }))}
                  style={inputStyle}
                  min={15}
                  step={15}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: '#9ca3af' }}>Category</label>
                <select
                  value={eventForm.category}
                  onChange={(e) => setEventForm(prev => ({ ...prev, category: e.target.value as EventCategory }))}
                  style={selectStyle}
                >
                  {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', color: '#9ca3af' }}>Description</label>
              <textarea
                value={eventForm.description}
                onChange={(e) => setEventForm(prev => ({ ...prev, description: e.target.value }))}
                style={{ ...inputStyle, minHeight: '80px', resize: 'vertical' }}
                placeholder="Optional description"
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '12px', marginTop: '20px', justifyContent: 'flex-end' }}>
            {editingEvent && (
              <button
                onClick={() => deleteEvent(editingEvent.id)}
                style={{ ...buttonStyle, background: '#7f1d1d', borderColor: '#991b1b' }}
              >
                Delete
              </button>
            )}
            <button onClick={() => setShowEventModal(false)} style={buttonStyle}>
              Cancel
            </button>
            <button
              onClick={saveEvent}
              disabled={!eventForm.title.trim() || savingEvent}
              style={{ ...buttonPrimaryStyle, opacity: !eventForm.title.trim() || savingEvent ? 0.5 : 1 }}
            >
              {savingEvent ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    );
  };

  // Chat panel
  const renderChatPanel = () => {
    return (
      <div style={{
        width: chatExpanded ? '320px' : '40px',
        background: '#1f2937',
        borderLeft: '1px solid #374151',
        display: 'flex',
        flexDirection: 'column',
        transition: 'width 0.2s',
      }}>
        {/* Chat header */}
        <div style={{
          padding: '12px',
          borderBottom: '1px solid #374151',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <button
            onClick={() => setChatExpanded(!chatExpanded)}
            style={{ ...buttonStyle, padding: '4px 8px' }}
          >
            {chatExpanded ? '>' : '<'}
          </button>
          {chatExpanded && (
            <>
              <span style={{ fontWeight: 'bold', fontSize: '14px' }}>AI Assistant</span>
              <button onClick={clearChat} style={{ ...buttonStyle, padding: '4px 8px', fontSize: '11px' }}>
                Clear
              </button>
            </>
          )}
        </div>

        {chatExpanded && (
          <>
            {/* Ollama status */}
            {serverStatus && !serverStatus.ollama_available && (
              <div style={{
                padding: '8px 12px',
                background: '#7f1d1d',
                fontSize: '12px',
                color: '#fca5a5',
              }}>
                Ollama not available. Install and run: ollama pull qwen2.5:0.5b
              </div>
            )}

            {/* Messages */}
            <div style={{
              flex: 1,
              overflow: 'auto',
              padding: '12px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}>
              {chatMessages.length === 0 && (
                <div style={{ color: '#6b7280', fontSize: '13px', textAlign: 'center', marginTop: '20px' }}>
                  Ask me about your schedule or to add events!
                  <br /><br />
                  Try: "What's on my calendar this week?" or "Add a meeting with John tomorrow at 2pm"
                </div>
              )}
              {chatMessages.map(msg => (
                <div
                  key={msg.id}
                  style={{
                    padding: '10px 12px',
                    borderRadius: '8px',
                    background: msg.role === 'user' ? '#3b82f6' : '#374151',
                    alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                    maxWidth: '90%',
                  }}
                >
                  <div style={{ fontSize: '13px', whiteSpace: 'pre-wrap' }}>{msg.content || '...'}</div>
                  <div style={{ fontSize: '10px', color: '#9ca3af', marginTop: '4px' }}>{msg.timestamp}</div>
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>

            {/* Input */}
            <div style={{ padding: '12px', borderTop: '1px solid #374151' }}>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      sendChatMessage();
                    }
                  }}
                  placeholder={serverStatus?.ollama_available ? "Ask about your schedule..." : "Ollama not available"}
                  disabled={!serverRunning || !serverStatus?.ollama_available || chatLoading}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <button
                  onClick={sendChatMessage}
                  disabled={!serverRunning || !serverStatus?.ollama_available || chatLoading || !chatInput.trim()}
                  style={{ ...buttonPrimaryStyle, opacity: (!serverRunning || !serverStatus?.ollama_available || chatLoading || !chatInput.trim()) ? 0.5 : 1 }}
                >
                  Send
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    );
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: '#111827',
      color: '#e5e7eb',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px',
        background: '#1f2937',
        borderBottom: '1px solid #374151',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
      }}>
        {/* Server controls */}
        {!serverRunning ? (
          <>
            <select
              value={selectedVenv}
              onChange={(e) => setSelectedVenv(e.target.value)}
              style={{ ...selectStyle, width: '150px' }}
            >
              <option value="">Select venv</option>
              {availableVenvs.map(v => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
            <input
              type="number"
              value={serverPort}
              onChange={(e) => setServerPort(parseInt(e.target.value) || 8767)}
              style={{ ...inputStyle, width: '80px' }}
              placeholder="Port"
            />
            <button
              onClick={startServer}
              disabled={connecting || !selectedVenv}
              style={{ ...buttonPrimaryStyle, opacity: (connecting || !selectedVenv) ? 0.5 : 1 }}
            >
              {connecting ? 'Starting...' : 'Start Server'}
            </button>
          </>
        ) : (
          <>
            <span style={{
              padding: '4px 8px',
              background: '#065f46',
              borderRadius: '4px',
              fontSize: '12px',
            }}>
              Server Running
            </span>
            <button onClick={stopServer} style={buttonStyle}>Stop</button>
          </>
        )}

        <div style={{ flex: 1 }} />

        {/* Navigation */}
        <button onClick={goToPrevious} style={buttonStyle}>&lt;</button>
        <span style={{ minWidth: '180px', textAlign: 'center', fontWeight: 'bold' }}>
          {view === 'month'
            ? `${MONTHS[currentDate.getMonth()]} ${currentDate.getFullYear()}`
            : `Week of ${formatDate(getWeekDays(currentDate)[0])}`
          }
        </span>
        <button onClick={goToNext} style={buttonStyle}>&gt;</button>
        <button onClick={goToToday} style={buttonStyle}>Today</button>

        <div style={{ width: '1px', height: '24px', background: '#374151', margin: '0 8px' }} />

        {/* View toggle */}
        <button
          onClick={() => setView('month')}
          style={{ ...buttonStyle, background: view === 'month' ? '#3b82f6' : '#374151' }}
        >
          Month
        </button>
        <button
          onClick={() => setView('week')}
          style={{ ...buttonStyle, background: view === 'week' ? '#3b82f6' : '#374151' }}
        >
          Week
        </button>

        <div style={{ width: '1px', height: '24px', background: '#374151', margin: '0 8px' }} />

        <button
          onClick={() => openNewEventModal()}
          disabled={!serverRunning}
          style={{ ...buttonPrimaryStyle, opacity: serverRunning ? 1 : 0.5 }}
        >
          + Event
        </button>
      </div>

      {/* Main content */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Calendar */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {view === 'month' ? renderMonthView() : renderWeekView()}
        </div>

        {/* Chat panel */}
        {serverRunning && renderChatPanel()}
      </div>

      {/* Status bar */}
      <div style={{
        padding: '8px 16px',
        background: '#1f2937',
        borderTop: '1px solid #374151',
        fontSize: '12px',
        color: '#6b7280',
        display: 'flex',
        justifyContent: 'space-between',
      }}>
        <span>
          {serverRunning
            ? `${events.length} events | Ollama: ${serverStatus?.ollama_available ? 'Connected' : 'Not available'}`
            : 'Server not running'
          }
        </span>
        <span>Selected: {selectedDate}</span>
      </div>

      {/* Event modal */}
      {renderEventModal()}
    </div>
  );
};
