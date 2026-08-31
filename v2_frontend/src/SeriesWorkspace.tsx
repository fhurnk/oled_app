import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type ActiveSeries,
  type SeriesConfigInput,
  type SeriesPixel,
  type SeriesState,
  closeSeries,
  createSeries,
  fetchSeriesState,
  fetchSeriesThumbnail,
  openSeries,
  refreshSeries,
  setSeriesRoot,
  setSpectrumPriority,
  updateSeries
} from "./api";
import {
  Button,
  Dialog,
  MetricCard,
  Notice,
  Panel,
  SelectField,
  StatusBadge,
  type StatusTone,
  TextField,
  Toast
} from "./design-system/components";

type DialogMode = "create" | "edit" | null;

const pixelOrder = [1, 2, 4, 3];
const quarterOrder = [2, 1, 3, 4];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function emptyConfig(root: string): SeriesConfigInput {
  return {
    root,
    deposition_date: todayIso(),
    keyword: "",
    series_led_color: "red",
    description_scope: "quarter",
    half_orientation: "top_bottom",
    quarter_bases: { "1": "Q", "2": "Q", "3": "Q", "4": "Q" },
    quarter_descriptions: { "1": "", "2": "", "3": "", "4": "" }
  };
}

function editConfig(active: ActiveSeries): SeriesConfigInput {
  return {
    deposition_date: active.deposition_date,
    keyword: active.keyword,
    series_led_color:
      active.series_led_color === "green" || active.series_led_color === "blue" || active.series_led_color === "white"
        ? active.series_led_color
        : "red",
    description_scope: active.description_scope,
    half_orientation: active.half_orientation,
    quarter_bases: Object.fromEntries(
      active.quarters.map((quarter) => [String(quarter.number), quarter.base])
    ),
    quarter_descriptions: Object.fromEntries(
      active.quarters.map((quarter) => [String(quarter.number), quarter.description])
    )
  };
}

function descriptionGroups(
  scope: SeriesConfigInput["description_scope"],
  halfOrientation: SeriesConfigInput["half_orientation"]
): number[][] {
  if (scope === "half") {
    return halfOrientation === "left_right" ? [[2, 3], [1, 4]] : [[2, 1], [3, 4]];
  }
  if (scope === "substrate") {
    return [[1, 2, 3, 4]];
  }
  return quarterOrder.map((number) => [number]);
}

function synchronizeScopeFields(
  form: SeriesConfigInput,
  scope: SeriesConfigInput["description_scope"],
  halfOrientation: SeriesConfigInput["half_orientation"]
): SeriesConfigInput {
  const bases = { ...form.quarter_bases };
  const descriptions = { ...form.quarter_descriptions };
  descriptionGroups(scope, halfOrientation).forEach((group) => {
    const sharedBase = bases[String(group[0])] ?? "Q";
    const sharedDescription = descriptions[String(group[0])] ?? "";
    group.forEach((number) => {
      bases[String(number)] = sharedBase;
      descriptions[String(number)] = sharedDescription;
    });
  });
  return {
    ...form,
    description_scope: scope,
    half_orientation: halfOrientation,
    quarter_bases: bases,
    quarter_descriptions: descriptions
  };
}

function descriptionGroupLabel(
  group: number[],
  index: number,
  halfOrientation: SeriesConfigInput["half_orientation"]
): string {
  if (group.length === 1) {
    return `Четверть ${group[0]}`;
  }
  if (group.length === 2) {
    const name = halfOrientation === "left_right"
      ? (index === 0 ? "Левая" : "Правая")
      : (index === 0 ? "Верхняя" : "Нижняя");
    return `${name} половина (${group.join("+")})`;
  }
  return "Вся подложка (1–4)";
}

