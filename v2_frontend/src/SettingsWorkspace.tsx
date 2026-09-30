import { useCallback, useEffect, useMemo, useState } from "react";

import { type SettingsDocument, fetchSettings, saveSettings } from "./api";
import { Button, Notice, Panel, SelectField, TextField } from "./design-system/components";

type Tab = "general" | "simulator" | "camera" | "ivl" | "spectrum" | "stability";
type Section = "measurement_units" | "spectral_calibration" | "camera" | "ivl_advanced" | "spectrum_advanced" | "stability_advanced";

const tabs: Array<[Tab, string]> = [
  ["general", "Общие"], ["simulator", "Эмулятор"], ["camera", "Камера"],
  ["ivl", "ВАЯХ"], ["spectrum", "Спектры"], ["stability", "Стабильность"]
];

const ivlFields = [
  ["photodiode_bias_V", "Смещение фотодиода, В"], ["photodiode_range", "Диапазон фотодиода"],
  ["photodiode_threshold_uA", "Порог рабочего фототока, мкА"], ["working_confirmation_points", "Точек подтверждения WORKING"],
  ["opening_photodiode_threshold_uA", "Порог открытия, мкА"], ["opening_confirmation_points", "Точек подтверждения открытия"],
  ["burnout_current_threshold_mA", "Ток пробоя, мА"], ["no_contact_max_led_current_mA", "Макс. ток без контакта, мА"],
  ["burned_confirmation_cycles", "Доп. циклов после BURNED"]
] as const;

const spectrumFields = [
  ["photodiode_bias_V", "Смещение фотодиода, В"], ["photodiode_range", "Диапазон фотодиода"],
  ["target_intensity", "Целевая интенсивность"], ["intensity_min", "Мин. интенсивность"],
  ["intensity_max", "Макс. интенсивность"], ["saturation_level", "Насыщение"],
  ["min_peak_width_nm", "Мин. ширина пика, нм"], ["t_int_initial_s", "Начальное T_int, с"],
  ["t_int_min_s", "Мин. T_int, с"], ["t_int_max_s", "Макс. T_int, с"],
  ["kp", "Kp подбора T_int"], ["ki", "Ki подбора T_int"],
  ["max_iterations", "Макс. итераций"], ["tolerance", "Допуск подбора"],
  ["settle_time_voltage_s", "Пауза после напряжения, с"], ["settle_time_spectrum_s", "Пауза спектрометра, с"],
  ["dark_spectrum_scans", "Число dark-сканов"]
] as const;

const spectrumChecks = [
  ["reuse_previous_integration_time", "Начинать следующую точку с предыдущего T_int"],
  ["discard_first_scan_after_tint_change", "Сбрасывать первый спектр после смены T_int"],
  ["dark_spectrum_enabled", "Снимать dark spectrum"],
  ["baseline_correction_enabled", "Вычитать средний фон"],
  ["peak_detection_enabled", "Искать пики производными"]
] as const;

const clone = (value: SettingsDocument): SettingsDocument => JSON.parse(JSON.stringify(value)) as SettingsDocument;

