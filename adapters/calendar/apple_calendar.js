function iso(date) {
  return date.toISOString();
}

function selectedCalendar(calendarApp, name) {
  const matches = calendarApp.calendars().filter((item) => item.name() === name);
  if (matches.length !== 1) {
    throw new Error(`未找到唯一目标日历：${name}`);
  }
  return matches[0];
}

function run(argv) {
  try {
    const action = argv[0];
    const calendarName = argv[1];
    const calendarApp = Application("Calendar");

    if (action === "list_busy") {
      const rangeStart = new Date(argv[2]);
      const rangeEnd = new Date(argv[3]);
      const busy = [];
      calendarApp.calendars().forEach((calendar) => {
        calendar.events().forEach((event) => {
          const start = event.startDate();
          const end = event.endDate();
          if (start < rangeEnd && end > rangeStart) {
            busy.push({start: iso(start), end: iso(end)});
          }
        });
      });
      return JSON.stringify({ok: true, busy: busy});
    }

    if (action === "create_event") {
      const stableId = argv[2];
      const title = argv[3];
      const start = new Date(argv[4]);
      const end = new Date(argv[5]);
      const marker = `[job-search-agent:${stableId}]`;
      const calendar = selectedCalendar(calendarApp, calendarName);
      const existing = calendar.events().filter(
        (event) => (event.description() || "").indexOf(marker) >= 0
      );
      if (existing.length > 0) {
        return JSON.stringify({ok: true, event_id: existing[0].uid() || stableId});
      }
      const event = calendarApp.Event({
        summary: title,
        startDate: start,
        endDate: end,
        description: marker,
      });
      calendar.events.push(event);
      calendarApp.save();
      return JSON.stringify({ok: true, event_id: event.uid() || stableId});
    }
    throw new Error(`不支持的动作：${action}`);
  } catch (error) {
    return JSON.stringify({ok: false, error: String(error)});
  }
}
