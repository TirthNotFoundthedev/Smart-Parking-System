(function () {
  const TOKEN_KEY = "guardToken";
  const loginView = document.getElementById("login-view");
  const gateView = document.getElementById("gate-view");
  const loginForm = document.getElementById("login-form");
  const loginSubmit = document.getElementById("login-submit");
  const loginMessage = document.getElementById("login-message");
  const gateForm = document.getElementById("gate-form");
  const entrySubmit = document.getElementById("entry-submit");
  const gateStatus = document.getElementById("gate-status");
  const gateWarning = document.getElementById("gate-warning");
  const gateResult = document.getElementById("gate-result");
  const logoutButton = document.getElementById("logout");

  let token = readToken();

  function readToken() {
    try {
      return sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  }

  function writeToken(value) {
    try {
      sessionStorage.setItem(TOKEN_KEY, value);
    } catch {}
  }

  function clearToken() {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {}
  }

  function messageFor(error) {
    return error instanceof TypeError
      ? "Could not reach the server. Check your connection and try again."
      : error.message;
  }

  function setBusy(button, isBusy, busyText) {
    if (!button.dataset.label) button.dataset.label = button.textContent;
    button.disabled = isBusy;
    button.textContent = isBusy ? busyText : button.dataset.label;
  }

  function setGateStatus(message, state) {
    gateStatus.textContent = message;
    gateStatus.dataset.state = state;
  }

  function resetResult() {
    gateResult.replaceChildren();
    gateResult.hidden = true;
    gateWarning.textContent = "";
    gateWarning.hidden = true;
    setGateStatus("", "idle");
  }

  function showLoginMessage(message) {
    loginMessage.textContent = message;
    loginMessage.hidden = !message;
  }

  function showLogin(message) {
    gateView.hidden = true;
    loginView.hidden = false;
    showLoginMessage(message || "");
    loginForm.elements.username.focus();
  }

  function showGate() {
    loginView.hidden = true;
    gateView.hidden = false;
    showLoginMessage("");
    resetResult();
    gateForm.elements.number_plate.focus();
  }

  function endSession(message) {
    token = null;
    clearToken();
    gateForm.reset();
    showLogin(message);
  }

  function showResult(payload) {
    const rows = [
      ["Name", payload.userdata.name],
      ["Number plate", payload.userdata.numberplate],
      ["Phone number", payload.userdata.phonenumber],
      ["Assigned slot", payload.parking_slot.name],
      ["Floor", payload.parking_slot.floor],
    ];
    gateResult.replaceChildren(...rows.flatMap(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = value;
      return [term, description];
    }));
    gateResult.hidden = false;
    gateWarning.textContent = payload.warning || "";
    gateWarning.hidden = !payload.warning;
  }

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = loginForm.elements.username.value.trim();
    const password = loginForm.elements.password.value;
    if (!username || !password) {
      showLoginMessage("Enter your username and password.");
      (username ? loginForm.elements.password : loginForm.elements.username).focus();
      return;
    }

    setBusy(loginSubmit, true, "Signing in...");
    showLoginMessage("");
    try {
      const data = await window.api("/guard/login", { method: "POST", body: { username, password } });
      token = data.token;
      writeToken(token);
      loginForm.reset();
      showGate();
    } catch (error) {
      loginForm.elements.password.value = "";
      showLoginMessage(messageFor(error));
      loginForm.elements.password.focus();
    } finally {
      setBusy(loginSubmit, false);
    }
  });

  gateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const numberPlate = gateForm.elements.number_plate.value.trim();
    const phoneNumber = gateForm.elements.phone_number.value.trim();
    const username = gateForm.elements.username.value.trim();

    resetResult();
    if (!numberPlate && !phoneNumber) {
      setGateStatus("Enter a number plate or phone number to continue.", "error");
      gateForm.elements.number_plate.focus();
      return;
    }

    setBusy(entrySubmit, true, "Submitting...");
    setGateStatus("Sending the gate-entry request...", "loading");
    try {
      const data = await window.api("/gate-entry", {
        method: "POST",
        token,
        body: {
          number_plate: numberPlate || "NA",
          phone_number: phoneNumber || "NA",
          username: username || "NA",
        },
      });
      showResult(data);
      setGateStatus("Gate entry completed.", "success");
    } catch (error) {
      if (error.status === 401) {
        endSession("Session expired. Sign in again.");
        return;
      }
      setGateStatus(messageFor(error), "error");
    } finally {
      setBusy(entrySubmit, false);
    }
  });

  gateForm.addEventListener("reset", () => {
    resetResult();
  });

  logoutButton.addEventListener("click", () => {
    token = null;
    clearToken();
    gateForm.reset();
    showLogin("");
  });

  if (token) {
    showGate();
  } else {
    showLogin("");
  }
})();
