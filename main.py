from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
import httpx
import datetime

# Material Design 3 风格的高密度 HTML 模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html style="width: 500px;">
<head>
<meta name="viewport" content="width=500, initial-scale=1.0">
<style>
    * { box-sizing: border-box; -webkit-font-smoothing: antialiased; }
    body {
        font-family: 'Roboto', 'Segoe UI', 'PingFang SC', sans-serif;
        margin: 0; padding: 12px;
        background: #fdfcff;
        width: 500px;
    }
    .card {
        background: #ffffff;
        border-radius: 24px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1), 0 1px 2px rgba(0,0,0,0.06);
        overflow: hidden;
        border: 1px solid #e1e2ec;
        width: 476px;
    }
    .header {
        background: #f0f4ff;
        padding: 24px 20px;
        border-bottom: 1px solid #e1e2ec;
    }
    .header h1 { 
        margin: 0; font-size: 22px; color: #1a1c1e; font-weight: 500; 
        display: flex; align-items: center; gap: 8px;
    }
    .header .meta { 
        margin-top: 8px; font-size: 12px; color: #44474e; 
        font-family: 'Roboto Mono', monospace; line-height: 1.5;
    }
    
    table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
    }
    th {
        background: #ffffff;
        color: #44474e;
        font-size: 11px;
        font-weight: 500;
        padding: 12px 4px;
        border-bottom: 1px solid #e1e2ec;
        text-align: center;
    }
    td {
        padding: 6px 2px;
        border-bottom: 1px solid #f0f0f8;
        text-align: center;
        height: 38px;
    }
    
    /* M3 风格列 */
    .date-col {
        background: #fdfcff;
        font-size: 20px;
        font-weight: 700;
        color: #1a73e8;
        border-right: 1px solid #e1e2ec;
    }
    .time-col {
        font-size: 14px;
        font-weight: 500;
        color: #1a1c1e;
    }
    
    /* 容器化填充方框 (M3 Progress Indicator 变体) */
    .progress-box {
        position: relative;
        width: 88%;
        height: 28px;
        background: #f0f0f8;
        margin: 0 auto;
        border-radius: 8px;
        overflow: hidden;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .fill {
        position: absolute;
        left: 0; top: 0; bottom: 0;
        z-index: 1;
        transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .val-text {
        position: relative;
        z-index: 2;
        font-size: 13px;
        font-weight: 700;
        font-family: 'Roboto Mono', monospace;
    }
    
    /* M3 配色逻辑 */
    .on-light { color: #1a1c1e; }
    .on-dark { color: #ffffff; text-shadow: 0 1px 2px rgba(0,0,0,0.2); }
    
    .footer {
        padding: 16px;
        text-align: center;
        font-size: 11px;
        color: #74777f;
        background: #fdfcff;
        border-top: 1px solid #e1e2ec;
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
                    <th style="width: 55px;">DATE</th>
                    <th style="width: 45px;">HR</th>
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
                        <div class="progress-box">
                            <div class="fill" style="width: {{ row.total }}%; background: {{ row.total_color }};"></div>
                            <span class="val-text {{ row.total_text_cls }}">{{ row.total }}</span>
                        </div>
                    </td>
                    <td>
                        <div class="progress-box">
                            <div class="fill" style="width: {{ row.low }}%; background: {{ row.low_color }};"></div>
                            <span class="val-text {{ row.low_text_cls }}">{{ row.low }}</span>
                        </div>
                    </td>
                    <td>
                        <div class="progress-box">
                            <div class="fill" style="width: {{ row.mid }}%; background: {{ row.mid_color }};"></div>
                            <span class="val-text {{ row.mid_text_cls }}">{{ row.mid }}</span>
                        </div>
                    </td>
                    <td>
                        <div class="progress-box">
                            <div class="fill" style="width: {{ row.high }}%; background: {{ row.high_color }};"></div>
                            <span class="val-text {{ row.high_text_cls }}">{{ row.high }}</span>
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

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.4.2")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        pass

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    @filter.command("设置定位")
    async def set_location(self, event: AstrMessageEvent, lat: float, lon: float):
        """设置当前会话的经纬度定位。"""
        key = self._get_storage_key(event)
        await self.put_kv_data(key, {"lat": lat, "lon": lon})
        yield event.plain_result(f"📍 定位设置成功：{lat}, {lon}")
        event.stop_event()

    @filter.command("云量预报")
    async def cloud_forecast(self, event: AstrMessageEvent):
        """获取当前绑定的 ECMWF 云量预报图（Material Design 3 风格）。"""
        key = self._get_storage_key(event)
        location = await self.get_kv_data(key, None)
        
        if not location:
            yield event.plain_result("❌ 请先使用 /设置定位 [纬度] [经度] 设置位置。")
            event.stop_event()
            return

        lat, lon = location["lat"], location["lon"]

        try:
            async with httpx.AsyncClient() as client:
                url = "https://api.open-meteo.com/v1/forecast"
                params = {
                    "latitude": lat, "longitude": lon,
                    "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
                    "models": "ecmwf_ifs025", "forecast_days": 3, "timezone": "auto"
                }
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                c_total = hourly.get("cloud_cover", [])
                c_low = hourly.get("cloud_cover_low", [])
                c_mid = hourly.get("cloud_cover_mid", [])
                c_high = hourly.get("cloud_cover_high", [])

                if not times:
                    yield event.plain_result("❌ 数据获取为空。")
                    event.stop_event()
                    return

                # 数据过滤与 M3 逻辑
                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                
                def get_m3_color(val):
                    # M3 调色盘逻辑
                    if val <= 20: return "#C4E7CB", "on-light" # 极佳 (浅绿)
                    if val <= 50: return "#A8C7FF", "on-light" # 良好 (浅蓝)
                    if val <= 80: return "#FFDAD6", "on-light" # 较差 (浅红)
                    return "#BA1A1A", "on-dark"  # 极差 (深红)

                all_rows = []
                day_counts = {}
                
                for i in range(len(times)):
                    dt = datetime.datetime.fromisoformat(times[i])
                    if dt < start_threshold: continue
                    
                    day = dt.strftime("%d")
                    day_counts[day] = day_counts.get(day, 0) + 1
                    
                    t_color, t_cls = get_m3_color(c_total[i])
                    l_color, l_cls = get_m3_color(c_low[i])
                    m_color, m_cls = get_m3_color(c_mid[i])
                    h_color, h_cls = get_m3_color(c_high[i])
                    
                    all_rows.append({
                        "day": day,
                        "hour": dt.strftime("%H"),
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
                    "ref_time": now.strftime("%Y-%m-%d %H:%M"),
                    "rows": all_rows
                }
                
                # 修复模糊：使用更高的分辨率和设备缩放比
                options = {
                    "viewport": {"width": 500, "height": 100},
                    "full_page": True,
                    "scale": "device",
                    "device_scale_factor": 2, # 尝试显式设置双倍分辨率
                    "omit_background": True
                }
                
                image_url = await self.html_render(HTML_TEMPLATE, render_data, options=options)
                yield event.image_result(image_url)
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报失败: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
