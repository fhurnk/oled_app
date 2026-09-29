import {type GuidedCameraState} from "./api";
import {Button, Notice, Panel, StatusBadge} from "./design-system/components";

const labels: Record<string, string> = {
  idle: "Ожидание",
  capturing_before: "Фото до измерения",
  awaiting_measurement: "Нужно подтверждение",
  starting_measurement: "Запуск",
  measuring: "Видео + измерение",
  postroll: "Post-roll",
  processing_video: "Скачивание видео",
  processing_telemetry: "Видео с показаниями",
  awaiting_after_photo: "Нужно финальное фото",
  capturing_after: "Фото после измерения",
  completed: "Завершено",
  cancelled: "Отменено",
  failed: "Ошибка"
};

export default function GuidedCameraPanel({
  state,
  busy,
  onContinue,
  onFinish,
  onCancel
}: {
  state: GuidedCameraState;
  busy: boolean;
  onContinue: () => void;
  onFinish: (takePhoto: boolean) => void;
  onCancel: () => void;
}) {
  const terminal = ["completed", "cancelled", "failed"].includes(state.status);
  return <Panel>
    <div className="panel__header">
      <div>
        <p className="panel__eyebrow">Сопровождаемая камера</p>
        <h2>Фото до → видео + измерение → фото после</h2>
      </div>
      <StatusBadge tone={state.status === "failed" ? "danger" : state.active ? "info" : state.status === "completed" ? "success" : "neutral"}>
        {labels[state.status] ?? state.status}
      </StatusBadge>
    </div>
    <p>{state.message}</p>
    {state.status === "postroll" && <p>Осталось: {state.postroll_remaining_s.toFixed(1)} с</p>}
    {state.before_photo && <p className="guided-camera-file" title={state.before_photo}>Фото до: {state.before_photo}</p>}
    {state.video_file && <p className="guided-camera-file" title={state.video_file}>Видео: {state.video_file}</p>}
    {state.timeline_file && <p className="guided-camera-file" title={state.timeline_file}>Шкала измерения: {state.timeline_file}</p>}
    {state.telemetry_file && <p className="guided-camera-file" title={state.telemetry_file}>Видео с показаниями: {state.telemetry_file}</p>}
    {state.telemetry_error && <Notice tone="warning" title="Исходное видео сохранено">Копию с показаниями создать не удалось: {state.telemetry_error}</Notice>}
    {state.after_photo && <p className="guided-camera-file" title={state.after_photo}>Фото после: {state.after_photo}</p>}
    {state.error && <Notice tone="danger" title="Сценарий остановлен">{state.error}</Notice>}
    <div className="ivl-actions">
      {state.status === "awaiting_measurement" && <Button disabled={busy} variant="primary" onClick={onContinue}>Продолжить: видео + измерение</Button>}
      {state.status === "awaiting_after_photo" && <>
        <Button disabled={busy} variant="primary" onClick={() => onFinish(true)}>Сделать фото после</Button>
        <Button disabled={busy} onClick={() => onFinish(false)}>Завершить без фото</Button>
      </>}
      {state.active && !["capturing_after", "awaiting_after_photo"].includes(state.status) && <Button disabled={busy || state.cancel_requested} variant="danger" onClick={onCancel}>Безопасно отменить сценарий</Button>}
      {terminal && state.status !== "completed" && <small>Фото и видео, сохранённые до остановки, остаются в журнале серии.</small>}
    </div>
  </Panel>;
}