function statusPresentation(status: string): { label: string; tone: StatusTone } {
  const normalized = String(status || "UNKNOWN").toUpperCase();
  if (normalized === "WORKING") {
    return { label: "Рабочий", tone: "success" };
  }
  if (normalized === "NO_CONTACT") {
    return { label: "Нет контакта", tone: "warning" };
  }
  if (normalized === "NEEDS_REVIEW") {
    return { label: "Проверить", tone: "warning" };
  }
  if (["NONWORKING", "BURNED", "FAILED", "CURRENT_LIMIT", "CURRENT_LIMIT_STOP"].includes(normalized)) {
    return { label: normalized === "BURNED" ? "Пробой" : "Остановлен", tone: "danger" };
  }
  return { label: "Не измерен", tone: "neutral" };
}

function shortValue(value: string | number | null, suffix = ""): string {
  if (value === null || value === "") {
    return "—";
  }
  return `${value}${suffix}`;
}

function shortDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  return value.slice(0, 16).replace("T", " ");
}

function HolderMap({
  active,
  selectedPixel,
  onSelect
}: {
  active: ActiveSeries;
  selectedPixel: string;
  onSelect: (pixelId: string) => void;
}) {
  return (
    <div className="holder-map" aria-label="Карта подложкодержателя">
      {quarterOrder.map((number) => {
        const quarter = active.quarters.find((item) => item.number === number);
        if (!quarter) {
          return null;
        }
        return (
          <section className={`holder-quarter holder-quarter--q${number}`} key={number}>
            <div className="holder-quarter__head">
              <strong>{quarter.code}{number}</strong>
              <span>{quarter.description || quarter.led_color_label}</span>
            </div>
            <div className="holder-quarter__substrates">
              {[1, 2, 3].map((substrate) => {
                const pixels = active.pixels.filter(
                  (pixel) => pixel.quarter_number === number && pixel.substrate_number === substrate
                );
                return (
                  <div className="holder-substrate" key={substrate}>
                    <small>{quarter.code}{number}_{substrate}</small>
                    <div className="holder-substrate__pixels">
                      {pixelOrder.map((pixelNumber) => {
                        const pixel = pixels.find((item) => item.pixel_number === pixelNumber);
                        if (!pixel) {
                          return <span key={pixelNumber} />;
                        }
                        const status = statusPresentation(pixel.status);
                        return (
                          <button
                            aria-label={`${pixel.pixel_id}: ${status.label}`}
                            className={`holder-pixel holder-pixel--${status.tone} ${
                              selectedPixel === pixel.pixel_id ? "holder-pixel--selected" : ""
                            }`}
                            key={pixel.pixel_id}
                            onClick={() => onSelect(pixel.pixel_id)}
                            title={`${pixel.pixel_id} · ${status.label}`}
                            type="button"
                          >
                            {pixel.pixel_number}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
      <div className="holder-map__center">OLED</div>
    </div>
  );
}

export default function SeriesWorkspace({
  onSeriesChanged
}: {
  onSeriesChanged?: () => void;
}) {
  const [state, setState] = useState<SeriesState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [rootDraft, setRootDraft] = useState("");
  const [manualPath, setManualPath] = useState("");
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);
  const [form, setForm] = useState<SeriesConfigInput>(() => emptyConfig(""));
  const [query, setQuery] = useState("");
  const [quarterFilter, setQuarterFilter] = useState("all");
  const [selectedPixelId, setSelectedPixelId] = useState("");
  const [thumbnailUrl, setThumbnailUrl] = useState("");

  const applyState = useCallback((next: SeriesState) => {
    setState(next);
    setRootDraft(next.root);
    onSeriesChanged?.();
  }, [onSeriesChanged]);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      applyState(await fetchSeriesState(signal));
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setError(reason instanceof Error ? reason.message : "Не удалось загрузить серии.");
      }
    } finally {
      setLoading(false);
    }
  }, [applyState]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = window.setTimeout(() => setToast(""), 3600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const active = state?.active ?? null;

  useEffect(() => {
    if (!active?.pixels.length) {
      setSelectedPixelId("");
      return;
    }
    if (!active.pixels.some((pixel) => pixel.pixel_id === selectedPixelId)) {
      setSelectedPixelId(active.pixels[0].pixel_id);
    }
  }, [active, selectedPixelId]);

  const selectedPixel = useMemo(
    () => active?.pixels.find((pixel) => pixel.pixel_id === selectedPixelId) ?? null,
    [active, selectedPixelId]
  );

  useEffect(() => {
    setThumbnailUrl("");
    if (!selectedPixel?.thumbnail_available) {
      return;
    }
    let disposed = false;
    let objectUrl = "";
    void fetchSeriesThumbnail(selectedPixel.pixel_id)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (disposed) {
          URL.revokeObjectURL(objectUrl);
        } else {
          setThumbnailUrl(objectUrl);
        }
      })
      .catch(() => {
        if (!disposed) {
          setThumbnailUrl("");
        }
      });
    return () => {
      disposed = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [selectedPixel?.last_updated, selectedPixel?.pixel_id, selectedPixel?.thumbnail_available]);

  const runAction = useCallback(async (
    action: () => Promise<SeriesState>,
    successMessage: string
  ): Promise<boolean> => {
    setBusy(true);
    setError("");
    try {
      const next = await action();
      applyState(next);
      setToast(successMessage);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Операция с серией не выполнена.");
      return false;
    } finally {
      setBusy(false);
    }
  }, [applyState]);

  const filteredPixels = useMemo(() => {
    if (!active) {
      return [];
    }
    const needle = query.trim().toLowerCase();
    return active.pixels.filter((pixel) => {
      const matchesQuery = !needle || pixel.pixel_id.toLowerCase().includes(needle);
      const matchesQuarter = quarterFilter === "all" || String(pixel.quarter_number) === quarterFilter;
      return matchesQuery && matchesQuarter;
    });
  }, [active, quarterFilter, query]);

  const ivlDays = useMemo(() => {
    if (!active) {
      return [];
    }
    return Array.from(
      new Set(
        active.history
          .filter((item) => item.type.toUpperCase() === "IVL" && item.measurement_day)
          .map((item) => String(item.measurement_day))
      )
    ).sort().slice(-5);
  }, [active]);

  const ivlHistory = useMemo(() => {
    const result = new Map<string, string>();
    active?.history.forEach((item) => {
      if (item.type.toUpperCase() === "IVL" && item.measurement_day) {
        result.set(`${item.pixel_id}::${item.measurement_day}`, item.status);
      }
    });
    return result;
  }, [active]);

  const openCreateDialog = () => {
    setForm(emptyConfig(state?.root ?? rootDraft));
    setDialogMode("create");
    setError("");
  };

  const openEditDialog = () => {
    if (!active) {
      return;
    }
    setForm(editConfig(active));
    setDialogMode("edit");
    setError("");
  };

  const submitForm = async () => {
    const createMode = dialogMode === "create";
    const ok = await runAction(
      () => createMode ? createSeries(form) : updateSeries(form),
      createMode ? "Серия создана и открыта." : "Настройки серии сохранены."
    );
    if (ok) {
      setDialogMode(null);
    }
  };

  if (loading && state === null) {
    return <Notice title="Загружаем серии" tone="progress">Читаем корневую папку и состояние журнала.</Notice>;
  }

  return (
    <>
      {error && <Notice title="Операция не выполнена" tone="danger">{error}</Notice>}

      {!active ? (
        <div className="series-start">
          <Notice title="Stage 4 работает с настоящими сериями" tone="success">
            Создание и открытие используют совместимые series_config.json и series_journal.xlsx.
            Измерения и рабочее напряжение на этом экране не запускаются.
          </Notice>

          <Panel className="series-start__root">
            <div className="panel__header">
              <div>
                <p className="panel__eyebrow">Корневая папка</p>
                <h2>Серии OLED</h2>
                <p className="panel__subtitle">Укажите существующую папку или создайте серию в новом пути.</p>
              </div>
              <Button disabled={busy} onClick={openCreateDialog} variant="primary">Создать серию</Button>
            </div>
            <div className="series-path-row">
              <TextField
                label="Путь к корню серий"
                onChange={(event) => setRootDraft(event.target.value)}
                value={rootDraft}
              />
              <Button disabled={busy || !rootDraft.trim()} onClick={() => void runAction(
                () => setSeriesRoot(rootDraft),
                "Список серий обновлён."
              )}>Показать серии</Button>
            </div>
            <div className="series-path-row series-path-row--manual">
              <TextField
                label="Открыть отдельную папку серии"
                onChange={(event) => setManualPath(event.target.value)}
                placeholder="Папка, где находится series_config.json"
                value={manualPath}
              />
              <Button disabled={busy || !manualPath.trim()} onClick={() => void runAction(
                () => openSeries(manualPath),
                "Серия открыта."
              )}>Открыть папку</Button>
            </div>
          </Panel>

          <Panel>
            <div className="panel__header">
              <div>
                <p className="panel__eyebrow">Найдено: {state?.recent.length ?? 0}</p>
                <h2>Последние серии</h2>
              </div>
              <Button compact disabled={busy} onClick={() => void load()}>Обновить</Button>
            </div>
            <div className="data-table-wrap series-list-table">
              <table className="data-table">
                <thead><tr><th>Дата напыления</th><th>Ключ</th><th>Создана</th><th>Измерений</th><th>Папка</th><th /></tr></thead>
                <tbody>
                  {state?.recent.map((series) => (
                    <tr key={series.path}>
                      <td><strong>{series.deposition_date || "—"}</strong></td>
                      <td>{series.keyword || "—"}</td>
                      <td>{shortDate(series.created_at)}</td>
                      <td>{series.measurements_count ?? "?"}</td>
                      <td className="data-table__path" title={series.path}>{series.folder_name}</td>
                      <td><Button compact disabled={busy} onClick={() => void runAction(
                        () => openSeries(series.path),
                        "Серия открыта."
                      )} variant="ghost">Открыть</Button></td>
                    </tr>
                  ))}
                  {!state?.recent.length && (
                    <tr><td className="data-table__empty" colSpan={6}>В выбранной папке серии не найдены.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      ) : (
        <div className="series-workspace">
          <Notice
            actions={<Button compact disabled={busy} onClick={() => void runAction(
              refreshSeries,
              "Данные серии и миниатюры обновлены."
            )}>Обновить данные</Button>}
            title="Рабочая серия открыта"
            tone="success"
          >
            {active.path}
          </Notice>

          <section className="metrics-grid metrics-grid--series" aria-label="Сводка серии">
            <MetricCard eyebrow="Серия" value={active.folder_name} note={active.keyword || "без кодового слова"} tone="blue" />
            <MetricCard eyebrow="Подложки" value={String(active.metrics.substrates)} note="4 четверти · 48 пикселей" />
            <MetricCard eyebrow="Измерено" value={`${active.metrics.measured} / ${active.metrics.pixels}`} note={`ВАЯХ: ${active.metrics.ivl} · спектры: ${active.metrics.spectra}`} tone="green" />
            <MetricCard eyebrow="Очередь спектров" value={String(active.metrics.spectrum_queue)} note={`Записей истории: ${active.metrics.history}`} />
          </section>

          <Panel className="series-toolbar">
            <div className="series-toolbar__identity">
              <p className="panel__eyebrow">Дата напыления {active.deposition_date}</p>
              <h2>Пиксели и измерения</h2>
            </div>
            <div className="series-toolbar__filters">
              <TextField label="Поиск пикселя" onChange={(event) => setQuery(event.target.value)} placeholder="AG1_1_1" value={query} />
              <SelectField label="Физическая четверть" onChange={(event) => setQuarterFilter(event.target.value)} value={quarterFilter}>
                <option value="all">Все четверти</option>
                {active.quarters.map((quarter) => <option key={quarter.number} value={quarter.number}>{quarter.code}{quarter.number} · {quarter.description || quarter.led_color_label}</option>)}
              </SelectField>
            </div>
            <div className="series-toolbar__actions">
              <Button compact disabled={busy} onClick={openEditDialog}>Настройки серии</Button>
              <Button compact disabled={busy} onClick={() => void runAction(closeSeries, "Серия закрыта.")}>Сменить серию</Button>
            </div>
          </Panel>

          <section className="series-stage4-grid">
            <Panel className="series-stage4-table">
              <div className="panel__header">
                <div><p className="panel__eyebrow">Показано: {filteredPixels.length}</p><h2>Таблица пикселей</h2></div>
                <StatusBadge tone={active.metrics.spectrum_queue ? "info" : "neutral"}>{active.metrics.spectrum_queue} в очереди</StatusBadge>
              </div>
              <div className="data-table-wrap">
                <table className="data-table data-table--pixels">
                  <thead><tr><th>Пиксель</th><th>Статус</th><th>V откр.</th><th>ВАЯХ</th><th>Max PD</th><th>Max I</th><th>Спектр / очередь</th><th>Пики</th><th>Стабильность</th></tr></thead>
                  <tbody>
                    {filteredPixels.map((pixel) => {
                      const status = statusPresentation(pixel.status);
                      return (
                        <tr className={selectedPixelId === pixel.pixel_id ? "data-table__row--selected" : ""} key={pixel.pixel_id} onClick={() => setSelectedPixelId(pixel.pixel_id)}>
                          <td><strong>{pixel.pixel_id}</strong></td>
                          <td><StatusBadge dot tone={status.tone}>{status.label}</StatusBadge></td>
                          <td>{shortValue(pixel.opening_voltage_V)}</td>
                          <td>{shortDate(pixel.last_ivl_date)}</td>
                          <td>{shortValue(pixel.last_ivl_max_photodiode_uA)}</td>
                          <td>{shortValue(pixel.last_ivl_max_current_mA)}</td>
                          <td>
                            {pixel.last_spectrum_file ? shortDate(pixel.last_spectrum_date) : (
                              <button
                                aria-label={pixel.spectrum_priority
                                  ? `Убрать ${pixel.pixel_id} из очереди спектров`
                                  : `Добавить ${pixel.pixel_id} в очередь спектров`}
                                className={`queue-toggle ${pixel.spectrum_priority ? "queue-toggle--checked" : ""}`}
                                disabled={busy}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void runAction(
                                    () => setSpectrumPriority(pixel.pixel_id, !pixel.spectrum_priority),
                                    pixel.spectrum_priority ? "Пиксель убран из очереди." : "Пиксель добавлен в очередь."
                                  );
                                }}
                                type="button"
                              >
                                {pixel.spectrum_priority ? "☑ В очереди" : "☐ Поставить"}
                              </button>
                            )}
                          </td>
                          <td>{shortValue(pixel.last_spectrum_peaks_nm)}</td>
                          <td>{shortDate(pixel.last_stability_date)}</td>
                        </tr>
                      );
                    })}
                    {!filteredPixels.length && <tr><td className="data-table__empty" colSpan={9}>Пиксели не найдены.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Panel>

            <div className="series-stage4-side">
              <Panel>
                <div className="panel__header"><div><p className="panel__eyebrow">Физический порядок</p><h2>Карта держателя</h2></div></div>
                <HolderMap active={active} onSelect={setSelectedPixelId} selectedPixel={selectedPixelId} />
                <div className="holder-legend">
                  <span><i className="holder-pixel--success" />Рабочий</span>
                  <span><i className="holder-pixel--warning" />Внимание</span>
                  <span><i className="holder-pixel--danger" />Стоп</span>
                  <span><i className="holder-pixel--neutral" />Не измерен</span>
                </div>
              </Panel>

              <Panel className="pixel-inspector">
                <div className="panel__header">
                  <div><p className="panel__eyebrow">Выбранный пиксель</p><h2>{selectedPixel?.pixel_id ?? "—"}</h2></div>
                  {selectedPixel && <StatusBadge tone={statusPresentation(selectedPixel.status).tone}>{statusPresentation(selectedPixel.status).label}</StatusBadge>}
                </div>
                {selectedPixel && (
                  <>
                    <div className="pixel-inspector__actions">
                      {!selectedPixel.last_spectrum_file && (
                        <>
                          <Button compact disabled={busy} onClick={() => void runAction(
                            () => setSpectrumPriority(selectedPixel.pixel_id, true, "substrate"),
                            "Подложка добавлена в очередь спектров."
                          )}>Отметить подложку</Button>
                          <Button compact disabled={busy} onClick={() => void runAction(
                            () => setSpectrumPriority(selectedPixel.pixel_id, false, "substrate"),
                            "Отметки подложки сняты."
                          )} variant="ghost">Снять подложку</Button>
                        </>
                      )}
                    </div>
                    <dl className="details-list details-list--pixel">
                      <div><dt>Напряжение открытия</dt><dd>{shortValue(selectedPixel.opening_voltage_V, " В")}</dd></div>
                      <div><dt>Последняя ВАЯХ</dt><dd>{shortDate(selectedPixel.last_ivl_date)}</dd></div>
                      <div><dt>Последний спектр</dt><dd>{shortDate(selectedPixel.last_spectrum_date)}</dd></div>
                      <div><dt>Стабильность</dt><dd>{shortDate(selectedPixel.last_stability_date)}</dd></div>
                    </dl>
                    <div className="pixel-thumbnail">
                      {thumbnailUrl ? <img alt={`Последняя ВАЯХ ${selectedPixel.pixel_id}`} src={thumbnailUrl} /> : <div><span>⌁</span><p>{selectedPixel.thumbnail_available ? "Загружаем миниатюру…" : "Миниатюры ВАЯХ пока нет"}</p></div>}
                    </div>
                  </>
                )}
              </Panel>
            </div>
          </section>

          <Panel className="series-history-panel">
            <div className="panel__header"><div><p className="panel__eyebrow">До пяти последних дат</p><h2>История ВАЯХ</h2></div><StatusBadge tone="neutral">{active.metrics.ivl} пикс.</StatusBadge></div>
            <div className="data-table-wrap series-history-table">
              <table className="data-table">
                <thead><tr><th>Пиксель</th>{ivlDays.map((day) => <th key={day}>{day}</th>)}</tr></thead>
                <tbody>
                  {ivlDays.length > 0 && filteredPixels.map((pixel) => (
                    <tr key={pixel.pixel_id}><td><strong>{pixel.pixel_id}</strong></td>{ivlDays.map((day) => {
                      const value = ivlHistory.get(`${pixel.pixel_id}::${day}`) ?? "";
                      const shown = value ? statusPresentation(value) : null;
                      return <td key={day}>{shown ? <StatusBadge tone={shown.tone}>{shown.label}</StatusBadge> : "—"}</td>;
                    })}</tr>
                  ))}
                  {!ivlDays.length && <tr><td className="data-table__empty" colSpan={1}>История ВАЯХ появится после первого измерения.</td></tr>}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      )}

      <Dialog
        eyebrow={dialogMode === "create" ? "Новая серия" : "Совместимое редактирование"}
        footer={<><Button disabled={busy} onClick={() => setDialogMode(null)}>Отмена</Button><Button disabled={busy} onClick={() => void submitForm()} variant="primary">{dialogMode === "create" ? "Создать и открыть" : "Сохранить"}</Button></>}
        onClose={() => !busy && setDialogMode(null)}
        open={dialogMode !== null}
        title={dialogMode === "create" ? "Создание серии OLED" : "Настройки открытой серии"}
      >
        <Notice title="Используется существующий формат данных" tone="info">
          Будут записаны series_config.json и совместимый series_journal.xlsx. Измерительные файлы не изменяются.
        </Notice>
        <div className="series-form-grid">
          {dialogMode === "create" && <TextField label="Корневая папка" onChange={(event) => setForm({ ...form, root: event.target.value })} value={form.root ?? ""} />}
          <TextField label="Дата напыления" onChange={(event) => setForm({ ...form, deposition_date: event.target.value })} type="date" value={form.deposition_date} />
          <TextField label="Кодовое слово" onChange={(event) => setForm({ ...form, keyword: event.target.value })} placeholder="Например, transport" value={form.keyword} />
          <SelectField label="Цвет светодиодов" onChange={(event) => setForm({ ...form, series_led_color: event.target.value as SeriesConfigInput["series_led_color"] })} value={form.series_led_color}>
            <option value="red">Красный (R)</option><option value="green">Зелёный (G)</option><option value="blue">Синий (B)</option><option value="white">Белый (W)</option>
          </SelectField>
          <SelectField
            label="Область серии"
            onChange={(event) => {
              const scope = event.target.value as SeriesConfigInput["description_scope"];
              setForm(synchronizeScopeFields(form, scope, form.half_orientation));
            }}
            value={form.description_scope}
          >
            <option value="quarter">Для каждой четверти</option>
            <option value="half">Для каждой половины</option>
            <option value="substrate">Для всей подложки</option>
          </SelectField>
          {form.description_scope === "half" && (
            <SelectField
              label="Расположение половин"
              onChange={(event) => {
                const halfOrientation = event.target.value as SeriesConfigInput["half_orientation"];
                setForm(synchronizeScopeFields(form, form.description_scope, halfOrientation));
              }}
              value={form.half_orientation}
            >
              <option value="top_bottom">Верх / низ</option>
              <option value="left_right">Лево / право</option>
            </SelectField>
          )}
        </div>
        <div className="series-quarter-form">
          {quarterOrder.map((number) => {
            const key = String(number);
            return (
              <fieldset key={number}>
                <legend>Четверть {number}</legend>
                <TextField
                  label="Короткая база"
                  maxLength={32}
                  onChange={(event) => {
                    const bases = { ...form.quarter_bases };
                    const group = descriptionGroups(form.description_scope, form.half_orientation)
                      .find((items) => items.includes(number)) ?? [number];
                    group.forEach((quarterNumber) => { bases[String(quarterNumber)] = event.target.value; });
                    setForm({ ...form, quarter_bases: bases });
                  }}
                  value={form.quarter_bases[key] ?? ""}
                />
              </fieldset>
            );
          })}
        </div>
        <div className="series-quarter-form">
          {descriptionGroups(form.description_scope, form.half_orientation).map((group, index) => {
            const key = String(group[0]);
            return (
              <fieldset key={group.join("-")}>
                <legend>{descriptionGroupLabel(group, index, form.half_orientation)}</legend>
                <TextField
                  label="Описание"
                  maxLength={180}
                  onChange={(event) => {
                    const descriptions = { ...form.quarter_descriptions };
                    group.forEach((number) => { descriptions[String(number)] = event.target.value; });
                    setForm({ ...form, quarter_descriptions: descriptions });
                  }}
                  value={form.quarter_descriptions[key] ?? ""}
                />
              </fieldset>
            );
          })}
        </div>
      </Dialog>

      <Toast onClose={() => setToast("")} title="Готово" visible={Boolean(toast)}>{toast}</Toast>
    </>
  );
}
