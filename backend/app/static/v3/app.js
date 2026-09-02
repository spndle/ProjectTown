const api = "/api/v3";
let csrf = "";
let allowed = [];
const operation = document.querySelector("#operation");
const details = document.querySelector("#details");
const status = document.querySelector("#status");

function headers(extra = {}) { return { "X-ProjectTown-V3-CSRF": csrf, "X-ProjectTown-V3-Operation": operation.value, ...extra }; }
async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, { credentials: "same-origin", ...options, headers: headers(options.headers) });
  const value = await response.json().catch(() => ({ error: { code: "INVALID_RESPONSE" } }));
  if (!response.ok) throw new Error(value.error?.code || "REQUEST_FAILED");
  return value;
}
function setStatus(text) { status.textContent = text; }
function enable(value) {
  document.querySelector("#check").disabled = !value;
  document.querySelectorAll("[data-action]").forEach(button => { button.disabled = !value || !allowed.includes(button.dataset.action); });
}

async function startSession() {
  try {
    const session = await fetch(`${api}/session`, { method: "POST", credentials: "same-origin", headers: { Origin: location.origin } });
    const sessionValue = await session.json();
    if (!session.ok) throw new Error(sessionValue.error?.code || "SESSION_FAILED");
    csrf = sessionValue.csrf;
    const list = await request("/bindings");
    for (const item of list.items) {
      const option = new Option(`${item.target} (${item.target_path_sha256.slice(0, 12)})`, item.operation_id);
      operation.add(option);
    }
    operation.disabled = false;
    document.querySelector("#start-session").disabled = true;
    setStatus("Select a pre-authorized operation. No action is dispatched automatically.");
  } catch (error) { setStatus(`Unavailable: ${error.message}`); }
}
function initialize() { enable(false); }
document.querySelector("#start-session").addEventListener("click", startSession);
operation.addEventListener("change", async () => {
  enable(Boolean(operation.value)); details.textContent = "";
  if (!operation.value) return;
  try {
    const value = await request("/operation");
    allowed = value.allowed_mutations;
    details.textContent = JSON.stringify(value, null, 2);
    enable(true);
  } catch (error) { setStatus(error.message); }
});
document.querySelector("#check").addEventListener("click", async () => {
  try { details.textContent = JSON.stringify(await request("/operation/check"), null, 2); } catch (error) { setStatus(error.message); }
});
document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", async () => {
  try {
    const action = button.dataset.action;
    const result = await request(`/operation/${action}`, { method: "POST", headers: { "Content-Type": "application/json", Origin: location.origin, "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ confirmation: document.querySelector("#confirmation").value }) });
    details.textContent = JSON.stringify(result, null, 2);
  } catch (error) { setStatus(error.message); }
}));
initialize();
