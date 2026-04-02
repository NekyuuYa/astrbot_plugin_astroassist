from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
import httpx
import datetime

# 高分辨率 Material Design 3 模板 (基础宽度 1000px)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html style="width: 1000px;">
<head>
<style>
    * { box-sizing: border-box; -webkit-font-smoothing: antialiased; }
    body {
        font-family: 'Roboto', 'PingFang SC', sans-serif;
        margin: 0; padding: 24px;
        background: #F0F2F5;
        width: 1000px;
    }
    .card {
        background: #FFFFFF;
        border-radius: 48px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        overflow: hidden;
        width: 952px;
        border: 2px solid #E1E2EC;
    }
    .header {
        background: #E8F0FF;
        padding: 48px 40px;
        border-bottom: 2px solid #E1E2EC;
    }
    .header h1 { 
        margin: 0; font-size: 48px; color: #1A1C1E; font-weight: 600; 
        display: flex; align-items: center; gap: 20px;
    }
    .header .meta { 
        margin-top: 16px; font-size: 24px; color: #44474E; 
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
        font-size: 22px;
        font-weight: 700;
        padding: 24px 8px;
        border-bottom: 2px solid #E1E2EC;
        text-align: center;
    }
    td {
        padding: 12px 4px;
        border-bottom: 2px solid #F0F0F8;
        text-align: center;
        height: 80px;
    }
    
    .date-col {
        background: #FFFFFF;
        font-size: 40px;
        font-weight: 900;
        color: #0056D2;
        border-right: 2px solid #E1E2EC;
    }
    .time-col {
        font-size: 28px;
        font-weight: 700;
        color: #1A1C1E;
    }
    
    .progress-box {
        position: relative;
        width: 92%;
        height: 56px;
        background: #F0F0F8;
        margin: 0 auto;
        border-radius: 16px;
        overflow: hidden;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .fill {
        position: absolute;
        left: 0; top: 0; bottom: 0;
        z-index: 1;
    }
    .val-text {
        position: relative;
        z-index: 2;
        font-size: 26px;
        font-weight: 900;
        font-family: 'Roboto Mono', monospace;
    }
    
    .on-light { color: #1A1C1E; }
    .on-dark { color: #FFFFFF; text-shadow: 0 2px 4px rgba(0,0,0,0.3); }
    
    .footer {
        padding: 32px;
        text-align: center;
        font-size: 22px;
        color: #74777F;
        background: #FDFCFF;
        border-top: 2px solid #E1E2EC;
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
                    <th style="width: 110px;">DATE</th>
                    <th style="width: 90px;">HR</th>
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
            Generated by AstroAssist • Material Design 3 High-Res
        </div>
    </div>
</body>
</html>
"""

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.4.3")
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
        """获取当前绑定的 ECMWF 云量预报图（Material Design 3 高清版）。"""
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
                times, c_total = hourly.get("time", []), hourly.get("cloud_cover", [])
                c_low, c_mid, c_high = hourly.get("cloud_cover_low", []), hourly.get("cloud_cover_mid", []), hourly.get("cloud_cover_high", [])

                if not times:
                    yield event.plain_result("❌ 数据获取为空。")
                    event.stop_event()
                    return

                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                
                def get_m3_color(val):
                    if val <= 20: return "#C4E7CB", "on-light" # 极佳
                    if val <= 50: return "#A8C7FF", "on-light" # 良好
                    if val <= 80: return "#FFDAD6", "on-light" # 较差
                    return "#BA1A1A", "on-dark"  # 极差

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
                    "ref_time": now.strftime("%Y-%m-%d %H:%M"),
                    "rows": all_rows
                }
                
                # 高清晰度渲染：将 Viewport 放大到 1000px，并设置 2 倍采样
                options = {
                    "viewport": {"width": 1000, "height": 100},
                    "full_page": True,
                    "scale": "device",
                    "device_scale_factor": 2, # 总有效宽度达到 2000px
                    "omit_background": False # 关闭背景透明，减少毛边
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
