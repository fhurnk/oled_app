import { useEffect, useState } from "react";
import { fetchIvlState, preflightIvl, startIvl, stopIvl, fetchSeriesState, decideIvlOpening, type ActiveSeries, type IvlTarget, type IvlState, type IvlPreflight } from "./api";
import { Button, Notice, Panel } from "./design-system/components";
import LivePocChart from "./LivePocChart";

const fields: [string, string][] = [
  ["sweep_start", "Начало, В"], ["sweep_end", "Конец, В"],
  ["sweep_increment", "Шаг, В"], ["sweep_time_per_point", "Выдержка, с"],
  ["current_limit_mA", "Лимит тока, мА"], ["pixel_area_mm2", "Площадь, мм²"],
  ["photodiode_threshold_uA", "Рабочий фототок, мкА"],
  ["working_confirmation_points", "Следующих точек WORKING"],
  ["opening_photodiode_threshold_uA", "Фототок открытия, мкА"],
  ["opening_confirmation_points", "Следующих точек открытия"],
  ["num_cycles", "Циклов"], ["delay_between_cycles", "Пауза между циклами, с"],
  ["burned_confirmation_cycles", "Подтверждений пробоя"],
  ["burnout_current_threshold_mA", "Порог пробоя, мА"]
];
const labels: Record<string, string> = {idle: "Ожидание", running: "Измерение",
  processing: "Обработка Excel", stop_requested: "Остановка", stopped: "Остановлено",
  completed: "Завершено", failed: "Ошибка", awaiting_opening: "Нужно напряжение открытия"};

