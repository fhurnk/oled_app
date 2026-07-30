export type AppState = {
  schema_version: number;
  session_id: string;
  timestamp: string;
  application: {
    name: string;
    version: string;
    channel: string;
    stable_base: string;
    shell: string;
  };
  backend: {
    ready: boolean;
    bound_host: string;
    started_at: string;
    api_docs_enabled: boolean;
    log_directory: string;
  };
  hardware: {
    mode: string;
    smu: string;
    spectrometer: string;
    camera: string;
  };
  series: {
    active: boolean;
    path: string | null;
  };
  migration: {
    stage: number;
    status: string;
    tkinter_default_preserved: boolean;
  };
};

const SESSION_STORAGE_KEY = "oled-v2-session-token";
const CLIENT_STORAGE_KEY = "oled-v2-client-id";

function consumeSessionToken(): string {
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const fragmentToken = fragment.get("session");
  if (fragmentToken) {
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, fragmentToken);
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    return fragmentToken;
  }
  return window.sessionStorage.getItem(SESSION_STORAGE_KEY) ?? "";
}

function controllerId(): string {
  const existing = window.sessionStorage.getItem(CLIENT_STORAGE_KEY);
  if (existing) {
    return existing;
  }
  const bytes = new Uint8Array(24);
  window.crypto.getRandomValues(bytes);
  const value = Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("");
  window.sessionStorage.setItem(CLIENT_STORAGE_KEY, value);
  return value;
}

export async function fetchAppState(signal?: AbortSignal): Promise<AppState> {
  const token = consumeSessionToken();
  if (!token) {
    throw new Error("Токен desktop-сеанса отсутствует. Запустите интерфейс через v2 launcher.");
  }
  const response = await fetch("/api/app/state", {
    method: "GET",
    cache: "no-store",
    headers: {
      "X-OLED-Session": token,
      "X-OLED-Client": controllerId()
    },
    signal
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Backend вернул HTTP ${response.status}.`);
  }
  return (await response.json()) as AppState;
}
