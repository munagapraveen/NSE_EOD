from datetime import date, datetime
from nicegui import ui


def create_candlestick_chart(
    dates: list[str],
    ohlc: list[list[float]],
    volumes: list[int],
    indicators: dict[str, list[float]] = None,
    corporate_actions: list[dict] = None
):
    """
    Create an interactive ECharts candlestick chart with volume bars.
    
    Args:
        dates: List of date strings ['2024-01-01', '2024-01-02', ...]
        ohlc: List of [open, close, low, high] corresponding to dates
        volumes: List of trade volumes
        indicators: Dict of line indicators (e.g. {"SMA 5": [values...]})
        corporate_actions: List of dicts (e.g. [{"date": date(2025,1,1), "label": "Split 2:1"}])
    """
    
    # 1. Price series (candlestick)
    series = [
        {
            "name": "Price",
            "type": "candlestick",
            "data": ohlc,
            "itemStyle": {
                "color": "#10b981",       # Bullish (emerald green)
                "color0": "#ef4444",      # Bearish (red)
                "borderColor": "#10b981",
                "borderColor0": "#ef4444",
            },
        }
    ]
    
    # 2. Add indicator line series
    if indicators:
        colors = ["#f59e0b", "#6366f1", "#22d3ee", "#f472b6", "#a78bfa"]
        for idx, (name, values) in enumerate(indicators.items()):
            series.append({
                "name": name,
                "type": "line",
                "data": [float(v) if v is not None else None for v in values],
                "smooth": True,
                "lineStyle": {"width": 1.5},
                "itemStyle": {"color": colors[idx % len(colors)]},
                "symbol": "none",
            })
            
    # 3. Add volume series (mapped to bottom xAxis/yAxis)
    volume_series = {
        "name": "Volume",
        "type": "bar",
        "data": [int(v) if v is not None else 0 for v in volumes],
        "xAxisIndex": 1,
        "yAxisIndex": 1,
        "itemStyle": {
            "color": "rgba(99, 102, 241, 0.4)" # Semi-transparent Indigo
        }
    }
    series.append(volume_series)
    
    # 4. Corporate Action markers (markLines on candlestick)
    mark_lines = []
    if corporate_actions:
        for ca in corporate_actions:
            dt = ca.get("date")
            dt_str = dt.strftime("%Y-%m-%d") if isinstance(dt, (date, datetime)) else str(dt)
            lbl = ca.get("label") or ca.get("description") or "Corporate Action"
            
            mark_lines.append({
                "xAxis": dt_str,
                "label": {
                    "formatter": lbl,
                    "fontSize": 9,
                    "color": "#94a3b8",
                    "position": "end"
                },
                "lineStyle": {
                    "type": "dashed",
                    "color": "#f59e0b",
                    "width": 1.2
                }
            })
            
    if mark_lines:
        series[0]["markLine"] = {
            "data": mark_lines,
            "symbol": "none"
        }
        
    chart_options = {
        "animation": True,
        "backgroundColor": "transparent",
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
        },
        "legend": {
            "data": [s["name"] for s in series if s["name"] != "Volume"],
            "textStyle": {"color": "#94a3b8"},
            "top": 0,
        },
        "grid": [
            {"left": "5%", "right": "3%", "top": "12%", "height": "58%"},   # Candlestick grid
            {"left": "5%", "right": "3%", "top": "76%", "height": "16%"},   # Volume grid
        ],
        "xAxis": [
            {"type": "category", "data": dates, "gridIndex": 0,
             "axisLabel": {"color": "#94a3b8"}},
            {"type": "category", "data": dates, "gridIndex": 1,
             "axisLabel": {"show": False}},
        ],
        "yAxis": [
            {"type": "value", "gridIndex": 0,
             "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.05)"}},
             "axisLabel": {"color": "#94a3b8"}},
            {"type": "value", "gridIndex": 1, "splitLine": {"show": False},
             "axisLabel": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 80, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "start": 80, "end": 100,
             "bottom": 5, "height": 18, "textStyle": {"color": "#94a3b8"}},
        ],
        "series": series,
    }
    
    ui.echart(chart_options).classes("w-full h-[520px]")
