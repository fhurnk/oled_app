import { useEffect, useMemo, useRef, useState } from "react";

import {
  type ReportGeneration,
  type ReportGroup,
  type ReportInput,
  type ReportOptions,
  fetchReportState,
  previewReport,
  startReport
} from "./api";
import { Button, Notice, Panel, SelectField, TextField } from "./design-system/components";

type Selection = ReportInput["selection"];
type Grid = ReportInput["global_grid"];

const modeLabels = { full: "Полный отчёт", ivl: "Только ВАЯХ", spectra: "Только спектры" };
const emptyGeneration: ReportGeneration = {
  status: "idle", active: false, started_at: null, finished_at: null,
  error: null, result: null, series_path: null
};

function outputName(mode: ReportInput["mode"], ivlDate: string, spectrumDate: string, format: ReportInput["format"]) {
  const suffix = format === "origin" ? ".opju" : ".xlsx";
  if (mode === "ivl") return `report_IVL_${ivlDate}${suffix}`;
  if (mode === "spectra") return `report_Spctr_${spectrumDate}${suffix}`;
  const stem = ivlDate === spectrumDate ? ivlDate : `IVL_${ivlDate}_Spctr_${spectrumDate}`;
  return `report_${stem}${suffix}`;
}

function selectedPixel(groups: ReportGroup[], selection: Selection, groupKey: string) {
  const choice = selection[groupKey];
  if (!choice) return null;
  const group = groups.find((item) => item.key === groupKey);
  const substrate = group?.substrates.find((item) => item.name === choice.substrate);
  return substrate?.pixels.find((item) => item.pixel_id === choice.pixel_id) ?? null;
}

function defaultGrid(voltages: number[]): Grid {
  const values = [...new Set(voltages)].sort((a, b) => a - b);
  const diffs = values.slice(1).map((value, index) => value - values[index]).filter((value) => value > 0);
  return { start: values[0] ?? 0, stop: values.at(-1) ?? 0, step: diffs.length ? Math.min(...diffs) : 0.1 };
}

