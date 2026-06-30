# Описание Проекта OLED Measurement App

Версия: `v1.7.2`

## Назначение

Приложение управляет сериями измерений OLED-пикселей: создаёт серию, ведёт Excel-журнал, снимает ВАЯХ/IVL, спектры и стабильность, сохраняет сырые и обработанные данные.

## Как Устроено Приложение

Проект начал переход от одного основного Python-файла к отдельному модульному каркасу:

- `tkinter/ttk` отвечает за графический интерфейс;
- `openpyxl` создаёт журналы и Excel-файлы измерений;
- `xtralien` подключается только при реальном измерении через SMU;
- `seabreeze` подключается только при съёмке спектров;
- симуляторный режим позволяет проверять интерфейс без оборудования.
- `oled_measurement_app_v2_5.py` оставлен нетронутым референсом рабочего приложения.
- новый вход модульного каркаса находится в `oled_modular_app.py`.
- общие константы, настройки и утилиты подготовлены в `oled_app/constants.py`, `oled_app/settings.py` и `oled_app/utils.py`.
- создание серии, Excel-журнал, пути измерений, статусы пикселей и геометрия карты подготовлены в `oled_app/series/`.
- проверка оборудования, авто-COM Ossila, встроенный симулятор, helpers SMU и поиск спектрометров подготовлены в `oled_app/hardware/`.
- подготовка Origin-отчета вынесена в `oled_app/reports/origin_report.py`, а прежний CLI-скрипт оставлен совместимой оболочкой.
- workflow ВАЯХ/IVL подготовлен в `oled_app/measurements/ivl.py` для будущего подключения нового GUI.
- workflow спектров подготовлен в `oled_app/measurements/spectrum.py` для будущего подключения нового GUI.
- workflow стабильности подготовлен в `oled_app/measurements/stability.py` для будущего подключения нового GUI.
- первые общие GUI helpers и progress windows подготовлены в `oled_app/gui/widgets.py` и `oled_app/gui/progress.py`.
- базовый класс нового GUI, стартовый экран, меню открытой серии и окно настроек подготовлены в `oled_app/gui/app.py`, `oled_app/gui/start_screen.py`, `oled_app/gui/measurement_menu.py` и `oled_app/gui/settings_window.py`.

## Структура Проекта

```text
oled_app_v2_5_package/
  oled_measurement_app_v2_5.py
  oled_modular_app.py
  oled_app/
    __init__.py
    main.py
    constants.py
    settings.py
    utils.py
    gui/
      app.py
      start_screen.py
      measurement_menu.py
      settings_window.py
      widgets.py
      progress.py
    series/
      manager.py
      journal.py
      paths.py
      statuses.py
      layout.py
    hardware/
      probe.py
      auto_com.py
      simulator.py
      ossila.py
      spectrometer.py
    measurements/
      ivl.py
      spectrum.py
      stability.py
    reports/
      origin_report.py
  requirements.txt
  oled_simulator_config.json
  README.md
  PROJECT_OVERVIEW.md
  CODEX_PROJECT.md
  AGENTS.md
  CHANGELOG.md
  scripts/
    create_github_release.py
    build_report_origin_workbook.py
  docs/
    GITHUB_RELEASE_WORKFLOW.md
    report_origin_pipeline.md
    modularization_plan.md
    project_manifest.json
    versions/
      v1.5.2.md
      v1.5.3.md
      v1.5.4.md
      v1.5.5.md
      v1.5.6.md
      v1.6.0.md
      v1.7.0.md
      v1.7.1.md
      v1.7.2.md
  OLED_series/              # локальные результаты измерений, не хранить в git
```

## Структура Данных Измерений

Новые измерения сохраняются по типу измерения, дате, четверти, подложке и пикселю:

```text
OLED_series/
  <series>/
    series_config.json
    series_journal.xlsx
    measurements/
      01_IVL_VAH/YYYY-MM-DD/CR1/CR1_1/CR1_1_1/
      02_SPECTRA/YYYY-MM-DD/CR1/CR1_1/CR1_1_1/
      03_STABILITY/YYYY-MM-DD/CR1/CR1_1/CR1_1_1/
```

## Основной Рабочий Процесс

1. Создать или открыть серию.
2. Задать названия четвертей, например `CR`.
3. Снять ВАЯХ одного пикселя или всей серии.
4. Для рабочего пикселя подтвердить напряжение открытия.
5. Снять спектры от напряжения открытия или от вручную заданного стартового напряжения.
6. При необходимости снять стабильность.
7. Составить Origin-отчет по серии через кнопку `Составить отчет`, выбрав спектральные пиксели и сетку напряжений.

## Учёт Площади И Светимости

В настройках приложения задаются:

- площадь пикселя в `мм^2`;
- коэффициент перевода фототока `мкА -> кд/м^2`.

По этим значениям приложение считает:

- плотность тока `mA/cm^2`;
- светимость `cd/m^2`.

Live-график ВАЯХ можно переключать прямо во время измерения:

- `I / ФД` - ток OLED и фототок;
- `J / L` - плотность тока и светимость.

## Обработка Спектров

Основной обработанный спектр считается из сырого спектра:

1. Берётся `raw` сигнал спектрометра.
2. В raw-спектре ищется короткий наиболее плоский участок.
3. Считается среднее значение фона на этом участке.
4. Это среднее значение вычитается из всего raw-спектра.

Dark-corrected данные сохраняются только как диагностический лист и не используются для основного обработанного спектра.

## Excel-Листы Спектров

- `Спектры` - `raw - средний фон`.
- `Processed counts per s` - обработанный спектр, делённый на время интегрирования.
- `Raw spectra` - сырые counts.
- `Dark corrected` - диагностический dark-corrected сигнал.
- `Baseline` - константный уровень фона.
- `Сводка` - параметры каждой точки.
- `Описание полей` - пояснения к листам.

## GitHub

Целевой репозиторий: `OLED_meas`.

В репозитории должны быть:

- исходный код;
- зависимости;
- документация;
- changelog;
- архив версий в `docs/versions/`;
- конфигурация симулятора как пример.

Папка `OLED_series/` не должна попадать в GitHub.
