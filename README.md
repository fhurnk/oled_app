# OLED Measurement App

Версия: `v1.8.0-alpha`

Приложение для ведения серий измерений OLED-пикселей: ВАЯХ/IVL, спектры, стабильность, журнал серии и экспорт данных в Excel.

## Быстрый Запуск

Новый модульный GUI:

```powershell
.\env\Scripts\python.exe .\oled_modular_app.py
```

Референсное приложение остается доступным для сравнения поведения:

```powershell
.\env\Scripts\python.exe .\oled_measurement_app_v2_5.py
```

Проверка нового входа без открытия GUI:

```powershell
.\env\Scripts\python.exe .\oled_modular_app.py --status
```

Если окружение нужно создать заново:

```powershell
py -3 -m venv env
.\env\Scripts\python.exe -m pip install -r requirements.txt
```

## Камера Canon (alpha)

В стартовом окне и в меню открытой серии доступна кнопка `Камера (alpha)`. Она открывает независимое тестовое окно для Canon, подключённой к Raspberry Pi:

- подключение к сервису и проверка `gphoto2`;
- LiveView без VNC Viewer;
- сохранение текущего preview-кадра;
- полноразмерное фото;
- запись LiveView-потока в MP4;
- скачивание файлов на основной компьютер.

На этом этапе камера не запускает измерения и не привязывает файлы к пикселям. Сервис Raspberry Pi, пример `systemd` и инструкция установки находятся в `raspberry_camera_service/`.

## Что Должно Попасть В Репозиторий

В GitHub-репозитории `OLED_meas` должны храниться исходники, конфиги-примеры и документация. Папка с результатами измерений не коммитится:

- `OLED_series/` исключена через `.gitignore`;
- виртуальные окружения `env/` и `.venv/` исключены;
- локальные настройки `oled_app_settings.json` исключены, потому что там могут быть пути конкретного компьютера.

## Основные Файлы

- `oled_measurement_app_v2_5.py` - оригинальное рабочее приложение, оставлено как референс.
- `oled_modular_app.py` - новый основной вход модульного приложения.
- `oled_app/` - новый пакет модульного приложения: константы, настройки, утилиты, серии, hardware-слой, отчеты, измерения и GUI без правки референса.
- `oled_app/camera/client.py` - HTTP-клиент сервиса камеры Raspberry Pi.
- `oled_app/gui/camera_window.py` - независимое тестовое окно LiveView, фото и видео.
- `raspberry_camera_service/` - сервис Canon/gPhoto2/FFmpeg для Raspberry Pi, конфигурация и инструкция.
- `requirements.txt` - зависимости Python.
- `README.md` - краткое описание проекта.
- `PROJECT_OVERVIEW.md` - структура и логика работы.
- `CODEX_PROJECT.md` - краткая карта проекта для будущих чатов Codex.
- `docs/project_manifest.json` - структурированные сведения о версии, файлах и release-workflow.
- `docs/modularization_plan.md` - предрелизный план разбиения монолита на модули.
- `docs/raw_csv_measurement_pipeline_plan.md` - план перехода измерений на raw CSV во время съемки и XLSX после обработки.
- `docs/camera_quality_research.md` - исследование выбора качества фото и видео по возможностям подключённой камеры; без реализации в API.
- `CHANGELOG.md` - история обновлений.
- `AGENTS.md` - инструкции для будущих чатов Codex по версиям, журналу и релизам.
- `scripts/create_github_release.py` - создание GitHub Release из файла версии.
- `scripts/build_report_origin_workbook.py` - совместимый CLI-вход подготовки Origin-отчета по серии измерений.
- `oled_app/reports/origin_report.py` - модульная логика подготовки Origin-отчета.
- `oled_app/measurements/ivl.py` - модульная логика ВАЯХ/IVL для нового приложения.
- `oled_app/measurements/raw_io.py` - общая запись raw CSV и политика хранения промежуточных файлов.
- `oled_app/processing/ivl_results.py`, `oled_app/processing/spectrum_results.py`, `oled_app/processing/stability_results.py` - сборка итоговых IVL, Spectrum и Stability `.xlsx` из raw CSV.
- `oled_app/measurements/spectrum.py` - модульная логика съёмки и обработки спектров для нового приложения.
- `oled_app/measurements/stability.py` - модульная логика измерения стабильности для нового приложения.
- `oled_app/gui/widgets.py`, `oled_app/gui/progress.py` - общие GUI helpers и progress windows для нового приложения.
- `oled_app/gui/app.py`, `oled_app/gui/start_screen.py`, `oled_app/gui/measurement_menu.py`, `oled_app/gui/settings_window.py`, `oled_app/gui/camera_window.py`, `oled_app/gui/ivl_window.py`, `oled_app/gui/spectrum_window.py`, `oled_app/gui/stability_window.py`, `oled_app/gui/report_window.py` - базовый класс нового GUI, основные окна и альфа-окно камеры.
- `docs/versions/` - архив описаний версий.
- `docs/GITHUB_RELEASE_WORKFLOW.md` - порядок публикации версий и GitHub Releases.

## Нумерация Версий

Стабильный формат: `vMAJOR.MIDDLE.MINOR`. Для экспериментальной сборки допускается суффикс prerelease, например `v1.8.0-alpha`.

- `MAJOR` - крупное изменение логики или структуры проекта.
- `MIDDLE` - функциональное обновление среднего масштаба.
- `MINOR` - небольшая правка или точечное добавление.

Текущая версия: `v1.8.0-alpha`. Все изменения текущего этапа интеграции камеры накапливаются в этой одной alpha-версии без суффиксов `.1`, `.2` и т. п.
