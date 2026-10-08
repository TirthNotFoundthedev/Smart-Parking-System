"use strict";

const DEFAULT_API = "http://127.0.0.1:5000";
const REFRESH_MS = 5000;

const $ = (id) => document.getElementById(id);

// ---- small helpers --------------------------------------------------------

/** Build an element. Children that are strings become text nodes (never HTML). */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "onclick") el.addEventListener("click", value);
    else if (key.startsWith("data-")) el.setAttribute(key, value);
    else el[key] = value;
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/**
 * Replace a container's children only when the new markup differs, so the
 * 5-second refresh never swaps out a button the guard is about to click.
 * Anything that changes behaviour (ids) must appear in the markup as data-*.
 */
function swap(host, ...nodes) {
  const probe = document.createElement("div");
  probe.append(...nodes.flat());
  if (probe.innerHTML === host.innerHTML) return;
  host.replaceChildren(...probe.childNodes);
}

function getApiBase() {
  try {
    return localStorage.getItem("guardApiBase") || DEFAULT_API;
  } catch {
    return DEFAULT_API;
  }
}

function setApiBase(value) {
  try {
    localStorage.setItem("guardApiBase", value);
  } catch {
    /* storage unavailable: the address just resets on reload */
  }
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(getApiBase().replace(/\/+$/, "") + path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    throw new Error("Cannot reach the server. Check the address under “Server”.");
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) {
    const detail = body && body.detail;
    throw new Error(
      typeof detail === "string" ? detail : `Request failed (${response.status}). Check the details entered.`
    );
  }
  return body;
}

const post = (path, data) => api(path, { method: "POST", body: JSON.stringify(data) });

const clean = (value) => value.trim() || "NA";
const plateOf = (value) => value.trim().toUpperCase();

function fmtTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return date.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function minutesLeft(iso) {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 60000);
}

function arrivalText(iso) {
  const left = minutesLeft(iso);
  return left > 0 ? `arrives within ${left} min` : "overdue, releasing soon";
}

// ---- state ----------------------------------------------------------------

let state = { slots: [], active: [], alerts: [], availability: [] };

const SLOT_STATES = {
  free: "Free",
  held: "Assigned, waiting",
  occupied: "Occupied",
  unbooked: "No booking",
  offline: "Sensor offline",
};
const LEGEND_COLORS = {
  free: "var(--free)",
  held: "var(--held)",
  occupied: "var(--busy)",
  unbooked: "var(--warn)",
  offline: "var(--off)",
};

function slotState(slot) {
  if (slot.sensor_state && slot.sensor_state !== "ok") return "offline";
  if (slot.digitalstatus && slot.physicalstatus) return "occupied";
  if (slot.digitalstatus) return "held";
  if (slot.physicalstatus) return "unbooked";
  return "free";
}

// ---- rendering ------------------------------------------------------------

function renderLegend() {
  $("legend").replaceChildren(
    ...Object.entries(SLOT_STATES).map(([key, label]) =>
      h("li", { style: `--c: ${LEGEND_COLORS[key]}` }, label)
    )
  );
}

function renderAvailability() {
  const chips = state.availability.map((f) =>
    h("span", { class: "chip" }, `Floor ${f.floor}: `, h("strong", {}, f.free), ` free of ${f.total}`)
  );
  swap($("availability"), chips);
}

function renderSlots() {
  const byPlate = new Map(state.active.map((b) => [b.slotid, b]));
  const floors = new Map();
  for (const slot of state.slots) {
    if (!floors.has(slot.floor)) floors.set(slot.floor, []);
    floors.get(slot.floor).push(slot);
  }

  if (floors.size === 0) {
    swap($("slots"), h("p", { class: "empty" }, "No slots in the database yet."));
    return;
  }

  const sections = [...floors.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([floor, slots]) =>
      h(
        "div",
        { class: "floor" },
        h("h3", {}, `Floor ${floor}`),
        h(
          "div",
          { class: "grid" },
          slots
            .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
            .map((slot) => {
              const kind = slotState(slot);
              const booking = byPlate.get(slot.id);
              const tile = [
                h("div", { class: "name" }, slot.name),
                h("div", { class: "state" }, SLOT_STATES[kind]),
                booking && h("div", { class: "plate" }, booking.numberplate),
              ];
              return booking
                ? h(
                    "button",
                    {
                      class: "slot",
                      type: "button",
                      "data-state": kind,
                      title: `Check out ${booking.numberplate}`,
                      "data-booking": booking.id,
                      onclick: () => prefillExit(booking.numberplate),
                    },
                    tile
                  )
                : h("div", { class: "slot", "data-state": kind }, tile);
            })
        )
      )
    );
  swap($("slots"), sections);
}

