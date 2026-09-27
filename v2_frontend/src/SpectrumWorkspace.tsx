import { useEffect, useMemo, useState } from "react";
import {
  decideSpectrum, fetchSeriesState, fetchSpectrumState, preflightSpectrum, startSpectrum, stopSpectrum,
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
  completed: "Завершено", stopped: "Остановлено", failed: "Ошибка", processing: "Обработка",
  awaiting_next_pixel: "Ожидается установка пикселя", awaiting_no_contact: "Нужно решение по контакту",
  awaiting_rejected_data: "Нужно решение по данным", awaiting_replacement: "Нужно выбрать замену"
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
  const [mode, setMode] = useState<"single" | "substrate" | "priority">("single");
  const [queuedOnly, setQueuedOnly] = useState(false);
  const [series, setSeries] = useState<ActiveSeries | null>(null);
  const [useOpening, setUseOpening] = useState(true);
  const [state, setState] = useState<SpectrumState | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [ledType, setLedType] = useState("auto");
  const [preflight, setPreflight] = useState<SpectrumPreflight | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [replacement, setReplacement] = useState("");

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
            if (next.active) {
              setTarget(next.target ?? null); setUseOpening(next.use_opening_voltage ?? true);
              setMode(next.queue?.scope ?? "single"); setQueuedOnly(next.queue?.queued_only ?? false);
            }
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
    if (mode !== "single" && !target) throw new Error("Выберите стартовый пиксель очереди.");
    const queue = mode !== "single" && target ? {
      series_path: target.series_path, start_pixel: target.pixel_id,
      scope: mode, queued_only: mode === "priority" ? true : queuedOnly
    } : null;
    return {
      ...Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])),
      led_type: ledType, use_opening_voltage: useOpening,
      target: mode === "single" ? target : null, queue
    };
  }

  async function action(kind: "check" | "start" | "stop") {
    setBusy(true); setError("");
    try {
      if (kind === "stop") setState(await stopSpectrum());
      else if (kind === "start" && preflight) {
        const queue = preflight.queue ? {
          series_path: preflight.queue.series_path, start_pixel: preflight.queue.start_pixel,
          scope: preflight.queue.scope, queued_only: preflight.queue.queued_only
        } : null;
        setState(await startSpectrum({...preflight.params, target: preflight.target, queue, use_opening_voltage: preflight.use_opening_voltage}));
        setPreflight(null);
      } else {
        setPreflight(await preflightSpectrum(payload()));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  async function decision(actionName: string, pixelId?: string) {
    if (!state?.run_id || !state.decision) return;
    setBusy(true); setError("");
    try {
      setState(await decideSpectrum(state.run_id, state.decision.id, actionName, pixelId));
      setReplacement("");
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
      <label>Режим <select disabled={busy || Boolean(state?.active)} value={mode} onChange={(event) => {
        const next = event.target.value as "single" | "substrate" | "priority";
        setMode(next); setPreflight(null);
        const candidates = series?.pixels.filter((pixel) => pixel.last_ivl_file && pixel.opening_voltage_V != null && !pixel.last_spectrum_file && (next !== "priority" || pixel.spectrum_priority)) ?? [];
        if (next !== "single" && candidates.length && !candidates.some((pixel) => pixel.pixel_id === target?.pixel_id)) {
          setTarget({series_path: series!.path, pixel_id: candidates[0].pixel_id});
        }
      }}>
        <option value="single">Один пиксель / SIM_SPECTRUM</option>
        <option value="substrate">Подложка последовательно</option>
        <option value="priority">Очередь отмеченных серии</option>
      </select></label>
      <label>Пиксель <select disabled={busy || Boolean(state?.active)} value={target?.pixel_id ?? ""} onChange={(event) => {
        setTarget(event.target.value && series ? {series_path: series.path, pixel_id: event.target.value} : null); setPreflight(null);
      }}>
        {mode === "single" && <option value="">Отдельный запуск SIM_SPECTRUM</option>}
        {series?.pixels.filter((pixel) => mode === "single" || (pixel.last_ivl_file && pixel.opening_voltage_V != null && !pixel.last_spectrum_file && (mode !== "priority" || pixel.spectrum_priority) && (!queuedOnly || pixel.spectrum_priority))).map((pixel) => <option key={pixel.pixel_id} value={pixel.pixel_id}>{pixel.pixel_id} · {pixel.status}{pixel.opening_voltage_V ? ` · Vоткр ${pixel.opening_voltage_V}` : ""}</option>)}
      </select></label>
      {!series && <p>Чтобы выбрать реальный идентификатор пикселя, откройте тестовую серию в разделе «Серия».</p>}
      {target && <label className="ivl-checkbox"><input type="checkbox" disabled={busy || Boolean(state?.active)} checked={useOpening} onChange={(event) => { setUseOpening(event.target.checked); setPreflight(null); }} />Начинать с напряжения открытия из журнала</label>}
      {mode === "substrate" && <label className="ivl-checkbox"><input type="checkbox" disabled={busy || Boolean(state?.active)} checked={queuedOnly} onChange={(event) => { setQueuedOnly(event.target.checked); setPreflight(null); }} />Только отмеченные пиксели выбранной подложки</label>}
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
      {preflight && <p>{preflight.note}<br />Диапазон первой съёмки: {preflight.effective_voltage_start}–{preflight.params.voltage_end} В, {preflight.point_count} точек.{preflight.queue && <><br />Пикселей в очереди от выбранной позиции: {preflight.queue.total}.</>}<br />Папка результатов: {preflight.output_root}</p>}
    </Panel>
    {state?.decision?.kind === "next_pixel" && <Panel><h2>Следующий пиксель · {state.decision.pixel_id}</h2><p>{state.decision.message}</p><div className="ivl-actions"><Button variant="primary" disabled={busy || !connected} onClick={() => void decision("measure")}>Пиксель установлен — снять</Button><Button disabled={busy || !connected} onClick={() => void decision("skip")}>Пропустить</Button><Button variant="danger" disabled={busy || !connected} onClick={() => void decision("stop")}>Завершить очередь</Button></div></Panel>}
    {state?.decision?.kind === "no_contact" && <Panel><h2>Нет контакта · {state.decision.pixel_id}</h2><p>{state.decision.message} Выходы SMU отключены.</p><div className="ivl-actions"><Button variant="primary" disabled={busy || !connected} onClick={() => void decision("retry")}>Повторить после проверки</Button><Button disabled={busy || !connected} onClick={() => void decision("continue")}>Продолжить очередь</Button><Button variant="danger" disabled={busy || !connected} onClick={() => void decision("stop")}>Завершить очередь</Button></div></Panel>}
    {state?.decision?.kind === "rejected_data" && <Panel><h2>Электрическое ограничение · {state.decision.pixel_id}</h2><p>{state.decision.message} Статус: {state.decision.status}. Выходы SMU отключены.</p><div className="ivl-actions"><Button variant="primary" disabled={busy || !connected} onClick={() => void decision("keep")}>Сохранить диагностический XLSX</Button><Button disabled={busy || !connected} onClick={() => void decision("delete")}>Удалить частичные данные</Button><Button variant="danger" disabled={busy || !connected} onClick={() => void decision("stop")}>Завершить без журнала</Button></div></Panel>}
    {state?.decision?.kind === "replacement" && <Panel><h2>Замена пикселя · {state.decision.pixel_id}</h2><p>{state.decision.message}</p><label>Новый пиксель <select value={replacement} onChange={(event) => setReplacement(event.target.value)}><option value="">Выберите пиксель</option>{state.decision.replacement_pixels?.map((pixel) => <option value={pixel} key={pixel}>{pixel}</option>)}</select></label><div className="ivl-actions"><Button variant="primary" disabled={busy || !connected || !replacement} onClick={() => void decision("replace", replacement)}>Снять выбранную замену</Button><Button disabled={busy || !connected} onClick={() => void decision("continue")}>Без замены</Button><Button variant="danger" disabled={busy || !connected} onClick={() => void decision("stop")}>Завершить очередь</Button></div></Panel>}
    <Panel>
      <h2>{statusLabels[state?.status ?? "idle"] ?? state?.status} · {state?.pixel_id ?? "SIM_SPECTRUM"} · {state?.point_count ?? 0} точек</h2>
      {state?.optimization && <p>Подбор T_int: точка {state.optimization.point}, итерация {state.optimization.iteration}, {state.optimization.integration_time_s * 1000} мс · {state.optimization.status}</p>}
      {state?.queue && <div className="ivl-queue-progress"><strong>Очередь: {state.queue.completed} завершено · {state.queue.remaining} осталось · {state.queue.attempts} попыток</strong><span>Старт: {state.queue.start_pixel}. Пропущено оператором: {state.queue.skipped_pixels.length}.</span></div>}
      <SpectrumChart curve={curve} />
      <p>{state?.message}</p>
      {state?.error && <Notice tone="danger" title="Ошибка измерения">{state.error}</Notice>}
      {state?.safe_shutdown_confirmed === true && <p>Отключение выходов SMU подтверждено.</p>}
      {state?.safe_shutdown_confirmed === false && <Notice tone="danger" title="Отключение не подтверждено">Результат не записан в журнал.</Notice>}
      {state?.result && <p>Статус: {state.result.status}. Excel: {state.result.file ?? "не создан"}.<br />{state.result.journaled ? "Результат записан в журнал серии." : "Результат в журнал серии не записан."}</p>}
    </Panel>
  </section>;
}
