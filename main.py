from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
import httpx
import datetime
import os

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.5.9")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        pass

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    def _load_template(self):
        try:
            curr_dir = os.path.dirname(__file__)
            template_path = os.path.join(curr_dir, "template.html")
            with open(template_path, "r", encoding="utf-8") as f:
                content = f.read()
                if not content:
                    raise ValueError("Template file is empty")
                return content
        except Exception as e:
            logger.error(f"Error loading template: {e}")
            return "<html><body><h1>Template Load Error</h1><p>{{ lat }}, {{ lon }}</p></body></html>"

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
                times = hourly.get("time", [])
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
                    all_rows.append({
                        "day": day, "hour": dt.strftime("%H"),
                        "total": hourly["cloud_cover"][i], 
                        "total_color": get_m3_color(hourly["cloud_cover"][i])[0],
                        "total_text_cls": get_m3_color(hourly["cloud_cover"][i])[1],
                        "low": hourly["cloud_cover_low"][i],
                        "low_color": get_m3_color(hourly["cloud_cover_low"][i])[0],
                        "low_text_cls": get_m3_color(hourly["cloud_cover_low"][i])[1],
                        "mid": hourly["cloud_cover_mid"][i],
                        "mid_color": get_m3_color(hourly["cloud_cover_mid"][i])[0],
                        "mid_text_cls": get_m3_color(hourly["cloud_cover_mid"][i])[1],
                        "high": hourly["cloud_cover_high"][i],
                        "high_color": get_m3_color(hourly["cloud_cover_high"][i])[0],
                        "high_text_cls": get_m3_color(hourly["cloud_cover_high"][i])[1],
                        "is_first_of_day": False
                    })

                seen_days = set()
                for row in all_rows:
                    if row["day"] not in seen_days:
                        row["is_first_of_day"], row["day_rowspan"] = True, day_counts[row["day"]]
                        seen_days.add(row["day"])

                render_data = {"lat": lat, "lon": lon, "ref_time": now.strftime("%Y-%m-%d %H:%M"), "rows": all_rows}
                
                # 遵循 Playwright screenshot 标准参数，移除非法键值
                options = {
                    "viewport": {"width": 1000, "height": 800}, # 提高初始高度
                    "full_page": True,
                    "type": "png",
                    "omit_background": True
                }
                
                template = self._load_template()
                image_path = await self.html_render(template, render_data, options=options, return_url=False)
                
                # 检查 image_path 是否有效
                if not image_path or not os.path.exists(image_path):
                    raise FileNotFoundError(f"Rendered image not found at {image_path}")

                yield event.chain_result([Comp.Image(file=image_path)])
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报执行异常: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
