import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  type GridComponentOption,
  type LegendComponentOption,
  type TooltipComponentOption
} from "echarts/components";
import * as echarts from "echarts/core";
import type { ComposeOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { LineSeriesOption } from "echarts/charts";
import { useEffect, useRef } from "react";

import type { PocPoint } from "./api";
import { chartTheme } from "./design-system/chartTheme";

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

type ChartOption = ComposeOption<
  LineSeriesOption | GridComponentOption | LegendComponentOption | TooltipComponentOption
>;

function LivePocChart({ points }: { points: PocPoint[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    const chart = echarts.init(containerRef.current, undefined, { renderer: "canvas" });
    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(containerRef.current);
    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    const chart = echarts.getInstanceByDom(containerRef.current);
    if (!chart) {
      return;
    }
    const option: ChartOption = {
      animationDuration: 180,
      color: [chartTheme.oled, chartTheme.photodiode],
      grid: { top: 42, right: 58, bottom: 42, left: 58 },
      legend: {
        top: 4,
        right: 8,
        itemWidth: 14,
        itemHeight: 7,
        textStyle: { color: chartTheme.axis, fontSize: 10 }
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: chartTheme.tooltip,
        borderWidth: 0,
        textStyle: { color: chartTheme.tooltipText, fontSize: 11 }
      },
      xAxis: {
        type: "value",
        name: "U, В",
        nameTextStyle: { color: chartTheme.axis, fontSize: 10 },
        axisLabel: { color: chartTheme.axis, fontSize: 9 },
        splitLine: { lineStyle: { color: chartTheme.grid } }
      },
      yAxis: [
        {
          type: "value",
          name: "I OLED, мА",
          nameTextStyle: { color: chartTheme.oled, fontSize: 9 },
          axisLabel: { color: chartTheme.axis, fontSize: 9 },
          splitLine: { lineStyle: { color: chartTheme.grid } }
        },
        {
          type: "value",
          name: "I PD, мкА",
          nameTextStyle: { color: chartTheme.photodiode, fontSize: 9 },
          axisLabel: { color: chartTheme.axis, fontSize: 9 },
          splitLine: { show: false }
        }
      ],
      series: [
        {
          name: "Ток OLED",
          type: "line",
          showSymbol: points.length < 45,
          symbolSize: 5,
          smooth: 0.18,
          lineStyle: { width: 2 },
          data: points.map((point) => [point.voltage_measured_V, point.current_mA])
        },
        {
          name: "Фототок",
          type: "line",
          yAxisIndex: 1,
          showSymbol: points.length < 45,
          symbolSize: 5,
          smooth: 0.18,
          lineStyle: { width: 2 },
          data: points.map((point) => [point.voltage_measured_V, point.photodiode_uA])
        }
      ]
    };
    chart.setOption(option, { notMerge: true });
  }, [points]);

  return (
    <div className="poc-chart-wrap">
      <div className="poc-chart" ref={containerRef} role="img" aria-label="Live-график аппаратного PoC" />
      {points.length === 0 && (
        <div className="poc-chart__empty">
          <span>⌁</span>
          <strong>Ожидание потока</strong>
          <p>Запустите эмулятор, чтобы увидеть точки SMU и фотодиода.</p>
        </div>
      )}
    </div>
  );
}

export default LivePocChart;
