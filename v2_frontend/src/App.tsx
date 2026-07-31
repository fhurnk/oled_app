import { useCallback, useEffect, useMemo, useState } from "react";

import LivePocChart from "./LivePocChart";
import SeriesWorkspace from "./SeriesWorkspace";
import {
  Button,
  HardwarePill,
  MetricCard,
  StatusBadge
} from "./design-system/components";
import {
  type AppState,
  type HardwareProbe,
  type PocEvent,
  type PocPoint,
  type PocState,
  fetchAppState,
  fetchPocState,
  openPocStream,
  probeHardware,
  startSimulatorPoc,
  stopPoc
} from "./api";

type LoadState = "loading" | "ready" | "error";
type StreamState = "connecting" | "connected" | "disconnected";
type ActiveView = "overview" | "series";

const navigation = [
  ["Обзор", "overview", true],
  ["Серия", "series", true],
  ["ВАЯХ", "ivl", false],
  ["Спектры", "spectrum", false],
  ["Стабильность", "stability", false],
  ["Камера", "camera", false],
  ["Отчёты", "reports", false]
] as const;

const pocStatusLabels: Record<string, string> = {
  idle: "Ожидание",
  starting: "Запуск",
  running: "Выполняется",
  stop_requested: "Остановка",
  completed: "Завершён",
  stopped: "Остановлен",
  safety_limit: "Защитный предел",
  failed: "Ошибка"
};

