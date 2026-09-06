import { useEffect, useState } from "react";
import { fetchIvlState, preflightIvl, startIvl, stopIvl, type IvlState, type IvlPreflight } from "./api";
import { Button, Notice, Panel } from "./design-system/components";
import LivePocChart from "./LivePocChart";

const fields: [string, string][] = [
  ["sweep_start", "Начало, В"], ["sweep_end", "Конец, В"],
  ["sweep_increment", "Шаг, В"], ["sweep_time_per_point", "Выдержка, с"],
  ["current_limit_mA", "Лимит тока, мА"], ["pixel_area_mm2", "Площадь, мм²"],
  ["photodiode_threshold_uA", "Рабочий фототок, мкА"],
  ["working_confirmation_points", "Следующих точек WORKING"],
  ["opening_photodiode_threshold_uA", "Фототок открытия, мкА"],
  ["opening_confirmation_points", "Следующих точек открытия"]
];
const labels: Record<string, string> = {idle: "Ожидание", running: "Измерение",
  processing: "Обработка Excel", stop_requested: "Остановка", stopped: "Остановлено",
  completed: "Завершено", failed: "Ошибка"};

export default function IvlWorkspace() {
  const [state, setState] = useState<IvlState | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [preflight, setPreflight] = useState<IvlPreflight | null>(null);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let disposed = false;
    let timer = 0;
    async function poll() {
      try {
        const next = await fetchIvlState();
        if (!disposed) { setState(next); setConnected(true); }
      } catch {
        if (!disposed) setConnected(false);
      } finally {
        if (!disposed) timer = window.setTimeout(poll, 500);
      }
    }
    void poll();
    void preflightIvl({}).then((initial) => {
      if (!disposed) setValues(Object.fromEntries(Object.entries(initial.params).map(([k, v]) => [k, String(v)])));
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; window.clearTimeout(timer); };
  }, []);

  async function action(kind: "check" | "start" | "stop") {
    setBusy(true); setError("");
    try {
      if (kind === "stop") setState(await stopIvl());
      else if (kind === "start" && preflight) { setState(await startIvl(preflight.params)); setPreflight(null); }
      else {
        setPreflight(null);
        if (Object.values(values).some((value) => !value.trim() || !Number.isFinite(Number(value)))) throw new Error("Заполните все поля числами.");
        setPreflight(await preflightIvl(Object.fromEntries(Object.entries(values).map(([k, v]) => [k, Number(v)]))));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  return <section className="ivl-workspace">
    <Notice title="Эмулятор · один цикл">SIM_IVL, без записи в журнал серии. Подтверждающие циклы пробоя, очередь и решения оператора будут перенесены отдельно.</Notice>
    {!connected && <Notice tone="warning" title="Соединение восстанавливается">Состояние операции будет получено с сервера. Измерение продолжает выполняться при уходе с экрана.</Notice>}
    {error && <Notice tone="danger" title="Не удалось выполнить действие">{error}</Notice>}
    <Panel>
      <h2>Параметры ВАЯХ</h2>
      <fieldset className="ivl-fields" disabled={busy || Boolean(state?.active)}>
        {fields.map(([key, label]) => <label key={key}>{label}<input type="number" step={key.endsWith("points") ? "1" : "any"} value={state?.active && state.params ? String(state.params[key]) : values[key] ?? ""} onChange={(event) => { setValues({...values, [key]: event.target.value}); setPreflight(null); }} /></label>)}
      </fieldset>
      <div className="ivl-actions">
        <Button disabled={busy || !connected || !Object.keys(values).length || state?.active} onClick={() => void action("check")}>Проверить параметры</Button>
        <Button variant="primary" disabled={busy || !connected || !preflight || state?.active} onClick={() => void action("start")}>Запустить цикл</Button>
        <Button variant="danger" disabled={busy || !connected || !state?.active || state.status === "processing"} onClick={() => void action("stop")}>Остановить</Button>
      </div>
      {preflight && <p>{preflight.note}<br />Папка результатов: {preflight.output_root}</p>}
    </Panel>
    <Panel>
      <h2>{labels[state?.status ?? "idle"]} · {state?.points.length ?? 0} точек</h2>
      {state?.params && state.active && <p>Активный цикл: {state.params.sweep_start}–{state.params.sweep_end} В, шаг {state.params.sweep_increment} В, лимит {state.params.current_limit_mA} мА.</p>}
      <LivePocChart points={state?.points ?? []} />
      <p>{state?.message}</p>
      {state?.error && <Notice tone="danger" title="Ошибка измерения">{state.error}</Notice>}
      {state?.safe_shutdown_confirmed === true && <p>Отключение выходов SMU подтверждено.</p>}
      {state?.safe_shutdown_confirmed === false && <Notice tone="danger" title="Отключение не подтверждено">Проверьте диагностику SMU.</Notice>}
      {state?.result && <p>Статус пикселя: {state.result.status}. Открытие: {state.result.opening_voltage?.toFixed(3) ?? "не определено"} В.{state.result.current_limit_reached && " Достигнут лимит тока."}<br />Excel: {state.result.file}</p>}
      {state?.raw_file && <p>Raw CSV: {state.raw_file}</p>}
    </Panel>
  </section>;
}
