import { type Dispatch, type SetStateAction, useCallback, useEffect, useRef, useState } from "react";

import {
  type CameraState,
  captureCameraFile,
  connectCamera,
  deleteCameraFile,
  disconnectCamera,
  downloadCameraFile,
  fetchCameraFrame,
  fetchCameraState,
  refreshCamera,
  startCameraRecording,
  startCameraLiveview,
  stopCameraRecording,
  stopCameraLiveview,
  updateCameraPreferences
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

function formatDuration(startedAt: string | null | undefined): string {
  if (!startedAt) return "00:00";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return [hours, minutes, rest]
    .filter((_value, index) => hours > 0 || index > 0)
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

export default function CameraWorkspace({ onConnectionChanged }: {onConnectionChanged?: () => void}) {
  const [state, setState] = useState<CameraState | null>(null);
  const [host, setHost] = useState("192.168.4.1");
  const [port, setPort] = useState("8765");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [photoSettings, setPhotoSettings] = useState<Record<string, string>>({});
  const [videoSettings, setVideoSettings] = useState<Record<string, string>>({});
  const [cropWidth, setCropWidth] = useState("100");
  const [cropHeight, setCropHeight] = useState("100");
  const [keepRemote, setKeepRemote] = useState(true);
  const [captureName, setCaptureName] = useState("");
  const frameSequence = useRef(0);
  const currentFrameUrl = useRef<string | null>(null);
  const connectionState = useRef<boolean | null>(null);
  const preferencesConnection = useRef<string | null>(null);

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
    const preferenceKey = next.connected ? next.base_url : "disconnected";
    if (preferencesConnection.current !== preferenceKey) {
      preferencesConnection.current = preferenceKey;
      const controls = [
        ...((next.capabilities?.photo_controls as Array<Record<string, unknown>> | undefined) ?? []),
        ...((next.capabilities?.exposure_controls as Array<Record<string, unknown>> | undefined) ?? [])
      ];
      const selected: Record<string, string> = {};
      controls.forEach((control) => {
        const path = String(control.path ?? "");
        const choices = Array.isArray(control.choices) ? control.choices.map(String) : [];
        const saved = next.preferences.photo_settings[path];
        if (path && choices.length) {
          selected[path] = choices.includes(saved) ? saved : String(control.current ?? choices[0]);
        }
      });
      setPhotoSettings(selected);
      const videoControls = [
        ...((next.capabilities?.video_quality_controls as Array<Record<string, unknown>> | undefined) ?? []),
        ...((next.capabilities?.video_fps_controls as Array<Record<string, unknown>> | undefined) ?? [])
      ];
      const selectedVideo: Record<string, string> = {};
      videoControls.forEach((control) => {
        const path = String(control.path ?? "");
        const choices = Array.isArray(control.choices) ? control.choices.map(String) : [];
        const saved = next.preferences.video_settings[path];
        if (path && choices.length) {
          selectedVideo[path] = choices.includes(saved) ? saved : String(control.current ?? choices[0]);
        }
      });
      setVideoSettings(selectedVideo);
      setCropWidth(String(next.preferences.crop.width_percent));
      setCropHeight(String(next.preferences.crop.height_percent));
      setKeepRemote(next.preferences.keep_remote_files);
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
  const qualityControls = (state?.capabilities?.photo_controls as Array<Record<string, unknown>> | undefined) ?? [];
  const exposureControls = (state?.capabilities?.exposure_controls as Array<Record<string, unknown>> | undefined) ?? [];
  const videoControls = [
    ...((state?.capabilities?.video_quality_controls as Array<Record<string, unknown>> | undefined) ?? []),
    ...((state?.capabilities?.video_fps_controls as Array<Record<string, unknown>> | undefined) ?? [])
  ];
  const crop = {width_percent: Number(cropWidth), height_percent: Number(cropHeight)};
  const transfer = state?.last_transfer;

  const renderControls = (
    controls: Array<Record<string, unknown>>,
    emptyText: string,
    selected: Record<string, string>,
    setSelected: Dispatch<SetStateAction<Record<string, string>>>
  ) => (
    controls.length ? controls.map((control) => {
      const path = String(control.path ?? "");
      const choices = Array.isArray(control.choices) ? control.choices.map(String) : [];
      return (
        <label className="field" key={path}>
          <span className="field__label">{String(control.label ?? path)}</span>
          <select className="select-input" disabled={busy || state?.recording_active} value={selected[path] ?? String(control.current ?? choices[0] ?? "")} onChange={(event) => setSelected((current) => ({...current, [path]: event.target.value}))}>
            {choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
          </select>
        </label>
      );
    }) : <p className="camera-controls-empty">{emptyText}</p>
  );

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
              <Button disabled={busy || !state?.connected || state?.liveview_active || state?.recording_active} onClick={() => void run(() => startCameraLiveview(videoSettings))} variant="primary">Запустить</Button>
              <Button disabled={busy || !state?.liveview_active || state?.recording_active} onClick={() => void run(stopCameraLiveview)} variant="danger">Остановить</Button>
            </div>
          </div>
          <div className="camera-preview">
            {frameUrl ? <div className="camera-image-stage"><img src={frameUrl} alt="LiveView камеры" />{Number(cropWidth) >= 1 && Number(cropHeight) >= 1 && <span className="camera-crop-guide" style={{width: `${Math.min(Number(cropWidth), 100)}%`, height: `${Math.min(Number(cropHeight), 100)}%`}} />}</div> : <div className="camera-preview__empty"><span aria-hidden="true">◎</span><strong>Кадр ещё не получен</strong><p>Подключите сервис и запустите LiveView.</p></div>}
          </div>
          <div className="camera-frame-meta">
            <span>Кадр {state?.frame_sequence ?? 0}</span>
            <span>{state?.frame_size ? formatSize(state.frame_size) : "—"}</span>
            <span>{state?.frame_received_at ? new Date(state.frame_received_at).toLocaleTimeString("ru-RU") : "—"}</span>
          </div>
          <div className="camera-capture-row">
            <label className="field">
              <span className="field__label">Имя файла, необязательно</span>
              <input className="text-input" disabled={busy || !state?.connected || state?.recording_active} value={captureName} onChange={(event) => setCaptureName(event.target.value)} placeholder="например, sample_before" />
            </label>
            <div className="camera-actions">
              <Button disabled={busy || !state?.liveview_active || state?.recording_active} onClick={() => void run(() => captureCameraFile("snapshot", captureName, photoSettings, crop, keepRemote))}>Сохранить preview</Button>
              <Button disabled={busy || !state?.connected || state?.recording_active} onClick={() => void run(() => captureCameraFile("photo", captureName, photoSettings, crop, keepRemote))} variant="primary">Сделать фото</Button>
            </div>
          </div>
          <div className={`camera-recording ${state?.recording_active ? "camera-recording--active" : ""}`}>
            <div><span className="camera-recording__dot" /><strong>{state?.recording_active ? `Запись · ${formatDuration(state.recording_started_at)}` : "Видеозапись остановлена"}</strong><small>MP4 формируется на Raspberry Pi из того же потока, что и LiveView.</small></div>
            <div className="camera-actions">
              <Button disabled={busy || !state?.connected || state?.recording_active} onClick={() => void run(() => startCameraRecording(videoSettings, crop, keepRemote))} variant="primary">Начать видео</Button>
              <Button disabled={busy || !state?.recording_active} onClick={() => void run(stopCameraRecording)} variant="danger">Завершить и скачать</Button>
            </div>
          </div>
          {transfer && <div className="camera-transfer"><strong>{transfer.action === "video" ? "Видео корректно завершено и проверено" : "Проверенное скачивание завершено"}</strong><span title={transfer.local_file}>{transfer.local_file}</span>{transfer.remote_deleted && <small>Исходник удалён с Raspberry Pi.</small>}{transfer.delete_error && <small>Исходник не удалён: {transfer.delete_error}</small>}</div>}
        </article>

        <aside className="panel camera-status-panel">
          <div className="panel__header"><div><p className="panel__eyebrow">Состояние</p><h2>Сервис и камера</h2></div></div>
          <dl className="details-list">
            <div><dt>Адрес</dt><dd title={state?.base_url ?? undefined}>{state?.base_url ?? "—"}</dd></div>
            <div><dt>Сервис</dt><dd>{field(health.status, state?.connected ? "online" : "—")}</dd></div>
            <div><dt>Инициализация</dt><dd>{state?.initialized ? "готова" : "нет"}</dd></div>
            <div><dt>LiveView</dt><dd>{state?.liveview_active ? "активен" : "остановлен"}</dd></div>
            <div><dt>Видеозапись</dt><dd>{state?.recording_active ? formatDuration(state.recording_started_at) : "остановлена"}</dd></div>
            <div><dt>Модель</dt><dd>{field(cameraStatus.camera_model ?? cameraStatus.model)}</dd></div>
            <div><dt>Удалённых файлов</dt><dd>{state?.files.length ?? 0}</dd></div>
          </dl>
        </aside>
      </div>

      <article className="panel camera-settings-panel">
        <div className="panel__header">
          <div><p className="panel__eyebrow">Параметры съёмки</p><h2>JPEG, экспозиция и центральный кроп</h2><p className="panel__subtitle">Показываются только значения, разрешённые подключённой камерой.</p></div>
          <Button disabled={busy || !state?.connected || state?.recording_active} onClick={() => void run(() => updateCameraPreferences(photoSettings, videoSettings, crop, keepRemote))} variant="primary">Сохранить параметры</Button>
        </div>
        <div className="camera-settings-grid">
          <section><h3>Качество JPEG</h3>{renderControls(qualityControls, "Камера не сообщила переключаемые JPEG-параметры.", photoSettings, setPhotoSettings)}</section>
          <section><h3>Экспозиция</h3>{renderControls(exposureControls, "Нет доступных параметров. Для Canon обычно требуется режим M.", photoSettings, setPhotoSettings)}</section>
          <section><h3>Видео Canon</h3>{renderControls(videoControls, "Камера сама задаёт качество и FPS; доступны текущие параметры потока.", videoSettings, setVideoSettings)}</section>
          <section>
            <h3>Центральный кроп</h3>
            <div className="camera-crop-fields">
              <label className="field"><span className="field__label">Ширина, %</span><input className="text-input" disabled={busy || state?.recording_active} type="number" min="1" max="100" step="1" value={cropWidth} onChange={(event) => setCropWidth(event.target.value)} /></label>
              <label className="field"><span className="field__label">Высота, %</span><input className="text-input" disabled={busy || state?.recording_active} type="number" min="1" max="100" step="1" value={cropHeight} onChange={(event) => setCropHeight(event.target.value)} /></label>
            </div>
            <Button compact disabled={busy || state?.recording_active} onClick={() => {setCropWidth("100"); setCropHeight("100");}}>Сбросить 100 × 100%</Button>
            <label className="camera-checkbox"><input checked={keepRemote} disabled={busy || state?.recording_active} type="checkbox" onChange={(event) => setKeepRemote(event.target.checked)} /><span>Оставлять исходник на Raspberry Pi после скачивания</span></label>
            <p className="camera-download-dir" title={state?.preferences.download_dir}>Локальная папка: {state?.preferences.download_dir ?? "—"}</p>
          </section>
        </div>
      </article>

      <article className="panel camera-files-panel">
        <div className="panel__header">
          <div><p className="panel__eyebrow">Хранилище Raspberry Pi</p><h2>Удалённые файлы</h2></div>
          <StatusBadge tone={state?.files.length ? "info" : "neutral"}>{state?.files.length ?? 0}</StatusBadge>
        </div>
        <div className="camera-files-table">
          <table className="data-table">
            <thead><tr><th>Имя</th><th>Тип</th><th>Размер</th><th>Создан</th><th>Действия</th></tr></thead>
            <tbody>
              {state?.files.length ? state.files.map((item) => (
                <tr key={item.file_id}><td title={item.name}>{item.name}</td><td>{item.kind}</td><td>{formatSize(item.size)}</td><td>{item.created_at || "—"}</td><td><div className="camera-file-actions"><Button compact disabled={busy} onClick={() => void run(() => downloadCameraFile(item.file_id))}>Скачать</Button><Button compact disabled={busy} onClick={() => {if (window.confirm(`Удалить ${item.name} с Raspberry Pi без возможности восстановления?`)) void run(() => deleteCameraFile(item.file_id));}} variant="danger">Удалить</Button></div></td></tr>
              )) : <tr><td className="camera-files-empty" colSpan={5}>{state?.connected ? "На сервисе нет файлов." : "Подключитесь к сервису камеры."}</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="camera-scope-note">Фото и завершённый MP4 скачиваются через временный `.part` с проверкой размера/SHA-256. Режим камеры серии будет перенесён следующим checkpoint Stage 6.</p>
      </article>
    </div>
  );
}
