import { useEffect, useMemo, useState } from "react";

import {
  cancelGuidedCamera, continueGuidedCamera, fetchGuidedCameraState, finishGuidedCamera,
  prepareGuidedCamera, fetchSeriesState, fetchStabilityState, preflightStability, setStabilitySetpoint,
  startStability, stopStability, type ActiveSeries, type StabilityPoint,
  type GuidedCameraState, type StabilityPreflight, type StabilityState, type StabilityTarget
} from "./api";
import { Button, Notice, Panel } from "./design-system/components";
import GuidedCameraPanel from "./GuidedCameraPanel";

const fields: [string, string, string][] = [
  ["current_setpoint_mA", "Уставка тока, мА", "0.1"],
  ["voltage_setpoint_V", "Уставка напряжения, В", "0.1"],
  ["voltage_start", "Стартовое напряжение, В", "0.1"],
  ["voltage_limit", "Предел напряжения, В", "0.1"],
  ["current_limit_mA", "Предел тока, мА", "0.1"],
  ["measurement_time_s", "Длительность, с", "1"],
  ["sample_interval_s", "Интервал точки, с", "0.1"],
  ["autosave_interval_s", "Интервал автосохранения, с", "1"]
];
const labels: Record<string, string> = {
  idle: "Ожидание", running: "Измерение", stop_requested: "Безопасная остановка",
  completed: "Завершено", stopped: "Остановлено", failed: "Ошибка"
};