export default function ReportWorkspace() {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [options, setOptions] = useState<ReportOptions | null>(null);
  const [generation, setGeneration] = useState<ReportGeneration>(emptyGeneration);
  const [mode, setMode] = useState<ReportInput["mode"]>("full");
  const [grouping, setGrouping] = useState<ReportInput["grouping"]>("settings");
  const [ivlDate, setIvlDate] = useState("");
  const [spectrumDate, setSpectrumDate] = useState("");
  const [excluded, setExcluded] = useState<number[]>([]);
  const [format, setFormat] = useState<ReportInput["format"]>("xlsx");
  const [name, setName] = useState("");
  const [selection, setSelection] = useState<Selection>({});
  const [sameGrid, setSameGrid] = useState(true);
  const [globalGrid, setGlobalGrid] = useState<Grid>({ start: 0, stop: 0, step: 0.1 });
  const [pixelGrids, setPixelGrids] = useState<Record<string, Grid>>({});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const initialized = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetchReportState(controller.signal).then((state) => {
      setAvailable(state.available);
      setGeneration(state.generation);
      setOptions(state.options);
      if (state.options) {
        const nextMode = state.options.available_modes[0] ?? "full";
        const nextIvl = state.options.ivl_dates.at(-1) ?? "";
        const nextSpectrum = state.options.spectrum_dates.at(-1) ?? "";
        setMode(nextMode); setIvlDate(nextIvl); setSpectrumDate(nextSpectrum);
        setName(outputName(nextMode, nextIvl, nextSpectrum, "xlsx"));
      }
      initialized.current = true;
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "Не удалось загрузить отчёт."))
      .finally(() => setBusy(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!generation.active) return;
    const timer = window.setInterval(() => {
      void fetchReportState().then((state) => setGeneration(state.generation)).catch(() => undefined);
    }, 700);
    return () => window.clearInterval(timer);
  }, [generation.active]);

  useEffect(() => {
    if (!initialized.current || !available) return;
    const timer = window.setTimeout(() => {
      setBusy(true); setError("");
      void previewReport({ grouping, spectrum_date: spectrumDate, excluded_quarters: excluded })
        .then((state) => { setOptions(state.options); setGeneration(state.generation); })
        .catch((reason) => setError(reason instanceof Error ? reason.message : "Не удалось обновить выбор спектров."))
        .finally(() => setBusy(false));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [available, grouping, spectrumDate, excluded.join(",")]);

  useEffect(() => {
    if (!options) return;
    setSelection((current) => {
      const next: Selection = {};
      for (const group of options.groups) {
        const existing = current[group.key];
        const existingPixel = existing && selectedPixel(options.groups, current, group.key);
        if (existing && existingPixel) next[group.key] = existing;
        else {
          const substrate = group.substrates[0];
          const pixel = substrate?.pixels[0];
          if (substrate && pixel) next[group.key] = { substrate: substrate.name, pixel_id: pixel.pixel_id };
        }
      }
      return next;
    });
  }, [options]);

  const selectedPixels = useMemo(() => {
    if (!options) return [];
    return options.groups.map((group) => selectedPixel(options.groups, selection, group.key)).filter((item): item is NonNullable<typeof item> => Boolean(item));
  }, [options, selection]);

  useEffect(() => {
    if (!selectedPixels.length) return;
    const common = selectedPixels.reduce<number[] | null>((result, pixel) => {
      const values = pixel.voltages.map((value) => Number(value.toFixed(6)));
      return result === null ? values : result.filter((value) => values.includes(value));
    }, null) ?? [];
    if (common.length) setGlobalGrid(defaultGrid(common));
    setPixelGrids(Object.fromEntries(selectedPixels.map((pixel) => [pixel.pixel_id, defaultGrid(pixel.voltages)])));
  }, [JSON.stringify(selectedPixels)]);

  useEffect(() => {
    if (initialized.current) setName(outputName(mode, ivlDate, spectrumDate, format));
  }, [mode, ivlDate, spectrumDate, format]);

  const changeSubstrate = (group: ReportGroup, substrate: string) => {
    const pixel = group.substrates.find((item) => item.name === substrate)?.pixels[0];
    if (pixel) setSelection((current) => ({ ...current, [group.key]: { substrate, pixel_id: pixel.pixel_id } }));
  };

  const build = async () => {
    if (!options) return;
    setBusy(true); setError("");
    try {
      const state = await startReport({
        mode, grouping, ivl_date: ivlDate, spectrum_date: spectrumDate,
        excluded_quarters: excluded, format, output_name: name, selection,
        same_grid: sameGrid, global_grid: globalGrid, pixel_grids: pixelGrids
      });
      setGeneration(state.generation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось запустить отчёт.");
    } finally {
      setBusy(false);
    }
  };

  if (available === null) return <Notice title="Загружаем отчёт" tone="progress">Проверяем измерения активной серии…</Notice>;
  if (!available) return <Notice title="Серия не открыта" tone="warning">Откройте серию в разделе «Серия», затем вернитесь к отчётам.</Notice>;
  if (!options?.available_modes.length) return <Notice title="Нет данных для отчёта" tone="warning">В активной серии пока нет папок измерений ВАЯХ или спектров.</Notice>;

  const includesIvl = mode !== "spectra";
  const includesSpectra = mode !== "ivl";
  return <div className="report-workspace">
    {error && <Notice title="Проверьте параметры" tone="danger">{error}</Notice>}
    {generation.status === "running" && <Notice title="Отчёт создаётся" tone="progress">Можно перейти в другой раздел — операция продолжится в backend.</Notice>}
    {generation.status === "failed" && <Notice title="Ошибка отчёта" tone="danger">{generation.error}</Notice>}
    {generation.status === "completed" && generation.result && <Notice title="Отчёт создан" tone="success">{generation.result.output} · ВАЯХ: {generation.result.ivl_records}, спектры: {generation.result.spectrum_records}</Notice>}

    <Panel className="report-panel">
      <header><p className="panel__eyebrow">Состав и источник</p><h2>Параметры отчёта</h2></header>
      <div className="report-grid">
        <SelectField label="Состав" value={mode} onChange={(event) => setMode(event.currentTarget.value as ReportInput["mode"])}>{options.available_modes.map((item) => <option key={item} value={item}>{modeLabels[item]}</option>)}</SelectField>
        <SelectField label="Группировка" value={grouping} onChange={(event) => setGrouping(event.currentTarget.value as ReportInput["grouping"])}><option value="settings">По области серии</option><option value="quarters">Раздельно по четвертям</option></SelectField>
        {includesIvl && <SelectField label="Дата ВАЯХ" value={ivlDate} onChange={(event) => setIvlDate(event.currentTarget.value)}>{options.ivl_dates.map((date) => <option key={date}>{date}</option>)}</SelectField>}
        {includesSpectra && <SelectField label="Дата спектров" value={spectrumDate} onChange={(event) => setSpectrumDate(event.currentTarget.value)}>{options.spectrum_dates.map((date) => <option key={date}>{date}</option>)}</SelectField>}
      </div>
      <div className="report-quarters"><span>Исключить четверти:</span>{[1, 2, 3, 4].map((quarter) => <label key={quarter}><input checked={excluded.includes(quarter)} type="checkbox" onChange={(event) => setExcluded((current) => event.currentTarget.checked ? [...current, quarter].sort() : current.filter((item) => item !== quarter))} />{quarter}</label>)}</div>
    </Panel>

    {includesSpectra && <Panel className="report-panel">
      <header><p className="panel__eyebrow">Один спектр на группу</p><h2>Подложки и пиксели</h2></header>
      {!options.groups.length ? <Notice title="Спектры не найдены" tone="warning">Измените дату, группировку или исключённые четверти.</Notice> : <div className="report-selection">
        {options.groups.map((group) => {
          const choice = selection[group.key];
          const substrate = group.substrates.find((item) => item.name === choice?.substrate) ?? group.substrates[0];
          return <section key={group.key}><strong>{group.label}</strong><SelectField label="Подложка" value={choice?.substrate ?? ""} onChange={(event) => changeSubstrate(group, event.currentTarget.value)}>{group.substrates.map((item) => <option key={item.name}>{item.name}</option>)}</SelectField><SelectField label="Пиксель" value={choice?.pixel_id ?? ""} onChange={(event) => setSelection((current) => ({ ...current, [group.key]: { substrate: substrate.name, pixel_id: event.currentTarget.value } }))}>{substrate.pixels.map((pixel) => <option key={pixel.pixel_id}>{pixel.pixel_id}</option>)}</SelectField></section>;
        })}
      </div>}
      <label className="report-check"><input checked={sameGrid} type="checkbox" onChange={(event) => setSameGrid(event.currentTarget.checked)} /><span>Одинаковая сетка напряжений для всех выбранных спектров</span></label>
      {sameGrid ? <div className="report-voltage-grid"><TextField label="Начало, В" type="number" step="any" value={globalGrid.start} onChange={(event) => setGlobalGrid({ ...globalGrid, start: event.currentTarget.valueAsNumber })} /><TextField label="Конец, В" type="number" step="any" value={globalGrid.stop} onChange={(event) => setGlobalGrid({ ...globalGrid, stop: event.currentTarget.valueAsNumber })} /><TextField label="Шаг, В" type="number" step="any" value={globalGrid.step} onChange={(event) => setGlobalGrid({ ...globalGrid, step: event.currentTarget.valueAsNumber })} /></div> : <div className="report-individual-grids">{selectedPixels.map((pixel) => { const grid = pixelGrids[pixel.pixel_id] ?? defaultGrid(pixel.voltages); return <section key={pixel.pixel_id}><strong>{pixel.pixel_id}</strong><TextField label="Начало, В" type="number" step="any" value={grid.start} onChange={(event) => setPixelGrids((current) => ({ ...current, [pixel.pixel_id]: { ...grid, start: event.currentTarget.valueAsNumber } }))} /><TextField label="Конец, В" type="number" step="any" value={grid.stop} onChange={(event) => setPixelGrids((current) => ({ ...current, [pixel.pixel_id]: { ...grid, stop: event.currentTarget.valueAsNumber } }))} /><TextField label="Шаг, В" type="number" step="any" value={grid.step} onChange={(event) => setPixelGrids((current) => ({ ...current, [pixel.pixel_id]: { ...grid, step: event.currentTarget.valueAsNumber } }))} /></section>; })}</div>}
    </Panel>}

    <Panel className="report-panel report-output">
      <header><p className="panel__eyebrow">Результат</p><h2>Файл отчёта</h2></header>
      <div className="report-output__fields"><SelectField label="Формат" value={format} onChange={(event) => setFormat(event.currentTarget.value as ReportInput["format"])}><option value="xlsx">Диагностический Excel</option><option value="origin">Origin Project</option></SelectField><TextField label="Имя файла в папке серии" value={name} onChange={(event) => setName(event.currentTarget.value)} /></div>
      <p>{format === "origin" ? "Для OPJU требуется установленный OriginPro с доступной Python-интеграцией." : "XLSX включает данные, графики и спецификацию построения для Origin."}</p>
      <Button disabled={busy || generation.active || (includesSpectra && !options.groups.length)} onClick={() => void build()} variant="primary">{generation.active ? "Создаётся…" : "Составить отчёт"}</Button>
    </Panel>
  </div>;
}
