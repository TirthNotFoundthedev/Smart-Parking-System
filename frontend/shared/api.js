// Shared fetch helper. Pages load config.js first, then this file.
(function () {
  const base = () => window.APP_CONFIG.API_BASE_URL.replace(/\/$/, "");

  async function api(path, { method = "GET", body, token } = {}) {
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(base() + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = Array.isArray(payload?.detail)
        ? payload.detail.map((item) => item.msg).join(" ")
        : payload?.detail;
      const error = new Error(typeof detail === "string" ? detail : "Something went wrong. Try again.");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  window.api = api;
})();
