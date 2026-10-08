"use strict";

/*
 * Sensor simulator. This page plays the hardware side of the system:
 *   - Arduino slot nodes (5 IR sensors each) and the gateway that talks to the server
 *   - the entry and exit number-plate cameras
 * It only uses the public HTTP API, exactly like the real hardware would.
 */

const DEFAULT_API = "http://127.0.0.1:5000";
const SLOTS_PER_NODE = 5;
const REFRESH_MS = 4000;
const LOG_LIMIT = 60;

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
    /* storage unavailable: setting resets on reload */
  }
}

const getApiBase = () => store("sensorApiBase", DEFAULT_API).replace(/\/+$/, "");
const getKey = () => store("sensorGatewayKey");

async function api(path, { method = "GET", body, gateway = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (gateway) headers["X-Gateway-Key"] = getKey();
  let response;
  try {
    response = await fetch(getApiBase() + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("Cannot reach the server. Check the address under Settings.");
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status}).`);
  }
  return data;
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
  active: [], // active bookings
  physical: new Map(), // slot id -> true when a car blocks the IR sensor (the "real world")
  pending: new Set(), // slot ids whose reading has not reached the server yet
  nodeOnline: new Map(), // node number -> boolean
  registeredFor: "", // signature of the slot list last sent to /gateway/slot-map
  initialised: new Set(),
};

const byName = (a, b) =>
  a.floor.localeCompare(b.floor, undefined, { numeric: true }) ||
  a.name.localeCompare(b.name, undefined, { numeric: true }) ||
  a.id.localeCompare(b.id);

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
    await api("/gateway/slot-map", { method: "POST", body: mapping, gateway: true });
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
    return;
  }
  const names = new Map(sim.slots.map((s) => [s.id, s.name]));
  try {
    const result = await api("/sensor-events", {
      method: "POST",
      gateway: true,
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
    await api("/gateway/heartbeat", {
      method: "POST",
      gateway: true,
      body: { nodes: [{ node: number, online }] },
    });
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

// ---- plate readers --------------------------------------------------------

function errorCard(message) {
  return h("div", { class: "card error", role: "alert" }, message);
}

async function scanEntry(event) {
  event.preventDefault();
  const plate = $("entry-plate").value.trim().toUpperCase();
  if (!plate) return;
  const host = $("entry-result");
  try {
    const result = await api("/gate-entry", {
      method: "POST",
      body: { number_plate: plate, phone_number: "NA", username: "NA" },
    });
    const slot = result.parking_slot;
    log("ok", `Entry camera read ${plate}`, `assigned ${slot.name}`);
    const parked = Boolean(sim.physical.get(slot.id));
    host.replaceChildren(
      h(
        "div",
        { class: "card ok" },
        h("div", { class: "meta" }, `${plate} was sent to`),
        h("div", { class: "big" }, slot.name),
        h("div", { class: "meta" }, `Floor ${slot.floor}`),
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
    $("entry-plate").value = "";
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
  const booking = sim.active.find((b) => b.numberplate.toUpperCase() === plate);
  try {
    const result = await api("/gate-exit", {
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
        slot && h("div", { class: "meta" }, `${slot.name} released`),
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

function renderNodes() {
  const host = $("nodes");
  if (sim.slots.length === 0) {
    swap(host, h("p", { class: "hint" }, "No slots in the database yet. Add slots on the server first."));
    return;
  }
  const bookingBySlot = new Map(sim.active.map((b) => [b.slotid, b]));
  swap(
    host,
    nodes().map((node) => {
      const online = isOnline(node.number);
      const first = node.slots[0].slot.name;
      const last = node.slots[node.slots.length - 1].slot.name;
      return h(
        "div",
        { class: "node", "data-online": String(online) },
        h(
          "div",
          { class: "node-head" },
          h("div", { class: "node-title" }, `Node ${node.number}`, h("small", {}, `${first} to ${last}`)),
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
          { class: "ir-grid" },
          node.slots.map(({ slot, channel }) => {
            const blocked = Boolean(sim.physical.get(slot.id));
            const pending = sim.pending.has(slot.id);
            const booking = bookingBySlot.get(slot.id);
            return h(
              "button",
              {
                class: "ir",
                type: "button",
                "data-slot": slot.id,
                "data-reading": blocked ? "blocked" : "clear",
                "data-pending": String(pending),
                "aria-pressed": String(blocked),
                title: `Channel ${channel}`,
                onclick: () => toggleSlot(slot.id),
              },
              h("div", { class: "name" }, slot.name),
              h("div", { class: "reading" }, blocked ? "Car detected" : "Clear"),
              h("div", { class: "tag" }, pending ? "not reported yet" : booking ? booking.numberplate : `ch ${channel}`)
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
    sim.active.map((b) => h("option", { value: b.numberplate }, `${b.slotname} · ${b.status}`))
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
    const [slots, active] = await Promise.all([api("/parking-slots"), api("/bookings?active=true")]);
    sim.slots = slots.sort(byName);
    sim.active = active;
    for (const slot of sim.slots) {
      // Start from what the server already believes, then the simulator owns the reading.
      if (!sim.initialised.has(slot.id)) {
        sim.physical.set(slot.id, Boolean(slot.physicalstatus));
        sim.initialised.add(slot.id);
      }
    }
    setConn("ok", `Connected · ${clock()}`);
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
      $("api-base").value = getApiBase();
      $("gateway-key").value = getKey();
      $("api-base").focus();
    }
  });
  settings.addEventListener("submit", (event) => {
    event.preventDefault();
    save("sensorApiBase", $("api-base").value.trim() || DEFAULT_API);
    save("sensorGatewayKey", $("gateway-key").value.trim());
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
