import { useEffect, useMemo, useState } from "react";
import {
  fetchSeriesState, fetchSpectrumState, preflightSpectrum, startSpectrum, stopSpectrum,
  type ActiveSeries, type SpectrumCurve, type SpectrumPreflight, type SpectrumState,
  type SpectrumTarget
} from "./api";
import { Button, Notice, Panel } from "./design-system/components";

const numericFields: [string, string, string][] = [
  ["voltage_start", "Начало, В", "0.1"], ["voltage_end", "Конец, В", "0.1"],
  ["voltage_step", "Шаг, В", "0.01"], ["current_limit_mA", "Лимит тока, мА", "0.1"],
  ["target_intensity", "Цель, counts", "100"], ["intensity_min", "Минимум, counts", "100"],
  ["intensity_max", "Максимум, counts", "100"], ["saturation_level", "Насыщение, counts", "100"],
  ["t_int_initial_s", "T_int начальное, с", "0.001"], ["t_int_min_s", "T_int минимум, с", "0.001"],
  ["t_int_max_s", "T_int максимум, с", "0.01"], ["max_iterations", "Итераций T_int", "1"]
];
const statusLabels: Record<string, string> = {
  idle: "Ожидание", running: "Съёмка спектров", stop_requested: "Безопасная остановка",
  completed: "Завершено", stopped: "Остановлено", failed: "Ошибка"
};

