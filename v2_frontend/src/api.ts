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
    root: string;
  };
  migration: {
    stage: number;
    status: string;
    tkinter_default_preserved: boolean;
  };
};

export type PocPoint = {
  index: number;
  elapsed_s: number;
  voltage_set_V: number;
  voltage_measured_V: number;
  current_mA: number;
  photodiode_uA: number;
  spectrum_peak_nm: number;
  spectrum_peak_counts: number;
};

export type HardwareProbe = {
  level: "ok" | "warning" | "error";
  title: string;
  details: string;
  smu: string;
  spectrometer: string;
  mode: string;
  checked_at: string;
};

export type PocStatus =
  | "idle"
  | "starting"
  | "running"
  | "stop_requested"
  | "completed"
  | "stopped"
  | "safety_limit"
  | "failed";

export type PocState = {
  status: PocStatus;
  run_id: string | null;
  mode: "simulator";
  started_at: string | null;
  finished_at: string | null;
  point_count: number;
  latest_point: PocPoint | null;
  stop_reason: string | null;
  error: string | null;
  safe_shutdown_confirmed: boolean | null;
  spectrometer_model: string | null;
  active: boolean;
  can_start: boolean;
  probe: HardwareProbe | null;
  last_event_sequence: number;
  points?: PocPoint[];
};

export type PocEvent =
  | { type: "poc_snapshot"; sequence: number; state: PocState }
  | { type: "poc_state"; sequence: number; state: PocState }
  | { type: "poc_point"; sequence: number; point: PocPoint }
  | { type: "poc_probe"; sequence: number; probe: HardwareProbe }
  | { type: "poc_log"; sequence: number; message: string }
  | { type: "poc_heartbeat"; sequence: number };

export type SeriesSummary = {
  path: string;
  folder_name: string;
  deposition_date: string | null;
  keyword: string | null;
  created_at: string | null;
  measurements_count: number | null;
};

export type SeriesQuarter = {
  number: number;
  code: string;
  base: string;
  description: string;
  led_color: string;
  led_color_label: string;
};

export type SeriesPixel = {
  pixel_id: string;
  quarter_code: string;
  quarter_number: number;
  quarter_description: string;
  led_color: string;
  substrate_number: number;
  pixel_number: number;
  status: string;
  opening_voltage_V: number | string | null;
  last_ivl_date: string | null;
  last_ivl_file: string | null;
  last_ivl_max_current_mA: number | string | null;
  last_ivl_max_photodiode_uA: number | string | null;
  spectrum_priority: boolean;
  last_spectrum_date: string | null;
  last_spectrum_file: string | null;
  last_spectrum_peak_count: number | string | null;
  last_spectrum_peaks_nm: string | null;
  last_spectrum_max_intensity: number | string | null;
  last_stability_date: string | null;
  last_stability_file: string | null;
  last_updated: string | null;
  thumbnail_available: boolean;
};

export type SeriesHistoryItem = {
  date_time: string | null;
  measurement_day: string | null;
  type: string;
  pixel_id: string;
  status: string;
  file: string | null;
  notes: string | null;
};

export type ActiveSeries = {
  path: string;
  folder_name: string;
  deposition_date: string;
  keyword: string;
  created_at: string | null;
  series_led_color: string;
  description_scope: "quarter" | "half" | "substrate";
  half_orientation: "top_bottom" | "left_right";
  quarters: SeriesQuarter[];
  pixels: SeriesPixel[];
  history: SeriesHistoryItem[];
  metrics: {
    substrates: number;
    pixels: number;
    measured: number;
    ivl: number;
    spectra: number;
    stability: number;
    spectrum_queue: number;
    history: number;
  };
};

export type SeriesState = {
  root: string;
  recent: SeriesSummary[];
  active: ActiveSeries | null;
  refreshed_thumbnails?: number;
  queue_update?: {
    scope: "pixel" | "substrate";
    requested: number;
    changed: number;
    enabled: boolean;
  };
};

