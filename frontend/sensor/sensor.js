"use strict";

/*
 * Sensor simulator. Plays the hardware side of the system:
 *   - Arduino slot nodes (5 IR sensors each) and the gateway that talks to the server
 *   - the entry and exit number plate cameras
 * It only uses the public HTTP API, exactly like the real hardware would.
 */

const SLOTS_PER_NODE = 5;
const REFRESH_MS = 4000;
const LOG_LIMIT = 60;
const KEY_STORE = "sensorGatewayKey";
const PLATE_STORE = "sensorLastEntryPlate";

const SLOT_STATES = {
  free: "Free",
  held: "Assigned, waiting",
  occupied: "Occupied",
  unbooked: "No booking",
  offline: "Sensor offline",
};

const $ = (id) => document.getElementById(id);

// ---- helpers --------------------------------------------------------------

/** Build an element. String children become text nodes (never HTML). */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "onclick") el.addEventListener("click", value);
    else if (key.startsWith("data-") || key.startsWith("aria-")) el.setAttribute(key, value);
    else el[key] = value;
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Replace children only when the markup changed, so a refresh never eats a click. */
function swap(host, ...nodes) {
  const probe = document.createElement("div");
  probe.append(...nodes.flat());
  if (probe.innerHTML === host.innerHTML) return;
  host.replaceChildren(...probe.childNodes);
}

function store(key, fallback = "") {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: value resets on reload */
  }
}

const getKey = () => store(KEY_STORE);

