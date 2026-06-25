# OLED Measurement App v2.5

GUI-приложение для ведения серий OLED-измерений: создание серий, журнал Excel, ВАХ/ВАЯХ, спектры, стабильность и встроенный режим симулятора.

## Что добавлено для запуска в Visual Studio / VS Code

- `requirements.txt` со списком библиотек.
- `.vscode/launch.json` с готовой конфигурацией запуска `OLED Measurement App`.
- `oled_app_v2_5_package.pyproj` для открытия проекта в полной Visual Studio.
- В скрипте рабочая папка при старте фиксируется на папку проекта, поэтому конфиги и результаты не теряются при запуске из IDE.

## Установка на новом компьютере

Откройте папку проекта в Visual Studio Code:

```powershell
cd "C:\Users\fhurn\OneDrive\Desktop\НМСЭ\sim_app\oled_app_v2_5_package"
code .
```

Создайте виртуальное окружение и установите библиотеки:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Если PowerShell не разрешает активацию окружения, выполните один раз:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Запуск из Visual Studio Code

1. Установите расширение `Python` от Microsoft.
2. Нажмите `Ctrl+Shift+P`, выберите `Python: Select Interpreter`.
3. Выберите интерпретатор из `.venv`.
4. Откройте вкладку `Run and Debug`.
5. Выберите конфигурацию `OLED Measurement App`.
6. Нажмите `F5`.

## Запуск из Visual Studio

1. Установите компонент `Python development` в Visual Studio Installer.
2. Откройте файл `oled_app_v2_5_package.pyproj`.
3. В Python Environments выберите интерпретатор из `.venv`.
4. Убедитесь, что стартовый файл проекта: `oled_measurement_app_v2_5.py`.
5. Нажмите `F5`.

## Запуск из терминала

```powershell
.\.venv\Scripts\Activate.ps1
python .\oled_measurement_app_v2_5.py
```

## Симулятор и реальное оборудование

По умолчанию включен режим `simulator`, поэтому приложение можно открыть без подключенного оборудования.

Для реальных измерений в настройках приложения выберите режим `real`, укажите COM-порт и убедитесь, что установлены драйверы и Python-библиотеки для оборудования:

- `xtralien` для SMU;
- `seabreeze` для спектрометра Ocean Optics/SeaBreeze.

Если `xtralien` не устанавливается через `pip`, установите пакет или wheel-файл от поставщика прибора, затем повторите запуск.

## Структура файлов измерений

Каждая серия создается в своей отдельной папке. Новые файлы измерений сохраняются внутри этой серии по типу измерения, дате, четверти, подложке и пикселю:

```text
<папка серии>/
  measurements/
    01_IVL_VAH/
      YYYY-MM-DD/
        quarter_1_CR/
          substrate_2/
            CR1_2_3/
              IVL_CR1_2_3_....xlsx
    02_SPECTRA/
      YYYY-MM-DD/
        quarter_1_CR/
          substrate_2/
            CR1_2_3/
              SPECTRUM_CR1_2_3_....xlsx
    03_STABILITY/
      YYYY-MM-DD/
        quarter_1_CR/
          substrate_2/
            CR1_2_3/
              STABILITY_CR1_2_3_....xlsx
```

Пути в `series_journal.xlsx` записываются относительно папки серии.
