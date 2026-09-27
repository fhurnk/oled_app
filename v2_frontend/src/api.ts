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

export type IvlTarget = {series_path: string; pixel_id: string};
export type IvlQueueInput = {series_path: string; start_pixel: string; skip_nonworking: boolean};
export type IvlQueuePreview = IvlQueueInput & {
  enabled: true; candidate_count: number; total: number; skipped_pixels: string[];
};
export type IvlQueueState = IvlQueuePreview & {
  completed: number; remaining: number;
  attempts: number; current_index: number; current_pixel: string | null;
  completed_pixels: string[];
  results: {pixel_id: string; status: string; file: string; journaled: boolean}[];
};
export type IvlInput = Record<string, number | IvlTarget | IvlQueueInput | null>;
export type IvlState = {
  status: string; active: boolean; run_id: string | null;
  points: Omit<PocPoint, "spectrum_peak_nm" | "spectrum_peak_counts">[];
  error: string | null; safe_shutdown_confirmed: boolean | null;
  params?: Record<string, number>; raw_file?: string; message?: string;
  target?: IvlTarget | null; pixel_id?: string; cycle?: number; point_count?: number;
  queue?: IvlQueueState | null;
  decision?: {id: string; kind: string; message: string; pixel_id?: string; actions?: string[]} | null;
  result: {file: string; status: string; opening_voltage: number | null;
    current_limit_reached: boolean; journaled: boolean; cycles: number} | null;
};
export type IvlPreflight = {params: Record<string, number>; output_root: string; note: string;
  target: IvlTarget | null; queue: IvlQueuePreview | null;
  luminance_coefficient: number; spectral_calibration: boolean | null};
export const fetchIvlState = () => requestJson<IvlState>("/api/ivl/state");
export const preflightIvl = (params: IvlInput) => requestJson<IvlPreflight>("/api/ivl/preflight", {method: "POST", body: JSON.stringify(params)});
export const startIvl = (params: IvlInput) => requestJson<IvlState>("/api/ivl/start", {method: "POST", body: JSON.stringify(params)});
export const stopIvl = () => requestJson<IvlState>("/api/ivl/stop", {method: "POST"});
export const decideIvlOpening = (run_id: string, decision_id: string, value: number | null) =>
  requestJson<IvlState>("/api/ivl/opening", {method: "POST", body: JSON.stringify({run_id, decision_id, value})});
export const decideIvlQueue = (run_id: string, decision_id: string, action: "retry" | "skip_substrate" | "continue") =>
  requestJson<IvlState>("/api/ivl/queue-decision", {method: "POST", body: JSON.stringify({run_id, decision_id, action})});

export type SpectrumTarget = {series_path: string; pixel_id: string};
export type SpectrumQueueInput = {series_path: string; start_pixel: string; scope: "substrate" | "priority"; queued_only: boolean};
export type SpectrumQueuePreview = SpectrumQueueInput & {enabled: true; candidate_count: number; total: number};
export type SpectrumQueueState = SpectrumQueuePreview & {
  completed: number; remaining: number; attempts: number; current_index: number;
  current_pixel: string | null; completed_pixels: string[]; skipped_pixels: string[];
  results: {pixel_id: string; status: string; file: string | null; journaled: boolean}[];
};
export type SpectrumInput = Record<string, number | boolean | string | SpectrumTarget | SpectrumQueueInput | null>;
export type SpectrumCurve = {
  point: number; voltage_V: number; status: string; integration_time_s: number;
  wavelengths_nm: number[]; intensities: number[];
};
export type SpectrumPoint = {
  index: number; voltage_V: number; integration_time_s: number; status: string;
  peak_nm: number | null; peak_counts: number | null; peaks_detected: number;
};
export type SpectrumState = {
  status: string; active: boolean; run_id: string | null; pixel_id?: string;
  target?: SpectrumTarget | null; use_opening_voltage?: boolean;
  queue?: SpectrumQueueState | null;
  params?: Record<string, number | boolean | string>; points: SpectrumPoint[];
  point_count?: number; latest_spectrum: SpectrumCurve | null;
  optimization: (SpectrumCurve & {iteration: number}) | null;
  decision?: {id: string; kind: string; message: string; actions: string[];
    pixel_id?: string; status?: string; replacement_pixels?: string[]} | null;
  message?: string; error: string | null; safe_shutdown_confirmed: boolean | null;
  result: {file: string | null; raw_files: string[]; status: string;
    stopped_by_user: boolean; discarded: boolean; spectrum_peak_count: number | null;
    spectrum_peaks_nm: string; spectrum_max_intensity: number | null; journaled: boolean} | null;
};
export type SpectrumPreflight = {
  params: Record<string, number | boolean | string>; target: SpectrumTarget | null;
  queue: SpectrumQueuePreview | null;
  use_opening_voltage: boolean; effective_voltage_start: number; point_count: number;
  output_root: string; note: string;
};
export const fetchSpectrumState = () => requestJson<SpectrumState>("/api/spectrum/state");
export const preflightSpectrum = (params: SpectrumInput) => requestJson<SpectrumPreflight>("/api/spectrum/preflight", {method: "POST", body: JSON.stringify(params)});
export const startSpectrum = (params: SpectrumInput) => requestJson<SpectrumState>("/api/spectrum/start", {method: "POST", body: JSON.stringify(params)});
export const stopSpectrum = () => requestJson<SpectrumState>("/api/spectrum/stop", {method: "POST"});
export const decideSpectrum = (run_id: string, decision_id: string, action: string, pixel_id?: string) =>
  requestJson<SpectrumState>("/api/spectrum/decision", {method: "POST", body: JSON.stringify({run_id, decision_id, action, pixel_id})});

