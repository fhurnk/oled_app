import { useCallback, useEffect, useState } from "react";

import {
  type DiagnosticsState,
  fetchDiagnostics,
  probeHardware
} from "./api";
import { Button, Notice, Panel, StatusBadge, type StatusTone } from "./design-system/components";

const pathLabels: Record<string, string> = {
  settings: "Настройки",
  logs: "Журналы",
  series_root: "Корень серий",
  active_series: "Активная серия",
  simulator_ivl: "Эмулятор ВАЯХ",
  simulator_spectrum: "Эмулятор спектров",
  simulator_stability: "Эмулятор стабильности"
};

function statusTone(status: string, active: boolean): StatusTone {
  if (active) return "progress";
  if (["failed", "error", "safety_limit"].includes(status)) return "danger";
  if (["completed", "stopped"].includes(status)) return "success";
  return "neutral";
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const area = document.createElement("textarea");
  area.value = value;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) throw new Error("Буфер обмена недоступен.");
}

export default function DiagnosticsWorkspace() {
  const [state, setState] = useState<DiagnosticsState | null>(null);
  const [busy, setBusy] = useState(true);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    setError("");
    try {
      setState(await fetchDiagnostics(signal));
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить диагностику.");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const runProbe = async () => {
    setProbing(true);
    setError("");
    try {
      await probeHardware();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Проверка оборудования не удалась.");
    } finally {
      setProbing(false);
    }
  };

  const copySummary = async () => {
    if (!state) return;
    setError("");
    try {
      await copyText(state.copy_text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2400);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось скопировать сводку.");
    }
  };

  if (busy && !state) {
    return <Notice title="Собираем диагностику" tone="progress">Читаем состояние приложения и локальный журнал…</Notice>;
  }

  return <div className="diagnostics-workspace">
    {error && <Notice title="Диагностика недоступна" tone="danger">{error}</Notice>}
    {copied && <Notice title="Сводка скопирована" tone="success">Секреты сеанса исключены; текст можно приложить к сообщению об ошибке.</Notice>}
    {state && <>
      <Panel className="diagnostics-hero">
        <div>
          <p className="panel__eyebrow">Техническая сводка</p>
          <h2>{state.application.name} {state.application.version}</h2>
          <p>Python {state.runtime.python} · {state.runtime.frozen ? "готовая сборка" : "исходный запуск"} · API {state.application.schema_version}</p>
        </div>
        <div className="diagnostics-actions">
          <Button disabled={probing} onClick={() => void runProbe()}>{probing ? "Проверяем…" : "Проверить приборы"}</Button>
          <Button disabled={busy} onClick={() => { setBusy(true); void load(); }}>Обновить</Button>
          <Button variant="primary" onClick={() => void copySummary()}>Копировать сводку</Button>
        </div>
      </Panel>

      <div className="diagnostics-grid">
        <Panel className="diagnostics-panel">
          <header><p className="panel__eyebrow">Подключения</p><h2>Оборудование и камера</h2></header>
          <dl className="diagnostics-list">
            <div><dt>Режим</dt><dd>{state.hardware.mode}</dd></div>
            <div><dt>SMU</dt><dd><StatusBadge tone={state.hardware.smu === "ready" ? "success" : "neutral"} dot>{state.hardware.smu}</StatusBadge></dd></div>
            <div><dt>Спектрометр</dt><dd><StatusBadge tone={state.hardware.spectrometer === "ready" ? "success" : "neutral"} dot>{state.hardware.spectrometer}</StatusBadge></dd></div>
            <div><dt>Камера</dt><dd><StatusBadge tone={state.camera.connected ? "success" : "neutral"} dot>{state.camera.connected ? "Подключена" : "Не подключена"}</StatusBadge></dd></div>
            <div><dt>Модель камеры</dt><dd>{state.camera.model ?? "—"}</dd></div>
            <div><dt>LiveView / запись</dt><dd>{state.camera.liveview_active ? "включён" : "выключен"} / {state.camera.recording_active ? "идёт" : "не идёт"}</dd></div>
          </dl>
        </Panel>

        <Panel className="diagnostics-panel">
          <header><p className="panel__eyebrow">Фоновые задачи</p><h2>Текущие операции</h2></header>
          <div className="diagnostics-operations">
            {state.operations.map((operation) => <div key={operation.name}>
              <span>{operation.name}</span>
              <StatusBadge tone={statusTone(operation.status, operation.active)} dot>{operation.status}</StatusBadge>
              {operation.error && <small>{operation.error}</small>}
            </div>)}
          </div>
        </Panel>
      </div>

      <Panel className="diagnostics-panel">
        <header><p className="panel__eyebrow">Локальные данные</p><h2>Активные пути</h2></header>
        <dl className="diagnostics-paths">
          {Object.entries(state.paths).map(([key, value]) => <div key={key}>
            <dt>{pathLabels[key] ?? key}</dt><dd>{value || "—"}</dd>
          </div>)}
        </dl>
      </Panel>

      <div className="diagnostics-grid diagnostics-grid--bottom">
        <Panel className="diagnostics-panel">
          <header><p className="panel__eyebrow">Журнал</p><h2>Последние предупреждения и ошибки</h2></header>
          {state.recent_errors.length ? <ol className="diagnostics-errors">{state.recent_errors.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol> : <p className="diagnostics-empty">Ошибок не обнаружено.</p>}
        </Panel>
        <Panel className="diagnostics-panel diagnostics-summary">
          <header><p className="panel__eyebrow">Для обращения</p><h2>Безопасная сводка</h2></header>
          <pre>{state.copy_text}</pre>
        </Panel>
      </div>
    </>}
  </div>;
}
