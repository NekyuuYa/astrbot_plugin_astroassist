from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
import httpx
import datetime

# 极致信息密度的 HTML 模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html style="width: 480px;">
<head>
<meta name="viewport" content="width=480, initial-scale=1.0">
<style>
    * { box-sizing: border-box; }
    body {
        font-family: 'Inter', 'PingFang SC', sans-serif;
        margin: 0; padding: 0;
        background: #000; /* 黑色背景辅助裁剪 */
        width: 480px;
    }
    .container {
        width: 480px;
        background: #ffffff;
        padding: 0;
    }
    .header {
        background: #111827;
        color: #f3f4f6;
        padding: 16px 20px;
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
    }
    .header .title { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
    .header .meta { font-size: 11px; color: #9ca3af; font-family: monospace; }
    
    table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
    }
    th {
        background: #f9fafb;
        color: #6b7280;
        font-size: 11px;
        font-weight: 600;
        padding: 8px 4px;
        border-bottom: 1px solid #e5e7eb;
        text-align: center;
    }
    td {
        padding: 4px 2px;
        border-bottom: 1px solid #f3f4f6;
        text-align: center;
        height: 32px;
    }
    
    .date-cell {
        background: #f9fafb;
        font-size: 18px;
        font-weight: 800;
        color: #374151;
        border-right: 1px solid #e5e7eb;
    }
    .time-cell {
        font-size: 13px;
        font-weight: 600;
        color: #4b5563;
        background: #fff;
    }
    
    /* 核心方框组件 */
    .cloud-box {
        position: relative;
        width: 90%;
        height: 24px;
        background: #f3f4f6;
        margin: 0 auto;
        border-radius: 3px;
        overflow: hidden;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .fill {
        position: absolute;
        left: 0; top: 0; bottom: 0;
        z-index: 1;
        transition: width 0.3s ease;
    }
    .val {
        position: relative;
        z-index: 2;
        font-size: 12px;
        font-weight: 800;
        font-family: 'JetBrains Mono', monospace;
    }
    
    /* 颜色逻辑 */
    .text-light { color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,0.3); }
    .text-dark { color: #1f2937; }
</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">CLOUDS FORECAST</div>
            <div class="meta">LOC: {{ lat }}, {{ lon }}<br>REF: {{ ref_time }}</div>
        </div>
        <table>
            <thead>
                <tr>
                    <th style="width: 50px;">DAY</th>
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
                    <td class="date-cell" rowspan="{{ row.day_rowspan }}">{{ row.day }}</td>
                    {% endif %}
                    <td class="time-cell">{{ row.hour }}</td>
                    
                    <!-- TOTAL -->
                    <td>
                        <div class="cloud-box">
                            <div class="fill" style="width: {{ row.total }}%; background: {{ row.total_color }};"></div>
                            <span class="val {{ row.total_text_cls }}">{{ row.total }}</span>
                        </div>
                    </td>
                    <!-- LOW -->
                    <td>
                        <div class="cloud-box">
                            <div class="fill" style="width: {{ row.low }}%; background: {{ row.low_color }};"></div>
                            <span class="val {{ row.low_text_cls }}">{{ row.low }}</span>
                        </div>
                    </td>
                    <!-- MID -->
                    <td>
                        <div class="cloud-box">
                            <div class="fill" style="width: {{ row.mid }}%; background: {{ row.mid_color }};"></div>
                            <span class="val {{ row.mid_text_cls }}">{{ row.mid }}</span>
                        </div>
                    </td>
                    <!-- HIGH -->
                    <td>
                        <div class="cloud-box">
                            <div class="fill" style="width: {{ row.high }}%; background: {{ row.high_color }};"></div>
                            <span class="val {{ row.high_text_cls }}">{{ row.high }}</span>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.4.1")
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
        """获取当前绑定的 ECMWF 云量预报图（工业化紧凑排版）。"""
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

                # 逻辑处理：计算当前时间并过滤
                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                
                def get_color_info(val):
                    # 绿 -> 黄 -> 红
                    if val <= 20: return "#10b981", "text-dark" # 浅色背景黑字
                    if val <= 50: return "#f59e0b", "text-dark"
                    if val <= 80: return "#f97316", "text-light" # 橙色背景白字
                    return "#ef4444", "text-light"

                # 展平数据并计算 rowspan
                all_rows = []
                day_counts = {}
                
                for i in range(len(times)):
                    dt = datetime.datetime.fromisoformat(times[i])
                    if dt < start_threshold: continue
                    
                    day = dt.strftime("%d")
                    day_counts[day] = day_counts.get(day, 0) + 1
                    
                    t_color, t_cls = get_color_info(c_total[i])
                    l_color, l_cls = get_color_info(c_low[i])
                    m_color, m_cls = get_color_info(c_mid[i])
                    h_color, h_cls = get_color_info(c_high[i])
                    
                    all_rows.append({
                        "day": day,
                        "hour": dt.strftime("%H"),
                        "total": c_total[i], "total_color": t_color, "total_text_cls": t_cls,
                        "low": c_low[i], "low_color": l_color, "low_text_cls": l_cls,
                        "mid": c_mid[i], "mid_color": m_color, "mid_text_cls": m_cls,
                        "high": c_high[i], "high_color": h_color, "high_text_cls": h_cls,
                        "is_first_of_day": False # 稍后修正
                    })

                # 修正 is_first_of_day 和 day_rowspan
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
                
                # 渲染选项：增加清晰度
                options = {
                    "viewport": {"width": 480, "height": 100},
                    "full_page": True,
                    "scale": "device", # 提升清晰度
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