export type StabilityTarget = {series_path: string; pixel_id: string};
export type StabilityInput = Record<string, number | boolean | string | StabilityTarget | null>;
export type StabilityPoint = {
  point: number; elapsed_s: number; control_mode: "current" | "voltage";
  target_setpoint: number; target_unit: "mA" | "V"; voltage_set_V: number;
  voltage_measured_V: number; current_measured_mA: number;
  photodiode_uA: number; luminance_cd_m2: number;
};
export type StabilityState = {
  status: string; active: boolean; run_id: string | null; pixel_id?: string;
  target?: StabilityTarget | null; use_ivl_start_voltage?: boolean;
  params?: Record<string, number | string>; points: StabilityPoint[];
  point_count: number; latest_point?: StabilityPoint | null;
  current_setpoint?: number; setpoint_revision?: number;
  message?: string; error: string | null; safe_shutdown_confirmed: boolean | null;
  result: {file: string; raw_file: string | null; status: string; max_photo_uA: number;
    control_mode: string; final_setpoint: number; stopped_by_user: boolean;
    journaled: boolean; events: {event: string; label: string; measurement_time_s: number | null}[]} | null;
};
export type StabilityPreflight = {
  params: Record<string, number | string>; target: StabilityTarget | null;
  use_ivl_start_voltage: boolean; effective_voltage_start: number;
  start_voltage_source: string; ivl_voltage_at_target: number | null;
  output_root: string; note: string;
};
export const fetchStabilityState = () => requestJson<StabilityState>("/api/stability/state");
export const preflightStability = (params: StabilityInput) => requestJson<StabilityPreflight>("/api/stability/preflight", {method: "POST", body: JSON.stringify(params)});
export const startStability = (params: StabilityInput) => requestJson<StabilityState>("/api/stability/start", {method: "POST", body: JSON.stringify(params)});
export const setStabilitySetpoint = (run_id: string, value: number) => requestJson<StabilityState>("/api/stability/setpoint", {method: "POST", body: JSON.stringify({run_id, value})});
export const stopStability = () => requestJson<StabilityState>("/api/stability/stop", {method: "POST"});

export type CameraRemoteFile = {
  file_id: string;
  name: string;
  kind: string;
  size: number;
  created_at: string;
  sha256: string;
};
export type CameraState = {
  connected: boolean;
  base_url: string | null;
  host: string;
  port: number;
  initialized: boolean;
  health: Record<string, unknown> | null;
  camera_status: Record<string, unknown> | null;
  capabilities: Record<string, unknown> | null;
  files: CameraRemoteFile[];
  liveview_active: boolean;
  frame_sequence: number;
  frame_size: number;
  frame_received_at: string | null;
  message: string;
  error: string | null;
  updated_at: string;
};
export const fetchCameraState = (signal?: AbortSignal) =>
  requestJson<CameraState>("/api/camera/state", {}, signal);
export const connectCamera = (host: string, port: number, initialize = true) =>
  requestJson<CameraState>("/api/camera/connect", {
    method: "POST", body: JSON.stringify({host, port, initialize})
  });
export const refreshCamera = () =>
  requestJson<CameraState>("/api/camera/refresh", {method: "POST"});
export const startCameraLiveview = () =>
  requestJson<CameraState>("/api/camera/liveview/start", {method: "POST", body: JSON.stringify({})});
export const stopCameraLiveview = () =>
  requestJson<CameraState>("/api/camera/liveview/stop", {method: "POST"});
export const disconnectCamera = () =>
  requestJson<CameraState>("/api/camera/disconnect", {method: "POST"});
export async function fetchCameraFrame(): Promise<Blob> {
  const response = await fetch(`/api/camera/frame?sequence=${Date.now()}`, {
    cache: "no-store", headers: desktopHeaders()
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {detail?: string} | null;
    throw new Error(payload?.detail ?? `Кадр LiveView недоступен: HTTP ${response.status}.`);
  }
  return response.blob();
}
