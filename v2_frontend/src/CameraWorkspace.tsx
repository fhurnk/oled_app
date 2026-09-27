import { useCallback, useEffect, useRef, useState } from "react";

import {
  type CameraState,
  connectCamera,
  disconnectCamera,
  fetchCameraFrame,
  fetchCameraState,
  refreshCamera,
  startCameraLiveview,
  stopCameraLiveview
} from "./api";
import { Button, StatusBadge } from "./design-system/components";

function field(value: unknown, fallback = "—"): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return fallback;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

export default function CameraWorkspace({ onConnectionChanged }: {onConnectionChanged?: () => void}) {
  const [state, setState] = useState<CameraState | null>(null);
  const [host, setHost] = useState("192.168.4.1");
  const [port, setPort] = useState("8765");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const frameSequence = useRef(0);
  const currentFrameUrl = useRef<string | null>(null);
  const connectionState = useRef<boolean | null>(null);

  const applyState = useCallback((next: CameraState) => {
    setState(next);
    if (!next.connected || !next.frame_sequence) {
      if (currentFrameUrl.current) URL.revokeObjectURL(currentFrameUrl.current);
      currentFrameUrl.current = null;
      frameSequence.current = 0;
      setFrameUrl(null);
    }
    if (connectionState.current !== next.connected) {
      connectionState.current = next.connected;
      onConnectionChanged?.();
    }
    if (!next.connected) {
      setHost(next.host || "192.168.4.1");
      setPort(String(next.port || 8765));
    }
  }, [onConnectionChanged]);

  const loadFrame = useCallback(async (sequence: number) => {
    if (!sequence || sequence === frameSequence.current) return;
    const blob = await fetchCameraFrame();
    const url = URL.createObjectURL(blob);
    if (currentFrameUrl.current) URL.revokeObjectURL(currentFrameUrl.current);
    currentFrameUrl.current = url;
    frameSequence.current = sequence;
    setFrameUrl(url);
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const next = await fetchCameraState();
        if (disposed) return;
        applyState(next);
        if (next.frame_sequence) await loadFrame(next.frame_sequence);
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : "Не удалось прочитать состояние камеры.");
      } finally {
        if (!disposed) timer = window.setTimeout(poll, 600);
      }
    };
    void poll();
    return () => {
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
      if (currentFrameUrl.current) URL.revokeObjectURL(currentFrameUrl.current);
      currentFrameUrl.current = null;
    };
  }, [applyState, loadFrame]);

  const run = useCallback(async (action: () => Promise<CameraState>) => {
    setBusy(true);
    setError("");
    try {
      applyState(await action());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Операция камеры не выполнена.");
    } finally {
      setBusy(false);
    }
  }, [applyState]);

  const cameraStatus = state?.camera_status ?? {};
  const health = state?.health ?? {};

  return (
    <div className="camera-workspace">
      <article className="panel camera-connect-panel">
        <div className="panel__header">
          <div>
            <p className="panel__eyebrow">Этап 6 · свободная камера</p>
            <h2>Raspberry Pi camera service</h2>
            <p className="panel__subtitle">Подключение, состояние сервиса, LiveView и список удалённых файлов без запуска измерения.</p>
          </div>
          <StatusBadge tone={state?.connected ? "success" : state?.error || error ? "danger" : "neutral"}>
            {state?.connected ? "Подключена" : "Отключена"}
          </StatusBadge>
        </div>
        <div className="camera-connect-row">
          <label className="field">
            <span className="field__label">Хост или IP</span>
            <input className="text-input" disabled={state?.connected || busy} value={host} onChange={(event) => setHost(event.target.value)} />
          </label>
          <label className="field camera-port-field">
            <span className="field__label">Порт</span>
            <input className="text-input" disabled={state?.connected || busy} inputMode="numeric" value={port} onChange={(event) => setPort(event.target.value)} />
          </label>
          <div className="camera-actions">
            {!state?.connected ? (
              <Button disabled={busy} onClick={() => void run(() => connectCamera(host, Number(port), true))} variant="primary">Подключить и инициализировать</Button>
            ) : (
              <>
                <Button disabled={busy || state.liveview_active} onClick={() => void run(refreshCamera)}>Обновить</Button>
                <Button disabled={busy} onClick={() => void run(disconnectCamera)} variant="danger">Отключить</Button>
              </>
            )}
          </div>
        </div>
        <div className={`camera-message ${error || state?.error ? "camera-message--error" : ""}`}>
          {error || state?.error || state?.message || "Введите адрес сервиса камеры."}
        </div>
      </article>

      <div className="camera-main-grid">
        <article className="panel camera-preview-panel">
          <div className="panel__header">
            <div>
              <p className="panel__eyebrow">LiveView</p>
              <h2>Предпросмотр камеры</h2>
            </div>
            <div className="camera-actions">
              <Button disabled={busy || !state?.connected || state?.liveview_active} onClick={() => void run(startCameraLiveview)} variant="primary">Запустить</Button>
              <Button disabled={busy || !state?.liveview_active} onClick={() => void run(stopCameraLiveview)} variant="danger">Остановить</Button>
            </div>
          </div>
          <div className="camera-preview">
            {frameUrl ? <img src={frameUrl} alt="LiveView камеры" /> : <div><span aria-hidden="true">◎</span><strong>Кадр ещё не получен</strong><p>Подключите сервис и запустите LiveView.</p></div>}
          </div>
          <div className="camera-frame-meta">
            <span>Кадр {state?.frame_sequence ?? 0}</span>
            <span>{state?.frame_size ? formatSize(state.frame_size) : "—"}</span>
            <span>{state?.frame_received_at ? new Date(state.frame_received_at).toLocaleTimeString("ru-RU") : "—"}</span>
          </div>
        </article>

        <aside className="panel camera-status-panel">
          <div className="panel__header"><div><p className="panel__eyebrow">Состояние</p><h2>Сервис и камера</h2></div></div>
          <dl className="details-list">
            <div><dt>Адрес</dt><dd title={state?.base_url ?? undefined}>{state?.base_url ?? "—"}</dd></div>
            <div><dt>Сервис</dt><dd>{field(health.status, state?.connected ? "online" : "—")}</dd></div>
            <div><dt>Инициализация</dt><dd>{state?.initialized ? "готова" : "нет"}</dd></div>
            <div><dt>LiveView</dt><dd>{state?.liveview_active ? "активен" : "остановлен"}</dd></div>
            <div><dt>Модель</dt><dd>{field(cameraStatus.camera_model ?? cameraStatus.model)}</dd></div>
            <div><dt>Удалённых файлов</dt><dd>{state?.files.length ?? 0}</dd></div>
          </dl>
        </aside>
      </div>

      <article className="panel camera-files-panel">
        <div className="panel__header">
          <div><p className="panel__eyebrow">Хранилище Raspberry Pi</p><h2>Удалённые файлы</h2></div>
          <StatusBadge tone={state?.files.length ? "info" : "neutral"}>{state?.files.length ?? 0}</StatusBadge>
        </div>
        <div className="camera-files-table">
          <table className="data-table">
            <thead><tr><th>Имя</th><th>Тип</th><th>Размер</th><th>Создан</th></tr></thead>
            <tbody>
              {state?.files.length ? state.files.map((item) => (
                <tr key={item.file_id}><td title={item.name}>{item.name}</td><td>{item.kind}</td><td>{formatSize(item.size)}</td><td>{item.created_at || "—"}</td></tr>
              )) : <tr><td className="camera-files-empty" colSpan={4}>{state?.connected ? "На сервисе нет файлов." : "Подключитесь к сервису камеры."}</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="camera-scope-note">Съёмка, загрузка, удаление и привязка файлов к серии будут перенесены следующими checkpoint-ами Stage 6.</p>
      </article>
    </div>
  );
}
