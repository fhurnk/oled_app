# OLED Measurement App

Версия: `v1.7.3`

Приложение для ведения серий измерений OLED-пикселей: ВАЯХ/IVL, спектры, стабильность, журнал серии и экспорт данных в Excel.

## Быстрый Запуск

```powershell
.\env\Scripts\python.exe .\oled_measurement_app_v2_5.py
```

Новый модульный каркас запускается отдельно и не изменяет референсное приложение:

```powershell
.\env\Scripts\python.exe .\oled_modular_app.py
```

Если окружение нужно создать заново:

```powershell
py -3 -m venv env
.\env\Scripts\python.exe -m pip install -r requirements.txt
```

## Что Должно Попасть В Репозиторий

В GitHub-репозитории `OLED_meas` должны храниться исходники, конфиги-примеры и документация. Папка с результатами измерений не коммитится:

- `OLED_series/` исключена через `.gitignore`;
- виртуальные окружения `env/` и `.venv/` исключены;
- локальные настройки `oled_app_settings.json` исключены, потому что там могут быть пути конкретного компьютера.

## Основные Файлы

- `oled_measurement_app_v2_5.py` - оригинальное рабочее приложение, оставлено как референс.
- `oled_modular_app.py` - новый вход модульного приложения-каркаса.
- `oled_app/` - новый пакет для постепенного переноса констант, настроек, утилит, серии и последующих модулей без правки референса.
- `requirements.txt` - зависимости Python.
- `README.md` - краткое описание проекта.
- `PROJECT_OVERVIEW.md` - структура и логика работы.
- `CODEX_PROJECT.md` - краткая карта проекта для будущих чатов Codex.
- `docs/project_manifest.json` - структурированные сведения о версии, файлах и release-workflow.
- `docs/modularization_plan.md` - предрелизный план разбиения монолита на модули.
- `CHANGELOG.md` - история обновлений.
- `AGENTS.md` - инструкции для будущих чатов Codex по версиям, журналу и релизам.
- `scripts/create_github_release.py` - создание GitHub Release из файла версии.
- `scripts/build_report_origin_workbook.py` - подготовка Origin-отчета по серии измерений.
- `docs/versions/` - архив описаний версий.
- `docs/GITHUB_RELEASE_WORKFLOW.md` - порядок публикации версий и GitHub Releases.

## Нумерация Версий

Формат: `vMAJOR.MIDDLE.MINOR`.

- `MAJOR` - крупное изменение логики или структуры проекта.
- `MIDDLE` - функциональное обновление среднего масштаба.
- `MINOR` - небольшая правка или точечное добавление.

Текущая версия: `v1.7.3`.
