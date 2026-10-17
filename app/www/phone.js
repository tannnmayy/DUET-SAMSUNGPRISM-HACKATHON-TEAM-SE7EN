/* DUET for Galaxy: the phone's side of every tool.
 *
 * On a Galaxy (the Android app), real actions go through the native DuetPhone plugin
 * (android/app/src/main/java/com/se7en/duet/DuetPhonePlugin.java): Clock alarms and
 * timers, battery and display readings, screen time, installed apps, brightness, settings
 * screens, Google Maps, calls and texts. In a laptop browser the same calls return
 * simulated readings, so the demo runs anywhere.
 *
 * The home devices, the medicine log and the car are simulated in both cases, in this
 * file, and saved on the device (localStorage) so a dose taken this morning is still
 * remembered tonight.
 */
(function () {
  "use strict";

  const cap = window.Capacitor;
  const NATIVE_APP = !!(cap && cap.isNativePlatform && cap.isNativePlatform());
  const Native = NATIVE_APP ? cap.Plugins.DuetPhone : null;
  const listeners = new Set();
  const changed = (what) => listeners.forEach((fn) => { try { fn(what); } catch (e) { console.error(e); } });

  // ---------------------------------------------------------------- saved state
  const KEY = "duet.state.v1";
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (d) => pad(d.getHours()) + ":" + pad(d.getMinutes());
  const today = () => new Date().toISOString().slice(0, 10);

  function freshState() {
    const now = new Date();
    const morningTaken = now.getHours() >= 9;
    return {
      day: today(),
      alarms: [],               // set through DUET: {time, label, at}
      home: [
        { id: "living_light", name: "Living room light", type: "light", on: true, level: 70 },
        { id: "bedroom_light", name: "Bedroom light", type: "light", on: false, level: 50 },
        { id: "ac", name: "Air conditioner", type: "ac", on: false, temp: 24, mode: "cool" },
        { id: "tv", name: "TV", type: "tv", on: false },
        { id: "door", name: "Front door lock", type: "lock", locked: true },
        { id: "vacuum", name: "Robot vacuum", type: "vacuum", state: "docked" },
        { id: "washer", name: "Washer", type: "washer", state: "running", left: 25 },
      ],
      meds: [
        { name: "Metformin 500 mg", for: "blood sugar", time: "09:00", taken: morningTaken ? "09:02" : null },
        { name: "Amlodipine 5 mg", for: "blood pressure", time: "09:00", taken: morningTaken ? "09:02" : null },
        { name: "Metformin 500 mg", for: "blood sugar", time: "20:00", taken: null },
        { name: "Atorvastatin 10 mg", for: "cholesterol", time: "22:00", taken: null },
      ],
      checkins: [],
      events: [],               // calendar events added through DUET (the browser build)
      volume: 60,
      alerts: [],
      trip: null,               // {destination, stops, totalKm, leftKm, startedAt, speedKmh}
      car: { ac: false, temp: 26, rangeKm: 212 },
    };
  }

  let state;
  function load() {
    try { state = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { state = null; }
    if (!state || !state.home) state = freshState();
    if (state.day !== today()) {  // a new day: medicines start again, the rest stays
      const fresh = freshState();
      state.meds = fresh.meds.map((m) => ({ ...m, taken: null }));
      state.day = today();
      state.alarms = [];
    }
    save();
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode */ } }
  function reset() { state = freshState(); save(); changed("all"); }
  load();

  // ---------------------------------------------------------------- settings
  function settings() {
    let s = {};
    try { s = JSON.parse(localStorage.getItem("duet.settings") || "{}"); } catch (e) { s = {}; }
    return s;
  }
  // Settings, "Simulate phone actions": the app's own simulation even on a phone (a failsafe for a
  // demo: one tap turns off any real action that misbehaves on a particular device)
  function isNative() { return NATIVE_APP && !settings().simulate; }
  function contacts() {
    return (settings().contacts || "").split("\n").map((line) => {
      const i = line.lastIndexOf(",");
      if (i < 0) return null;
      const name = line.slice(0, i).trim(), number = line.slice(i + 1).trim();
      return name && number ? { name, number } : null;
    }).filter(Boolean);
  }
  const words = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean);
  function bestMatch(items, query, label) {
    const q = words(query).filter((w) => !["the", "my", "a", "please", "to"].includes(w));
    let best = null, bestScore = 0;
    for (const it of items) {
      const w = words(label(it));
      const score = q.filter((x) => w.some((y) => y === x || y.startsWith(x) || x.startsWith(y))).length;
      if (score > bestScore) { best = it; bestScore = score; }
    }
    return best;
  }
  function findContact(name) {
    const list = contacts();
    if (!list.length) return null;
    return bestMatch(list, name, (c) => c.name) || null;
  }
  // The contacts typed in DUET's settings first (they say who is "my daughter"), then the phone's
  // own address book when the user has allowed Contacts.
  async function lookUpContact(name) {
    const typed = findContact(name);
    if (typed) return typed;
    if (!isNative()) return null;
    // the whole name first, then its words ("my sister Anita" finds "Anita Rao")
    const tries = [name].concat(words(name).filter((w) => w.length >= 3 && !["the", "my", "please", "call", "text"].includes(w)));
    for (const q of tries) {
      try {
        const r = await Native.findContacts({ query: q });
        if (r.granted && r.contacts && r.contacts.length) return bestMatch(r.contacts, name, (c) => c.name) || r.contacts[0];
        if (!r.granted) return null;
      } catch (e) { return null; /* no address book: the typed list was the answer */ }
    }
    return null;
  }

  // ---------------------------------------------------------------- helpers
  const ok = (extra) => ({ status: "ok", ...extra });
  const fail = (error, extra) => ({ status: "error", error, ...extra });
  const needs = (permission, instruction) => ({ status: "needs_permission", permission, instruction });
  // The native side answers {blocked} when Android would not let DUET open another app's screen: DUET is
  // behind another app (Google Maps) and the user has not let it appear on top. Nothing was done.
  const BLOCKED = () => needs("display_over_apps", "Nothing was done: DUET is behind another app, and Android only lets " +
    "it open the Clock, the dialer, Messages or Maps from there if the user allows 'Appear on top' for DUET (DUET's " +
    "settings has the button). Say this in one short sentence; or the user can switch back to DUET and ask again.");
  // DUET's own alarms (the default on a phone) or the Clock app (Settings). Measured on Android 16
  // (17 Oct 2026): Google's Clock app ignored a cancel request for an alarm hours away and came to the
  // front instead, and Android gives other apps no way to read the Clock's alarms. DUET's own alarms are
  // exact (set, cancel, list), open no screen, and show as the phone's next alarm on the lock screen.
  const duetAlarms = () => isNative() && settings().alarms !== "clock";
  let backgroundListening = false;   // set by app.js once the microphone service runs
  function parseTime(t) {
    const m = /^(\d{1,2}):(\d{2})/.exec(String(t || "").trim());
    if (!m) return null;
    const h = +m[1], min = +m[2];
    return h < 24 && min < 60 ? { hour: h, minute: min, text: pad(h) + ":" + pad(min) } : null;
  }
  function spoken(t) {  // "16:30" -> "4:30 PM"
    const p = parseTime(t); if (!p) return t;
    const h12 = ((p.hour + 11) % 12) + 1;
    return h12 + ":" + pad(p.minute) + (p.hour < 12 ? " AM" : " PM");
  }
  const minutes = (ms) => Math.round(ms / 60000);

  // ---------------------------------------------------------------- simulated phone (laptop browser)
  const SIM = {
    battery: {
      level: 23, charging: false, temperature_c: 41.5, health: "good", current_ma: -980, voltage_v: 3.78,
      cycle_count: 412, power_saving: false, thermal_status: "moderate",
      brightness_percent: 92, adaptive_brightness: false, refresh_rate_hz: 120, max_refresh_rate_hz: 120,
      screen_timeout_s: 600, dark_mode: false, wifi_on: true, bluetooth_on: true, location_on: true,
    },
    usage: [
      { app: "BGMI", category: "game", minutes: 155 }, { app: "Instagram", category: "social", minutes: 82 },
      { app: "YouTube", category: "video", minutes: 58 }, { app: "WhatsApp", category: "social", minutes: 31 },
      { app: "Chrome", category: null, minutes: 22 }, { app: "Spotify", category: "audio", minutes: 18 },
    ],
    apps: ["Amazon Shopping", "Flipkart", "Myntra", "Meesho", "Swiggy", "Zomato", "Blinkit", "BGMI", "Asphalt 9",
      "Instagram", "WhatsApp", "YouTube", "Spotify", "Google Maps", "Chrome", "Gmail", "Samsung Health",
      "Samsung Wallet", "Galaxy Store", "SmartThings", "Samsung Members", "Paytm", "PhonePe", "Uber", "Netflix",
      "Camera", "Gallery", "Calendar", "Clock", "My Files"].map((name) => ({
      name, category: { BGMI: "game", "Asphalt 9": "game", Instagram: "social", WhatsApp: "social", YouTube: "video",
        Netflix: "video", Spotify: "audio", "Google Maps": "maps", Gallery: "image", Camera: "image",
        Gmail: "productivity", Calendar: "productivity" }[name] || null })),
  };

  // ---------------------------------------------------------------- the tools
  const T = {};

  T.set_alarm = async ({ time, label }) => {
    const p = parseTime(time);
    if (!p) return fail("not a time: " + time);
    let where = isNative() ? "Clock app" : "Clock (simulated)";
    if (duetAlarms()) {
      const r = await Native.duetAlarmSet({ hour: p.hour, minute: p.minute, label: label || "" });
      if (!r.granted) return r.need === "notifications"
        ? needs("notifications", "Ask the user to allow notifications for DUET, so its alarm can ring; the button is in DUET's settings.")
        : needs("alarms_and_reminders", "Ask the user to allow 'Alarms and reminders' for DUET; the button is in DUET's settings.");
      where = "DUET's alarms";
    } else if (isNative()) {
      const r = await Native.setAlarm({ hour: p.hour, minute: p.minute, label: label || "" });
      if (r && r.blocked) return BLOCKED();
    }
    state.alarms = state.alarms.filter((a) => a.time !== p.text);
    state.alarms.push({ time: p.text, label: label || "", at: hhmm(new Date()) });
    save(); changed("alarms");
    return ok({ alarm: spoken(p.text), where });
  };

  T.cancel_alarm = async ({ time }) => {
    const p = parseTime(time);
    if (!p) return fail("not a time: " + time);
    const had = state.alarms.some((a) => a.time === p.text);
    if (duetAlarms()) {    // exact: DUET knows its own alarms
      const r = await Native.duetAlarmCancel({ hour: p.hour, minute: p.minute });
      state.alarms = state.alarms.filter((a) => a.time !== p.text);
      save(); changed("alarms");
      return r.cancelled ? ok({ cancelled: spoken(p.text) })
        : ok({ cancelled: false, note: "there was no " + spoken(p.text) + " alarm, so nothing needed cancelling" });
    }
    let requested = false;
    if (isNative()) {
      let r = null;
      try { r = await Native.dismissAlarm({ hour: p.hour, minute: p.minute }); } catch (e) { r = null; }
      if (r && r.blocked) return BLOCKED();
      requested = !!(r && r.requested);
    }
    state.alarms = state.alarms.filter((a) => a.time !== p.text);
    save(); changed("alarms");
    if (!had && !isNative()) return ok({ cancelled: false, note: "there was no " + spoken(p.text) + " alarm, so nothing needed cancelling" });
    if (isNative()) {   // the Clock app was asked; Android lets no other app check that it did it
      return ok({ cancelled: spoken(p.text), verified: false, note: requested
        ? "the Clock app was asked to cancel it, but Android gives no way to check: say it was asked, and that they can check the Clock app"
        : "the Clock app did not take the request: ask the user to cancel it in the Clock app" });
    }
    return ok({ cancelled: spoken(p.text) });
  };

  T.list_alarms = async () => {
    let next = null;
    if (isNative()) { try { next = (await Native.nextAlarm()).time || null; } catch (e) { next = null; } }
    if (duetAlarms()) {
      const r = await Native.duetAlarmList();
      return ok({ set_by_duet: (r.alarms || []).map((a) => spoken(pad(a.hour) + ":" + pad(a.minute)) + (a.label ? " (" + a.label + ")" : "")),
        phone_next_alarm: next });
    }
    return ok({ set_by_duet: state.alarms.map((a) => spoken(a.time) + (a.label ? " (" + a.label + ")" : "")),
      phone_next_alarm: next });
  };

  T.set_timer = async ({ minutes: m, label }) => {
    const seconds = Math.max(1, Math.round(Number(m) * 60));
    if (isNative()) {
      const r = await Native.setTimer({ seconds, label: label || "" });
      if (r && r.blocked) return BLOCKED();
    }
    return ok({ timer_minutes: +(seconds / 60).toFixed(1), rings_at: hhmm(new Date(Date.now() + seconds * 1000)) });
  };

  async function usage(hours) {
    if (!isNative()) return { granted: true, apps: SIM.usage };
    return await Native.appUsage({ hours: hours || 24 });
  }

  T.get_battery_report = async () => {
    const b = isNative() ? await Native.batteryReport() : { ...SIM.battery };
    const u = await usage(24);
    b.top_apps_today = u.granted ? (u.apps || []).slice(0, 5).map((a) => a.app + " " + a.minutes + " min" + (a.category ? " (" + a.category + ")" : ""))
      : "unknown: the user has not allowed usage access (open_settings usage_access)";
    b.source = isNative() ? "this phone" : "simulated phone (browser demo)";
    changed({ battery: b });
    return ok(b);
  };

  T.get_app_usage = async ({ hours }) => {
    const u = await usage(hours || 24);
    if (!u.granted) return needs("usage_access", "Ask the user to allow Usage access for DUET; the button is in DUET's settings.");
    return ok({ hours: hours || 24, apps: (u.apps || []).slice(0, 12) });
  };

  T.list_installed_apps = async () => {
    const apps = isNative() ? (await Native.installedApps()).apps : SIM.apps;
    return ok({ count: apps.length, apps: apps.map((a) => a.name + (a.category ? " [" + a.category + "]" : "")) });
  };

  T.set_brightness = async ({ percent, adaptive }) => {
    if (percent == null && adaptive == null) return fail("give a percent or adaptive on/off");
    if (isNative()) {
      const r = await Native.setBrightness({ percent: percent == null ? -1 : percent, adaptive: adaptive == null ? null : !!adaptive });
      if (!r.granted) return needs("modify_system_settings", "Ask the user to allow 'Modify system settings' for DUET; DUET's settings has the button.");
    } else {
      if (percent != null) SIM.battery.brightness_percent = percent;
      if (adaptive != null) SIM.battery.adaptive_brightness = !!adaptive;
    }
    return ok({ brightness_percent: percent == null ? undefined : percent, adaptive: adaptive == null ? undefined : !!adaptive });
  };

  T.set_screen_timeout = async ({ seconds }) => {
    if (isNative()) {
      const r = await Native.setScreenTimeout({ seconds });
      if (!r.granted) return needs("modify_system_settings", "Ask the user to allow 'Modify system settings' for DUET.");
    } else SIM.battery.screen_timeout_s = seconds;
    return ok({ screen_timeout_s: seconds });
  };

  T.open_settings = async ({ page }) => {
    if (isNative()) {
      const r = await Native.openSettings({ page });
      if (r && r.blocked) return BLOCKED();
    } else toast("Opening " + page.replace(/_/g, " ") + " settings (simulated)");
    return ok({ opened: page });
  };

  T.open_app = async ({ name }) => {
    if (isNative()) {
      const r = await Native.openApp({ name });
      if (r && r.blocked) return BLOCKED();
      return r.opened ? ok({ opened: r.opened }) : fail("no app called " + name + " on this phone");
    }
    const app = bestMatch(SIM.apps, name, (a) => a.name);
    if (!app) return fail("no app called " + name);
    toast("Opening " + app.name + " (simulated)");
    return ok({ opened: app.name });
  };

  T.navigate_to = async ({ destination, mode }) => {
    if (isNative()) {
      const r = await Native.navigate({ destination, mode: mode || "driving" });
      if (r && r.blocked) return BLOCKED();
    } else window.open("https://www.google.com/maps/dir/?api=1&destination=" + encodeURIComponent(destination), "_blank");
    return ok({ navigating_to: destination, app: "Google Maps",
      note: !isNative() ? "Google Maps opened in another browser tab"
        : backgroundListening ? "Google Maps is now in front; DUET keeps listening, so the user can just keep talking"
        : "Google Maps is now in front; the user comes back to DUET to talk again" });
  };

  T.call_contact = async ({ name }) => {
    const c = await lookUpContact(name);
    if (!c) return fail("no saved contact matches '" + name + "'", { saved: contacts().map((x) => x.name) });
    const direct = !!settings().direct;
    if (isNative()) {
      const r = await Native.call({ number: c.number, direct });
      if (r && r.blocked) return BLOCKED();
      return ok({ calling: c.name, state: r.direct ? "ringing" : "the dialer is open; the user taps call",
        note: "nothing is known yet about the conversation itself" });
    }
    toast("Calling " + c.name + "… (simulated)");
    return ok({ calling: c.name, state: "ringing (simulated call)", note: "nothing is known yet about the conversation itself" });
  };

  T.send_message = async ({ to, text }) => {
    const c = await lookUpContact(to);
    if (!c) return fail("no saved contact matches '" + to + "'", { saved: contacts().map((x) => x.name) });
    const direct = !!settings().direct;
    if (isNative()) {
      const r = await Native.sms({ number: c.number, text, direct });
      if (r && r.blocked) return BLOCKED();
      if (r.error) return fail(r.error);   // the radio said no: no SIM, no signal, airplane mode
      return ok({ to: c.name, text, how: r.sent ? "sent"
        : r.unconfirmed ? "handed to the phone; it has not confirmed the text went out"
        : "Messages is open with the text; the user taps send" });
    }
    toast("Message to " + c.name + ": " + text);
    return ok({ to: c.name, text, how: "sent (simulated)" });
  };

  // ------------------------------------------------ torch, media, volume, calendar
  T.set_torch = async ({ on }) => {
    const want = on === true || on === "true";
    if (isNative()) {
      const r = await Native.torch({ on: want });
      if (!r.ok) return fail(r.error || "this phone has no torch");
    } else toast("Torch " + (want ? "on" : "off") + " (simulated)");
    return ok({ torch: want ? "on" : "off" });
  };

  T.control_media = async ({ action }) => {
    if (!["play", "pause", "next", "previous"].includes(action)) return fail("unknown media action " + action);
    if (isNative()) await Native.mediaKey({ action });
    else toast("Media: " + action + " (simulated)");
    return ok({ media: action });
  };

  T.set_volume = async ({ percent }) => {
    const p = Math.round(Number(percent));
    if (!isFinite(p)) return fail("give the volume as a percent");
    const want = Math.max(0, Math.min(100, p));
    if (isNative()) {
      const r = await Native.setVolume({ percent: want });
      if (r.percent < 0) return fail(r.error || "the phone did not allow the volume change");
      return ok({ volume_percent: r.percent });
    }
    state.volume = want; save();
    return ok({ volume_percent: want });
  };

  T.create_event = async ({ title, date, time, minutes }) => {
    const p = parseTime(time);
    if (!p) return fail("not a time: " + time);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(date || ""))) return fail("not a date (YYYY-MM-DD): " + date);
    const start = new Date(date + "T" + p.text + ":00");
    if (isNaN(start.getTime())) return fail("not a date: " + date);
    const end = new Date(start.getTime() + (Number(minutes) > 0 ? Number(minutes) : 60) * 60000);
    if (isNative()) {
      const r = await Native.addEvent({ title: String(title || "Event"), start: start.getTime(), end: end.getTime() });
      if (!r.granted) return needs("calendar", "Ask the user to allow Calendar for DUET; the button is in DUET's settings.");
      if (!r.added) return fail(r.error || "the calendar did not accept the event");
    }
    state.events = (state.events || []).concat([{ title: String(title || "Event"), date, time: p.text }]);
    save(); changed("events");
    return ok({ event: title, date, time: spoken(p.text), where: isNative() ? "the phone's calendar" : "Calendar (simulated)" });
  };

  // ------------------------------------------------ the self-test (Settings, or from a laptop)
  // Every real action the phone can take, tried safely: what it changes, it puts back. On a new
  // device, one tap shows which actions work before anyone talks to DUET.
  async function selfTest() {
    const out = [];
    async function step(name, fn) {
      const t0 = Date.now();
      try {
        const r = await fn();
        const okay = r && r.status !== "error" && r.status !== "needs_permission";
        out.push({ name, ok: !!okay, ms: Date.now() - t0, detail: JSON.stringify(r).slice(0, 160) });
      } catch (e) {
        out.push({ name, ok: false, ms: Date.now() - t0, detail: String((e && e.message) || e).slice(0, 160) });
      }
    }
    await step("battery and screen readings", () => T.get_battery_report({}));
    await step("installed apps", () => T.list_installed_apps({}));
    await step("screen time per app", () => T.get_app_usage({ hours: 24 }));
    const b = isNative() ? await Native.batteryReport().catch(() => ({})) : SIM.battery;
    if (b.brightness_percent != null) {
      await step("brightness (set to its current value)", () => T.set_brightness({ percent: b.brightness_percent }));
    }
    await step("alarm: set 23:58", () => T.set_alarm({ time: "23:58", label: "DUET self-test" }));
    await step("alarm: cancel 23:58", () => T.cancel_alarm({ time: "23:58" }));
    await step("next alarm", () => T.list_alarms({}));
    if (isNative()) {   // DUET's own alarms, whichever kind Settings uses: set, find, cancel, gone
      await step("DUET's own alarm: set, list, cancel 23:57", async () => {
        const s = await Native.duetAlarmSet({ hour: 23, minute: 57, label: "DUET self-test" });
        if (!s.granted) return { status: "needs_permission", need: s.need };
        const listed = ((await Native.duetAlarmList()).alarms || []).some((a) => a.hour === 23 && a.minute === 57);
        const c = await Native.duetAlarmCancel({ hour: 23, minute: 57 });
        const gone = !((await Native.duetAlarmList()).alarms || []).some((a) => a.hour === 23 && a.minute === 57);
        return listed && c.cancelled && gone ? { status: "ok", rings: s.rings } : { status: "error", listed, cancelled: c.cancelled, gone };
      });
    }
    await step("torch on", () => T.set_torch({ on: true }));
    await new Promise((r) => setTimeout(r, 600));
    await step("torch off", () => T.set_torch({ on: false }));
    if (isNative()) {
      await step("media volume (read)", async () => ({ status: "ok", ...(await Native.volume()) }));
      await step("contacts", async () => {
        const r = await Native.findContacts({ query: "a" });
        return r.granted ? { status: "ok", found: (r.contacts || []).length } : { status: "needs_permission" };
      });
      await step("permissions", async () => ({ status: "ok", ...(await Native.permissions()) }));
      await step("act while Maps is in front (Appear on top)", async () => {
        const p = await Native.permissions();
        return p.display_over_apps ? { status: "ok", display_over_apps: true }
          : { status: "needs_permission", fix: "allow Appear on top for DUET (settings button)" };
      });
    }
    return { device: isNative() ? "this phone" : "simulated phone", results: out,
      passed: out.filter((x) => x.ok).length, total: out.length };
  }

  // ------------------------------------------------ home (simulated SmartThings)
  function describe(d) {
    switch (d.type) {
      case "light": return d.on ? "on, " + d.level + "%" : "off";
      case "ac": return d.on ? "on, " + d.temp + "°C " + d.mode : "off (set to " + d.temp + "°C)";
      case "tv": return d.on ? "on" : "off";
      case "lock": return d.locked ? "locked" : "unlocked";
      case "vacuum": return d.state;
      case "washer": return d.state === "running" ? "running, " + d.left + " min left" : d.state;
      default: return "";
    }
  }
  T.get_home_status = async () => ok({ devices: state.home.map((d) => d.name + ": " + describe(d)) });

  T.control_home_device = async ({ device, action, value }) => {
    const d = bestMatch(state.home, device, (x) => x.name + " " + x.type + (x.type === "ac" ? " aircon air conditioning" : ""));
    if (!d) return fail("no device called " + device, { devices: state.home.map((x) => x.name) });
    const v = value == null || String(value).trim() === "" ? null : String(value);
    const num = v == null ? NaN : parseFloat(v);
    const can = { light: ["turn_on", "turn_off", "set"], ac: ["turn_on", "turn_off", "set"], tv: ["turn_on", "turn_off"],
      lock: ["lock", "unlock"], vacuum: ["start", "stop", "dock"], washer: ["start", "stop"] }[d.type] || [];
    // "turn on the AC and set it to 22" can arrive as turn_on with the value: on, at that value
    if (action === "turn_on" && v != null && can.includes("set")) action = "set";
    if (!can.includes(action)) return fail(d.name + " cannot " + action.replace("_", " "), { can: can });
    if (action === "turn_on") d.on = true;
    if (action === "turn_off") d.on = false;
    if (action === "set") {
      d.on = true;
      if (d.type === "light" && !isNaN(num)) d.level = Math.max(1, Math.min(100, Math.round(num)));
      if (d.type === "ac") { if (!isNaN(num)) d.temp = Math.max(16, Math.min(30, Math.round(num))); else if (v) d.mode = v.toLowerCase(); }
    }
    if (action === "lock") d.locked = true;
    if (action === "unlock") d.locked = false;
    if (d.type === "vacuum") d.state = { start: "cleaning", stop: "paused", dock: "returning to dock" }[action];
    if (d.type === "washer") d.state = action === "start" ? "running" : "paused";
    save(); changed({ device: d.id });
    return ok({ device: d.name, now: describe(d) });
  };

  // ------------------------------------------------ care
  function slotNow(med) {  // minutes from the dose time to now
    const p = parseTime(med.time); const now = new Date();
    return now.getHours() * 60 + now.getMinutes() - (p.hour * 60 + p.minute);
  }
  // One record per medicine, with its next dose worked out here, so the model never has to
  // match a dose time to the wrong medicine.
  T.get_medication_schedule = async () => {
    const names = [...new Set(state.meds.map((m) => m.name))];
    return ok({ medicines: names.map((name) => {
      const doses = state.meds.filter((m) => m.name === name);
      const next = doses.find((m) => !m.taken);
      const due = doses.find((m) => !m.taken && slotNow(m) >= -60);
      const takenToday = doses.filter((m) => m.taken);
      const now_ = due ? "due now: it may be taken"
        : takenToday.length ? "already taken today at " + spoken(takenToday[takenToday.length - 1].taken) +
          ": do NOT take another dose now; the next one is " + (next ? "today at " + spoken(next.time) : "tomorrow at " + spoken(doses[0].time))
        : "not due yet";
      return { medicine: name, for: doses[0].for, right_now: now_,
        doses_today: doses.map((m) => spoken(m.time) + ": " + (m.taken ? "taken at " + spoken(m.taken)
          : slotNow(m) < -60 ? "later today" : "due, not taken yet")),
        next_dose: next ? "today " + spoken(next.time) : "tomorrow " + spoken(doses[0].time) };
    }) });
  };

  T.log_medication = async ({ medicine }) => {
    const same = state.meds.filter((m) => bestMatch([m], medicine, (x) => x.name + " " + x.for));
    if (!same.length) return fail("no medicine called " + medicine + " in today's schedule",
      { schedule: [...new Set(state.meds.map((m) => m.name))] });
    // the dose that is due: the nearest untaken one from two hours ago onwards
    const due = same.filter((m) => !m.taken && slotNow(m) >= -120).sort((a, b) => Math.abs(slotNow(a)) - Math.abs(slotNow(b)))[0];
    if (!due) {
      const last = same.filter((m) => m.taken).sort((a, b) => (a.taken < b.taken ? 1 : -1))[0];
      const next = same.filter((m) => !m.taken)[0];
      return { status: "refused_double_dose", medicine: same[0].name,
        taken_at: last ? spoken(last.taken) : null, next_due: next ? spoken(next.time) : "tomorrow",
        instruction: "tell the user kindly that this dose was already taken and not to take another now" };
    }
    due.taken = hhmm(new Date());
    save(); changed("meds");
    return ok({ logged: due.name, dose_time: spoken(due.time), taken_at: spoken(due.taken) });
  };

  T.set_reminder = async ({ time, text }) => T.set_alarm({ time, label: text });

  T.alert_family = async ({ reason }) => {
    const fam = contacts();
    state.alerts.push({ at: hhmm(new Date()), reason });
    save(); changed("alerts");
    if (settings().direct && isNative() && fam.length) {
      // only the people whose text the phone's radio took count as alerted
      const reached = [], missed = [];
      for (const c of fam) {
        let r = null;
        try { r = await Native.sms({ number: c.number, text: "DUET alert from " + (settings().name || "your family member") + ": " + reason, direct: true }); }
        catch (e) { r = { error: String((e && e.message) || e) }; }
        if (r && r.sent) reached.push(c.name);
        else if (r && r.unconfirmed) reached.push(c.name + " (sending, not confirmed yet)");
        else missed.push(c.name + (r && r.error ? ": " + r.error : ""));
      }
      if (!reached.length) return fail("the alert texts did not go out (" + missed.join("; ") + ")");
      return ok({ alerted: reached, how: "text message", not_reached: missed });
    }
    return ok({ alerted: fam.length ? fam.map((c) => c.name) : ["family (demo)"], how: "family app alert (simulated)" });
  };

  T.log_checkin = async ({ mood, note }) => {
    state.checkins.push({ day: today(), at: hhmm(new Date()), mood, note: note || "" });
    save(); changed("checkins");
    return ok({ recorded: mood, shared_with: "family" });
  };

  // ------------------------------------------------ drive (simulated car and trip)
  const PLACES = { home: 12, office: 18, work: 18, airport: 31, school: 7, gym: 5, mall: 9, hospital: 11, station: 14 };
  function distanceTo(place) {
    const w = words(place);
    for (const k in PLACES) if (w.includes(k)) return PLACES[k];
    let h = 0; for (const ch of String(place)) h = (h * 31 + ch.charCodeAt(0)) % 997;
    return 6 + (h % 24);
  }
  function tripView() {
    const t = state.trip;
    if (!t) return null;
    const stopMin = t.stops.length * 6;
    const minsLeft = Math.max(0, Math.round(t.leftKm / t.speedKmh * 60 + stopMin));
    return { destination: t.destination, stops: t.stops.slice(), km_left: +t.leftKm.toFixed(1),
      minutes_left: minsLeft, arrival: spoken(hhmm(new Date(Date.now() + minsLeft * 60000))),
      done: 1 - t.leftKm / t.totalKm };
  }
  T.get_trip_status = async () => {
    const v = tripView();
    return ok({ trip: v ? { destination: v.destination, stops: v.stops, km_left: v.km_left, minutes_left: v.minutes_left, arrival: v.arrival } : "no active trip",
      car: { range_km: state.car.rangeKm, ac_on: state.car.ac, cabin_temp_c: state.car.temp } });
  };
  T.set_destination = async ({ place }) => {
    const km = distanceTo(place);
    state.trip = { destination: place, stops: [], totalKm: km, leftKm: km, speedKmh: 32 };
    save(); changed("trip");
    const v = tripView();
    return ok({ destination: place, minutes: v.minutes_left, arrival: v.arrival, km: km });
  };
  T.add_stop = async ({ place }) => {
    if (!state.trip) return fail("there is no trip yet; set a destination first");
    state.trip.stops.push(place);
    save(); changed("trip");
    const v = tripView();
    return ok({ stop: place, destination: v.destination, arrival: v.arrival, minutes: v.minutes_left });
  };
  T.set_car_climate = async ({ on, temperature }) => {
    if (on != null) state.car.ac = !!on;
    if (temperature != null) { state.car.temp = Math.max(16, Math.min(30, Number(temperature))); state.car.ac = true; }
    save(); changed("car");
    return ok({ ac_on: state.car.ac, cabin_temp_c: state.car.temp });
  };
  T.open_navigation = async () => {
    if (!state.trip) return fail("there is no destination to hand over");
    return T.navigate_to({ destination: state.trip.destination, mode: "driving" });
  };
  function driveTick() {  // the simulated car moves while a trip is on
    const t = state.trip;
    if (!t || t.leftKm <= 0) return;
    t.leftKm = Math.max(0, t.leftKm - t.speedKmh / 3600 * 5);   // 5 s of driving
    save(); changed("trip");
  }
  setInterval(driveTick, 5000);
  function skipAhead(minutesLeft) {  // demo control: jump to N minutes before arrival
    const t = state.trip; if (!t) return null;
    t.stops = [];
    t.leftKm = Math.min(t.totalKm, t.speedKmh * minutesLeft / 60);
    save(); changed("trip");
    return tripView();
  }

  // ---------------------------------------------------------------- the RPC entry point
  async function run(tool, args) {
    const fn = T[tool];
    if (!fn) return fail("this phone has no tool " + tool);
    try {
      const out = await fn(args || {});
      return out;
    } catch (e) {
      const msg = (e && (e.message || e.errorMessage)) || String(e);
      if (/permission/i.test(msg)) return needs("android", msg);
      return fail(msg);
    }
  }

  function toast(text) { if (window.DuetUI) window.DuetUI.toast(text); }

  window.DuetPhone = {
    NATIVE: NATIVE_APP, get acting() { return isNative(); }, selfTest, run, reset, settings, contacts, skipAhead, tripView, describe, spoken,
    set backgroundListening(on) { backgroundListening = !!on; }, get backgroundListening() { return backgroundListening; },
    get state() { return state; },
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    native: Native,
  };
})();
