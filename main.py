from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
import httpx
import datetime
import os

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.5.7")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        pass

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    def _load_template(self):
        curr_dir = os.path.dirname(__file__)
        template_path = os.path.join(curr_dir, "template.html")
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()

    @filter.command("设置定位")
    async def set_location(self, event: AstrMessageEvent, lat: float, lon: float):
        key = self._get_storage_key(event)
        await self.put_kv_data(key, {"lat": lat, "lon": lon})
        yield event.plain_result(f"📍 定位设置成功：{lat}, {lon}")
        event.stop_event()

    @filter.command("云量预报")
    async def cloud_forecast(self, event: AstrMessageEvent):
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
                times, c_total, c_low, c_mid, c_high = hourly.get("time", []), hourly.get("cloud_cover", []), hourly.get("cloud_cover_low", []), hourly.get("cloud_cover_mid", []), hourly.get("cloud_cover_high", [])

                if not times:
                    yield event.plain_result("❌ 接口返回数据为空。")
                    event.stop_event()
                    return

                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                
                def get_m3_color(val):
                    if val <= 20: return "#C4E7CB", "on-light"
                    if val <= 50: return "#A8C7FF", "on-light"
                    if val <= 80: return "#FFDAD6", "on-light"
                    return "#BA1A1A", "on-dark"

                all_rows, day_counts = [], {}
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
                        row["is_first_of_day"], row["day_rowspan"] = True, day_counts[row["day"]]
                        seen_days.add(row["day"])

                render_data = {"lat": lat, "lon": lon, "ref_time": now.strftime("%Y-%m-%d %H:%M"), "rows": all_rows}
                
                options = {
                    "viewport": {"width": 1000, "height": 100},
                    "full_page": True,
                    "scale": "device",
                    "device_scale_factor": 3,
                    "type": "png",
                    "omit_background": True
                }
                
                template = self._load_template()
                # 获取本地文件路径 (绝对路径)
                image_path = await self.html_render(template, render_data, options=options, return_url=False)
                
                # 修复方案：直接构造 Image 对象，避开可能导致 4 斜杠的 fromFileSystem 静态方法
                # 直接传入路径，AstrBot 底层会处理 path -> file 的转换
                chain = [
                    Comp.Image(file=image_path)
                ]
                yield event.chain_result(chain)
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报执行异常: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
