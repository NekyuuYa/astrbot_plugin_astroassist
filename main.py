from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from jinja2 import Template
import httpx
import datetime
import os
import asyncio
import subprocess
import sys
import math
import re

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 专业天文气象看板", "0.8.9")
class AstroAssist(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.env_ready = False

    async def initialize(self):
        """初始化环境"""
        is_ready = await self.get_kv_data("env_v077_ok", False)
        if is_ready: self.env_ready = True
        else: asyncio.create_task(self._ensure_env())

    async def _ensure_env(self):
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                    await browser.close()
                    self.env_ready = True
                    await self.put_kv_data("env_v077_ok", True)
                except:
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
                    if sys.platform == "linux":
                        subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"])
                    self.env_ready = True
                    await self.put_kv_data("env_v077_ok", True)
        except: pass

    # --- 辅助工具 ---
    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    def _load_template(self):
        curr_dir = os.path.dirname(__file__)
        template_path = os.path.join(curr_dir, "template.html")
        with open(template_path, "r", encoding="utf-8") as f: return f.read()

    async def _amap_geocode(self, address):
        key = self.config.get("amap_key")
        if not key: raise ValueError("请在设置中配置 amap_key")
        async with httpx.AsyncClient() as client:
            url = f"https://restapi.amap.com/v3/geocode/geo?address={address}&key={key}"
            res = await client.get(url)
            data = res.json()
            if data["status"] == "1" and data["geocodes"]:
                lng, lat = map(float, data["geocodes"][0]["location"].split(","))
                return lng, lat # 返回 GCJ-02，后续会转 WGS-84
            raise ValueError(f"高德未找到地名: {address}")

    @filter.on_message()
    async def handle_message(self, event: AstrMessageEvent):
        """全监听解析器"""
        msg = event.message_str.strip()
        
        # 指令 1: 设置位置
        set_match = re.match(r'^[^\w]?(设置位置)\s+(.*)', msg)
        if set_match:
            async for res in self._handle_set_location(event, set_match.group(2)): yield res
            return

        # 指令 2: 晴天钟
        forecast_match = re.match(r'^[^\w]?(晴天钟)(\s+.*)?', msg)
        if forecast_match:
            async for res in self._handle_cloud_forecast(event, forecast_match.group(2) or ""): yield res
            return

    async def _handle_set_location(self, event: AstrMessageEvent, arg_str: str):
        key = self._get_storage_key(event)
        args = arg_str.split()
        try:
            if args[0].lower() == "-c":
                if len(args) < 3: raise ValueError("格式：-c <纬度> <经度>")
                lat, lon = float(args[1]), float(args[2])
                loc_data = {"lat": lat, "lon": lon, "name": f"坐标({lat},{lon})"}
            else:
                lng, lat = await self._amap_geocode(" ".join(args))
                loc_data = {"lat": lat, "lon": lng, "name": " ".join(args)}
            await self.put_kv_data(key, loc_data)
            yield event.plain_result(f"📍 位置已设置为：{loc_data['name']}")
        except Exception as e: yield event.plain_result(f"❌ 失败: {e}")
        event.stop_event()

    async def _handle_cloud_forecast(self, event: AstrMessageEvent, arg_str: str):
        args = arg_str.split()
        days, night_only, target_place = 3, False, None
        i = 0
        while i < len(args):
            if args[i] == "-d" and i+1 < len(args):
                try: days = int(args[i+1]); i += 2; continue
                except: pass
            if args[i] == "-n": night_only = True; i += 1; continue
            target_place = " ".join(args[i:]); break
        
        if target_place:
            try:
                lng, lat = await self._amap_geocode(target_place)
                location = {"lat": lat, "lon": lng, "name": target_place}
            except Exception as e: yield event.plain_result(f"❌ 地名解析失败: {e}"); return
        else:
            key = self._get_storage_key(event)
            location = await self.get_kv_data(key, None)
            if not location: yield event.plain_result("❌ 请先设置位置。"); return

        if not self.env_ready:
            yield event.plain_result("⌛ 环境初始化中，请稍后重试..."); return

        lat, lon = location["lat"], location["lon"]
        try:
            async with httpx.AsyncClient() as client:
                m_params = {"latitude": lat, "longitude": lon, "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m", "daily": "sunrise,sunset", "models": "ecmwf_ifs025", "forecast_days": days, "timezone": "auto"}
                # Open-Meteo 比较稳健，先请求
                m_res = await client.get("https://api.open-meteo.com/v1/forecast", params=m_params, timeout=10.0)
                m_res.raise_for_status()
                m_data = m_res.json()
                
                # 7Timer 经常宕机，增加异常处理
                t_data = {}
                try:
                    t_url = f"http://www.7timer.info/bin/api.pl?lon={lon}&lat={lat}&product=astro&output=json"
                    t_res = await client.get(t_url, timeout=10.0)
                    if t_res.status_code == 200:
                        t_data = t_res.json()
                except Exception as te:
                    logger.warning(f"7Timer API 请求失败 (降级处理): {te}")

                hourly, daily = m_data["hourly"], m_data["daily"]
                timer_series = t_data.get("dataseries", [])
                transitions = sorted([datetime.datetime.fromisoformat(s).replace(tzinfo=None) for s in daily.get("sunrise", []) + daily.get("sunset", [])])
                init_dt = None
                if t_data.get("init"):
                    init_dt = datetime.datetime.strptime(t_data["init"], "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)

                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)

                def get_m3_color(v, t="temp"):
                    if t == "temp":
                        if v < -10: return "#003258", "on-dark"
                        if v <= 0: return "#D1E4FF", "on-light"
                        if v <= 8: return "#C4E7CB", "on-light"
                        if v <= 16: return "#A8C7FF", "on-light"
                        if v <= 24: return "#E8F0FF", "on-light"
                        if v <= 30: return "#FFECB3", "on-light"
                        if v <= 36: return "#FFDAD6", "on-light"
                        if v <= 38: return "#BA1A1A", "on-dark"
                        return "#4A148C", "on-dark"
                    if t == "astro":
                        if v <= 2: return "#10b981", "on-dark"
                        if v <= 4: return "#3b82f6", "on-dark"
                        if v <= 6: return "#f59e0b", "on-light"
                        return "#BA1A1A", "on-dark"

                all_rows, day_counts = [], {}
                for i in range(len(hourly["time"])):
                    dt = datetime.datetime.fromisoformat(hourly["time"][i]).replace(tzinfo=None)
                    if dt < start_threshold.replace(tzinfo=None): continue
                    if night_only and not (dt.hour >= 18 or dt.hour <= 6): continue
                    
                    # 匹配 7Timer
                    astro = {"seeing": 5, "transparency": 5}
                    if init_dt:
                        timer_idx = int(((dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))) - init_dt).total_seconds() / 3600) / 3)
                        if 0 <= timer_idx < len(timer_series): astro = timer_series[timer_idx]

                    is_transition, trans_type = False, ""
                    for t in transitions:
                        if dt <= t < dt + datetime.timedelta(hours=1):
                            is_transition, trans_type = True, ("SUNRISE" if t in [datetime.datetime.fromisoformat(x).replace(tzinfo=None) for x in daily["sunrise"]] else "SUNSET")
                            break

                    dk = dt.strftime("%d")
                    day_counts[dk] = day_counts.get(dk, 0) + 1
                    t_v, d_v, w_v = hourly['temperature_2m'][i], hourly['dew_point_2m'][i], hourly['wind_speed_10m'][i]
                    
                    all_rows.append({
                        "day": dk, "hour": dt.strftime("%H"), "is_transition": is_transition, "transition_type": trans_type,
                        "temp_val": int(t_v), "temp_color": get_m3_color(t_v)[0], "temp_cls": get_m3_color(t_v)[1],
                        "dew_val": int(d_v), "dew_color": "#ef4444" if d_v < 2 else ("#f59e0b" if d_v <= 5 else "#10b981"), "dew_cls": "on-dark" if d_v < 2 or d_v > 5 else "on-light",
                        "humi_val": int(hourly['relative_humidity_2m'][i]),
                        "wind_val": int(w_v), "wind_color": "#BA1A1A" if w_v > 35 else ("#FFECB3" if w_v > 20 else ("#A8C7FF" if w_v > 10 else "#C4E7CB")), "wind_cls": "on-dark" if w_v > 35 else "on-light",
                        "seeing_val": astro.get("seeing", 5), "seeing_color": get_m3_color(astro.get("seeing", 5), "astro")[0], "seeing_cls": get_m3_color(astro.get("seeing", 5), "astro")[1],
                        "trans_val": astro.get("transparency", 5), "trans_color": get_m3_color(astro.get("transparency", 5), "astro")[0], "trans_cls": get_m3_color(astro.get("transparency", 5), "astro")[1],
                        "total": hourly["cloud_cover"][i], "low": hourly["cloud_cover_low"][i], "mid": hourly["cloud_cover_mid"][i], "high": hourly["cloud_cover_high"][i]
                    })

                seen_days = set()
                for row in all_rows:
                    if row["day"] not in seen_days:
                        row["is_first_of_day"] = True
                        row["day_rowspan"] = day_counts[row["day"]] + len([r for r in all_rows if r["day"] == row["day"] and r["is_transition"]])
                        seen_days.add(row["day"])

                theme_mode, theme_label = "light-mode", "日间"
                if self.config.get("auto_theme", True):
                    try:
                        ts, tr = datetime.datetime.fromisoformat(daily["sunset"][0]).replace(tzinfo=None), datetime.datetime.fromisoformat(daily["sunrise"][0]).replace(tzinfo=None)
                        if now.replace(tzinfo=None) > ts or now.replace(tzinfo=None) < tr: theme_mode, theme_label = "night-mode", "夜间"
                    except: pass

                render_data = {"lat": round(lat, 4), "lon": round(lon, 4), "location_name": location["name"], "ref_time": now.strftime("%Y-%m-%d %H:%M"), "rows": all_rows, "theme_mode": theme_mode, "theme_label": theme_label, "model_name": "ECMWF+7Timer"}
                template_str, save_path = self._load_template(), os.path.abspath(f"data/plugin_data/astrbot_plugin_astroassist/forecast_v89.png")
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(viewport={"width": 1250, "height": 800}, device_scale_factor=3)
                    page = await context.new_page()
                    await page.set_content(Template(template_str).render(**render_data))
                    await asyncio.sleep(1.5)
                    await page.screenshot(path=save_path, full_page=True)
                    await browser.close()

                yield event.chain_result([Comp.Image(file=save_path)])
                event.stop_event()
        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报异常: {str(e)}")

    async def terminate(self): pass
