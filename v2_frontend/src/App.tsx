import { useCallback, useEffect, useMemo, useState } from "react";

import { AppState, fetchAppState } from "./api";

type LoadState = "loading" | "ready" | "error";

const navigation = [
  ["Обзор", "overview", true],
  ["Серия", "series", false],
  ["ВАЯХ", "ivl", false],
  ["Спектры", "spectrum", false],
  ["Стабильность", "stability", false],
  ["Камера", "camera", false],
  ["Отчёты", "reports", false]
] as const;

const statusLabels: Record<string, string> = {
  not_probed: "Не проверено",
  ready: "Готово",
  unavailable: "Недоступно"
};

function HardwarePill({ label, state }: { label: string; state?: string }) {
  const value = state ?? "not_probed";
  return (
    <div className={`hardware-pill hardware-pill--${value}`}>
      <span className="hardware-pill__dot" aria-hidden="true" />
      <span>{label}</span>
      <strong>{statusLabels[value] ?? value}</strong>
    </div>
  );
}

function MetricCard({
  eyebrow,
  value,
  note,
  tone = "neutral"
}: {
  eyebrow: string;
  value: string;
  note: string;
  tone?: "neutral" | "blue" | "green";
}) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <p>{eyebrow}</p>
      <strong>{value}</strong>
      <span>{note}</span>
    </article>
  );
}

function App() {
  const [appState, setAppState] = useState<AppState | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [error, setError] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoadState("loading");
    setError("");
    try {
      const next = await fetchAppState(signal);
      setAppState(next);
      setLoadState("ready");
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") {
        return;
      }
      setLoadState("error");
      setError(reason instanceof Error ? reason.message : "Неизвестная ошибка backend.");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

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
              className={`nav-item ${key === "overview" ? "nav-item--active" : ""}`}
              disabled={!enabled}
              key={key}
              type="button"
            >
              <span className={`nav-icon nav-icon--${key}`} aria-hidden="true" />
              {label}
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
          <div className="build-label">v2.0.0 alpha · этап 1</div>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <div className="title-row">
              <h1>Обзор приложения</h1>
              <span className="alpha-badge">ALPHA</span>
            </div>
            <p>Технический прототип новой desktop-оболочки</p>
          </div>
          <div className="topbar__hardware">
            <HardwarePill label="SMU" state={appState?.hardware.smu} />
            <HardwarePill label="SPEC" state={appState?.hardware.spectrometer} />
            <HardwarePill label="CAM" state={appState?.hardware.camera} />
          </div>
        </header>

        <section className="content">
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
                  ? `FastAPI отвечает только на ${appState?.backend.bound_host}. Токен принят, управляющий клиент зафиксирован.`
                  : loadState === "error"
                    ? error
                    : "Проверяем токен сеанса и актуальное состояние приложения…"}
              </p>
            </div>
            {loadState === "error" && (
              <button className="button button--secondary" onClick={() => void load()} type="button">
                Повторить
              </button>
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
              eyebrow="Режим оборудования"
              value={appState?.hardware.mode ?? "—"}
              note="проверка приборов начнётся на этапе 2"
            />
            <MetricCard eyebrow="Активная серия" value="Не открыта" note="перенос серий — этап 4" />
          </section>

          <section className="dashboard-grid">
            <article className="panel panel--large">
              <div className="panel__header">
                <div>
                  <p className="panel__eyebrow">Этап 1</p>
                  <h2>Вертикальный срез desktop-архитектуры</h2>
                </div>
                <span className="status-chip status-chip--ok">Завершён</span>
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
                    <p>127.0.0.1, проверка Host и Origin</p>
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
                  <strong>Контур измерений не изменён</strong>
                  <p>
                    Новый интерфейс пока читает только состояние приложения. Команды SMU,
                    спектрометра и камеры не подключены.
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
                <span className={`status-chip ${loadState === "ready" ? "status-chip--ok" : ""}`}>
                  {loadState === "ready" ? "Online" : "Ожидание"}
                </span>
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
              <button className="button button--secondary button--wide" onClick={() => void load()} type="button">
                Обновить состояние
              </button>
            </article>
          </section>

          <section className="next-stage">
            <div>
              <p className="panel__eyebrow">Следующий контрольный рубеж</p>
              <h2>Аппаратный proof of concept</h2>
              <p>SMU, спектрометр, WebSocket-поток точек и безопасная остановка в эмуляторе.</p>
            </div>
            <div className="next-stage__meta">
              <span>Этап 2</span>
              <strong>Готов к началу</strong>
            </div>
          </section>
        </section>

        <footer className="statusbar">
          <div>
            <span className={`statusbar__dot statusbar__dot--${loadState}`} />
            {loadState === "ready" ? "Backend готов" : "Backend проверяется"}
          </div>
          <div>Серия не выбрана</div>
          <div className="statusbar__safe">Рабочее приложение v1.9.1 сохранено</div>
        </footer>
      </main>
    </div>
  );
}

export default App;
