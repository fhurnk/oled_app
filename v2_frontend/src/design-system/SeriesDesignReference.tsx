import { useEffect, useMemo, useState } from "react";

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
} from "./components";

type ReferenceRow = {
  pixel: string;
  quarter: string;
  substrate: string;
  ivl: string;
  spectrum: string;
  status: string;
  tone: StatusTone;
};

const referenceRows: ReferenceRow[] = [
  {
    pixel: "CR1_1_1",
    quarter: "CR1 · красный",
    substrate: "CR1_1",
    ivl: "31.07 · 10:42",
    spectrum: "31.07 · 10:58",
    status: "Готово",
    tone: "success"
  },
  {
    pixel: "CR1_1_2",
    quarter: "CR1 · красный",
    substrate: "CR1_1",
    ivl: "31.07 · 11:04",
    spectrum: "В очереди",
    status: "Выбран",
    tone: "info"
  },
  {
    pixel: "CG2_3_1",
    quarter: "CG2 · зелёный",
    substrate: "CG2_3",
    ivl: "Нет контакта",
    spectrum: "—",
    status: "Внимание",
    tone: "warning"
  },
  {
    pixel: "CB4_2_4",
    quarter: "CB4 · синий",
    substrate: "CB4_2",
    ivl: "Токовый предел",
    spectrum: "—",
    status: "Остановлен",
    tone: "danger"
  }
];

function ReferenceChart() {
  return (
    <div className="reference-chart" role="img" aria-label="Эталон оформления графика ВАЯХ">
      <div className="reference-chart__legend">
        <span><i className="reference-chart__oled" />Ток OLED</span>
        <span><i className="reference-chart__photo" />Фототок</span>
      </div>
      <svg viewBox="0 0 620 210" preserveAspectRatio="none" aria-hidden="true">
        <g className="reference-chart__grid">
          <path d="M40 20H600M40 65H600M40 110H600M40 155H600M40 200H600" />
          <path d="M40 20V200M180 20V200M320 20V200M460 20V200M600 20V200" />
        </g>
        <path className="reference-chart__line reference-chart__line--oled" d="M40 190 C160 188, 220 174, 300 145 S445 58, 600 28" />
        <path className="reference-chart__line reference-chart__line--photo" d="M40 194 C185 192, 250 183, 330 158 S480 93, 600 72" />
      </svg>
      <span className="reference-chart__axis reference-chart__axis--x">U, В</span>
      <span className="reference-chart__axis reference-chart__axis--y">I, мА</span>
    </div>
  );
}