function renderActive() {
  $("count-active").textContent = state.active.length;
  const host = $("list-active");
  if (state.active.length === 0) {
    swap(host, h("p", { class: "empty" }, "No cars in the lot."));
    return;
  }
  const rows = state.active.map((b) =>
    h(
      "tr",
      {},
      h("td", { class: "plate" }, b.numberplate),
      h("td", {}, b.username === "NA" ? "—" : b.username),
      h("td", {}, b.phonenumber === "NA" ? "—" : b.phonenumber),
      h("td", {}, `${b.slotname} · ${b.floor}`),
      h(
        "td",
        {},
        b.status === "parked"
          ? h("span", { class: "pill occupied" }, "Parked")
          : h("span", { class: "pill held" }, `Assigned, ${arrivalText(b.expires_at)}`)
      ),
      h("td", {}, fmtTime(b.start_time)),
      h("td", {}, h("button", { class: "btn btn-small", type: "button", "data-booking": b.id, onclick: () => checkOut(b) }, "Check out"))
    )
  );
  swap(host, table(["Plate", "Name", "Phone", "Slot", "Status", "Entered", ""], rows));
}

const ALERT_LABELS = {
  unbooked_car: "Car in a slot with no booking",
  node_offline: "Sensor node offline",
  sensor_fault: "Sensor fault",
};

function renderAlerts() {
  const count = $("count-alerts");
  count.textContent = state.alerts.length;
  count.dataset.hot = String(state.alerts.length > 0);

  const host = $("list-alerts");
  if (state.alerts.length === 0) {
    swap(host, h("p", { class: "empty" }, "No open alerts."));
    return;
  }
  const slotNames = new Map(state.slots.map((s) => [s.id, s.name]));
  const rows = state.alerts.map((a) =>
    h(
      "tr",
      {},
      h("td", {}, h("span", { class: "pill bad" }, ALERT_LABELS[a.type] || a.type)),
      h("td", {}, a.slotid ? slotNames.get(a.slotid) || a.slotid : "—"),
      h("td", {}, a.detail || "—"),
      h("td", {}, fmtTime(a.created_at)),
      h("td", {}, h("button", { class: "btn btn-small", type: "button", "data-alert": a.id, onclick: () => resolveAlert(a.id) }, "Resolve"))
    )
  );
  swap(host, table(["Alert", "Slot", "Detail", "Raised", ""], rows));
}

const HISTORY_PILLS = { completed: "ok", noshow: "bad", cancelled: "" };
const HISTORY_LABELS = { completed: "Completed", noshow: "No-show", cancelled: "Cancelled" };

async function renderHistory() {
  const host = $("list-history");
  try {
    const list = await api("/bookings?active=false&limit=50");
    if (list.length === 0) {
      swap(host, h("p", { class: "empty" }, "No finished visits yet."));
      return;
    }
    const rows = list.map((b) =>
      h(
        "tr",
        {},
        h("td", { class: "plate" }, b.numberplate),
        h("td", {}, `${b.slotname} · ${b.floor}`),
        h("td", {}, b.kind === "walkin" ? "Walk-in" : "Reservation"),
        h("td", {}, h("span", { class: `pill ${HISTORY_PILLS[b.status] || ""}` }, HISTORY_LABELS[b.status] || b.status)),
        h("td", {}, fmtTime(b.start_time))
      )
    );
    swap(host, table(["Plate", "Slot", "Type", "Outcome", "Entered"], rows));
  } catch (err) {
    swap(host, h("p", { class: "empty" }, err.message));
  }
}

function table(headers, rows) {
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      {},
      h("thead", {}, h("tr", {}, headers.map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", {}, rows)
    )
  );
}

function showResult(card) {
  $("gate-result").replaceChildren(card);
}

function errorCard(message) {
  return h("div", { class: "card error", role: "alert" }, message);
}

// ---- data -----------------------------------------------------------------

function setConn(stateName, text) {
  const el = $("conn");
  el.dataset.state = stateName;
  el.textContent = text;
}

async function refresh() {
  try {
    const [slots, active, alerts, availability] = await Promise.all([
      api("/parking-slots"),
      api("/bookings?active=true"),
      api("/alerts"),
      api("/availability"),
    ]);
    state = { slots, active, alerts, availability };
    renderAvailability();
    renderSlots();
    renderActive();
    renderAlerts();
    setConn("ok", `Live · updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`);
  } catch (err) {
    setConn("down", "Server unreachable");
  }
}

// ---- actions --------------------------------------------------------------

async function withBusy(button, task) {
  button.disabled = true;
  try {
    await task();
  } finally {
    button.disabled = false;
  }
}

