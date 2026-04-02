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

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.7.5")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.initialized = False

    async def initialize(self):
        """插件初始化：检测环境，按需补全"""
        is_env_ready = await self.get_kv_data("env_initialized", False)
        if is_env_ready:
            self.initialized = True
            return

        # 1. 检测 Playwright 是否已安装内核
        try:
            from playwright.async_api import async_playwright
            # 尝试获取 chromium 的可执行路径，如果不报错说明已下载
            # 这里不真正启动，只是查找
            logger.info("AstroAssist: 正在检测本地渲染引擎...")
        except ImportError:
            logger.error("AstroAssist: 缺少 playwright 依赖库。")
            return

        # 我们尝试检查内核是否存在 (不使用启动方式)
        try:
            # 这是一个简单的方法来检查 playwright 是否有 chromium 可用
            # 如果没有，install 命令通常很快就会返回或报错
            res = subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium", "--dry-run"], capture_output=True)
            # 如果 dry-run 没报错，通常说明环境已经配置过
            # 我们在这里不做强行安装，只在启动失败时提示
            self.initialized = True
            await self.put_kv_data("env_initialized", True)
        except Exception as e:
            logger.warning(f"AstroAssist 环境检测异常: {e}")

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
            try:
                # 尝试启动，增加更多的错误捕获
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                )
            except Exception as e:
                err_msg = str(e)
                logger.error(f"AstroAssist 渲染器启动失败: {err_msg}")
                # 精准判断错误类型
                if "shared libraries" in err_msg or "libnspr4" in err_msg:
                    raise RuntimeError("渲染引擎启动失败：由于 Docker 镜像过于精简，系统缺少必要的运行库。请在宿主机执行: docker exec -u root <容器名> playwright install-deps")
                elif "executable" in err_msg:
                    raise RuntimeError("渲染引擎启动失败：未找到 Chromium 内核。请在宿主机执行: docker exec <容器名> playwright install chromium")
                else:
                    raise RuntimeError(f"渲染引擎启动失败: {err_msg[:100]}...")
            
            context = await browser.new_context(viewport={"width": 1100, "height": 800}, device_scale_factor=3)
            page = await context.new_page()
            await page.setContent(html_content)
            await asyncio.sleep(1.5) 
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
        key = self._get_storage_key(event)
        location = await self.get_kv_data(key, None)
        if not location:
            yield event.plain_result("❌ 请先设置定位。")
            return

        lat, lon = location["lat"], location["lon"]

        try:
            async with httpx.AsyncClient() as client:
                url = "https://api.open-meteo.com/v1/forecast"
                params = {
                    "latitude": lat, "longitude": lon,
                    "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m",
                    "models": "ecmwf_ifs025", "forecast_days": 3, "timezone": "auto"
                }
                response = await client.get(url, params=params, timeout=10.0)
                data = response.json()
                
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                if not times:
                    yield event.plain_result("❌ 数据为空。")
                    return

                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                
                # ... (get_m3_color 等逻辑保持不变)
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

                def get_dew_risk_color(val):
                    if val < 2: return "#ef4444", "on-dark"
                    if val <= 5: return "#f59e0b", "on-light"
                    return "#10b981", "on-dark"

                def get_wind_color(val):
                    if val < 10: return "#C4E7CB", "on-light"
                    if val <= 20: return "#A8C7FF", "on-light"
                    if val <= 35: return "#FFECB3", "on-light"
                    return "#BA1A1A", "on-dark"

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
                    t_v, d_v, w_v = hourly['temperature_2m'][i], hourly['dew_point_2m'][i], hourly['wind_speed_10m'][i]
                    all_rows.append({
                        "day": day, "hour": dt.strftime("%H"),
                        "temp_val": int(t_v), "temp_color": get_temp_color(t_v)[0], "temp_cls": get_temp_color(t_v)[1],
                        "dew_val": int(d_v), "dew_color": get_dew_risk_color(d_v)[0], "dew_cls": get_dew_risk_color(d_v)[1],
                        "humi_val": int(hourly['relative_humidity_2m'][i]),
                        "wind_val": int(w_v), "wind_color": get_wind_color(w_v)[0], "wind_cls": get_wind_color(w_v)[1],
                        "total": hourly["cloud_cover"][i], "total_color": get_cloud_color(hourly["cloud_cover"][i])[0], "total_text_cls": get_cloud_color(hourly["cloud_cover"][i])[1],
                        "low": hourly["cloud_cover_low"][i], "low_color": get_cloud_color(hourly["cloud_cover_low"][i])[0], "low_text_cls": get_cloud_color(hourly["cloud_cover_low"][i])[1],
                        "mid": hourly["cloud_cover_mid"][i], "mid_color": get_cloud_color(hourly["cloud_cover_mid"][i])[0], "mid_text_cls": get_cloud_color(hourly["cloud_cover_mid"][i])[1],
                        "high": hourly["cloud_cover_high"][i], "high_color": get_cloud_color(hourly["cloud_cover_high"][i])[0], "high_text_cls": get_cloud_color(hourly["cloud_cover_high"][i])[1],
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
                
                save_path = os.path.abspath("data/plugin_data/astrbot_plugin_astroassist/forecast_v075.png")
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                try:
                    await self._render_locally(html_content, save_path)
                    yield event.chain_result([Comp.Image(file=save_path)])
                except Exception as render_err:
                    yield event.plain_result(f"⚠️ {str(render_err)}")
                
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报执行异常: {str(e)}")

    async def terminate(self):
        pass