export type SeriesConfigInput = {
  root?: string;
  deposition_date: string;
  keyword: string;
  series_led_color: "red" | "green" | "blue" | "white";
  description_scope: "quarter" | "half" | "substrate";
  half_orientation: "top_bottom" | "left_right";
  quarter_bases: Record<string, string>;
  quarter_descriptions: Record<string, string>;
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

function desktopHeaders(): Record<string, string> {
  const token = consumeSessionToken();
  if (!token) {
    throw new Error("Токен desktop-сеанса отсутствует. Запустите интерфейс через v2 launcher.");
  }
  return {
    "X-OLED-Session": token,
    "X-OLED-Client": controllerId()
  };
}

async function requestJson<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: {
      ...desktopHeaders(),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers ?? {})
    },
    signal
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Backend вернул HTTP ${response.status}.`);
  }
  return (await response.json()) as T;
}

export function fetchAppState(signal?: AbortSignal): Promise<AppState> {
  return requestJson<AppState>("/api/app/state", {}, signal);
}

export function fetchPocState(signal?: AbortSignal): Promise<PocState> {
  return requestJson<PocState>("/api/poc/state", {}, signal);
}

export function fetchSeriesState(signal?: AbortSignal): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/state", {}, signal);
}

export function setSeriesRoot(path: string): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/root", {
    method: "PUT",
    body: JSON.stringify({ path })
  });
}

export function openSeries(path: string): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/open", {
    method: "POST",
    body: JSON.stringify({ path })
  });
}

export function closeSeries(): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/close", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function createSeries(payload: SeriesConfigInput): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/create", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function updateSeries(payload: SeriesConfigInput): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/current", {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export function refreshSeries(): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/current/refresh", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function setSpectrumPriority(
  pixelId: string,
  enabled: boolean,
  scope: "pixel" | "substrate" = "pixel"
): Promise<SeriesState> {
  return requestJson<SeriesState>("/api/series/current/spectrum-priority", {
    method: "PUT",
    body: JSON.stringify({ pixel_id: pixelId, enabled, scope })
  });
}

export async function fetchSeriesThumbnail(pixelId: string): Promise<Blob> {
  const response = await fetch(
    `/api/series/current/thumbnail/${encodeURIComponent(pixelId)}`,
    { cache: "no-store", headers: desktopHeaders() }
  );
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Миниатюра недоступна: HTTP ${response.status}.`);
  }
  return response.blob();
}

export function probeHardware(): Promise<HardwareProbe> {
  return requestJson<HardwareProbe>("/api/poc/probe", { method: "POST" });
}

export function startSimulatorPoc(): Promise<PocState> {
  return requestJson<PocState>("/api/poc/start", {
    method: "POST",
    body: JSON.stringify({ point_count: 32, interval_ms: 80 })
  });
}

export function stopPoc(): Promise<PocState> {
  return requestJson<PocState>("/api/poc/stop", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function openPocStream(
  onEvent: (event: PocEvent) => void,
  onConnection: (connected: boolean) => void
): WebSocket {
  const token = consumeSessionToken();
  if (!token) {
    throw new Error("Токен desktop-сеанса отсутствует.");
  }
  const url = new URL("/api/poc/stream", window.location.href);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url, [
    "oled-v2",
    `oled-session.${token}`,
    `oled-client.${controllerId()}`
  ]);
  socket.addEventListener("open", () => onConnection(true));
  socket.addEventListener("close", () => onConnection(false));
  socket.addEventListener("error", () => onConnection(false));
  socket.addEventListener("message", (message) => {
    onEvent(JSON.parse(String(message.data)) as PocEvent);
  });
  return socket;
}

export type IvlState = {
  status: string; active: boolean; run_id: string | null;
  points: Omit<PocPoint, "spectrum_peak_nm" | "spectrum_peak_counts">[];
  error: string | null; safe_shutdown_confirmed: boolean | null;
  params?: Record<string, number>; raw_file?: string; message?: string;
  result: {file: string; status: string; opening_voltage: number | null; current_limit_reached: boolean} | null;
};
export type IvlPreflight = {params: Record<string, number>; output_root: string; note: string};
export const fetchIvlState = () => requestJson<IvlState>("/api/ivl/state");
export const preflightIvl = (params: Record<string, number>) => requestJson<IvlPreflight>("/api/ivl/preflight", {method: "POST", body: JSON.stringify(params)});
export const startIvl = (params: Record<string, number>) => requestJson<IvlState>("/api/ivl/start", {method: "POST", body: JSON.stringify(params)});
export const stopIvl = () => requestJson<IvlState>("/api/ivl/stop", {method: "POST"});