async function onEntry(event) {
  event.preventDefault();
  const form = event.target;
  const plate = plateOf(form.plate.value);
  if (!plate) return;
  await withBusy(form.querySelector("button[type=submit]"), async () => {
    try {
      const result = await post("/gate-entry", {
        number_plate: plate,
        username: clean(form.name.value),
        phone_number: clean(form.phone.value),
      });
      const slot = result.parking_slot;
      showResult(
        h(
          "div",
          { class: "card ok" },
          h("div", { class: "meta" }, `Send ${result.userdata.numberplate} to`),
          h("div", { class: "big" }, slot.name),
          h("div", { class: "meta" }, `Floor ${slot.floor}. Must park within 15 minutes.`),
          result.warning && h("div", { class: "note" }, result.warning)
        )
      );
      form.reset();
      form.plate.focus();
      refresh();
    } catch (err) {
      showResult(errorCard(err.message));
    }
  });
}

async function onExit(event) {
  event.preventDefault();
  const form = event.target;
  const plate = plateOf(form.plate.value);
  if (!plate) return;
  await withBusy(form.querySelector("button[type=submit]"), () => doExit(plate, clean(form.phone.value), form));
}

async function doExit(plate, phone, form) {
  try {
    const result = await post("/gate-exit", { number_plate: plate, phone_number: phone });
    const log = result.log;
    const minutes = Math.max(1, Math.round((new Date(log.endtime) - new Date(log.starttime)) / 60000));
    showResult(
      h(
        "div",
        { class: "card ok" },
        h("div", { class: "meta" }, "Checked out"),
        h("div", { class: "big" }, result.userdata.numberplate),
        h("div", { class: "meta" }, `${result.parking_slot ? result.parking_slot.name : "Slot"} released. Stayed ${minutes} min.`),
        result.warning && h("div", { class: "note" }, result.warning)
      )
    );
    if (form) {
      form.reset();
      form.plate.focus();
    }
    refresh();
  } catch (err) {
    showResult(errorCard(err.message));
  }
}

function checkOut(booking) {
  if (!window.confirm(`Check out ${booking.numberplate} from ${booking.slotname}?`)) return;
  return doExit(booking.numberplate, "NA", null);
}

function prefillExit(plate) {
  selectTab("gate", "exit");
  $("exit-plate").value = plate;
  $("exit-plate").focus();
}

async function resolveAlert(id) {
  try {
    await post(`/alerts/${encodeURIComponent(id)}/resolve`, {});
  } catch (err) {
    showResult(errorCard(err.message));
  }
  refresh();
}

// ---- tabs -----------------------------------------------------------------

const TAB_GROUPS = {
  gate: [
    ["entry", "tab-entry", "form-entry"],
    ["exit", "tab-exit", "form-exit"],
  ],
  lists: [
    ["active", "tab-active", "list-active"],
    ["alerts", "tab-alerts", "list-alerts"],
    ["history", "tab-history", "list-history"],
  ],
};

function selectTab(group, name) {
  for (const [key, tabId, panelId] of TAB_GROUPS[group]) {
    const selected = key === name;
    const tab = $(tabId);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $(panelId).hidden = !selected;
  }
  if (group === "lists" && name === "history") renderHistory();
}

function wireTabs() {
  for (const [group, tabs] of Object.entries(TAB_GROUPS)) {
    tabs.forEach(([key, tabId], index) => {
      const tab = $(tabId);
      tab.addEventListener("click", () => selectTab(group, key));
      tab.addEventListener("keydown", (event) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (!step) return;
        const [nextKey, nextId] = tabs[(index + step + tabs.length) % tabs.length];
        selectTab(group, nextKey);
        $(nextId).focus();
      });
    });
  }
}

// ---- startup --------------------------------------------------------------

function init() {
  renderLegend();
  wireTabs();

  $("form-entry").addEventListener("submit", onEntry);
  $("form-exit").addEventListener("submit", onExit);

  const settings = $("settings");
  $("settings-btn").addEventListener("click", () => {
    settings.hidden = !settings.hidden;
    $("settings-btn").setAttribute("aria-expanded", String(!settings.hidden));
    if (!settings.hidden) {
      $("api-base").value = getApiBase();
      $("api-base").focus();
    }
  });
  settings.addEventListener("submit", (event) => {
    event.preventDefault();
    setApiBase($("api-base").value.trim() || DEFAULT_API);
    settings.hidden = true;
    $("settings-btn").setAttribute("aria-expanded", "false");
    refresh();
  });

  refresh();
  setInterval(() => {
    refresh();
    if (!$("list-history").hidden) renderHistory();
  }, REFRESH_MS);
  // Keep "arrives within N min" fresh between server refreshes.
  setInterval(renderActive, 30000);
}

init();
