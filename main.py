from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
import httpx
import datetime

# HTML 模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<style>
    body {
        font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
        background: #f0f2f5;
        display: flex;
        justify-content: center;
        padding: 20px;
    }
    .card {
        background: white;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        width: 500px;
        overflow: hidden;
    }
    .header {
        background: #1e3a8a;
        color: white;
        padding: 20px;
        text-align: center;
    }
    .header h1 { margin: 0; font-size: 20px; }
    .header p { margin: 5px 0 0; font-size: 14px; opacity: 0.8; }
    .content { padding: 15px; }
    .day-group { margin-bottom: 20px; }
    .day-title {
        font-weight: bold;
        color: #1e3a8a;
        border-bottom: 2px solid #e5e7eb;
        padding-bottom: 5px;
        margin-bottom: 10px;
        display: flex;
        align-items: center;
    }
    table {
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
    }
    th {
        text-align: left;
        color: #6b7280;
        padding: 8px;
        font-weight: normal;
        border-bottom: 1px solid #f3f4f6;
    }
    td {
        padding: 8px;
        border-bottom: 1px solid #f3f4f6;
    }
    .time-col { font-weight: 500; color: #374151; width: 60px; }
    .val-col { text-align: center; width: 60px; }
    .status-dot {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 6px;
    }
    .level-low { color: #10b981; } /* 晴 */
    .level-mid { color: #f59e0b; } /* 多云 */
    .level-high { color: #ef4444; } /* 阴 */
    
    .percent-bar {
        height: 4px;
        background: #e5e7eb;
        border-radius: 2px;
        margin-top: 4px;
        overflow: hidden;
    }
    .percent-fill {
        height: 100%;
    }
</style>
</head>
<body>
    <div class="card">
        <div class="header">
            <h1>☁️ ECMWF 云量预报</h1>
            <p>经纬度: {{ lat }}, {{ lon }} | 模型: ECMWF IFS 0.25°</p>
        </div>
        <div class="content">
            {% for day in days %}
            <div class="day-group">
                <div class="day-title">📅 {{ day.date }}</div>
                <table>
                    <thead>
                        <tr>
                            <th class="time-col">时间</th>
                            <th class="val-col">总云</th>
                            <th class="val-col">低空</th>
                            <th class="val-col">中空</th>
                            <th class="val-col">高空</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in day.rows %}
                        <tr>
                            <td class="time-col">{{ row.time }}时</td>
                            <td class="val-col">
                                <span class="{{ row.total_class }}">{{ row.total }}%</span>
                                <div class="percent-bar"><div class="percent-fill" style="width: {{ row.total }}%; background: {{ row.total_color }};"></div></div>
                            </td>
                            <td class="val-col">{{ row.low }}%</td>
                            <td class="val-col">{{ row.mid }}%</td>
                            <td class="val-col">{{ row.high }}%</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.2.0")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        pass

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if group_id:
            return f"location_group_{group_id}"
        else:
            return f"location_user_{event.get_sender_id()}"

    @filter.command("设置定位")
    async def set_location(self, event: AstrMessageEvent, lat: float, lon: float):
        """设置当前群组或私聊的观测点经纬度。"""
        key = self._get_storage_key(event)
        location_data = {"lat": lat, "lon": lon}
        await self.put_kv_data(key, location_data)
        
        target = "当前群组" if event.message_obj.group_id else "您"
        yield event.plain_result(f"📍 {target}的定位已设置成功：纬度 {lat}, 经度 {lon}")
        event.stop_event()

    @filter.command("云量预报")
    async def cloud_forecast(self, event: AstrMessageEvent):
        """获取当前绑定的 ECMWF 云量预报（渲染为图片）。"""
        key = self._get_storage_key(event)
        location = await self.get_kv_data(key, None)
        
        if not location:
            yield event.plain_result("❌ 请先使用 /设置定位 [纬度] [经度] 设置位置。")
            event.stop_event()
            return

        lat, lon = location["lat"], location["lon"]

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
            "models": "ecmwf_ifs025",
            "forecast_days": 3,
            "timezone": "auto"
        }

        try:
            async with httpx.AsyncClient() as client:
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
                    yield event.plain_result("❌ 未能获取到有效的云量数据。")
                    event.stop_event()
                    return

                # 数据过滤与格式化
                now = datetime.datetime.now()
                # 寻找当前时间点在数据中的索引（由于 API 返回本地时间，直接对比）
                # 为了简化，我们按之前的逻辑：当前-2h开始
                start_threshold = now - datetime.timedelta(hours=2)
                
                days_data = []
                current_day = None
                
                def get_color(val):
                    if val <= 20: return "#10b981" # 绿
                    if val <= 70: return "#f59e0b" # 黄
                    return "#ef4444" # 红

                for i in range(len(times)):
                    dt = datetime.datetime.fromisoformat(times[i])
                    if dt < start_threshold:
                        continue
                    
                    day_str = dt.strftime("%m-%d")
                    if current_day is None or current_day["date"] != day_str:
                        current_day = {"date": day_str, "rows": []}
                        days_data.append(current_day)
                    
                    val = c_total[i]
                    current_day["rows"].append({
                        "time": dt.strftime("%H"),
                        "total": val,
                        "low": c_low[i],
                        "mid": c_mid[i],
                        "high": c_high[i],
                        "total_color": get_color(val),
                        "total_class": "level-low" if val <= 20 else ("level-mid" if val <= 70 else "level-high")
                    })

                # 渲染图片
                render_data = {
                    "lat": lat,
                    "lon": lon,
                    "days": days_data
                }
                
                image_url = await self.html_render(HTML_TEMPLATE, render_data)
                yield event.image_result(image_url)
                event.stop_event()

        except Exception as e:
            logger.error(f"获取天气数据失败: {e}")
            yield event.plain_result(f"❌ 获取预报失败: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
