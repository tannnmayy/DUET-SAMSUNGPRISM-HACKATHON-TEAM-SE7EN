/* DUET for Galaxy: connection, conversation and the live view of DUET's two minds. */
(function () {
  "use strict";

  const LK = window.LivekitClient;
  const P = window.DuetPhone;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---------------------------------------------------------------- settings
  const SKEY = "duet.settings";
  function readSettings() { try { return JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { return {}; } }
  function writeSettings(s) { try { localStorage.setItem(SKEY, JSON.stringify(s)); } catch (e) { /* private mode */ } }
  const S = readSettings();
  if (!S.contacts) S.contacts = "Priya (daughter), +910000000001\nRahul (son), +910000000002";
  $("set-server").value = S.server || "";
  $("set-name").value = S.name || "";
  $("set-contacts").value = S.contacts;
  $("set-direct").checked = !!S.direct;
  $("set-city").value = S.city || "";
  $("set-simulate").checked = !!S.simulate;
  $("set-alarms").value = S.alarms === "clock" ? "clock" : "duet";
  writeSettings(S);
  for (const [id, key, prop] of [["set-server", "server", "value"], ["set-name", "name", "value"],
    ["set-contacts", "contacts", "value"], ["set-direct", "direct", "checked"], ["set-city", "city", "value"],
    ["set-simulate", "simulate", "checked"], ["set-alarms", "alarms", "value"]]) {
    $(id).addEventListener("change", () => { S[key] = $(id)[prop]; writeSettings(S); });
  }
  const servedHere = !P.NATIVE && /^https?:$/.test(location.protocol);
  if (servedHere) $("server-field").hidden = true;
  $("reset-demo").addEventListener("click", () => { P.reset(); toast("Demo data reset"); });

  // the phone self-test: every real action, tried safely (phone.js selfTest)
  $("self-test").addEventListener("click", async () => {
    const list = $("selftest-results");
    list.hidden = false;
    list.innerHTML = "<li>Running…</li>";
    const r = await P.selfTest();
    list.innerHTML = "<li><b>" + r.passed + " of " + r.total + " work on " + esc(r.device) + "</b></li>" +
      r.results.map((x) => "<li class='" + (x.ok ? "ok" : "bad") + "'>" + (x.ok ? "✓ " : "✕ ") + esc(x.name) +
        " <small>" + x.ms + " ms · " + esc(x.detail) + "</small></li>").join("");
  });

  // ---------------------------------------------------------------- phone permissions (Android app)
  async function refreshPerms() {
    if (!P.NATIVE) return;
    $("perm-list").hidden = false;
    try {
      const p = await P.native.permissions();
      document.querySelectorAll(".perm").forEach((b) => {
        const granted = !!p[b.dataset.perm];
        b.classList.toggle("granted", granted);
        b.querySelector("em").textContent = granted ? "Allowed" : "Allow";
      });
    } catch (e) { console.warn(e); }
  }
  document.querySelectorAll(".perm").forEach((b) => b.addEventListener("click", async () => {
    try { await P.native.requestPermission({ name: b.dataset.perm }); } catch (e) { toast(String(e.message || e)); }
    setTimeout(refreshPerms, 800);
  }));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshPerms(); });
  refreshPerms();

  // ---------------------------------------------------------------- toast
  let toastTimer = null;
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
  }
  window.DuetUI = { toast };

  // ---------------------------------------------------------------- words for the screen
  const TOOL_TEXT = {
    set_alarm: (a) => "Set an alarm for " + P.spoken(a.time),
    cancel_alarm: (a) => "Cancel the " + P.spoken(a.time) + " alarm",
    list_alarms: () => "Check the alarms",
    set_timer: (a) => "Start a " + a.minutes + " min timer",
    get_battery_report: () => "Read battery, screen and app usage",
    get_app_usage: () => "Read screen time per app",
    list_installed_apps: () => "List the installed apps",
    set_brightness: (a) => a.percent != null ? "Brightness to " + a.percent + "%" : "Adaptive brightness " + (a.adaptive ? "on" : "off"),
    set_screen_timeout: (a) => "Screen timeout to " + a.seconds + " s",
    open_settings: (a) => "Open " + String(a.page).replace(/_/g, " ") + " settings",
    open_app: (a) => "Open " + a.name,
    navigate_to: (a) => "Navigate to " + a.destination,
    call_contact: (a) => "Call " + a.name,
    send_message: (a) => "Text " + a.to + ": “" + a.text + "”",
    get_home_status: () => "Check the home",
    control_home_device: (a) => String(a.action).replace("_", " ") + " " + a.device + (a.value ? " (" + a.value + ")" : ""),
    get_medication_schedule: () => "Check today's medicines",
    log_medication: (a) => "Log a dose of " + a.medicine,
    set_reminder: (a) => "Reminder at " + P.spoken(a.time) + ": " + a.text,
    alert_family: (a) => "Alert the family: " + a.reason,
    log_checkin: (a) => "Check-in: feeling " + a.mood,
    get_trip_status: () => "Check the trip",
    set_destination: (a) => "Destination: " + a.place,
    add_stop: (a) => "Add a stop: " + a.place,
    set_car_climate: (a) => "Car AC " + (a.on === false ? "off" : "on") + (a.temperature ? " at " + a.temperature + "°C" : ""),
    open_navigation: () => "Hand the route to Google Maps",
    set_torch: (a) => "Torch " + (a.on ? "on" : "off"),
    control_media: (a) => ({ play: "Play", pause: "Pause", next: "Next track", previous: "Previous track" }[a.action] || "Media"),
    set_volume: (a) => "Volume to " + a.percent + "%",
    create_event: (a) => "Calendar: " + a.title + " on " + a.date + " at " + P.spoken(a.time),
    get_weather: (a) => "Weather in " + a.place + (a.day && a.day !== "today" ? " (" + a.day + ")" : ""),
    web_search: (a) => "Search the web: “" + a.question + "”",
  };
  // where each step ran: on the phone, or on DUET's side (its own tools)
  const SERVER_SIDE = new Set(["get_weather", "web_search"]);
  const toolText = (t, a) => { try { return (TOOL_TEXT[t] || (() => t))(a || {}); } catch (e) { return t; } };
  const key = (t, a) => t + ":" + JSON.stringify(a || {});

  // ---------------------------------------------------------------- the conversation view
  const chat = $("chat"), timeline = $("timeline");
  let lastUser = null, duetOpen = null, toolRows = new Map();
  const setAlarms = new Set();   // alarms DUET set in this conversation (a later cancel is an undo)

  function addChat(cls, text) {
    const li = document.createElement("li");
    li.className = cls; li.textContent = text;
    chat.appendChild(li); chat.scrollTop = chat.scrollHeight;
    return li;
  }
  // the page that embeds the app (the demo stage), if any, gets a copy of every step
  const toParent = (msg) => { if (window.parent !== window) window.parent.postMessage(msg, "*"); };
  let stepId = 0;
  function addStep(cls, icon, html, sub) {
    const li = document.createElement("li");
    li.dataset.sid = String(++stepId);
    li.className = cls;
    li.innerHTML = "<i>" + icon + "</i><div>" + html + (sub ? "<small>" + sub + "</small>" : "") + "</div>";
    timeline.appendChild(li); timeline.scrollTop = timeline.scrollHeight;
    while (timeline.children.length > 60) timeline.removeChild(timeline.firstChild);
    toParent({ type: "step", id: li.dataset.sid, cls, icon, html, sub: sub || "" });
    return li;
  }
  function setStep(li, cls, icon, html, sub) {
    li.className = cls;
    li.innerHTML = "<i>" + icon + "</i><div>" + html + (sub ? "<small>" + sub + "</small>" : "") + "</div>";
    moment(cls, icon + "  " + li.querySelector("div").firstChild.textContent);
    toParent({ type: "step", id: li.dataset.sid, cls, icon, html, sub: sub || "" });
  }
  // the latest DUET moment, under the orb, for a phone screen
  function moment(cls, text) {
    const m = $("moment");
    m.className = "moment " + cls; m.textContent = text; m.hidden = false;
  }
  function summary(result) {
    if (!result || typeof result !== "object") return "";
    const skip = new Set(["status", "source", "top_apps_today", "note", "instruction"]);
    return Object.entries(result).filter(([k, v]) => !skip.has(k) && v != null && typeof v !== "object")
      .slice(0, 4).map(([k, v]) => k.replace(/_/g, " ") + ": " + v).join(" · ");
  }

  const ORB_TEXT = { listening: "Listening… talk any time", thinking: "Thinking…", speaking: "Speaking: interrupt any time",
    initializing: "Getting ready…", connecting: "Connecting…", ended: "Conversation ended", waiting: "Waiting for you to finish…" };
  function orb(state) {
    $("orb").dataset.state = state === "waiting" ? "listening" : state;
    $("orb-label").textContent = ORB_TEXT[state] || "";
  }

  // demo mode (demo-driver.js): a scripted voice instead of the microphone, and a recorder
  const hooks = { events: [], mic: null, remoteAudio: null };

  function onEvent(ev) {
    hooks.events.forEach((fn) => { try { fn(ev); } catch (e) { console.error(e); } });
    toParent({ type: "duet", ev });
    switch (ev.kind) {
      case "session_start":
        reconnects = 0; rejoinUntil = 0;   // a fresh conversation: a later drop gets its own 3 minutes
        $("conn").textContent = "Connected · brain: " + (ev.model || "?");
        homeLive = null;
        homeNote = ev.home === "simulated" ? (ev.home_why || "") : "";
        twoSpeeds = /\(fast\)/.test(ev.model || "");
        cloudOff = false;
        renderPanel("all");
        break;
      case "drill":
        cloudOff = !!ev.cloud_off;
        addStep(cloudOff ? "t-fail" : "t-done", cloudOff ? "☁✕" : "☁",
          cloudOff ? "Drill: the cloud mind is cut off" : "Drill over: the cloud mind is back",
          cloudOff ? "simple actions stay on the fast mind on our own machine" : "");
        renderPanel("all");
        break;
      case "home": {
        const was = homeLive && homeLive.devices ? new Map(homeLive.devices.map((d) => [d.name, d.state])) : null;
        homeLive = ev;
        renderPanel("all");
        if (was && ev.devices) {   // flash what just changed
          ev.devices.filter((d) => was.get(d.name) !== d.state).forEach((d) => {
            const el = document.querySelector('#panel .device[data-name="' + CSS.escape(d.name) + '"]');
            if (el) el.classList.add("flash");
          });
        }
        break;
      }
      case "agent_state":
        orb(ev.state);
        break;
      case "user_state":
        if (ev.state === "speaking") orb("listening");
        break;
      case "heard":
        if (!ev.text || !ev.text.trim()) break;
        if (lastUser && !duetOpen) { lastUser.textContent += " " + ev.text.trim(); chat.scrollTop = chat.scrollHeight; }
        else { lastUser = addChat("user", ev.text.trim()); duetOpen = null; }
        addStep("t-heard", "↓", "Heard: “" + esc(ev.text.trim()) + "”");
        break;
      case "thinker_listen":
        orb("waiting");
        addStep("t-listen", "…", "Waiting: you haven't finished", esc(ev.reason || ""));
        moment("t-listen", "…  Waiting for you to finish");
        break;
      case "talker":
        if (ev.text) {
          duetOpen = addChat("duet ack", ev.text); lastUser = null;
          addStep("t-say", "💬", "Quick voice: “" + esc(ev.text) + "”", "said at once while DUET thinks");
        }
        break;
      case "route":
        addStep("t-route", ev.where === "edge" ? "⚡" : "☁",
          ev.where === "edge" ? "Fast mind: Gemma 4 E2B on our own GPU" : "Cloud mind: Gemma 4 26B",
          esc(ev.why || ""));
        break;
      case "confirmed":
        if (ev.text) {
          duetOpen = addChat("duet", ev.text); lastUser = null;
          addStep("t-say", "✓", "Confirmed: “" + esc(ev.text) + "”", "from the phone's reply, no model call");
        }
        break;
      case "thinker_say":
        if (ev.text && !/<silent>/.test(ev.text)) {
          duetOpen = addChat("duet", ev.text); lastUser = null;
          addStep("t-say", "◆", "Slow mind answered", ev.after_s != null ? "after " + ev.after_s + " s" : "");
        }
        break;
      case "agent_said":
        if (!duetOpen && ev.text) { duetOpen = addChat("duet", ev.text); lastUser = null; }
        if (ev.interrupted) {
          if (duetOpen) duetOpen.classList.add("cut");
          addStep("t-cut", "✂", "You interrupted: DUET stopped talking",
            ev.text ? "you had heard: “" + esc(ev.text.slice(-120)) + "”" : "");
        }
        duetOpen = null;
        break;
      case "thinker_error":
        if (/cannot reach|offline drill/i.test(ev.text || "")) {
          addStep("t-fail", "☁✕", "Cloud mind unreachable: DUET said so at once", esc(ev.text || ""));
        } else addStep("t-fail", "!", "Something went wrong in the slow mind", esc(ev.text || ""));
        break;
      case "superseded":
        addStep("t-drop", "↺", "Plan dropped: you kept talking", "nothing was done with the old words");
        break;
      case "tool_planned": {
        const li = addStep("t-plan", "⏸", "Planned: " + esc(toolText(ev.tool, ev.args)),
          "held until you've been quiet " + ev.hold_s + " s");
        moment("t-plan", "⏸  Holding: " + toolText(ev.tool, ev.args));
        toolRows.set(key(ev.tool, ev.args), li);
        break;
      }
      case "tool_done": case "tool_dropped": case "tool_cached": case "tool_failed": case "tool_unknown": case "tool_asked": {
        const k = key(ev.tool, ev.args);
        const li = toolRows.get(k) || addStep("", "", "");
        toolRows.delete(k);
        const what = esc(toolText(ev.tool, ev.args));
        const r0 = ev.result || {};
        const where = r0.source === "SmartThings" ? "in your SmartThings home"
          : SERVER_SIDE.has(ev.tool) ? "looked up by DUET" : "on the phone";
        if (ev.kind === "tool_done" && ev.tool === "set_alarm") setAlarms.add(String(ev.args.time));
        if (ev.kind === "tool_done" && ev.tool === "cancel_alarm" && ev.result && ev.result.cancelled === false) {
          setStep(li, "t-cached", "=", "Nothing to cancel: " + esc(P.spoken(ev.args.time)) + " was never set", "DUET had held it back");
        } else if (ev.kind === "tool_done" && ev.tool === "cancel_alarm" && setAlarms.has(String(ev.args.time))) {
          setAlarms.delete(String(ev.args.time));
          setStep(li, "t-undo", "↶", "Undone: the " + esc(P.spoken(ev.args.time)) + " alarm", "you took it back after it was set, so DUET reversed it");
        } else if (ev.kind === "tool_done" && r0.status === "sent") {
          setStep(li, "t-fail", "?", "Sent, not confirmed yet: " + what, [where, esc(r0.note || "")].filter(Boolean).join(" · "));
        } else if (ev.kind === "tool_done") {
          setStep(li, "t-done", "✓", "Done: " + what, [where, esc(summary(ev.result))].filter(Boolean).join(" · "));
        }
        if (ev.kind === "tool_dropped") setStep(li, "t-drop", "✕", "Never ran: " + what, "you corrected yourself before it committed");
        if (ev.kind === "tool_cached") setStep(li, "t-cached", "=", "Not repeated: " + what, "it already ran for this request");
        if (ev.kind === "tool_asked") setStep(li, "t-cached", "?", "Asked first: " + what, "you hadn't asked for this, so DUET offers it instead");
        if (ev.kind === "tool_unknown") setStep(li, "t-fail", "?", "Unconfirmed: " + what, esc(ev.error || ""));
        if (ev.kind === "tool_failed") {
          const r = ev.result || {};
          setStep(li, "t-fail", "!", (r.status === "needs_permission" ? "Needs your permission: " : "Failed: ") + what,
            esc(r.instruction || r.error || ev.error || ""));
        }
        break;
      }
    }
  }

  // ---------------------------------------------------------------- mode panels
  let mode = "assistant";
  let battery = null;
  P.onChange((what) => { if (what && what.battery) battery = what.battery; renderPanel(what); });

  let twoSpeeds = false;      // a fast mind runs next to the cloud mind (the drill needs it)
  let cloudOff = false;       // the offline drill is on
  // the home: live from SmartThings when DUET has a token (the "home" events), else simulated in the app
  let homeLive = null;        // {source, devices: [{name, room, kind, state, on}], error}
  let homeNote = "";          // why the home is simulated, from the session start
  function devicesHTML() {
    if (homeLive && homeLive.devices) {
      return '<p class="sub">Home · SmartThings (live)</p><div class="devices">' + homeLive.devices.map((d) =>
        '<div class="device' + (d.on ? " on" : "") + '" data-name="' + esc(d.name) + '"><b>' + esc(d.name) + "</b><small>" +
        esc(d.state) + (d.room ? " · " + esc(d.room) : "") + "</small></div>").join("") + "</div>";
    }
    const label = homeLive && homeLive.error ? "Home · SmartThings unavailable: " + esc(homeLive.error)
      : "Home · simulated" + (homeNote ? " <small>(" + esc(homeNote) + ")</small>" : "");
    return '<p class="sub">' + label + '</p><div class="devices">' + P.state.home.map((d) => {
      const on = d.on || d.locked === false || d.state === "cleaning" || d.state === "running";
      return '<div class="device' + (on ? " on" : "") + '" data-id="' + d.id + '"><b>' + esc(d.name) + "</b><small>" + esc(P.describe(d)) + "</small></div>";
    }).join("") + "</div>";
  }

  function renderPanel(what) {
    const el = $("panel");
    let html = "";
    if (mode === "assistant") {
      const b = battery;
      html += '<header class="card-head"><b>This phone</b><small>' + (b ? esc(b.source) : "DUET reads it when you ask") + "</small></header>";
      html += '<div class="stats">' +
        stat("Battery", b ? b.level + "%" : "—") +
        stat("Temperature", b && b.temperature_c != null ? b.temperature_c + "°C" : "—", b && b.temperature_c >= 40) +
        stat("Brightness", b && b.brightness_percent != null ? b.brightness_percent + "%" + (b.adaptive_brightness ? " auto" : "") : "—") +
        stat("Refresh", b && b.refresh_rate_hz ? Math.round(b.refresh_rate_hz) + " Hz" : "—") + "</div>";
      if (b && Array.isArray(b.top_apps_today)) html += '<p class="sub">Most used today</p><ul class="list">' + b.top_apps_today.slice(0, 4).map((a) => "<li>" + esc(a) + "</li>").join("") + "</ul>";
      const alarms = P.state.alarms;
      html += '<p class="sub">Alarms DUET set</p>' + (alarms.length ? '<ul class="list">' + alarms.map((a) =>
        "<li><span>" + esc(P.spoken(a.time)) + (a.label ? " <small>" + esc(a.label) + "</small>" : "") + '</span><span class="pill on">set at ' + esc(a.at) + "</span></li>").join("") + "</ul>"
        : '<p class="note">None yet. Try: “Set an alarm for 3:30, no, cancel that, 4:30.”</p>');
      html += devicesHTML();
    } else if (mode === "care") {
      html += '<header class="card-head"><b>Today</b><small>what DUET and the family can see</small></header><p class="sub">Medicines</p><ul class="list">';
      html += P.state.meds.map((m) => "<li><span>" + esc(m.name) + " <small>" + esc(P.spoken(m.time)) + "</small></span>" +
        (m.taken ? '<span class="pill on">taken ' + esc(P.spoken(m.taken)) + "</span>" : '<span class="pill due">not yet</span>') + "</li>").join("") + "</ul>";
      const c = P.state.checkins.filter((x) => x.day === new Date().toISOString().slice(0, 10)).slice(-1)[0];
      html += '<p class="sub">Check-in</p><ul class="list"><li><span>' + (c ? "Feeling " + esc(c.mood) + (c.note ? " <small>" + esc(c.note) + "</small>" : "") : "Not yet today") + "</span>" +
        (c ? '<span class="pill on">' + esc(c.at) + "</span>" : "") + "</li></ul>";
      if (P.state.alerts.length) html += '<p class="sub">Alerts sent to family</p><ul class="list">' + P.state.alerts.slice(-3).map((a) => "<li><span>" + esc(a.reason) + '</span><span class="pill alert">' + esc(a.at) + "</span></li>").join("") + "</ul>";
      html += '<div class="row-btns"><button class="chip" id="ev-med">Simulate: evening medicine time</button></div>';
      html += devicesHTML();
    } else {
      const t = P.tripView();
      html += '<header class="card-head"><b>Trip</b><small>simulated drive · hands off to Google Maps</small></header>';
      if (t) {
        html += '<div class="trip"><span class="dest">' + esc(t.destination) + '</span><span class="eta">' + t.minutes_left + " min · " + t.km_left + " km · arrive " + esc(t.arrival) + "</span>" +
          (t.stops.length ? '<span class="eta">via ' + esc(t.stops.join(", ")) + "</span>" : "") + '</div><div class="progress"><span style="width:' + Math.round(t.done * 100) + '%"></span></div>';
      } else html += '<p class="note">No trip yet. Try: “Take me to the office, no wait, the airport.”</p>';
      html += '<div class="stats">' + stat("Car AC", P.state.car.ac ? "on" : "off") + stat("Cabin", P.state.car.temp + "°C") + stat("Range", P.state.car.rangeKm + " km") + "</div>";
      html += '<div class="row-btns"><button class="chip" id="ev-near"' + (t ? "" : " disabled") + '>Simulate: 10 min from destination</button></div>';
      html += devicesHTML();
    }
    if (twoSpeeds) {   // the offline drill: cut DUET's cloud mind, live, and watch the fast mind carry on
      html += '<div class="row-btns"><button class="chip" id="drill">' +
        (cloudOff ? "Restore the cloud mind" : "Drill: cut the cloud mind") + "</button></div>";
    }
    el.innerHTML = html;
    const drill = $("drill");
    if (drill) drill.onclick = () => {
      if (!room) return;
      room.localParticipant.publishData(enc.encode(JSON.stringify({ kind: "drill", cloud_off: !cloudOff })),
        { reliable: true, topic: "duet.phone" });
    };
    if (what && what.device) { const d = el.querySelector('[data-id="' + what.device + '"]'); if (d) d.classList.add("flash"); }
    const near = $("ev-near");
    if (near) near.onclick = () => simulate("near");
    const med = $("ev-med");
    if (med) med.onclick = () => simulate("medicine");
  }
  // the demo buttons: a phone or car event DUET reacts to without being asked
  function simulate(kind) {
    if (kind === "near") {
      const v = P.skipAhead(10);
      if (v) phoneEvent("The car is 10 minutes from " + v.destination + ".");
    }
    if (kind === "medicine") phoneEvent("It is medicine time: the evening dose of Metformin 500 mg is due now.");
  }
  function stat(label, value, hot) { return '<div class="stat' + (hot ? " hot" : "") + '"><small>' + label + "</small><b>" + esc(value) + "</b></div>"; }

  // ---------------------------------------------------------------- LiveKit
  let room = null;
  const enc = new TextEncoder(), dec = new TextDecoder();

  function serverBase() {
    if (servedHere) return location.origin;
    let s = (S.server || "").trim();
    if (!s) return null;
    if (!/^https?:\/\//.test(s)) s = "http://" + s;
    if (!/:\d+$/.test(s.replace(/\/$/, ""))) s = s.replace(/\/$/, "") + ":8787";
    return s.replace(/\/$/, "");
  }

  // ---------------------------------------------------------------- is everything up? (home screen)
  async function refreshHealth() {
    const el = $("health"), base = serverBase();
    if (!base || !$("session").hidden) return;
    try {
      const h = await (await fetch(base + "/api/health", { cache: "no-store" })).json();
      const part = (name, x) => x.ok === null ? "" : (x.ok ? "✓ " : "✕ ") + name + (x.ok ? "" : " <small>(" + esc(x.why || "") + ")</small>");
      const parts = [part("Cloud mind", h.cloud_mind), part("Fast mind", h.fast_mind),
        h.smartthings.ok === null ? "" : part("SmartThings" + (h.smartthings.devices ? " · " + h.smartthings.devices + " devices" : ""), h.smartthings)]
        .filter(Boolean);
      const allOk = [h.cloud_mind, h.fast_mind, h.smartthings].every((x) => x.ok !== false);
      el.className = "health " + (allOk ? "ok" : "bad");
      el.innerHTML = (allOk ? "Ready · " : "Check · ") + parts.join(" · ");
    } catch (e) {
      el.className = "health bad";
      el.textContent = "✕ The laptop running DUET does not answer at " + base;
    }
    el.hidden = false;
  }
  refreshHealth();
  setInterval(refreshHealth, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshHealth(); });
  $("set-server").addEventListener("change", refreshHealth);

  async function deviceLine() {
    if (P.NATIVE) { try { const d = await P.native.deviceInfo(); return d.line; } catch (e) { return "Android phone"; } }
    return "a laptop browser (phone actions simulated)";
  }

  function phoneEvent(text) {
    if (!room) return;
    room.localParticipant.publishData(enc.encode(JSON.stringify({ kind: "event", text })), { reliable: true, topic: "duet.phone" });
    addStep("t-heard", "⚙", "Phone event sent to DUET", esc(text));
  }

  // The user wants a conversation: after a drop (the network, or DUET's laptop restarting, which takes about
  // a minute), the app rejoins on its own for up to 3 minutes, then leaves it to the user.
  let wantRoom = false, reconnects = 0, rejoinUntil = 0, rejoinTimer = null;
  // One microphone for the whole conversation: a rejoin publishes the same one again. A new capture opened
  // while another app is in front (Google Maps) never starts on Android 16, so DUET would stop hearing.
  let micTrack = null;
  function rejoinSoon(why, ms) {
    if (!wantRoom || rejoinTimer) return;
    if (!rejoinUntil) rejoinUntil = Date.now() + 180000;
    if (Date.now() > rejoinUntil) {
      $("conn").textContent = "Could not reconnect";
      addStep("t-fail", "!", "Could not reconnect", "tap End, then start the conversation again");
      return;
    }
    reconnects++;
    addStep("t-fail", "↻", why + ": reconnecting", "attempt " + reconnects);
    if (reconnects === 1) toast(why + ", reconnecting…");
    rejoinTimer = setTimeout(() => { rejoinTimer = null; if (wantRoom && !room) start(mode, null, true); }, ms);
  }

  async function start(m, creds, again) {
    const base = serverBase();
    if (!creds && !base) { $("settings").open = true; $("set-server").focus(); toast("Enter the server address first, or scan the laptop's code"); return; }
    wantRoom = true;
    if (!again) {   // a new conversation: a fresh rejoin window, and the microphone starts unmuted
      reconnects = 0; rejoinUntil = 0;
      $("mute").setAttribute("aria-pressed", "false"); $("mute").setAttribute("aria-label", "Mute microphone");
    }
    mode = m;
    document.body.classList.remove("mode-assistant", "mode-care", "mode-drive");
    document.body.classList.add("mode-" + m);
    $("home").hidden = true; $("session").hidden = false;
    $("mode-title").textContent = { assistant: "Assistant", care: "Care", drive: "Drive" }[m];
    $("conn").textContent = "Connecting…";
    chat.innerHTML = ""; timeline.innerHTML = ""; toolRows = new Map(); lastUser = duetOpen = null; setAlarms.clear();
    $("moment").hidden = true;
    orb("connecting");
    renderPanel("all");
    if (m === "assistant" && P.acting) P.run("get_battery_report", {});
    try {
      let url, token;
      if (creds) ({ url, token } = creds);
      else {
        const names = P.contacts().map((c) => c.name).join("; ");
        const q = new URLSearchParams({ mode: m, name: S.name || "", city: S.city || "", device: await deviceLine(), contacts: names });
        const resp = await fetch(base + "/api/token?" + q.toString());
        if (!resp.ok) throw new Error("server said " + resp.status + ": " + (await resp.text()).slice(0, 120));
        ({ url, token } = await resp.json());
      }

      room = new LK.Room({ adaptiveStream: false, dynacast: false, stopLocalTrackOnUnpublish: false,
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      room.registerRpcMethod("duet.tool", async (data) => {
        let req = {};
        try { req = JSON.parse(data.payload || "{}"); } catch (e) { return JSON.stringify({ status: "error", error: "bad request" }); }
        const out = await P.run(req.tool, req.args);
        return JSON.stringify(out);
      });
      room.on(LK.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
        if (topic !== "duet") return;
        try { onEvent(JSON.parse(dec.decode(payload))); } catch (e) { console.warn(e); }
      });
      room.on(LK.RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === "audio") {
          const a = track.attach(); a.autoplay = true; document.body.appendChild(a);
          if (hooks.remoteAudio) hooks.remoteAudio(track);
        }
      });
      room.on(LK.RoomEvent.TrackUnsubscribed, (track) => { track.detach().forEach((a) => a.remove()); });
      const thisRoom = room;
      let up = false;   // this room was connected (a room that never connected leaves through the catch below)
      room.on(LK.RoomEvent.Disconnected, () => {
        if (room === thisRoom) room = null;
        orb("ended"); $("conn").textContent = "Disconnected";
        // the connection dropped (not the user ending it): join a new conversation
        if (up) rejoinSoon("Connection lost", 1500);
      });
      room.on(LK.RoomEvent.ParticipantDisconnected, (p) => {
        if (p.isAgent || /agent/.test(p.identity)) {
          orb("ended"); $("conn").textContent = "DUET left the room";
          // the agent restarted or failed: leave, and the rejoin above starts a fresh conversation
          if (wantRoom && room === thisRoom) thisRoom.disconnect(false);   // false: keep the microphone
        }
      });

      await room.connect(url, token);
      up = true;
      // DUET joins within seconds; a room it never joins (its laptop was still starting) is left for a new one
      setTimeout(() => {
        const here = [...thisRoom.remoteParticipants.values()].some((p) => p.isAgent || /agent/.test(p.identity));
        if (wantRoom && room === thisRoom && !here) {
          addStep("t-fail", "!", "DUET did not join", "trying a new conversation");
          thisRoom.disconnect(false);
        }
      }, 30000);
      await room.startAudio();
      if (hooks.mic) await hooks.mic(room);
      else {
        if (!micTrack || micTrack.mediaStreamTrack.readyState === "ended") {
          micTrack = await LK.createLocalAudioTrack({ echoCancellation: true, noiseSuppression: true, autoGainControl: true });
        }
        await room.localParticipant.publishTrack(micTrack, { source: LK.Track.Source.Microphone });
      }
      if (P.NATIVE) {  // after the microphone: WebRTC has switched the phone to call audio by now
        try { await P.native.audioRoute({}); } catch (e) { console.warn(e); }
        try { await P.native.keepAwake({ on: true }); } catch (e) { console.warn(e); }
        // keep listening while another app (Google Maps) is in front; a failure here never stops the talk
        try { await P.native.listenInBackground({ on: true }); P.backgroundListening = true; }
        catch (e) { P.backgroundListening = false; console.warn("background listening:", e); }
      }
      $("conn").textContent = "Connected · waiting for DUET…";
      orb("initializing");
    } catch (e) {
      console.error(e);
      if (room && room.state !== "connected") { const r = room; room = null; try { await r.disconnect(false); } catch (x) { /* never connected */ } }
      orb("ended");
      $("conn").textContent = "Could not connect";
      addStep("t-fail", "!", "Could not connect", esc(e.message || String(e)));
      if (again) rejoinSoon("DUET's laptop did not answer", 3000);   // it may be restarting
    }
  }

  async function end() {
    wantRoom = false;
    if (rejoinTimer) { clearTimeout(rejoinTimer); rejoinTimer = null; }
    if (room) { try { await room.disconnect(); } catch (e) { /* already gone */ } room = null; }
    if (micTrack) { micTrack.stop(); micTrack = null; }
    if (P.NATIVE) {
      try { await P.native.keepAwake({ on: false }); } catch (e) { /* ignore */ }
      try { await P.native.listenInBackground({ on: false }); } catch (e) { /* ignore */ }
      P.backgroundListening = false;
    }
    document.querySelectorAll("body > audio").forEach((a) => a.remove());
    $("session").hidden = true; $("home").hidden = false;
    document.body.classList.remove("mode-assistant", "mode-care", "mode-drive");
  }

  document.querySelectorAll(".mode-card").forEach((b) => b.addEventListener("click", () => start(b.dataset.mode)));

  // ---------------------------------------------------------------- pairing by QR code
  // The laptop's pair page shows "DUET1|mode|livekit url|room token": the phone joins with it
  // directly, so it needs only internet, not a network path to the laptop.
  let camStream = null, scanning = false;
  async function scan() {
    $("scanner").hidden = false;
    try {
      camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    } catch (e) { stopScan(); toast("The camera is not available: " + (e.message || e)); return; }
    const video = $("cam"); video.srcObject = camStream; await video.play();
    const canvas = document.createElement("canvas"), g = canvas.getContext("2d", { willReadFrequently: true });
    scanning = true;
    const tick = () => {
      if (!scanning) return;
      if (video.videoWidth) {
        const w = 640, h = Math.round(640 * video.videoHeight / video.videoWidth);
        canvas.width = w; canvas.height = h; g.drawImage(video, 0, 0, w, h);
        const code = window.jsQR(g.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: "dontInvert" });
        if (code && code.data.startsWith("DUET1|")) {
          const [, m, url, token] = code.data.split("|");
          stopScan();
          start(["assistant", "care", "drive"].includes(m) ? m : "assistant", { url, token });
          return;
        }
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  function stopScan() {
    scanning = false;
    if (camStream) { camStream.getTracks().forEach((t) => t.stop()); camStream = null; }
    $("scanner").hidden = true;
  }
  $("scan").addEventListener("click", scan);
  $("scan-cancel").addEventListener("click", stopScan);
  $("end").addEventListener("click", end);
  $("mute").addEventListener("click", async () => {
    if (!room) return;
    const muted = $("mute").getAttribute("aria-pressed") === "true";
    await room.localParticipant.setMicrophoneEnabled(muted);
    $("mute").setAttribute("aria-pressed", String(!muted));
    $("mute").setAttribute("aria-label", muted ? "Mute microphone" : "Unmute microphone");
  });

  // the orb follows whoever is talking
  setInterval(() => {
    if (!room) return;
    let level = 0;
    const agent = [...room.remoteParticipants.values()][0];
    if ($("orb").dataset.state === "speaking" && agent) level = agent.audioLevel || 0;
    else level = room.localParticipant.audioLevel || 0;
    $("orb").style.setProperty("--level", Math.min(1, level * 2.5).toFixed(2));
  }, 100);

  window.DuetApp = { start, end, onEvent, phoneEvent, simulate, hooks, settings: S, get room() { return room; } };
})();
