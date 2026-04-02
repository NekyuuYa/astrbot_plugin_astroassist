from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from jinja2 import Template
import httpx
import datetime
import os
import asyncio
import subprocess
import sys

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.7.1")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.initialized = False

    async def initialize(self):
        is_env_ready = await self.get_kv_data("env_initialized", False)
        if is_env_ready:
            self.initialized = True
            return
        try:
            import playwright
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            if proc.returncode == 0:
                await self.put_kv_data("env_initialized", True)
                self.initialized = True
        except:
            pass

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    def _load_template(self):
        curr_dir = os.path.dirname(__file__)
        template_path = os.path.join(curr_dir, "template.html")
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()

    async def _render_locally(self, html_content: str, save_path: str):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1000, "height": 800}, device_scale_factor=3)
            page = await context.new_page()
            await page.set_content(html_content)
            await asyncio.sleep(1)
            await page.screenshot(path=save_path, full_page=True)
            await browser.close()

    @filter.command("设置定位")
    async def set_location(self, event: AstrMessageEvent, lat: float, lon: float):
        key = self._get_storage_key(event)
        await self.put_kv_data(key, {"lat": lat, "lon": lon})
        yield event.plain_result(f"📍 定位设置成功：{lat}, {lon}")
        event.stop_event()

    @filter.command("云量预报")
    async def cloud_forecast(self, event: AstrMessageEvent):
        if not self.initialized:
            is_env_ready = await self.get_kv_data("env_initialized", False)
            if not is_env_ready:
                yield event.plain_result("⌛ 插件环境正在初始化...")
                return

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
                    "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m",
                    "models": "ecmwf_ifs025", "forecast_days": 3, "timezone": "auto"
                }
                response = await client.get(url, params=params, timeout=10.0)
                data = response.json()
                
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                if not times:
                    yield event.plain_result("❌ 接口数据为空。")
                    return

                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                
                def get_temp_color(val):
                    if val < -10: return "#003258", "on-dark"
                    if val <= 0: return "#D1E4FF", "on-light"
                    if val <= 8: return "#C4E7CB", "on-light"
                    if val <= 16: return "#A8C7FF", "on-light"
                    if val <= 24: return "#E8F0FF", "on-light"
                    if val <= 30: return "#FFECB3", "on-light"
                    if val <= 36: return "#FFDAD6", "on-light"
                    if val <= 38: return "#BA1A1A", "on-dark"
                    return "#4A148C", "on-dark"

                def get_cloud_color(val):
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
                        "temp_val": int(hourly['temperature_2m'][i]),
                        "temp_color": get_temp_color(hourly['temperature_2m'][i])[0],
                        "temp_cls": get_temp_color(hourly['temperature_2m'][i])[1],
                        "dew_val": int(hourly['dew_point_2m'][i]),
                        "dew_color": get_temp_color(hourly['dew_point_2m'][i])[0],
                        "dew_cls": get_temp_color(hourly['dew_point_2m'][i])[1],
                        "humi_val": int(hourly['relative_humidity_2m'][i]),
                        "total": hourly["cloud_cover"][i], 
                        "total_color": get_cloud_color(hourly["cloud_cover"][i])[0],
                        "total_text_cls": get_cloud_color(hourly["cloud_cover"][i])[1],
                        "low": hourly["cloud_cover_low"][i],
                        "low_color": get_cloud_color(hourly["cloud_cover_low"][i])[0],
                        "low_text_cls": get_cloud_color(hourly["cloud_cover_low"][i])[1],
                        "mid": hourly["cloud_cover_mid"][i],
                        "mid_color": get_cloud_color(hourly["cloud_cover_mid"][i])[0],
                        "mid_text_cls": get_cloud_color(hourly["cloud_cover_mid"][i])[1],
                        "high": hourly["cloud_cover_high"][i],
                        "high_color": get_cloud_color(hourly["cloud_cover_high"][i])[0],
                        "high_text_cls": get_cloud_color(hourly["cloud_cover_high"][i])[1],
                        "is_first_of_day": False
                    })

                seen_days = set()
                for row in all_rows:
                    if row["day"] not in seen_days:
                        row["is_first_of_day"], row["day_rowspan"] = True, day_counts[row["day"]]
                        seen_days.add(row["day"])

                render_data = {"lat": lat, "lon": lon, "ref_time": now.strftime("%Y-%m-%d %H:%M"), "rows": all_rows}
                template_str = self._load_template()
                html_content = Template(template_str).render(**render_data)
                
                save_path = os.path.abspath("data/plugin_data/astrbot_plugin_astroassist/forecast_v071.png")
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                await self._render_locally(html_content, save_path)
                yield event.chain_result([Comp.Image(file=save_path)])
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报执行异常: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
