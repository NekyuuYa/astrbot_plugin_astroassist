from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
import httpx
import datetime

# 极致优化的 HTML 模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html style="width: 500px; margin: 0; padding: 0;">
<head>
<meta name="viewport" content="width=500, initial-scale=1.0">
<style>
    * { box-sizing: border-box; }
    body {
        font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
        margin: 0;
        padding: 0;
        width: 500px;
        background: transparent;
    }
    .container {
        width: 500px;
        padding: 12px;
        background: #f8fafc;
    }
    .card {
        background: white;
        border-radius: 16px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.08);
        overflow: hidden;
        border: 1px solid #e2e8f0;
    }
    .header {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        color: white;
        padding: 24px 20px;
        text-align: left;
    }
    .header h1 { margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.5px; }
    .header .meta { margin-top: 8px; font-size: 13px; color: #94a3b8; }
    
    .day-section { margin-top: 0; }
    .day-header {
        background: #f1f5f9;
        padding: 12px 20px;
        font-size: 15px;
        font-weight: 700;
        color: #334155;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    table {
        width: 100%;
        border-collapse: collapse;
    }
    th {
        text-align: center;
        color: #94a3b8;
        font-size: 11px;
        font-weight: 600;
        padding: 12px 5px;
        border-bottom: 1px solid #f1f5f9;
    }
    td {
        padding: 14px 5px;
        text-align: center;
        font-size: 14px;
        color: #1e293b;
        border-bottom: 1px solid #f8fafc;
    }
    .time-cell { font-weight: 700; color: #475569; width: 70px; }
    
    .cloud-val {
        font-weight: 800;
        font-size: 14px;
    }
    .text-clear { color: #10b981; }
    .text-partly { color: #f59e0b; }
    .text-cloudy { color: #ef4444; }
    
    .bar-bg {
        width: 44px;
        height: 5px;
        background: #f1f5f9;
        border-radius: 3px;
        margin: 6px auto 0;
        overflow: hidden;
    }
    .bar-fill { height: 100%; border-radius: 3px; }
    
    .footer {
        padding: 16px;
        text-align: center;
        font-size: 11px;
        color: #cbd5e1;
        background: white;
        border-top: 1px solid #f1f5f9;
    }
</style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>🔭 晴天钟气象预报</h1>
                <div class="meta">📍 {{ lat }}, {{ lon }} | ECMWF IFS 模型</div>
            </div>
            <div class="content">
                {% for day in days %}
                <div class="day-section">
                    <div class="day-header">
                        <span>📅 {{ day.date }}</span>
                        <span style="font-weight: 400; font-size: 11px; color: #64748b;">ECMWF 0.25°</span>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 70px;">时间</th>
                                <th>总云量</th>
                                <th>低</th>
                                <th>中</th>
                                <th>高</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for row in day.rows %}
                            <tr>
                                <td class="time-cell">{{ row.time }}:00</td>
                                <td>
                                    <div class="cloud-val {{ row.cls }}">{{ row.total }}%</div>
                                    <div class="bar-bg"><div class="bar-fill" style="width: {{ row.total }}%; background: {{ row.color }};"></div></div>
                                </td>
                                <td>{{ row.low }}%</td>
                                <td>{{ row.mid }}%</td>
                                <td>{{ row.high }}%</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% endfor %}
            </div>
            <div class="footer">
                由 AstroAssist 为您生成 • 数据源自 Open-Meteo
            </div>
        </div>
    </div>
</body>
</html>
"""

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.4.0")
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
        """获取当前绑定的 ECMWF 云量预报图。"""
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
                
                days_data = []
                curr_day = None
                
                for i in range(len(times)):
                    dt = datetime.datetime.fromisoformat(times[i])
                    if dt < start_threshold: continue
                    
                    d_str = dt.strftime("%m月%d日")
                    if not curr_day or curr_day["date"] != d_str:
                        curr_day = {"date": d_str, "rows": []}
                        days_data.append(curr_day)
                    
                    val = c_total[i]
                    if val <= 20: 
                        cls, color = "text-clear", "#10b981"
                    elif val <= 70: 
                        cls, color = "text-partly", "#f59e0b"
                    else: 
                        cls, color = "text-cloudy", "#ef4444"
                        
                    curr_day["rows"].append({
                        "time": dt.strftime("%H"),
                        "total": val, "low": c_low[i], "mid": c_mid[i], "high": c_high[i],
                        "cls": cls, "color": color
                    })

                render_data = {"lat": lat, "lon": lon, "days": days_data}
                
                # 核心修复：锁定 viewport 宽度为 500px，并配合 HTML 的 meta 标签
                options = {
                    "viewport": {"width": 500, "height": 1000}, 
                    "full_page": True,
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