function line(points: StabilityPoint[], value: (point: StabilityPoint) => number) {
  if (points.length < 2) return "";
  const xs = points.map((point) => point.elapsed_s);
  const ys = points.map(value);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys, 0), maxY = Math.max(...ys, 1e-9);
  return points.map((point, index) => {
    const x = 46 + ((point.elapsed_s - minX) / Math.max(maxX - minX, 1e-9)) * 710;
    const y = 218 - ((value(point) - minY) / Math.max(maxY - minY, 1e-9)) * 178;
    return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function StabilityChart({points}: {points: StabilityPoint[]}) {
  const current = useMemo(() => line(points, (point) => point.current_measured_mA), [points]);
  const photo = useMemo(() => line(points, (point) => point.photodiode_uA), [points]);
  return <div className="stability-chart">
    {current ? <svg viewBox="0 0 780 250" role="img" aria-label="График стабильности">
      {[0, 1, 2, 3, 4].map((row) => <line key={row} x1="46" x2="756" y1={40 + row * 44.5} y2={40 + row * 44.5} className="spectrum-chart__grid" />)}
      <line x1="46" x2="46" y1="40" y2="218" className="spectrum-chart__axis" />
      <line x1="46" x2="756" y1="218" y2="218" className="spectrum-chart__axis" />
      <path d={current} className="stability-chart__current" />
      <path d={photo} className="stability-chart__photo" />
      <text x="46" y="240">0 с</text><text x="690" y="240">{points.at(-1)?.elapsed_s.toFixed(1)} с</text>
    </svg> : <div className="spectrum-chart__empty">Live-график появится после первой точки</div>}
    <div className="stability-chart__legend"><span>— ток OLED, мА</span><span>— фототок, мкА</span></div>
  </div>;
}

export default function StabilityWorkspace({initialTarget = null, guidedCamera = false}: {initialTarget?: StabilityTarget | null; guidedCamera?: boolean}) {
  const [target, setTarget] = useState<StabilityTarget | null>(initialTarget);
  const [series, setSeries] = useState<ActiveSeries | null>(null);
  const [mode, setMode] = useState<"current" | "voltage">("current");
  const [useIvlStart, setUseIvlStart] = useState(Boolean(initialTarget));
  const [values, setValues] = useState<Record<string, string>>({});
  const [state, setState] = useState<StabilityState | null>(null);
  const [preflight, setPreflight] = useState<StabilityPreflight | null>(null);
  const [setpoint, setSetpoint] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [guided, setGuided] = useState<GuidedCameraState | null>(null);
  const [createTelemetry, setCreateTelemetry] = useState(true);

  useEffect(() => {
    let disposed = false, initialized = false, timer = 0;
    async function poll() {
      try {
        const next = await fetchStabilityState();
        if (!disposed) {
          setState(next); setConnected(true);
          if (!initialized) {
            const defaults = await preflightStability({});
            if (disposed) return;
            const source = next.params ?? defaults.params;
            setValues(Object.fromEntries(fields.map(([key]) => [key, String(source[key])])));
            const activeMode = String(source.control_mode ?? "current") as "current" | "voltage";
            setMode(activeMode); setSetpoint(String(next.current_setpoint ?? source[activeMode === "current" ? "current_setpoint_mA" : "voltage_setpoint_V"]));
            if (next.active) { setTarget(next.target ?? null); setUseIvlStart(next.use_ivl_start_voltage ?? false); }
            initialized = true;
          }
        }
      } catch { if (!disposed) setConnected(false); }
      finally { if (!disposed) timer = window.setTimeout(poll, 500); }
    }
    void poll();
    void fetchSeriesState().then((current) => { if (!disposed) setSeries(current.active); })
      .catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; window.clearTimeout(timer); };
  }, []);

  useEffect(() => {
    let disposed = false, timer = 0;
    async function pollGuided() {
      try {
        const next = await fetchGuidedCameraState();
        if (!disposed) setGuided(next);
      } catch { /* main connection warning already covers backend loss */ }
      finally { if (!disposed) timer = window.setTimeout(pollGuided, 500); }
    }
    void pollGuided();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, []);

  useEffect(() => {
    if (!state || state.active || !["completed", "stopped", "failed"].includes(state.status)) return;
    void fetchSeriesState().then((current) => setSeries(current.active)).catch(() => undefined);
  }, [state?.status, state?.active]);

  useEffect(() => {
    if (state?.active && state.current_setpoint != null) {
      setSetpoint(String(state.current_setpoint));
    }
  }, [state?.run_id]);

  function payload() {
    if (Object.values(values).some((value) => !value.trim() || !Number.isFinite(Number(value)))) {
      throw new Error("Заполните все числовые поля.");
    }
    return {
      ...Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])),
      control_mode: mode, target, use_ivl_start_voltage: mode === "current" && Boolean(target) && useIvlStart
    };
  }

  async function action(kind: "check" | "start" | "stop") {
    setBusy(true); setError("");
    try {
      if (kind === "stop") setState(await stopStability());
      else if (kind === "start" && preflight) {
        const measurement = {...preflight.params, target: preflight.target, use_ivl_start_voltage: preflight.use_ivl_start_voltage};
        if (guidedCamera) setGuided(await prepareGuidedCamera("stability", measurement, createTelemetry));
        else setState(await startStability(measurement));
        setPreflight(null);
      } else setPreflight(await preflightStability(payload()));
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  async function guidedAction(kind: "continue" | "photo" | "skip" | "cancel") {
    setBusy(true); setError("");
    try {
      setGuided(kind === "continue" ? await continueGuidedCamera()
        : kind === "photo" ? await finishGuidedCamera(true)
        : kind === "skip" ? await finishGuidedCamera(false)
        : await cancelGuidedCamera());
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  async function changeSetpoint(value: number) {
    if (!state?.run_id || !Number.isFinite(value)) return;
    setBusy(true); setError("");
    try {
      const next = await setStabilitySetpoint(state.run_id, value);
      setState(next); setSetpoint(String(next.current_setpoint ?? value));
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  const latest = state?.latest_point;
  const unit = mode === "current" ? "мА" : "В";
  const guidedActive = Boolean(guided?.active);
  return <section className="stability-workspace">
    <Notice title="Стабильность · эмулятор">Backend выполняет одиночный запуск, хранит live-точки и позволяет менять уставку без остановки. Для пикселя тестовой серии результат записывается в совместимый журнал; реальные приборы не включаются.</Notice>
    {guidedCamera && <Notice tone="warning" title="Измерение с камерой">После проверки параметров приложение сделает фото до измерения, запишет стабильность на видео и предложит контрольное фото после.</Notice>}
    {!connected && <Notice tone="warning" title="Соединение восстанавливается">Измерение продолжает выполняться на сервере; экран восстановит состояние.</Notice>}
    {error && <Notice tone="danger" title="Не удалось выполнить действие">{error}</Notice>}
    <Panel>
      <h2>Параметры стабильности</h2>
      <label>Пиксель <select disabled={busy || Boolean(state?.active) || guidedActive || guidedCamera} value={target?.pixel_id ?? ""} onChange={(event) => {
        setTarget(event.target.value && series ? {series_path: series.path, pixel_id: event.target.value} : null);
        setUseIvlStart(Boolean(event.target.value)); setPreflight(null);
      }}><option value="">Отдельный запуск SIM_STABILITY</option>{series?.pixels.filter((pixel) => pixel.last_ivl_file).map((pixel) => <option value={pixel.pixel_id} key={pixel.pixel_id}>{pixel.pixel_id} · {pixel.status}</option>)}</select></label>
      {!series && <p>Чтобы записать результат в журнал, откройте тестовую серию в разделе «Серия».</p>}
      <label>Режим <select disabled={busy || Boolean(state?.active) || guidedActive} value={mode} onChange={(event) => { setMode(event.target.value as "current" | "voltage"); setPreflight(null); }}><option value="current">Удерживать ток</option><option value="voltage">Удерживать напряжение</option></select></label>
      {mode === "current" && target && <label className="ivl-checkbox"><input type="checkbox" checked={useIvlStart} disabled={busy || Boolean(state?.active)} onChange={(event) => { setUseIvlStart(event.target.checked); setPreflight(null); }} />Рассчитать старт как 90% напряжения последней ВАЯХ при выбранном токе</label>}
      {guidedCamera && <label className="ivl-checkbox"><input type="checkbox" checked={createTelemetry} disabled={busy || guidedActive} onChange={(event) => setCreateTelemetry(event.target.checked)} />Создать рядом копию MP4 с показаниями стабильности</label>}
      <fieldset className="ivl-fields" disabled={busy || Boolean(state?.active) || guidedActive}>
        {fields.filter(([key]) => mode === "current" ? key !== "voltage_setpoint_V" : !["current_setpoint_mA", "voltage_start"].includes(key)).map(([key, label, step]) => <label key={key}>{label}<input type="number" step={step} value={values[key] ?? ""} onChange={(event) => { setValues({...values, [key]: event.target.value}); setPreflight(null); }} /></label>)}
      </fieldset>
      <div className="ivl-actions"><Button disabled={busy || !connected || state?.active || guidedActive || !Object.keys(values).length} onClick={() => void action("check")}>Проверить параметры</Button><Button variant="primary" disabled={busy || !connected || !preflight || state?.active || guidedActive} onClick={() => void action("start")}>{guidedCamera ? "Сделать фото до измерения" : "Начать стабильность"}</Button><Button variant="danger" disabled={busy || !connected || !state?.active} onClick={() => void action("stop")}>Безопасно остановить</Button></div>
      {preflight && <p>{preflight.note}<br />Стартовое напряжение: {preflight.effective_voltage_start.toFixed(3)} В{preflight.ivl_voltage_at_target != null ? ` — 90% от ${preflight.ivl_voltage_at_target.toFixed(3)} В по ВАЯХ` : ""}.<br />Папка результатов: {preflight.output_root}</p>}
    </Panel>
    {guided && (guidedCamera || guided.station === "stability") && guided.status !== "idle" && <GuidedCameraPanel state={guided} busy={busy} onContinue={() => void guidedAction("continue")} onFinish={(takePhoto) => void guidedAction(takePhoto ? "photo" : "skip")} onCancel={() => void guidedAction("cancel")} />}
    {state?.active && <Panel><h2>Динамическая уставка</h2><p>Текущая цель: {state.current_setpoint?.toFixed(3)} {latest?.target_unit ?? unit}. Новое значение применяется backend без перезапуска.</p><div className="stability-setpoint"><input type="number" step="0.1" value={setpoint} onChange={(event) => setSetpoint(event.target.value)} />{[-1, -0.5, -0.25, -0.1, 0.1, 0.25, 0.5, 1].map((delta) => <Button compact disabled={busy || !connected} key={delta} onClick={() => void changeSetpoint((state.current_setpoint ?? 0) + delta)}>{delta > 0 ? "+" : ""}{delta}</Button>)}<Button variant="primary" disabled={busy || !connected || !Number.isFinite(Number(setpoint))} onClick={() => void changeSetpoint(Number(setpoint))}>Применить</Button></div></Panel>}
    <Panel>
      <h2>{labels[state?.status ?? "idle"] ?? state?.status} · {state?.pixel_id ?? "SIM_STABILITY"} · {state?.point_count ?? 0} точек</h2>
      <div className="stability-metrics"><span>Время<strong>{latest ? `${latest.elapsed_s.toFixed(1)} с` : "—"}</strong></span><span>Напряжение<strong>{latest ? `${latest.voltage_measured_V.toFixed(3)} В` : "—"}</strong></span><span>Ток OLED<strong>{latest ? `${latest.current_measured_mA.toFixed(4)} мА` : "—"}</strong></span><span>Фототок<strong>{latest ? `${latest.photodiode_uA.toFixed(4)} мкА` : "—"}</strong></span><span>Яркость<strong>{latest ? `${latest.luminance_cd_m2.toFixed(2)} кд/м²` : "—"}</strong></span></div>
      <StabilityChart points={state?.points ?? []} />
      <p>{state?.message}</p>
      {state?.error && <Notice tone="danger" title="Ошибка измерения">{state.error}</Notice>}
      {state?.safe_shutdown_confirmed === true && <p>Отключение выходов SMU подтверждено.</p>}
      {state?.safe_shutdown_confirmed === false && <Notice tone="danger" title="Отключение не подтверждено">Результат не записан в журнал.</Notice>}
      {state?.result && <p>Статус: {state.result.status}. Финальная уставка: {state.result.final_setpoint} {state.result.control_mode === "current" ? "мА" : "В"}.<br />Excel: {state.result.file}<br />{state.result.journaled ? "Результат записан в журнал серии." : "Результат в журнал серии не записан."}</p>}
    </Panel>
  </section>;
}
