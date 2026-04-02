import asyncio
import os
import datetime
import json
from jinja2 import Template
import urllib.request

# 从 main.py 中复制最新的模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html style="width: 1200px;">
<head>
<style>
    * { box-sizing: border-box; -webkit-font-smoothing: antialiased; }
    body {
        font-family: 'Roboto', 'PingFang SC', sans-serif;
        margin: 0; padding: 30px;
        background: #F7F9FC;
        width: 1200px;
        display: inline-block;
    }
    .card {
        background: #FFFFFF;
        border-radius: 40px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.05);
        overflow: hidden;
        width: 1140px;
        border: 1px solid #E1E3E8;
    }
    .header {
        background: #F0F4F8;
        padding: 50px 40px;
        border-bottom: 1px solid #E1E3E8;
    }
    .header h1 { 
        margin: 0; font-size: 52px; color: #1B1B1F; font-weight: 600; 
        display: flex; align-items: center; gap: 24px;
    }
    .header .meta { 
        margin-top: 16px; font-size: 26px; color: #44474E; 
        font-family: 'Roboto Mono', monospace; line-height: 1.6;
    }
    
    table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
    }
    th {
        background: #FFFFFF;
        color: #44474E;
        font-size: 24px;
        font-weight: 700;
        padding: 30px 10px;
        border-bottom: 2px solid #E1E3E8;
        text-align: center;
    }
    td {
        padding: 12px 6px;
        border-bottom: 1px solid #F0F2F5;
        text-align: center;
        height: 90px;
    }
    
    .date-col {
        background: #F7F9FC;
        font-size: 44px;
        font-weight: 800;
        color: #005AC1;
        border-right: 1px solid #E1E3E8;
    }
    .time-col {
        font-size: 32px;
        font-weight: 600;
        color: #1B1B1F;
    }
    
    .box {
        position: relative;
        width: 90%;
        height: 64px;
        background: #F0F2F5;
        margin: 0 auto;
        border-radius: 12px;
        overflow: hidden;
        display: flex;
        align-items: center;
        justify-content: center;
        border: 1px solid #E1E3E8;
    }
    .fill {
        position: absolute;
        left: 0; top: 0; bottom: 0;
        z-index: 1;
    }
    .val {
        position: relative;
        z-index: 2;
        font-size: 30px;
        font-weight: 800;
        font-family: 'Roboto Mono', monospace;
    }
    
    .on-light { color: #1B1B1F; }
    .on-dark { color: #FFFFFF; text-shadow: 0 2px 4px rgba(0,0,0,0.3); }
    
    .footer {
        padding: 40px;
        text-align: center;
        font-size: 24px;
        color: #76777A;
        background: #F7F9FC;
        border-top: 1px solid #E1E3E8;
    }
</style>
</head>
<body>
    <div class="card">
        <div class="header">
            <h1><span>🔭</span> 晴天钟预报</h1>
            <div class="meta">
                LOC: {{ lat }}, {{ lon }}<br>
                REF: {{ ref_time }} | ECMWF IFS 0.25°
            </div>
        </div>
        <table>
            <thead>
                <tr>
                    <th style="width: 120px;">DAY</th>
                    <th style="width: 100px;">HR</th>
                    <th>TOTAL</th>
                    <th>LOW</th>
                    <th>MID</th>
                    <th>HIGH</th>
                </tr>
            </thead>
            <tbody>
                {% for row in rows %}
                <tr>
                    {% if row.is_first_of_day %}
                    <td class="date-col" rowspan="{{ row.day_rowspan }}">{{ row.day }}</td>
                    {% endif %}
                    <td class="time-col">{{ row.hour }}</td>
                    
                    <td>
                        <div class="box">
                            <div class="fill" style="width: {{ row.total }}%; background: {{ row.total_color }};"></div>
                            <span class="val {{ row.total_text_cls }}">{{ row.total }}</span>
                        </div>
                    </td>
                    <td>
                        <div class="box">
                            <div class="fill" style="width: {{ row.low }}%; background: {{ row.low_color }};"></div>
                            <span class="val {{ row.low_text_cls }}">{{ row.low }}</span>
                        </div>
                    </td>
                    <td>
                        <div class="box">
                            <div class="fill" style="width: {{ row.mid }}%; background: {{ row.mid_color }};"></div>
                            <span class="val {{ row.mid_text_cls }}">{{ row.mid }}</span>
                        </div>
                    </td>
                    <td>
                        <div class="box">
                            <div class="fill" style="width: {{ row.high }}%; background: {{ row.high_color }};"></div>
                            <span class="val {{ row.high_text_cls }}">{{ row.high }}</span>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <div class="footer">
            Generated by AstroAssist • Material Design 3
        </div>
    </div>
</body>
</html>
"""

def run_render_test():
    lat, lon = 39.9, 116.4
    
    print("正在请求 API 获取真实数据...")
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high&models=ecmwf_ifs025&forecast_days=1&timezone=auto"
    
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode())
    
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    c_total = hourly.get("cloud_cover", [])
    c_low = hourly.get("cloud_cover_low", [])
    c_mid = hourly.get("cloud_cover_mid", [])
    c_high = hourly.get("cloud_cover_high", [])

    def get_m3_color(val):
        if val <= 20: return "#C4E7CB", "on-light"
        if val <= 50: return "#A8C7FF", "on-light"
        if val <= 80: return "#FFDAD6", "on-light"
        return "#BA1A1A", "on-dark"

    all_rows = []
    day_counts = {}
    for i in range(len(times)):
        dt = datetime.datetime.fromisoformat(times[i])
        day = dt.strftime("%d")
        day_counts[day] = day_counts.get(day, 0) + 1
        t_color, t_cls = get_m3_color(c_total[i])
        l_color, l_cls = get_m3_color(c_low[i])
        m_color, m_cls = get_m3_color(c_mid[i])
        h_color, h_cls = get_m3_color(c_high[i])
        
        all_rows.append({
            "day": day, "hour": dt.strftime("%H"),
            "total": c_total[i], "total_color": t_color, "total_text_cls": t_cls,
            "low": c_low[i], "low_color": l_color, "low_text_cls": l_cls,
            "mid": c_mid[i], "mid_color": m_color, "mid_text_cls": m_cls,
            "high": c_high[i], "high_color": h_color, "high_text_cls": h_cls,
            "is_first_of_day": False
        })
    
    seen_days = set()
    for row in all_rows:
        if row["day"] not in seen_days:
            row["is_first_of_day"] = True
            row["day_rowspan"] = day_counts[row["day"]]
            seen_days.add(row["day"])

    render_data = {
        "lat": lat, "lon": lon, 
        "ref_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rows": all_rows
    }

    tmpl = Template(HTML_TEMPLATE)
    html_content = tmpl.render(**render_data)
    
    with open("test_render_result.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("✅ HTML 源码已保存至 test_render_result.html")
    print(f"请手动打开 {os.path.abspath('test_render_result.html')} 确认样式。")

if __name__ == "__main__":
    run_render_test()