export default function IvlWorkspace({initialTarget = null}: {initialTarget?: IvlTarget | null}) {
  const [target, setTarget] = useState<IvlTarget | null>(initialTarget);
  const [series, setSeries] = useState<ActiveSeries | null>(null);
  const [opening, setOpening] = useState("");
  const [state, setState] = useState<IvlState | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [preflight, setPreflight] = useState<IvlPreflight | null>(null);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let disposed = false;
    let timer = 0;
    let initialized = false;
    async function poll() {
      try {
        const next = await fetchIvlState();
        if (!disposed) {
          setState(next); setConnected(true);
          if (!initialized) {
            const initial = await preflightIvl({});
            if (disposed) return;
            setValues(Object.fromEntries(Object.entries(next.params ?? initial.params).map(([k, v]) => [k, String(v)])));
            if (next.active) setTarget(next.target ?? null);
            initialized = true;
          }
        }
      } catch {
        if (!disposed) setConnected(false);
      } finally {
        if (!disposed) timer = window.setTimeout(poll, 500);
      }
    }
    void poll();
    void fetchSeriesState().then((current) => { if (!disposed) setSeries(current.active); })
      .catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; window.clearTimeout(timer); };
  }, []);

  async function action(kind: "check" | "start" | "stop" | "retry" | "opening" | "skip_opening") {
    setBusy(true); setError("");
    try {
      if ((kind === "opening" || kind === "skip_opening") && state?.decision && state.run_id) {
        if (kind === "opening" && (!opening.trim() || !Number.isFinite(Number(opening)))) throw new Error("Введите напряжение открытия.");
        setState(await decideIvlOpening(state.run_id, state.decision.id, kind === "opening" ? Number(opening) : null));
        setOpening("");
      }
      else if (kind === "retry" && state?.params) {
        setTarget(state.target ?? null);
        setState(await startIvl({...state.params, target: state.target ?? null}));
        setPreflight(null);
      }
      else if (kind === "stop") setState(await stopIvl());
      else if (kind === "start" && preflight) { setState(await startIvl({...preflight.params, target: preflight.target})); setPreflight(null); }
      else {
        setPreflight(null);
        if (Object.values(values).some((value) => !value.trim() || !Number.isFinite(Number(value)))) throw new Error("Заполните все поля числами.");
        setPreflight(await preflightIvl({...Object.fromEntries(Object.entries(values).map(([k, v]) => [k, Number(v)])), target}));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  return <section className="ivl-workspace">
    <Notice title="ВАЯХ · эмулятор">Можно измерить выбранный пиксель тестовой серии или выполнить отдельный запуск SIM_IVL. Серийные результаты попадут в журнал с пометкой «ЭМУЛЯТОР v2». Реальные приборы не включаются.</Notice>
    {!connected && <Notice tone="warning" title="Соединение восстанавливается">Состояние операции будет получено с сервера. Измерение продолжает выполняться при уходе с экрана.</Notice>}
    {error && <Notice tone="danger" title="Не удалось выполнить действие">{error}</Notice>}
    <Panel>
      <h2>Параметры ВАЯХ</h2>
      <label>Пиксель <select disabled={busy || Boolean(state?.active)} value={target?.pixel_id ?? ""} onChange={(event) => {
        setTarget(event.target.value && series ? {series_path: series.path, pixel_id: event.target.value} : null); setPreflight(null);
      }}>
        <option value="">Отдельный запуск SIM_IVL</option>
        {series?.pixels.map((pixel) => <option key={pixel.pixel_id} value={pixel.pixel_id}>{pixel.pixel_id} · {pixel.status}</option>)}
      </select></label>
      {target && <p>Серия: {target.series_path}</p>}
      {!series && <p>Чтобы выбрать пиксель серии, откройте её в разделе «Серия».</p>}
      <fieldset className="ivl-fields" disabled={busy || Boolean(state?.active)}>
        {fields.map(([key, label]) => <label key={key}>{label}<input type="number" step={key.endsWith("points") ? "1" : "any"} value={state?.active && state.params ? String(state.params[key]) : values[key] ?? ""} onChange={(event) => { setValues({...values, [key]: event.target.value}); setPreflight(null); }} /></label>)}
      </fieldset>
      <div className="ivl-actions">
        <Button disabled={busy || !connected || !Object.keys(values).length || state?.active} onClick={() => void action("check")}>Проверить параметры</Button>
        <Button variant="primary" disabled={busy || !connected || !preflight || state?.active} onClick={() => void action("start")}>Начать ВАЯХ</Button>
        <Button variant="danger" disabled={busy || !connected || !state?.active || state.status === "processing"} onClick={() => void action("stop")}>Остановить</Button>
      </div>
      {preflight && <p>{preflight.note}<br />Папка результатов: {preflight.output_root}<br />Коэффициент светимости: {preflight.luminance_coefficient}. Спектральная калибровка: {preflight.spectral_calibration ? "применяется" : "нет"}.</p>}
    </Panel>
    {state?.decision && <Panel>
      <h2>Напряжение открытия · {state.pixel_id}</h2>
      <p>{state.decision.message} Выходы SMU отключены; значение сохраняется в журнале.</p>
      <label>Напряжение открытия, В <input type="number" step="any" min="0" max="10" value={opening} onChange={(event) => setOpening(event.target.value)} /></label>
      <div className="ivl-actions">
        <Button disabled={busy || !connected} variant="primary" onClick={() => void action("opening")}>Сохранить напряжение</Button>
        <Button disabled={busy || !connected} onClick={() => void action("skip_opening")}>Сохранить без значения</Button>
        <Button disabled={busy || !connected} variant="danger" onClick={() => void action("stop")}>Завершить без журнала</Button>
      </div>
    </Panel>}
    <Panel>
      <h2>{labels[state?.status ?? "idle"]} · {state?.pixel_id ?? "SIM_IVL"} · {state?.point_count ?? 0} точек</h2>
      <p>На графике цикл {state?.cycle ?? 1}. Всего завершённых циклов: {state?.result?.cycles ?? "—"}.</p>
      {state?.params && state.active && <p>Активный цикл: {state.params.sweep_start}–{state.params.sweep_end} В, шаг {state.params.sweep_increment} В, лимит {state.params.current_limit_mA} мА.</p>}
      <LivePocChart points={state?.points ?? []} />
      <p>{state?.message}</p>
      {state?.error && <Notice tone="danger" title="Ошибка измерения">{state.error}</Notice>}
      {state?.safe_shutdown_confirmed === true && <p>Отключение выходов SMU подтверждено.</p>}
      {state?.safe_shutdown_confirmed === false && <Notice tone="danger" title="Отключение не подтверждено">Проверьте диагностику SMU.</Notice>}
      {state?.result && <p>Статус пикселя: {state.result.status}. Открытие: {state.result.opening_voltage?.toFixed(3) ?? "не определено"} В.{state.result.current_limit_reached && " Достигнут лимит тока."}<br />Excel: {state.result.file}</p>}
      {state?.result && <p>{state.result.journaled ? "Результат записан в журнал серии." : "Результат в журнал не записан."}</p>}
      {state?.status === "completed" && state.result?.status === "NO_CONTACT" && <Notice tone="warning" title="Нет контакта">
        Проверьте контакт выбранной подложки. Повторный запуск измерит тот же пиксель и добавит новую запись.
        <Button disabled={busy || !connected} onClick={() => void action("retry")}>Повторить этот пиксель</Button>
      </Notice>}
      {state?.raw_file && <p>Raw CSV: {state.raw_file}</p>}
    </Panel>
  </section>;
}