export default function SeriesDesignReference() {
  const [query, setQuery] = useState("");
  const [quarter, setQuarter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [toastVisible, setToastVisible] = useState(false);

  useEffect(() => {
    if (!toastVisible) {
      return;
    }
    const timer = window.setTimeout(() => setToastVisible(false), 3200);
    return () => window.clearTimeout(timer);
  }, [toastVisible]);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return referenceRows.filter((row) => {
      const matchesQuery = !needle || `${row.pixel} ${row.substrate}`.toLowerCase().includes(needle);
      const matchesQuarter = quarter === "all" || row.quarter.startsWith(quarter);
      return matchesQuery && matchesQuarter;
    });
  }, [quarter, query]);

  return (
    <>
      <Notice title="Эталон Stage 3 — данные не изменяются" tone="info">
        Экран проверяет компоненты, плотность таблиц и операторские состояния. Создание и
        открытие настоящих серий подключается на Stage 4.
      </Notice>

      <section className="metrics-grid metrics-grid--series" aria-label="Сводка эталонной серии">
        <MetricCard eyebrow="Серия" value="OLED-RGB-042" note="визуальный эталон" tone="blue" />
        <MetricCard eyebrow="Подложки" value="16" note="4 физических четверти" />
        <MetricCard eyebrow="Измерено" value="37 / 64" note="58% пикселей" tone="green" />
        <MetricCard eyebrow="Очередь спектров" value="6" note="следующий CR1_1_2" />
      </section>

      <section className="series-reference-grid">
        <Panel className="series-reference-main">
          <div className="panel__header series-reference__header">
            <div>
              <p className="panel__eyebrow">Эталонный экран серии</p>
              <h2>Пиксели и измерения</h2>
              <p className="panel__subtitle">
                Рабочая плотность 38 px, подписи статусов и действия одного уровня.
              </p>
            </div>
            <div className="series-reference__actions">
              <Button onClick={() => setToastVisible(true)} variant="primary">
                Создать серию
              </Button>
              <Button onClick={() => setToastVisible(true)}>Открыть папку</Button>
              <Button onClick={() => setDialogOpen(true)} variant="danger">
                Пример stop-диалога
              </Button>
            </div>
          </div>

          <div className="series-reference__filters">
            <TextField
              label="Поиск пикселя"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Например, CR1_1_2"
              value={query}
            />
            <SelectField
              label="Физическая четверть"
              onChange={(event) => setQuarter(event.target.value)}
              value={quarter}
            >
              <option value="all">Все четверти</option>
              <option value="CR1">CR1 · красный</option>
              <option value="CG2">CG2 · зелёный</option>
              <option value="CB4">CB4 · синий</option>
            </SelectField>
            <div className="field">
              <span className="field__label">Состояния</span>
              <div className="series-reference__status-filter">
                <StatusBadge dot tone="success">Готово</StatusBadge>
                <StatusBadge dot tone="warning">Внимание</StatusBadge>
                <StatusBadge dot tone="danger">Стоп</StatusBadge>
              </div>
            </div>
          </div>

          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Пиксель</th>
                  <th>Четверть</th>
                  <th>Подложка</th>
                  <th>ВАЯХ</th>
                  <th>Спектр</th>
                  <th>Состояние</th>
                  <th aria-label="Действия" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.pixel}>
                    <td><strong>{row.pixel}</strong></td>
                    <td>{row.quarter}</td>
                    <td>{row.substrate}</td>
                    <td>{row.ivl}</td>
                    <td>{row.spectrum}</td>
                    <td><StatusBadge dot tone={row.tone}>{row.status}</StatusBadge></td>
                    <td><Button aria-label={`Действия ${row.pixel}`} compact variant="ghost">•••</Button></td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td className="data-table__empty" colSpan={7}>Пиксели не найдены.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <div className="series-reference-side">
          <Panel>
            <div className="panel__header">
              <div>
                <p className="panel__eyebrow">Графики</p>
                <h2>Единая визуальная шкала</h2>
              </div>
              <StatusBadge tone="progress">Live</StatusBadge>
            </div>
            <ReferenceChart />
          </Panel>

          <Panel>
            <div className="panel__header">
              <div>
                <p className="panel__eyebrow">Токены</p>
                <h2>Семантические состояния</h2>
              </div>
            </div>
            <div className="token-list">
              <span><i className="token-swatch token-swatch--primary" />Выбор</span>
              <span><i className="token-swatch token-swatch--success" />Готово</span>
              <span><i className="token-swatch token-swatch--warning" />Внимание</span>
              <span><i className="token-swatch token-swatch--danger" />Критично</span>
              <span><i className="token-swatch token-swatch--progress" />Выполняется</span>
            </div>
          </Panel>
        </div>
      </section>

      <Notice
        actions={<Button compact onClick={() => setToastVisible(true)}>Проверить уведомление</Button>}
        title="Неблокирующее действие"
        tone="success"
      >
        Успешное сохранение показывается уведомлением. Диалог оставлен только для решения,
        без которого измерение нельзя продолжить.
      </Notice>

      <Dialog
        eyebrow="Блокирующее решение"
        footer={
          <>
            <Button onClick={() => setDialogOpen(false)}>Продолжить измерение</Button>
            <Button onClick={() => setDialogOpen(false)} variant="danger">
              Остановить и поставить 0 В
            </Button>
          </>
        }
        onClose={() => setDialogOpen(false)}
        open={dialogOpen}
        title="Безопасно остановить измерение?"
      >
        <Notice title="Выходы SMU будут отключены" tone="warning">
          Backend сначала установит 0 В на обоих каналах и только после подтверждения
          завершит активную операцию.
        </Notice>
        <dl className="dialog__facts">
          <div><dt>Пиксель</dt><dd>CR1_1_2</dd></div>
          <div><dt>Текущий ток</dt><dd>2,841 мА</dd></div>
          <div><dt>Получено точек</dt><dd>24 из 60</dd></div>
        </dl>
      </Dialog>

      <Toast
        onClose={() => setToastVisible(false)}
        title="Компонент отработал"
        visible={toastVisible}
      >
        Это демонстрация: пользовательские данные и файлы не изменены.
      </Toast>
    </>
  );
}