export default function SettingsWorkspace({ onSaved }: { onSaved: () => void }) {
  const [tab, setTab] = useState<Tab>("general");
  const [saved, setSaved] = useState<SettingsDocument | null>(null);
  const [draft, setDraft] = useState<SettingsDocument | null>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    setBusy(true);
    setError("");
    try {
      const state = await fetchSettings(signal);
      setSaved(clone(state.settings));
      setDraft(clone(state.settings));
      setPath(state.path);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) {
        setError(reason instanceof Error ? reason.message : "Не удалось загрузить настройки.");
      }
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const dirty = useMemo(() => Boolean(saved && draft && JSON.stringify(saved) !== JSON.stringify(draft)), [draft, saved]);

  const updateTop = <K extends keyof SettingsDocument>(key: K, value: SettingsDocument[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
    setMessage("");
  };

  const updateNested = (section: Section, key: string, value: string | number | boolean) => {
    setDraft((current) => current ? ({
      ...current,
      [section]: { ...(current[section] as Record<string, unknown>), [key]: value }
    } as SettingsDocument) : current);
    setMessage("");
  };

  const numberField = (section: Section, key: string, label: string, step = "any") => (
    <TextField key={key} label={label} type="number" step={step}
      value={String((draft?.[section] as Record<string, unknown> | undefined)?.[key] ?? "")}
      onChange={(event) => updateNested(section, key, event.currentTarget.valueAsNumber)} />
  );

  const check = (section: Section, key: string, label: string) => (
    <label className="settings-check" key={key}>
      <input checked={Boolean((draft?.[section] as Record<string, unknown> | undefined)?.[key])} type="checkbox"
        onChange={(event) => updateNested(section, key, event.currentTarget.checked)} />
      <span>{label}</span>
    </label>
  );

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const state = await saveSettings(draft);
      setSaved(clone(state.settings));
      setDraft(clone(state.settings));
      setPath(state.path);
      setMessage("Настройки сохранены и будут использованы следующими операциями.");
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить настройки.");
    } finally {
      setBusy(false);
    }
  };

  if (!draft) {
    return <Notice title={error ? "Настройки недоступны" : "Загружаем настройки"} tone={error ? "danger" : "progress"}>{error || "Читаем локальный файл приложения…"}</Notice>;
  }

  return <div className="settings-workspace">
    <Panel className="settings-shell">
      <div className="settings-tabs" role="tablist" aria-label="Разделы настроек">
        {tabs.map(([key, label]) => <button className={tab === key ? "settings-tab settings-tab--active" : "settings-tab"} key={key} onClick={() => setTab(key)} role="tab" type="button">{label}</button>)}
      </div>

      <div className="settings-content">
        {error && <Notice title="Настройки не сохранены" tone="danger">{error}</Notice>}
        {message && <Notice title="Готово" tone="success">{message}</Notice>}

        {tab === "general" && <>
          <header><p className="panel__eyebrow">Приложение и расчёты</p><h2>Общие настройки</h2></header>
          <div className="settings-grid settings-grid--wide">
            <TextField label="Корневая папка серий" value={draft.default_root} onChange={(event) => updateTop("default_root", event.currentTarget.value)} />
            <SelectField label="Режим оборудования" value={draft.hardware_mode} onChange={(event) => updateTop("hardware_mode", event.currentTarget.value as SettingsDocument["hardware_mode"])}><option value="simulator">Эмулятор</option><option value="real">Реальное оборудование</option></SelectField>
            <TextField label="COM-порт" value={draft.com_port} onChange={(event) => updateTop("com_port", event.currentTarget.value)} />
            <SelectField label="Сырые CSV" value={draft.raw_data.policy} onChange={(event) => updateTop("raw_data", { ...draft.raw_data, policy: event.currentTarget.value as SettingsDocument["raw_data"]["policy"] })}><option value="keep_separate">Сохранять отдельно</option><option value="delete_after_xlsx">Удалять после XLSX</option></SelectField>
          </div>
          <label className="settings-check"><input checked={draft.auto_com_port} type="checkbox" onChange={(event) => updateTop("auto_com_port", event.currentTarget.checked)} /><span>Автоматически определять COM-порт Ossila</span></label>
          <h3>Единицы и коэффициенты</h3>
          <div className="settings-grid">
            {numberField("measurement_units", "pixel_area_mm2", "Площадь пикселя, мм²")}
            {numberField("measurement_units", "luminance_red_cd_m2_per_uA", "Коэффициент яркости R")}
            {numberField("measurement_units", "luminance_green_cd_m2_per_uA", "Коэффициент яркости G")}
            {numberField("measurement_units", "luminance_blue_cd_m2_per_uA", "Коэффициент яркости B")}
            {numberField("measurement_units", "luminance_white_cd_m2_per_uA", "Коэффициент яркости W")}
            {numberField("measurement_units", "geometric_conversion_coefficient", "Геометрический коэффициент")}
            {numberField("measurement_units", "integral_conversion_coefficient", "Интегральный коэффициент")}
            {numberField("spectral_calibration", "median_tolerance_percent", "Допуск медианы, %")}
            {numberField("spectral_calibration", "linear_model_outlier_percent", "Порог выброса модели, %")}
          </div>
        </>}

        {tab === "simulator" && <>
          <header><p className="panel__eyebrow">Встроенное оборудование</p><h2>Эмулятор</h2></header>
          <TextField label="JSON-конфиг пикселей" value={draft.simulator_config_path} onChange={(event) => updateTop("simulator_config_path", event.currentTarget.value)} />
          <p className="settings-help">Файл задаёт режимы пикселей, напряжение открытия, токи, спектральные пики и деградацию.</p>
        </>}

        {tab === "camera" && <>
          <header><p className="panel__eyebrow">Raspberry Pi и Canon</p><h2>Камера</h2></header>
          <div className="settings-grid">
            <TextField label="IP-адрес или имя Raspberry Pi" value={draft.camera.host} onChange={(event) => updateNested("camera", "host", event.currentTarget.value)} />
            {numberField("camera", "port", "Порт", "1")}{numberField("camera", "request_timeout_s", "Тайм-аут запросов, с")}{numberField("camera", "stream_timeout_s", "Тайм-аут LiveView, с")}
            <TextField label="Wi-Fi-профиль Windows" value={draft.camera.wifi_profile} onChange={(event) => updateNested("camera", "wifi_profile", event.currentTarget.value)} />
            <TextField label="Wi-Fi-адаптер" value={draft.camera.wifi_interface} onChange={(event) => updateNested("camera", "wifi_interface", event.currentTarget.value)} />
            {numberField("camera", "wifi_connect_timeout_s", "Тайм-аут Wi-Fi, с")}
            <TextField label="Папка скачивания" value={draft.camera.download_dir} onChange={(event) => updateNested("camera", "download_dir", event.currentTarget.value)} />
            {numberField("camera", "crop_width_percent", "Ширина кадрирования, %")}{numberField("camera", "crop_height_percent", "Высота кадрирования, %")}
          </div>
          <div className="settings-checks">
            {check("camera", "auto_connect_wifi", "Подключаться к Wi-Fi автоматически")}{check("camera", "restore_previous_wifi", "Возвращать прежнюю Wi-Fi-сеть")}
            {check("camera", "keep_remote_files_after_download", "Оставлять файлы на Raspberry Pi после скачивания")}{check("camera", "combine_stability_telemetry_video", "Создавать видео стабильности с телеметрией")}
          </div>
        </>}

        {tab === "ivl" && <><header><p className="panel__eyebrow">Расширенные параметры</p><h2>ВАЯХ</h2></header><div className="settings-grid">{ivlFields.map(([key, label]) => numberField("ivl_advanced", key, label))}</div><div className="settings-checks">{check("ivl_advanced", "mark_current_limit_as_burnout", "Считать достижение ограничения тока пробоем")}</div></>}

        {tab === "spectrum" && <>
          <header><p className="panel__eyebrow">Расширенные параметры</p><h2>Спектры</h2></header>
          <div className="settings-grid">{spectrumFields.map(([key, label]) => numberField("spectrum_advanced", key, label))}<SelectField label="Область поиска пика" value={String(draft.spectrum_advanced.peak_search_mode_for_tint)} onChange={(event) => updateNested("spectrum_advanced", "peak_search_mode_for_tint", event.currentTarget.value)}><option value="auto">Автоматически</option><option value="visible">Видимый диапазон</option><option value="all">Весь диапазон</option></SelectField></div>
          <div className="settings-checks">{spectrumChecks.map(([key, label]) => check("spectrum_advanced", key, label))}</div>
        </>}

        {tab === "stability" && <><header><p className="panel__eyebrow">Расширенные параметры</p><h2>Стабильность</h2></header><div className="settings-grid">
          {numberField("stability_advanced", "voltage_step_max", "Макс. шаг напряжения, В")}{numberField("stability_advanced", "current_control_kp", "Kp удержания тока, В/мА")}
          {numberField("stability_advanced", "photodiode_bias_V", "Смещение фотодиода, В")}{numberField("stability_advanced", "photodiode_threshold_uA", "Порог фототока, мкА")}{numberField("stability_advanced", "photodiode_range", "Диапазон фотодиода", "1")}
        </div></>}
      </div>

      <footer className="settings-footer">
        <div><strong>{dirty ? "Есть несохранённые изменения" : "Все изменения сохранены"}</strong><span>{path}</span></div>
        <div><Button disabled={busy || !dirty} onClick={() => { if (saved) setDraft(clone(saved)); setError(""); setMessage("Изменения отменены без записи в файл."); }}>Отменить изменения</Button><Button disabled={busy || !dirty} onClick={() => void save()} variant="primary">{busy ? "Сохраняем…" : "Сохранить"}</Button></div>
      </footer>
    </Panel>
  </div>;
}
