import { useCallback, useEffect, useState } from "react";

import {
  type RecalculationOperation,
  type RecalculationOptions,
  fetchRecalculationState,
  startLuminanceRecalculation,
  startSpectralCalibration
} from "./api";
import { Button, Notice, Panel, SelectField, TextField } from "./design-system/components";

type GroupChoice = {
  enabled: boolean;
  pixel_id: string;
  strategy: "replace" | "reuse";
};

const emptyOperation: RecalculationOperation = {
  status: "idle", active: false, operation: null, started_at: null,
  finished_at: null, error: null, result: null, series_path: null,
  progress: { completed: 0, total: 0, current: "" }
};

export default function RecalculationWorkspace() {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [options, setOptions] = useState<RecalculationOptions | null>(null);
  const [operation, setOperation] = useState<RecalculationOperation>(emptyOperation);
  const [choices, setChoices] = useState<Record<string, GroupChoice>>({});
  const [medianTolerance, setMedianTolerance] = useState(10);
  const [linearThreshold, setLinearThreshold] = useState(50);
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  const applyState = useCallback((state: Awaited<ReturnType<typeof fetchRecalculationState>>) => {
    setAvailable(state.available);
    setOperation(state.operation);
    if (state.options) {
      setOptions(state.options);
      setMedianTolerance(state.options.thresholds.median_tolerance_percent);
      setLinearThreshold(state.options.thresholds.linear_model_outlier_percent);
      setChoices((current) => Object.fromEntries(state.options!.groups.map((group) => {
        const existing = current[group.key];
        const candidate = group.candidates.some((item) => item.pixel_id === existing?.pixel_id)
          ? existing.pixel_id : (group.candidates[0]?.pixel_id ?? "");
        return [group.key, {
          enabled: existing?.enabled ?? Boolean(candidate),
          pixel_id: candidate,
          strategy: existing?.strategy === "reuse" && group.stored_calibration
            ? "reuse" : "replace"
        }];
      })));
    }
  }, []);

  const load = useCallback(async (signal?: AbortSignal) => {
    setError("");
    try {
      applyState(await fetchRecalculationState(signal));
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить пересчёты.");
    } finally {
      setBusy(false);
    }
  }, [applyState]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!operation.active) return;
    const timer = window.setInterval(() => {
      void fetchRecalculationState().then((state) => {
        applyState(state);
        if (!state.operation.active) setReplaceConfirmed(false);
      }).catch(() => undefined);
    }, 650);
    return () => window.clearInterval(timer);
  }, [operation.active, applyState]);

  const runCalibration = async () => {
    const selections = Object.fromEntries(Object.entries(choices)
      .filter(([, choice]) => choice.enabled && choice.pixel_id)
      .map(([key, choice]) => [key, { pixel_id: choice.pixel_id, strategy: choice.strategy }]));
    setBusy(true); setError("");
    try {
      const state = await startSpectralCalibration({
        selections,
        thresholds: {
          median_tolerance_percent: medianTolerance,
          linear_model_outlier_percent: linearThreshold
        }
      });
      setOperation(state.operation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось запустить калибровку.");
    } finally {
      setBusy(false);
    }
  };

  const runLuminance = async () => {
    if (!replaceConfirmed) return;
    setBusy(true); setError("");
    try {
      const state = await startLuminanceRecalculation();
      setOperation(state.operation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось запустить пересчёт светимости.");
    } finally {
      setBusy(false);
    }
  };

  if (available === null) return <Notice title="Загружаем пересчёты" tone="progress">Проверяем спектры и книги активной серии…</Notice>;
  if (!available) return <Notice title="Серия не открыта" tone="warning">Откройте серию в разделе «Серия», затем вернитесь к пересчётам.</Notice>;
  if (!options) return <Notice title="Данные недоступны" tone="warning">Не удалось прочитать параметры активной серии.</Notice>;

  const progress = operation.progress;
  const spectralResult = operation.operation === "spectral_calibration" ? operation.result as { completed?: number } | null : null;
  const luminanceResult = operation.operation === "luminance" ? operation.result as {
    workbooks_updated?: number; raw_files_updated?: number; raw_files_restored?: number;
    thumbnails_created?: number; errors?: number;
  } | null : null;
  const selectedCount = Object.values(choices).filter((choice) => choice.enabled && choice.pixel_id).length;

  return <div className="recalculation-workspace">
    {error && <Notice title="Проверьте параметры" tone="danger">{error}</Notice>}
    {operation.status === "running" && <Notice title="Пересчёт выполняется" tone="progress">
      {progress.current || "Подготовка"} · {progress.completed} из {progress.total}. Можно перейти в другой раздел.
    </Notice>}
    {operation.status === "failed" && <Notice title="Пересчёт завершился с ошибкой" tone="danger">{operation.error}</Notice>}
    {operation.status === "completed" && spectralResult && <Notice title="Спектральная калибровка завершена" tone="success">
      Областей обработано: {spectralResult.completed ?? 0}. Исходные книги спектров не изменялись.
    </Notice>}
    {operation.status === "completed" && luminanceResult && <Notice title="Светимость пересчитана" tone={luminanceResult.errors ? "warning" : "success"}>
      XLSX: {luminanceResult.workbooks_updated ?? 0}; raw CSV обновлено: {luminanceResult.raw_files_updated ?? 0}; восстановлено: {luminanceResult.raw_files_restored ?? 0}; миниатюр: {luminanceResult.thumbnails_created ?? 0}; ошибок: {luminanceResult.errors ?? 0}.
    </Notice>}

    <Panel className="recalculation-panel">
      <header><p className="panel__eyebrow">CIE / BPW34</p><h2>Спектральная калибровка областей</h2></header>
      <p className="recalculation-help">Выберите один сохранённый спектр на каждую область. Результат записывается отдельной книгой <code>SPECTRAL_RECALC_*.xlsx</code>; исходный спектр остаётся без изменений.</p>
      <div className="recalculation-thresholds">
        <TextField label="Допуск интеграла от медианы, %" type="number" min="0.01" max="100" step="any" value={medianTolerance} onChange={(event) => setMedianTolerance(event.currentTarget.valueAsNumber)} />
        <TextField label="Порог линейной модели, %" type="number" min="0.01" max="100" step="any" value={linearThreshold} onChange={(event) => setLinearThreshold(event.currentTarget.valueAsNumber)} />
      </div>
      <div className="recalculation-groups">
        {options.groups.map((group) => {
          const choice = choices[group.key] ?? { enabled: false, pixel_id: "", strategy: "replace" as const };
          return <section key={group.key} className={!group.candidates.length ? "recalculation-group--empty" : ""}>
            <label className="recalculation-check"><input type="checkbox" checked={choice.enabled} disabled={!group.candidates.length || operation.active} onChange={(event) => setChoices((current) => ({ ...current, [group.key]: { ...choice, enabled: event.currentTarget.checked } }))} /><span><strong>{group.label}</strong><small>{group.candidates.length ? `Спектров: ${group.candidates.length}` : "Нет сохранённых спектров"}</small></span></label>
            <SelectField label="Пиксель-калибратор" value={choice.pixel_id} disabled={!choice.enabled || operation.active} onChange={(event) => setChoices((current) => ({ ...current, [group.key]: { ...choice, pixel_id: event.currentTarget.value } }))}>{group.candidates.map((item) => <option key={item.pixel_id} value={item.pixel_id}>{item.pixel_id} · четверть {item.quarter}</option>)}</SelectField>
            <SelectField label="Действие" value={choice.strategy} disabled={!choice.enabled || operation.active} onChange={(event) => setChoices((current) => ({ ...current, [group.key]: { ...choice, strategy: event.currentTarget.value as GroupChoice["strategy"] } }))}><option value="replace">Рассчитать и заменить калибровку</option>{group.stored_calibration && <option value="reuse">Применить сохранённую калибровку</option>}</SelectField>
            <p>{group.stored_calibration ? `Сохранено: ${group.stored_calibration.source_pixel || "источник не указан"}; коэффициент ${group.stored_calibration.coefficient ?? "—"}.` : "Сохранённой калибровки области пока нет."}</p>
          </section>;
        })}
      </div>
      <Button variant="primary" disabled={busy || operation.active || selectedCount === 0} onClick={() => void runCalibration()}>{operation.active ? "Выполняется…" : `Пересчитать выбранные (${selectedCount})`}</Button>
    </Panel>

    <Panel className="recalculation-panel recalculation-danger">
      <header><p className="panel__eyebrow">Пакетная операция</p><h2>Пересчёт светимости существующей серии</h2></header>
      <div className="recalculation-counts"><span>ВАЯХ<strong>{options.measurement_counts.IVL}</strong></span><span>Спектры<strong>{options.measurement_counts.SPECTRUM}</strong></span><span>Стабильность<strong>{options.measurement_counts.STABILITY}</strong></span><span>Всего книг<strong>{options.measurement_counts.total}</strong></span></div>
      <p className="recalculation-help">Расчётные XLSX будут атомарно заменены значениями по текущим коэффициентам. Отсутствующие raw CSV будут восстановлены из книг. Исходные измерительные точки не удаляются.</p>
      <label className="recalculation-confirm"><input type="checkbox" checked={replaceConfirmed} disabled={operation.active} onChange={(event) => setReplaceConfirmed(event.currentTarget.checked)} /><span>Я подтверждаю замену расчётных XLSX в активной серии</span></label>
      <Button variant="danger" disabled={busy || operation.active || !replaceConfirmed || options.measurement_counts.total === 0} onClick={() => void runLuminance()}>{operation.active ? "Выполняется…" : "Пересчитать светимость"}</Button>
    </Panel>
  </div>;
}
