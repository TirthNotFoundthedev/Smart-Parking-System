(function () {
  const REFRESH_MS = 10000;
  const statusEl = document.getElementById("status");
  const floorsEl = document.getElementById("floors");
  const messageEl = document.getElementById("message");
  const updatedEl = document.getElementById("updated");
  const refreshBtn = document.getElementById("refresh");

  let slots = [];
  let loaded = false;
  let busy = false;

  function describe(slot) {
    const booked = slot.digitalstatus === 1;
    const occupied = slot.physicalstatus === 1;
    if (booked && occupied) return "Booked and occupied";
    if (booked) return "Booked";
    if (occupied) return "Car detected";
    return "Free";
  }

  function showMessage(text) {
    messageEl.textContent = text;
    messageEl.hidden = !text;
  }

  function errorText(error) {
    if (error instanceof TypeError) {
      return "Could not reach the server. Check that the backend is running and try again.";
    }
    return error.message || "Something went wrong. Try again.";
  }

  function setStatus(text, className) {
    statusEl.textContent = text;
    statusEl.className = "panel " + className;
    statusEl.hidden = false;
  }

  function createSlotRow(slot) {
    const li = document.createElement("li");
    li.className = "slot";

    const info = document.createElement("div");
    info.className = "slot-info";

    const name = document.createElement("p");
    name.className = "slot-name";
    name.textContent = slot.name;

    const meta = document.createElement("p");
    meta.className = "slot-meta";
    meta.textContent = "Floor " + slot.floor;

    info.append(name, meta);

    const actions = document.createElement("div");
    actions.className = "slot-actions";

    const status = document.createElement("span");
    status.className = "status";
    status.textContent = describe(slot);

    const button = document.createElement("button");
    button.type = "button";
    const isOccupied = slot.physicalstatus === 1;
    button.textContent = isOccupied ? "Mark empty" : "Mark occupied";
    button.setAttribute("aria-label", button.textContent + " " + slot.name);
    button.addEventListener("click", () => toggleSlot(slot.id, !isOccupied, button));

    actions.append(status, button);
    li.append(info, actions);
    return li;
  }

  function render() {
    floorsEl.replaceChildren();
    if (!slots.length) {
      setStatus("No slots yet. Run the mock data script in the backend.", "empty");
      return;
    }
    statusEl.hidden = true;

    const byFloor = new Map();
    [...slots]
      .sort((a, b) => String(a.floor).localeCompare(String(b.floor), undefined, { numeric: true }) || String(a.name).localeCompare(String(b.name), undefined, { numeric: true }))
      .forEach((slot) => {
        const key = String(slot.floor);
        if (!byFloor.has(key)) byFloor.set(key, []);
        byFloor.get(key).push(slot);
      });

    byFloor.forEach((floorSlots, floor) => {
      const section = document.createElement("section");
      section.className = "floor";

      const heading = document.createElement("h2");
      heading.textContent = "Floor " + floor;

      const list = document.createElement("ul");
      list.className = "slot-list";
      floorSlots.forEach((slot) => list.append(createSlotRow(slot)));

      section.append(heading, list);
      floorsEl.append(section);
    });
  }

  function markUpdated() {
    updatedEl.textContent = "Last updated " + new Date().toLocaleTimeString();
  }

  async function loadSlots() {
    if (busy) return;
    busy = true;
    refreshBtn.disabled = true;
    try {
      const data = await window.api("/parking-slots");
      slots = Array.isArray(data) ? data : [];
      loaded = true;
      showMessage("");
      markUpdated();
      render();
    } catch (error) {
      showMessage(errorText(error));
      if (!loaded) setStatus("Slots could not be loaded.", "error");
    } finally {
      busy = false;
      refreshBtn.disabled = false;
    }
  }

  async function toggleSlot(slotId, occupied, button) {
    button.disabled = true;
    try {
      const updated = await window.api("/sensor/slots/" + encodeURIComponent(slotId), {
        method: "PUT",
        body: { occupied },
      });
      slots = slots.map((slot) => (slot.id === updated.id ? updated : slot));
      showMessage("");
      markUpdated();
      render();
    } catch (error) {
      showMessage(errorText(error));
      button.disabled = false;
    }
  }

  refreshBtn.addEventListener("click", loadSlots);
  loadSlots();
  setInterval(loadSlots, REFRESH_MS);
})();