function SpectrumChart({curve}: {curve: SpectrumCurve | null}) {
  const path = useMemo(() => {
    if (!curve || curve.wavelengths_nm.length < 2) return "";
    const xs = curve.wavelengths_nm, ys = curve.intensities;
    const minX = Math.min(...xs), maxX = Math.max(...xs), maxY = Math.max(...ys, 1);
    return xs.map((x, index) => {
      const px = 44 + ((x - minX) / Math.max(maxX - minX, 1)) * 716;
      const py = 224 - (ys[index] / maxY) * 190;
      return `${index ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
    }).join(" ");
  }, [curve]);
  return <div className="spectrum-chart">
    {path ? <svg viewBox="0 0 780 250" role="img" aria-label="Последний спектр">
      {[0, 1, 2, 3, 4].map((row) => <line key={row} x1="44" x2="760" y1={34 + row * 47.5} y2={34 + row * 47.5} className="spectrum-chart__grid" />)}
      <line x1="44" x2="44" y1="34" y2="224" className="spectrum-chart__axis" />
      <line x1="44" x2="760" y1="224" y2="224" className="spectrum-chart__axis" />
      <path d={path} className="spectrum-chart__line" />
      <text x="44" y="244">{curve?.wavelengths_nm[0]?.toFixed(0)} нм</text>
      <text x="710" y="244">{curve?.wavelengths_nm.at(-1)?.toFixed(0)} нм</text>
    </svg> : <div className="spectrum-chart__empty">Спектр появится во время подбора T_int</div>}
  </div>;
}

export default function SpectrumWorkspace({initialTarget = null}: {initialTarget?: SpectrumTarget | null}) {
  const [target, setTarget] = useState<SpectrumTarget | null>(initialTarget);
  const [series, setSeries] = useState<ActiveSeries | null>(null);
  const [useOpening, setUseOpening] = useState(true);
  const [state, setState] = useState<SpectrumState | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [ledType, setLedType] = useState("auto");
  const [preflight, setPreflight] = useState<SpectrumPreflight | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let disposed = false, initialized = false, timer = 0;
    async function poll() {
      try {
        const next = await fetchSpectrumState();
        if (!disposed) {
          setState(next); setConnected(true);
          if (!initialized) {
            const defaults = await preflightSpectrum({});
            if (disposed) return;
            const source = next.params ?? defaults.params;
            setValues(Object.fromEntries(numericFields.map(([key]) => [key, String(source[key])])));
            setLedType(String(source.led_type ?? "auto"));
            if (next.active) { setTarget(next.target ?? null); setUseOpening(next.use_opening_voltage ?? true); }
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
    if (!state || state.active || !["completed", "stopped", "failed"].includes(state.status)) return;
    void fetchSeriesState().then((current) => setSeries(current.active)).catch(() => undefined);
  }, [state?.status, state?.active]);

  function payload() {
    if (Object.values(values).some((value) => !value.trim() || !Number.isFinite(Number(value)))) {
      throw new Error("Заполните все числовые поля.");
    }
    return {
      ...Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])),
      led_type: ledType, use_opening_voltage: useOpening, target
    };
  }

  async function action(kind: "check" | "start" | "stop") {
    setBusy(true); setError("");
    try {
      if (kind === "stop") setState(await stopSpectrum());
      else if (kind === "start" && preflight) {
        setState(await startSpectrum({...preflight.params, target: preflight.target, use_opening_voltage: preflight.use_opening_voltage}));
        setPreflight(null);
      } else {
        setPreflight(await preflightSpectrum(payload()));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  const curve = state?.optimization ?? state?.latest_spectrum ?? null;
  return <section className="spectrum-workspace">
    <Notice title="Спектры · эмулятор">Первый спектральный контур v2 снимает один выбранный пиксель или отдельный SIM_SPECTRUM. Backend подбирает T_int, контролирует ток и сохраняет совместимые CSV/Excel. Реальные приборы не включаются.</Notice>
    {!connected && <Notice tone="warning" title="Соединение восстанавливается">Измерение продолжает выполняться на сервере; экран восстановит состояние.</Notice>}
    {error && <Notice tone="danger" title="Не удалось выполнить действие">{error}</Notice>}
    <Panel>
      <h2>Параметры спектра</h2>
      <label>Пиксель <select disabled={busy || Boolean(state?.active)} value={target?.pixel_id ?? ""} onChange={(event) => {
        setTarget(event.target.value && series ? {series_path: series.path, pixel_id: event.target.value} : null); setPreflight(null);
      }}>
        <option value="">Отдельный запуск SIM_SPECTRUM</option>
        {series?.pixels.map((pixel) => <option key={pixel.pixel_id} value={pixel.pixel_id}>{pixel.pixel_id} · {pixel.status}{pixel.opening_voltage_V ? ` · Vоткр ${pixel.opening_voltage_V}` : ""}</option>)}
      </select></label>
      {!series && <p>Чтобы выбрать реальный идентификатор пикселя, откройте тестовую серию в разделе «Серия».</p>}
      {target && <label className="ivl-checkbox"><input type="checkbox" disabled={busy || Boolean(state?.active)} checked={useOpening} onChange={(event) => { setUseOpening(event.target.checked); setPreflight(null); }} />Начинать с напряжения открытия из журнала</label>}
      <fieldset className="ivl-fields" disabled={busy || Boolean(state?.active)}>
        {numericFields.map(([key, label, step]) => <label key={key}>{label}<input type="number" step={step} value={state?.active && state.params ? String(state.params[key]) : values[key] ?? ""} onChange={(event) => { setValues({...values, [key]: event.target.value}); setPreflight(null); }} /></label>)}
        <label>Диапазон LED<select value={ledType} onChange={(event) => { setLedType(event.target.value); setPreflight(null); }}>
          {[["auto", "Авто"], ["red", "Красный"], ["green", "Зелёный"], ["blue", "Синий"], ["white", "Белый"], ["all", "Весь диапазон"]].map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select></label>
      </fieldset>
      <div className="ivl-actions">
        <Button disabled={busy || !connected || state?.active || !Object.keys(values).length} onClick={() => void action("check")}>Проверить параметры</Button>
        <Button variant="primary" disabled={busy || !connected || !preflight || state?.active} onClick={() => void action("start")}>Начать съёмку</Button>
        <Button variant="danger" disabled={busy || !connected || !state?.active} onClick={() => void action("stop")}>Остановить</Button>
      </div>
      {preflight && <p>{preflight.note}<br />Диапазон: {preflight.effective_voltage_start}–{preflight.params.voltage_end} В, {preflight.point_count} точек.<br />Папка результатов: {preflight.output_root}</p>}
    </Panel>
    <Panel>
      <h2>{statusLabels[state?.status ?? "idle"] ?? state?.status} · {state?.pixel_id ?? "SIM_SPECTRUM"} · {state?.point_count ?? 0} точек</h2>
      {state?.optimization && <p>Подбор T_int: точка {state.optimization.point}, итерация {state.optimization.iteration}, {state.optimization.integration_time_s * 1000} мс · {state.optimization.status}</p>}
      <SpectrumChart curve={curve} />
      <p>{state?.message}</p>
      {state?.error && <Notice tone="danger" title="Ошибка измерения">{state.error}</Notice>}
      {state?.safe_shutdown_confirmed === true && <p>Отключение выходов SMU подтверждено.</p>}
      {state?.safe_shutdown_confirmed === false && <Notice tone="danger" title="Отключение не подтверждено">Результат не записан в журнал.</Notice>}
      {state?.result && <p>Статус: {state.result.status}. Excel: {state.result.file ?? "не создан"}.<br />{state.result.journaled ? "Результат записан в журнал серии." : "Результат в журнал серии не записан."}</p>}
    </Panel>
  </section>;
}