/** Same contract as window.api, plus the X-Gateway-Key header (shared/api.js has no header option). */
async function call(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const key = getKey();
  if (key) headers["X-Gateway-Key"] = key;
  let response;
  try {
    response = await fetch(window.APP_CONFIG.API_BASE_URL.replace(/\/$/, "") + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("Cannot reach the server. Check API_BASE_URL in config.js and that the backend is running.");
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = Array.isArray(payload?.detail) ? payload.detail.map((d) => d.msg).join(" ") : payload?.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status}).`);
  }
  return payload;
}

const clock = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function randomPlate() {
  const letters = () => String.fromCharCode(65 + Math.floor(Math.random() * 26));
  const digits = (n) => String(Math.floor(Math.random() * 10 ** n)).padStart(n, "0");
  return `TS${digits(2)}${letters()}${letters()}${digits(4)}`;
}

// ---- state ----------------------------------------------------------------

const sim = {
  slots: [], // from the server, sorted floor then name
  plates: new Map(), // slot id -> number plate of the active booking
  physical: new Map(), // slot id -> true when a car blocks the IR sensor (the "real world")
  pending: new Set(), // slot ids whose reading has not reached the server yet
  nodeOnline: new Map(), // node number -> boolean
  registeredFor: "", // signature of the slot list last sent to /gateway/slot-map
  initialised: new Set(),
};

const byName = (a, b) =>
  String(a.floor).localeCompare(String(b.floor), undefined, { numeric: true }) ||
  String(a.name).localeCompare(String(b.name), undefined, { numeric: true }) ||
  String(a.id).localeCompare(String(b.id));

/** Slots grouped into nodes of five, the way the hardware is wired. */
function nodes() {
  const out = [];
  sim.slots.forEach((slot, index) => {
    const number = Math.floor(index / SLOTS_PER_NODE) + 1;
    if (!out[number - 1]) out[number - 1] = { number, slots: [] };
    out[number - 1].slots.push({ slot, channel: index % SLOTS_PER_NODE });
  });
  return out;
}

function locate(slotId) {
  for (const node of nodes()) {
    const found = node.slots.find((s) => s.slot.id === slotId);
    if (found) return { node: node.number, channel: found.channel };
  }
  return null;
}

const isOnline = (node) => sim.nodeOnline.get(node) !== false;

function slotState(slot, online) {
  if (!online || (slot.sensor_state && slot.sensor_state !== "ok")) return "offline";
  if (slot.digitalstatus && slot.physicalstatus) return "occupied";
  if (slot.digitalstatus) return "held";
  if (slot.physicalstatus) return "unbooked";
  return "free";
}

// ---- log ------------------------------------------------------------------

const logEntries = [];

function log(kind, what, outcome) {
  logEntries.unshift({ time: clock(), kind, what, outcome });
  logEntries.length = Math.min(logEntries.length, LOG_LIMIT);
  renderLog();
}

function renderLog() {
  const host = $("log");
  if (logEntries.length === 0) {
    swap(host, h("li", { class: "empty" }, "Nothing sent yet."));
    return;
  }
  swap(
    host,
    logEntries.map((e) =>
      h(
        "li",
        { "data-kind": e.kind },
        h("time", {}, e.time),
        h("span", { class: "what" }, e.what),
        h("span", { class: "outcome" }, e.outcome)
      )
    )
  );
}

// ---- gateway actions ------------------------------------------------------

function requireKey() {
  if (getKey()) return true;
  $("key-notice").hidden = false;
  return false;
}

async function registerNodes() {
  if (!requireKey() || sim.slots.length === 0) return;
  // Mark as attempted first so a failure is logged once, not retried every refresh.
  sim.registeredFor = sim.slots.map((s) => s.id).join("|");
  const mapping = nodes().flatMap((n) =>
    n.slots.map((s) => ({ slot_id: s.slot.id, node: n.number, channel: s.channel }))
  );
  try {
    await call("/gateway/slot-map", { method: "POST", body: mapping });
    log("ok", `Registered ${mapping.length} sensors on ${nodes().length} node(s)`, "mapped");
  } catch (err) {
    log("error", "Register nodes", err.message);
  }
}

async function sendReadings(slotIds) {
  const events = slotIds
    .map((id) => ({ id, where: locate(id) }))
    .filter((e) => e.where && isOnline(e.where.node));
  if (events.length === 0) return;
  if (!requireKey()) {
    events.forEach((e) => sim.pending.add(e.id));
    renderNodes();
    return;
  }
  const names = new Map(sim.slots.map((s) => [s.id, s.name]));
  try {
    const result = await call("/sensor-events", {
      method: "POST",
      body: events.map((e) => ({
        node: e.where.node,
        channel: e.where.channel,
        occupied: Boolean(sim.physical.get(e.id)),
      })),
    });
    for (const applied of result.applied) {
      const e = events[applied.index];
      sim.pending.delete(e.id);
      const reading = sim.physical.get(e.id) ? "car detected" : "clear";
      log(
        applied.outcome === "unbooked" ? "alert" : "ok",
        `IR ${names.get(e.id)} (node ${e.where.node}, ch ${e.where.channel}): ${reading}`,
        applied.outcome === "unbooked" ? "no booking, guard alerted" : applied.outcome
      );
    }
    for (const rejected of result.rejected) {
      const e = events[rejected.index];
      log("error", `IR ${names.get(e.id)}`, rejected.reason);
    }
  } catch (err) {
    events.forEach((e) => sim.pending.add(e.id));
    log("error", "Send IR readings", err.message);
  }
  refresh();
}

function toggleSlot(slotId, force) {
  const next = force === undefined ? !sim.physical.get(slotId) : force;
  if (Boolean(sim.physical.get(slotId)) === next && force !== undefined) return;
  sim.physical.set(slotId, next);
  const where = locate(slotId);
  if (where && !isOnline(where.node)) {
    sim.pending.add(slotId);
    const name = sim.slots.find((s) => s.id === slotId)?.name;
    log("alert", `IR ${name}: ${next ? "car detected" : "clear"}`, "node offline, not reported");
    renderNodes();
    return;
  }
  renderNodes();
  sendReadings([slotId]);
}

async function setNodeOnline(number, online) {
  sim.nodeOnline.set(number, online);
  renderNodes();
  if (!requireKey()) return;
  try {
    await call("/gateway/heartbeat", { method: "POST", body: { nodes: [{ node: number, online }] } });
    log(online ? "ok" : "alert", `Heartbeat: node ${number} ${online ? "answering" : "not answering"}`, online ? "online" : "offline");
  } catch (err) {
    log("error", `Heartbeat node ${number}`, err.message);
  }
  if (online) {
    // The gateway re-reads the node and reports its full state after a reconnect.
    const node = nodes().find((n) => n.number === number);
    if (node) await sendReadings(node.slots.map((s) => s.slot.id));
  }
  refresh();
}

function clearAll() {
  const changed = [];
  for (const slot of sim.slots) {
    if (sim.physical.get(slot.id)) changed.push(slot.id);
    sim.physical.set(slot.id, false);
  }
  const reachable = changed.filter((id) => isOnline(locate(id)?.node));
  changed.filter((id) => !reachable.includes(id)).forEach((id) => sim.pending.add(id));
  renderNodes();
  sendReadings(reachable);
}

// ---- number plate readers -------------------------------------------------

const errorCard = (message) => h("div", { class: "card error", role: "alert" }, message);

async function scanEntry(event) {
  event.preventDefault();
  const plate = $("entry-plate").value.trim().toUpperCase();
  if (!plate) return;
  save(PLATE_STORE, plate);
  const host = $("entry-result");
  if (!requireKey()) {
    host.replaceChildren(errorCard("Enter the gateway key first."));
    return;
  }
  try {
    const result = await call("/gate-entry", {
      method: "POST",
      body: { number_plate: plate, phone_number: "NA", username: "NA" },
    });
    const slot = result.parking_slot;
    log("ok", `Entry camera read ${plate}`, `assigned ${slot.name}, floor ${slot.floor}`);
    const parked = Boolean(sim.physical.get(slot.id));
    host.replaceChildren(
      h(
        "div",
        { class: "card ok" },
        h("div", { class: "meta" }, `${plate} was sent to`),
        h("div", { class: "big" }, slot.name),
        h("div", { class: "meta" }, `Floor ${slot.floor}`),
        result.warning && h("div", { class: "meta" }, result.warning),
        h(
          "div",
          { class: "follow" },
          h(
            "button",
            {
              class: "btn btn-small",
              type: "button",
              disabled: parked,
              onclick: (e) => {
                e.target.disabled = true;
                toggleSlot(slot.id, true);
              },
            },
            parked ? "Sensor already blocked" : `Park the car in ${slot.name}`
          )
        )
      )
    );
  } catch (err) {
    log("error", `Entry camera read ${plate}`, err.message);
    host.replaceChildren(errorCard(err.message));
  }
  refresh();
}

async function scanExit(event) {
  event.preventDefault();
  const plate = $("exit-plate").value.trim().toUpperCase();
  if (!plate) return;
  const host = $("exit-result");
  if (!requireKey()) {
    host.replaceChildren(errorCard("Enter the gateway key first."));
    return;
  }
  try {
    const result = await call("/gate-exit", {
      method: "POST",
      body: { number_plate: plate, phone_number: "NA" },
    });
    const slot = result.parking_slot;
    log("ok", `Exit camera read ${plate}`, slot ? `released ${slot.name}` : "released");
    const stillBlocked = slot && sim.physical.get(slot.id);
    host.replaceChildren(
      h(
        "div",
        { class: "card ok" },
        h("div", { class: "meta" }, "Barrier opens for"),
        h("div", { class: "big" }, plate),
        slot && h("div", { class: "meta" }, `${slot.name}, floor ${slot.floor}, released`),
        result.warning && h("div", { class: "meta" }, result.warning),
        stillBlocked &&
          h(
            "div",
            { class: "follow" },
            h("div", { class: "meta" }, `The IR sensor on ${slot.name} still sees a car.`),
            h(
              "button",
              {
                class: "btn btn-small",
                type: "button",
                onclick: (e) => {
                  e.target.disabled = true;
                  toggleSlot(slot.id, false);
                },
              },
              `The car left ${slot.name}`
            )
          )
      )
    );
    $("exit-plate").value = "";
  } catch (err) {
    log("error", `Exit camera read ${plate}`, err.message);
    host.replaceChildren(errorCard(err.message));
  }
  refresh();
}

// ---- rendering ------------------------------------------------------------

const LEGEND_COLORS = { free: "free", held: "held", occupied: "busy", unbooked: "warn", offline: "off" };

function renderLegend() {
  $("legend").replaceChildren(
    ...Object.entries(SLOT_STATES).map(([key, label]) => {
      const dot = h("i");
      dot.style.background = `var(--${LEGEND_COLORS[key]}-bg)`;
      dot.style.borderLeftColor = `var(--${LEGEND_COLORS[key]})`;
      return h("span", {}, dot, label);
    })
  );
}

function renderNodes() {
  const host = $("nodes");
  if (sim.slots.length === 0) {
    swap(host, h("p", { class: "hint" }, "No slots in the database yet. Add slots on the server first."));
    return;
  }
  swap(
    host,
    nodes().map((node) => {
      const online = isOnline(node.number);
      const first = node.slots[0].slot;
      const last = node.slots[node.slots.length - 1].slot;
      return h(
        "div",
        { class: "node", "data-online": String(online) },
        h(
          "div",
          { class: "node-head" },
          h(
            "div",
            { class: "node-title" },
            `Node ${node.number}`,
            h("small", {}, `${first.name} to ${last.name}, floor ${first.floor}${last.floor !== first.floor ? " to " + last.floor : ""}`)
          ),
          h(
            "button",
            {
              class: "toggle",
              type: "button",
              "aria-pressed": String(online),
              "data-node": node.number,
              onclick: () => setNodeOnline(node.number, !online),
            },
            online ? "Online" : "Offline"
          )
        ),
        h(
          "div",
          { class: "grid" },
          node.slots.map(({ slot, channel }) => {
            const blocked = Boolean(sim.physical.get(slot.id));
            const pending = sim.pending.has(slot.id);
            const plate = slot.numberplate || sim.plates.get(slot.id);
            return h(
              "button",
              {
                class: "slot",
                type: "button",
                "data-state": slotState(slot, online),
                "data-blocked": String(blocked),
                "data-pending": String(pending),
                "aria-pressed": String(blocked),
                title: `Floor ${slot.floor}, node ${node.number}, channel ${channel}. Click to toggle the sensor.`,
                onclick: () => toggleSlot(slot.id),
              },
              h("div", { class: "name" }, slot.name),
              h("div", { class: "state" }, SLOT_STATES[slotState(slot, online)]),
              h("div", { class: "ir" }, pending ? "not reported yet" : blocked ? "Sensor: car" : "Sensor: clear"),
              plate && h("div", { class: "plate" }, plate)
            );
          })
        )
      );
    })
  );
}

function renderPlates() {
  swap(
    $("parked-plates"),
    [...sim.plates.entries()].map(([slotId, plate]) =>
      h("option", { value: plate }, sim.slots.find((s) => s.id === slotId)?.name || "")
    )
  );
}

function setConn(state, text) {
  const el = $("conn");
  el.dataset.state = state;
  el.textContent = text;
}

// ---- data -----------------------------------------------------------------

async function refresh() {
  try {
    const slots = await call("/parking-slots");
    sim.slots = slots.sort(byName);
    sim.plates = new Map();
    for (const slot of sim.slots) {
      if (slot.numberplate) sim.plates.set(slot.id, slot.numberplate);
      // Start from what the server already believes, then the simulator owns the reading.
      if (!sim.initialised.has(slot.id)) {
        sim.physical.set(slot.id, Boolean(slot.physicalstatus));
        sim.initialised.add(slot.id);
      }
    }
    // Older servers do not put the plate on the slot; the booking list may still be readable.
    if (sim.plates.size === 0 && sim.slots.some((s) => s.digitalstatus)) {
      try {
        const active = await call("/bookings?active=true");
        for (const b of active) sim.plates.set(b.slotid, b.numberplate);
      } catch {
        /* bookings need a guard login on some servers: plates are then simply not shown */
      }
    }
    setConn("ok", `Connected, ${clock()}`);
    renderNodes();
    renderPlates();
    if (getKey() && sim.registeredFor !== sim.slots.map((s) => s.id).join("|")) {
      await registerNodes();
    }
  } catch {
    setConn("down", "Server unreachable");
  }
}

// ---- startup --------------------------------------------------------------

function init() {
  $("key-notice").hidden = Boolean(getKey());
  $("entry-plate").value = store(PLATE_STORE);
  renderLegend();
  renderLog();

  $("reader-entry").addEventListener("submit", scanEntry);
  $("reader-exit").addEventListener("submit", scanExit);
  document.querySelectorAll("[data-random]").forEach((button) =>
    button.addEventListener("click", () => {
      const input = $(button.dataset.random);
      input.value = randomPlate();
      input.focus();
    })
  );
  $("register-btn").addEventListener("click", registerNodes);
  $("clear-btn").addEventListener("click", clearAll);

  const settings = $("settings");
  $("settings-btn").addEventListener("click", () => {
    settings.hidden = !settings.hidden;
    $("settings-btn").setAttribute("aria-expanded", String(!settings.hidden));
    if (!settings.hidden) {
      $("gateway-key").value = getKey();
      $("gateway-key").focus();
    }
  });
  settings.addEventListener("submit", (event) => {
    event.preventDefault();
    save(KEY_STORE, $("gateway-key").value.trim());
    sim.registeredFor = "";
    $("key-notice").hidden = Boolean(getKey());
    settings.hidden = true;
    $("settings-btn").setAttribute("aria-expanded", "false");
    refresh();
  });

  refresh();
  setInterval(refresh, REFRESH_MS);
}

init();
