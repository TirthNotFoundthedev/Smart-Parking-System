const form = document.getElementById("lookup");
const input = document.getElementById("number-plate");
const submit = document.getElementById("submit");
const message = document.getElementById("message");
const result = document.getElementById("result");
const clearButton = document.getElementById("clear");
const STORAGE_KEY = "parking.numberPlate";
let refreshTimer = null;

function savedPlate() {
  try { return (localStorage.getItem(STORAGE_KEY) || "").trim(); } catch { return ""; }
}

function savePlate(plate) {
  try { localStorage.setItem(STORAGE_KEY, plate); } catch {}
  clearButton.hidden = false;
}

function formatDuration(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours} h ${rest} min` : `${rest} min`;
}

async function lookup(plate) {
  const payload = await window.api("/parking-status?number_plate=" + encodeURIComponent(plate));
  document.getElementById("slot-name").textContent = payload.slot_name;
  document.getElementById("floor").textContent = payload.floor;
  document.getElementById("duration").textContent = formatDuration(payload.minutes_parked);
  document.getElementById("cost").textContent = `Rs ${payload.cost}`;
  const plateEl = document.getElementById("result-plate");
  plateEl.textContent = payload.numberplate || "";
  plateEl.hidden = !payload.numberplate;
  result.hidden = false;
}

function showError(error) {
  clearInterval(refreshTimer);
  result.hidden = true;
  message.textContent = error instanceof TypeError
    ? "Could not reach the server. Check your connection and try again."
    : error.message;
  message.hidden = false;
}

async function run(plate) {
  clearInterval(refreshTimer);
  message.hidden = true;
  savePlate(plate);
  submit.disabled = true;
  submit.textContent = "Searching...";
  try {
    await lookup(plate);
    refreshTimer = setInterval(() => lookup(plate).catch(showError), 15000);
  } catch (error) {
    showError(error);
  } finally {
    submit.disabled = false;
    submit.textContent = "Find my slot";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  run(input.value.trim());
});

clearButton.addEventListener("click", () => {
  clearInterval(refreshTimer);
  try { localStorage.removeItem(STORAGE_KEY); } catch {}
  input.value = "";
  message.hidden = true;
  result.hidden = true;
  clearButton.hidden = true;
  input.focus();
});

const remembered = savedPlate();
if (remembered) {
  input.value = remembered;
  clearButton.hidden = false;
  run(remembered);
}