function App() {
  const [activeView, setActiveView] = useState<ActiveView>("overview");
  const [appState, setAppState] = useState<AppState | null>(null);
  const [pocState, setPocState] = useState<PocState | null>(null);
  const [points, setPoints] = useState<PocPoint[]>([]);
  const [probe, setProbe] = useState<HardwareProbe | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState("");
  const [pocMessage, setPocMessage] = useState(
    "Эмулятор готов к короткому sweep без записи файлов измерений."
  );

  const applyPocState = useCallback((state: PocState) => {
    setPocState(state);
    setProbe(state.probe ?? null);
    if (state.points) {
      setPoints(state.points);
    }
  }, []);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoadState("loading");
      setError("");
      try {
        const [nextAppState, nextPocState] = await Promise.all([
          fetchAppState(signal),
          fetchPocState(signal)
        ]);
        setAppState(nextAppState);
        applyPocState(nextPocState);
        setLoadState("ready");
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === "AbortError") {
          return;
        }
        setLoadState("error");
        setError(reason instanceof Error ? reason.message : "Неизвестная ошибка backend.");
      }
    },
    [applyPocState]
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: number | null = null;
    let disposed = false;

    const handleEvent = (event: PocEvent) => {
      if (event.type === "poc_snapshot" || event.type === "poc_state") {
        applyPocState(event.state);
      } else if (event.type === "poc_point") {
        setPoints((current) => {
          if (current.some((point) => point.index === event.point.index)) {
            return current;
          }
          return [...current, event.point].slice(-160);
        });
      } else if (event.type === "poc_probe") {
        setProbe(event.probe);
      } else if (event.type === "poc_log") {
        setPocMessage(event.message);
      }
    };

    const connectStream = () => {
      if (disposed) {
        return;
      }
      setStreamState("connecting");
      try {
        socket = openPocStream(handleEvent, (connected) => {
          setStreamState(connected ? "connected" : "disconnected");
        });
        socket.addEventListener("close", () => {
          if (!disposed) {
            retryTimer = window.setTimeout(connectStream, 900);
          }
        });
      } catch (reason) {
        setStreamState("disconnected");
        setError(reason instanceof Error ? reason.message : "WebSocket недоступен.");
      }
    };

    connectStream();
    return () => {
      disposed = true;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
      }
      socket?.close();
    };
  }, [applyPocState]);

  const runSimulator = useCallback(async () => {
    setActionBusy(true);
    setError("");
    setPoints([]);
    setPocMessage("Запускаем изолированный эмулятор SMU и спектрометра…");
    try {
      applyPocState(await startSimulatorPoc());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось запустить PoC.");
    } finally {
      setActionBusy(false);
    }
  }, [applyPocState]);

  const stopSimulator = useCallback(async () => {
    setActionBusy(true);
    setError("");
    setPocMessage("Запрошено безопасное отключение обоих каналов SMU…");
    try {
      applyPocState(await stopPoc());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось остановить PoC.");
    } finally {
      setActionBusy(false);
    }
  }, [applyPocState]);

  const runProbe = useCallback(async () => {
    setActionBusy(true);
    setError("");
    setPocMessage("Проверяем текущее оборудование без подачи рабочего напряжения…");
    try {
      const nextProbe = await probeHardware();
      setProbe(nextProbe);
      setPocMessage(nextProbe.details);
      setAppState(await fetchAppState());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Проверка оборудования не удалась.");
    } finally {
      setActionBusy(false);
    }
  }, []);

  const refreshAppState = useCallback(async () => {
    try {
      setAppState(await fetchAppState());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось обновить состояние приложения.");
    }
  }, []);

  const time = useMemo(() => {
    if (!appState?.timestamp) {
      return "—";
    }
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    }).format(new Date(appState.timestamp));
  }, [appState?.timestamp]);

  const latestPoint = points.length ? points[points.length - 1] : pocState?.latest_point;
  const pocActive = Boolean(pocState?.active);
  const simulatorReady = points.length > 0 || pocState?.status === "completed";
  const smuState = probe
    ? probe.smu.toUpperCase().includes("OK")
      ? "ready"
      : "unavailable"
    : simulatorReady
      ? "ready"
      : appState?.hardware.smu;
  const spectrometerState = probe
    ? probe.spectrometer.toUpperCase().includes("OK")
      ? "ready"
      : "unavailable"
    : simulatorReady
      ? "ready"
      : appState?.hardware.spectrometer;
  const shutdownState =
    pocState?.safe_shutdown_confirmed === true
      ? "Выходы отключены"
      : pocState?.safe_shutdown_confirmed === false
        ? "Не подтверждено"
        : "Не выполнялось";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand__mark" aria-hidden="true">
            O
          </div>
          <div>
            <strong>OLED</strong>
            <span>Measurement App</span>
          </div>
        </div>

        <nav aria-label="Основная навигация">
          <p className="sidebar__caption">Рабочая область</p>
          {navigation.map(([label, key, enabled]) => (
            <button
              className={`nav-item ${key === activeView ? "nav-item--active" : ""}`}
              disabled={!enabled}
              key={key}
              onClick={() => {
                if (key === "overview" || key === "series") {
                  setActiveView(key);
                }
              }}
              type="button"
            >
              <span className={`nav-icon nav-icon--${key}`} aria-hidden="true" />
              {label}
              {key === "series" && <small>рабочий</small>}
              {!enabled && <small>скоро</small>}
            </button>
          ))}
        </nav>

        <div className="sidebar__bottom">
          <button className="nav-item" disabled type="button">
            <span className="nav-icon nav-icon--settings" aria-hidden="true" />
            Настройки
            <small>скоро</small>
          </button>
          <button className="nav-item" disabled type="button">
            <span className="nav-icon nav-icon--diagnostics" aria-hidden="true" />
            Диагностика
            <small>скоро</small>
          </button>
          <div className="build-label">v2.0.0 alpha · этап 4</div>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <div className="title-row">
              <h1>{activeView === "overview" ? "Обзор приложения" : "Серии OLED"}</h1>
              <span className="alpha-badge">ALPHA</span>
            </div>
            <p>
              {activeView === "overview"
                ? "Аппаратный proof of concept новой desktop-оболочки"
                : "Создание, открытие и совместимый журнал измерений"}
            </p>
          </div>
          <div className="topbar__hardware">
            <HardwarePill label="SMU" state={smuState} />
            <HardwarePill label="SPEC" state={spectrometerState} />
            <HardwarePill label="CAM" state={appState?.hardware.camera} />
          </div>
        </header>

        <section className="content">
          {activeView === "series" ? (
            <SeriesWorkspace onSeriesChanged={() => void refreshAppState()} />
          ) : (
            <>
          <div className={`connection-banner connection-banner--${loadState}`}>
            <div className="connection-banner__icon" aria-hidden="true">
              {loadState === "ready" ? "✓" : loadState === "error" ? "!" : "…"}
            </div>
            <div>
              <strong>
                {loadState === "ready"
                  ? "Защищённый desktop-сеанс подключён"
                  : loadState === "error"
                    ? "Не удалось подключиться к backend"
                    : "Подключение к локальному backend"}
              </strong>
              <p>
                {loadState === "ready"
                  ? `FastAPI отвечает только на ${appState?.backend.bound_host}. WebSocket: ${
                      streamState === "connected" ? "подключён" : "переподключается"
                    }.`
                  : loadState === "error"
                    ? error
                    : "Проверяем токен сеанса и актуальное состояние приложения…"}
              </p>
            </div>
            {loadState === "error" && (
              <Button onClick={() => void load()}>
                Повторить
              </Button>
            )}
          </div>

          <section className="metrics-grid" aria-label="Состояние прототипа">
            <MetricCard
              eyebrow="Версия"
              value={appState ? `v${appState.application.version}` : "—"}
              note="единый обновляемый prerelease"
              tone="blue"
            />
            <MetricCard
              eyebrow="Рабочая база"
              value={appState?.application.stable_base ?? "v1.9.1"}
              note="Tkinter остаётся основным входом"
              tone="green"
            />
            <MetricCard
              eyebrow="Режим приложения"
              value={appState?.hardware.mode ?? "—"}
              note="PoC запускается только в эмуляторе"
            />
            <MetricCard
              eyebrow="Активная серия"
              value={appState?.series.active ? "Открыта" : "Не открыта"}
              note={appState?.series.path ?? "PoC не записывает измерительные файлы"}
              tone={appState?.series.active ? "green" : "neutral"}
            />
          </section>

          <article className="panel poc-panel">
            <div className="panel__header poc-panel__header">
              <div>
                <p className="panel__eyebrow">Этап 2 · simulator first</p>
                <h2>SMU + спектрометр + WebSocket</h2>
                <p className="poc-panel__subtitle">
                  Короткий sweep до 3,6 В через существующий hardware-слой без выбора серии.
                </p>
              </div>
              <div className="poc-actions">
                <Button
                  disabled={actionBusy || pocActive}
                  onClick={() => void runProbe()}
                >
                  Проверить приборы
                </Button>
                <Button
                  disabled={actionBusy || pocActive}
                  onClick={() => void runSimulator()}
                  variant="primary"
                >
                  Запустить эмулятор
                </Button>
                <Button
                  disabled={actionBusy || !pocActive}
                  onClick={() => void stopSimulator()}
                  variant="danger"
                >
                  Безопасно остановить
                </Button>
              </div>
            </div>

            <div className="poc-state-strip">
              <div>
                <span>Состояние</span>
                <strong data-testid="poc-status">
                  {pocStatusLabels[pocState?.status ?? "idle"]}
                </strong>
              </div>
              <div>
                <span>Точек</span>
                <strong data-testid="poc-point-count">{points.length}</strong>
              </div>
              <div>
                <span>Напряжение</span>
                <strong>{latestPoint ? `${latestPoint.voltage_measured_V.toFixed(3)} В` : "—"}</strong>
              </div>
              <div>
                <span>Ток OLED</span>
                <strong>{latestPoint ? `${latestPoint.current_mA.toFixed(4)} мА` : "—"}</strong>
              </div>
              <div>
                <span>Пик спектра</span>
                <strong>
                  {latestPoint
                    ? `${latestPoint.spectrum_peak_nm.toFixed(0)} нм`
                    : "—"}
                </strong>
              </div>
              <div className={pocState?.safe_shutdown_confirmed === false ? "poc-state--danger" : ""}>
                <span>Безопасный stop</span>
                <strong>{shutdownState}</strong>
              </div>
            </div>

            <div className="poc-workspace">
              <LivePocChart points={points} />
              <aside className="poc-telemetry">
                <div className="poc-telemetry__head">
                  <span className={`stream-dot stream-dot--${streamState}`} />
                  <strong>
                    {streamState === "connected" ? "Live-поток подключён" : "Подключение потока"}
                  </strong>
                </div>
                <dl>
                  <div>
                    <dt>Фототок</dt>
                    <dd>{latestPoint ? `${latestPoint.photodiode_uA.toFixed(4)} мкА` : "—"}</dd>
                  </div>
                  <div>
                    <dt>Пик, counts</dt>
                    <dd>
                      {latestPoint
                        ? latestPoint.spectrum_peak_counts.toLocaleString("ru-RU", {
                            maximumFractionDigits: 0
                          })
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>Спектрометр PoC</dt>
                    <dd>{pocState?.spectrometer_model ?? "не запускался"}</dd>
                  </div>
                  <div>
                    <dt>Проверка стенда</dt>
                    <dd>{probe?.title ?? "не выполнялась"}</dd>
                  </div>
                </dl>
                <div className="poc-message">
                  <span aria-hidden="true">i</span>
                  <p>{error || pocState?.error || probe?.details || pocMessage}</p>
                </div>
              </aside>
            </div>
          </article>

          <section className="dashboard-grid">
            <article className="panel panel--large">
              <div className="panel__header">
                <div>
                  <p className="panel__eyebrow">Этап 1</p>
                  <h2>Вертикальный срез desktop-архитектуры</h2>
                </div>
                <StatusBadge tone="success">Завершён</StatusBadge>
              </div>
              <div className="architecture-flow">
                <div className="architecture-node architecture-node--ready">
                  <span>01</span>
                  <div>
                    <strong>Desktop launcher</strong>
                    <p>Случайный порт, токен и WebView2</p>
                  </div>
                  <b>готов</b>
                </div>
                <div className="architecture-line" aria-hidden="true" />
                <div className="architecture-node architecture-node--ready">
                  <span>02</span>
                  <div>
                    <strong>FastAPI backend</strong>
                    <p>127.0.0.1 и один управляющий клиент</p>
                  </div>
                  <b>готов</b>
                </div>
                <div className="architecture-line" aria-hidden="true" />
                <div className="architecture-node architecture-node--ready">
                  <span>03</span>
                  <div>
                    <strong>React frontend</strong>
                    <p>Production assets внутри приложения</p>
                  </div>
                  <b>готов</b>
                </div>
              </div>
              <div className="guardrail">
                <span aria-hidden="true">⌁</span>
                <div>
                  <strong>Измерительные данные не изменяются</strong>
                  <p>
                    PoC всегда использует эмулятор, не открывает серию и не создаёт CSV/XLSX.
                    Проверка реальных приборов отдельно не включает рабочее напряжение.
                  </p>
                </div>
              </div>
            </article>

            <article className="panel">
              <div className="panel__header">
                <div>
                  <p className="panel__eyebrow">Desktop session</p>
                  <h2>Сведения backend</h2>
                </div>
                <StatusBadge tone={loadState === "ready" ? "success" : "neutral"}>
                  {loadState === "ready" ? "Online" : "Ожидание"}
                </StatusBadge>
              </div>
              <dl className="details-list">
                <div>
                  <dt>Session ID</dt>
                  <dd title={appState?.session_id}>{appState?.session_id.slice(0, 13) ?? "—"}…</dd>
                </div>
                <div>
                  <dt>Schema</dt>
                  <dd>{appState ? `v${appState.schema_version}` : "—"}</dd>
                </div>
                <div>
                  <dt>API docs</dt>
                  <dd>{appState?.backend.api_docs_enabled ? "включены" : "выключены"}</dd>
                </div>
                <div>
                  <dt>Обновлено</dt>
                  <dd>{time}</dd>
                </div>
              </dl>
              <Button
                className="button--wide"
                onClick={() => void load()}
              >
                Обновить состояние
              </Button>
            </article>
          </section>

          <section className="next-stage">
            <div>
              <p className="panel__eyebrow">Этап 4 · серии</p>
              <h2>Рабочий экран серии подключён к совместимому журналу</h2>
              <p>Создание, открытие, редактирование, карта, история, миниатюры и очередь спектров.</p>
            </div>
            <div className="next-stage__meta">
              <Button compact onClick={() => setActiveView("series")} variant="primary">
                Открыть серии
              </Button>
            </div>
          </section>
            </>
          )}
        </section>

        <footer className="statusbar">
          <div>
            <span className={`statusbar__dot statusbar__dot--${loadState}`} />
            {loadState === "ready" ? "Backend готов" : "Backend проверяется"}
          </div>
          <div>
            WebSocket {streamState === "connected" ? "подключён" : "переподключается"}
          </div>
          <div className="statusbar__safe">Рабочее приложение v1.9.1 сохранено</div>
        </footer>
      </main>
    </div>
  );
}

export default App;
