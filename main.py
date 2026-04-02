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

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.6.2")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.initialized = False

    async def initialize(self):
        """插件初始化：自动检查并安装必要的运行环境"""
        # 检查是否已经成功初始化过
        is_env_ready = await self.get_kv_data("env_initialized", False)
        if is_env_ready:
            self.initialized = True
            return

        logger.info("AstroAssist: 正在检查运行环境...")
        try:
            # 1. 检查 playwright 是否安装
            import playwright
            
            # 2. 尝试检查 chromium 是否可用 (非阻塞)
            # 我们通过运行一个简单的命令来测试
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            logger.info("AstroAssist: 正在后台下载/校验 Chromium 内核，请稍后...")
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                logger.info("AstroAssist: 环境初始化成功。")
                await self.put_kv_data("env_initialized", True)
                self.initialized = True
            else:
                logger.error(f"AstroAssist: 环境初始化失败 (ReturnCode {proc.returncode})。")
                logger.error(f"Error: {stderr.decode()}")
        except ImportError:
            logger.error("AstroAssist: 未找到 playwright 库，请确保 requirements.txt 已被正确安装。")
        except Exception as e:
            logger.error(f"AstroAssist: 初始化过程发生异常: {e}")

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
        try:
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(headless=True)
                except Exception as e:
                    # 如果 initialize 失败了，这里作为最后的防线给出提示
                    logger.error(f"Playwright Chromium 启动失败: {e}")
                    raise RuntimeError("本地 Chromium 未安装或缺少依赖。请在容器内执行: playwright install chromium && playwright install-deps chromium")
                
                context = await browser.new_context(
                    viewport={"width": 1000, "height": 800},
                    device_scale_factor=3
                )
                page = await context.new_page()
                await page.set_content(html_content)
                await asyncio.sleep(1)
                await page.screenshot(path=save_path, full_page=True)
                await browser.close()
        except ImportError:
            raise ImportError("未安装 playwright 库，请重启 Bot 以自动安装依赖。")

    @filter.command("设置定位")
    async def set_location(self, event: AstrMessageEvent, lat: float, lon: float):
        key = self._get_storage_key(event)
        await self.put_kv_data(key, {"lat": lat, "lon": lon})
        yield event.plain_result(f"📍 定位设置成功：{lat}, {lon}")
        event.stop_event()

    @filter.command("云量预报")
    async def cloud_forecast(self, event: AstrMessageEvent):
        if not self.initialized:
            # 再次检查，防止 initialize 时的异步操作未完成
            is_env_ready = await self.get_kv_data("env_initialized", False)
            if not is_env_ready:
                yield event.plain_result("⌛ 插件环境正在初始化（下载浏览器内核），请在一分钟后重试。")
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
                    "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
                    "models": "ecmwf_ifs025", "forecast_days": 3, "timezone": "auto"
                }
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                if not times:
                    yield event.plain_result("❌ 接口数据为空。")
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
                    
                    val = hourly["cloud_cover"][i]
                    c, t = get_m3_color(val)
                    all_rows.append({
                        "day": day, "hour": dt.strftime("%H"),
                        "total": val, "total_color": c, "total_text_cls": t,
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
                
                template_str = self._load_template()
                html_content = Template(template_str).render(**render_data)
                
                save_path = os.path.abspath("data/plugin_data/astrbot_plugin_astroassist/forecast_v062.png")
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                try:
                    await self._render_locally(html_content, save_path)
                    yield event.chain_result([Comp.Image(file=save_path)])
                except Exception as render_err:
                    yield event.plain_result(f"⚠️ 渲染失败：{str(render_err)}")
                
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报执行异常: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
