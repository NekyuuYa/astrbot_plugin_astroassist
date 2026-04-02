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

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 专业天文气象看板", "0.8.18")
class AstroAssist(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.env_ready = False

    async def initialize(self):
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

    def _load_template(self):
        curr_dir = os.path.dirname(__file__)
        template_path = os.path.join(curr_dir, "template.html")
        with open(template_path, "r", encoding="utf-8") as f: return f.read()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        msg = event.message_str.strip()
        set_match = re.match(r'^[^\w]?(设置位置)\s+(.*)', msg)
        if set_match:
            async for res in self._handle_set_location(event, set_match.group(2)): yield res
            return
        forecast_match = re.match(r'^[^\w]?(晴天钟)(\s+.*)?', msg)
        if forecast_match:
            async for res in self._handle_cloud_forecast(event, forecast_match.group(2) or ""): yield res
            return

    async def _handle_set_location(self, event: AstrMessageEvent, arg_str: str):
        key = self._get_storage_key(event); args = arg_str.split()
        try:
            if args[0].lower() == "-c":
                lat, lon = float(args[1]), float(args[2]); loc_data = {"lat": lat, "lon": lon, "name": f"坐标({lat},{lon})"}
            else:
                async with httpx.AsyncClient() as client:
                    url = f"https://restapi.amap.com/v3/geocode/geo?address={' '.join(args)}&key={self.config.get('amap_key')}"
                    res = (await client.get(url)).json()
                    if res["status"] == "1" and res["geocodes"]:
                        lng, lat = map(float, res["geocodes"][0]["location"].split(",")); loc_data = {"lat": lat, "lon": lng, "name": " ".join(args)}
                    else: raise ValueError("地名解析失败")
            await self.put_kv_data(key, loc_data)
            yield event.plain_result(f"📍 位置已设置为：{loc_data['name']}")
        except Exception as e: yield event.plain_result(f"❌ 失败: {e}")
        event.stop_event()

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    async def _handle_cloud_forecast(self, event: AstrMessageEvent, arg_str: str):
        args = arg_str.split(); days, night_only, target_place = 3, False, None
        i = 0
        while i < len(args):
            if args[i] == "-d" and i+1 < len(args):
                try: days = int(args[i+1]); i += 2; continue
                except: pass
            if args[i] == "-n": night_only = True; i += 1; continue
            target_place = " ".join(args[i:]); break
        
        if target_place:
            try:
                async with httpx.AsyncClient() as client:
                    url = f"https://restapi.amap.com/v3/geocode/geo?address={target_place}&key={self.config.get('amap_key')}"
                    res = (await client.get(url)).json()
                    lng, lat = map(float, res["geocodes"][0]["location"].split(","))
                    location = {"lat": lat, "lon": lng, "name": target_place}
            except: yield event.plain_result("❌ 解析失败。"); return
        else:
            key = self._get_storage_key(event); location = await self.get_kv_data(key, None)
            if not location: yield event.plain_result("❌ 请先设置位置。"); return

        if not self.env_ready: yield event.plain_result("⌛ 环境正在准备..."); return

        lat, lon = location["lat"], location["lon"]
        try:
            async with httpx.AsyncClient() as client:
                m_params = {"latitude": lat, "longitude": lon, "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m", "daily": "sunrise,sunset", "models": "ecmwf_ifs025", "forecast_days": days, "timezone": "auto"}
                t_url = f"https://www.7timer.info/bin/astro.php?lon={lon}&lat={lat}&ac=0&unit=metric&output=json&tzshift=0"
                m_res, t_res = await asyncio.gather(client.get("https://api.open-meteo.com/v1/forecast", params=m_params, timeout=10.0), client.get(t_url, timeout=10.0))
                m_data, t_data = m_res.json(), {}
                if t_res.status_code == 200 and "dataseries" in t_res.text: t_data = t_res.json()

                hourly, daily = m_data["hourly"], m_data["daily"]
                transitions = sorted([{"time": datetime.datetime.fromisoformat(s), "label": f"日出 {datetime.datetime.fromisoformat(s).strftime('%H:%M')}" if s in daily["sunrise"] else f"日落 {datetime.datetime.fromisoformat(s).strftime('%H:%M')}"} for s in daily.get("sunrise", []) + daily.get("sunset", [])], key=lambda x: x["time"])
                
                timer_map = {}
                if t_data.get("init"):
                    init_dt = datetime.datetime.strptime(t_data["init"], "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)
                    for entry in t_data.get("dataseries", []):
                        timer_map[int((init_dt + datetime.timedelta(hours=entry["timepoint"])).timestamp())] = entry

                def get_color(v, type):
                    if type == "temp":
                        if v < -10: return "#003258", "on-dark"
                        if v <= 0: return "#D1E4FF", "on-light"
                        if v <= 8: return "#C4E7CB", "on-light"
                        if v <= 16: return "#A8C7FF", "on-light"
                        if v <= 24: return "#E8F0FF", "on-light"
                        if v <= 30: return "#FFECB3", "on-light"
                        if v <= 36: return "#FFDAD6", "on-light"
                        return "#BA1A1A" if v <= 38 else "#4A148C", "on-dark"
                    if type in ["seeing", "trans"]:
                        if v == -9999 or v == "/": return "#E5E7EB", "on-light"
                        if v <= 2: return "#10b981", "on-dark"
                        if v <= 4: return "#3b82f6", "on-dark"
                        if v <= 6: return "#f59e0b", "on-light"
                        return "#BA1A1A", "on-dark"
                    if type == "humi":
                        if v < 40: return "#C4E7CB", "on-light"
                        if v < 70: return "#A8C7FF", "on-light"
                        if v < 90: return "#FFECB3", "on-light"
                        return "#BA1A1A", "on-dark"
                    return "#E5E7EB", "on-light"

                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                processed_rows = []
                
                for i in range(len(hourly["time"])):
                    dt = datetime.datetime.fromisoformat(hourly["time"][i])
                    if dt.replace(tzinfo=None) < start_threshold.replace(tzinfo=None): continue
                    if night_only and not (dt.hour >= 18 or dt.hour <= 6): continue
                    
                    # 1. 生成常规数据行
                    ts = int(dt.astimezone(datetime.timezone.utc).timestamp())
                    match = None
                    min_d = 999999
                    for t_ts, val in timer_map.items():
                        d = abs(ts - t_ts)
                        if d < min_d and d <= 7200: min_d = d; match = val
                    
                    s_v = (match["seeing"] if match["seeing"] != -9999 else "/") if match else "/"
                    tr_v = (match["transparency"] if match["transparency"] != -9999 else "/") if match else "/"
                    
                    t_v, d_v, w_v, h_v = hourly['temperature_2m'][i], hourly['dew_point_2m'][i], hourly['wind_speed_10m'][i], hourly['relative_humidity_2m'][i]
                    processed_rows.append({
                        "is_transition": False, "day": dt.strftime("%d"), "hour": dt.strftime("%H"),
                        "temp_val": int(t_v), "temp_color": get_color(t_v, "temp")[0], "temp_cls": get_color(t_v, "temp")[1],
                        "dew_val": int(d_v), "dew_color": "#ef4444" if d_v < 2 else ("#f59e0b" if d_v <= 5 else "#10b981"), "dew_cls": "on-dark" if d_v < 2 or d_v > 5 else "on-light",
                        "humi_val": int(h_v), "humi_color": get_color(h_v, "humi")[0], "humi_cls": get_color(h_v, "humi")[1],
                        "wind_val": int(w_v), "wind_color": "#BA1A1A" if w_v > 35 else ("#FFECB3" if w_v > 20 else ("#A8C7FF" if w_v > 10 else "#C4E7CB")), "wind_cls": "on-dark" if w_v > 35 else "on-light",
                        "seeing_val": s_v, "seeing_color": get_color(s_v, "seeing")[0], "seeing_cls": get_color(s_v, "seeing")[1],
                        "trans_val": tr_v, "trans_color": get_color(tr_v, "trans")[0], "trans_cls": get_color(tr_v, "trans")[1],
                        "total": hourly["cloud_cover"][i], "low": hourly["cloud_cover_low"][i], "mid": hourly["cloud_cover_mid"][i], "high": hourly["cloud_cover_high"][i]
                    })

                    # 2. 检查并插入交替行 (放在当前小时之后)
                    for trans in transitions:
                        t_time = trans["time"].replace(tzinfo=None)
                        if dt.replace(tzinfo=None) <= t_time < (dt + datetime.timedelta(hours=1)).replace(tzinfo=None):
                            processed_rows.append({"is_transition": True, "label": trans["label"], "day": dt.strftime("%d")})

                seen_days = set()
                for row in processed_rows:
                    if row["day"] not in seen_days:
                        row["is_first_of_day"] = True
                        row["day_rowspan"] = len([r for r in processed_rows if r["day"] == row["day"]])
                        seen_days.add(row["day"])

                theme_mode = "night-mode" if (now.replace(tzinfo=None) > datetime.datetime.fromisoformat(daily["sunset"][0]).replace(tzinfo=None) or now.replace(tzinfo=None) < datetime.datetime.fromisoformat(daily["sunrise"][0]).replace(tzinfo=None)) else "light-mode"
                render_data = {"lat": round(lat, 4), "lon": round(lon, 4), "location_name": location["name"], "ref_time": now.strftime("%Y-%m-%d %H:%M"), "rows": processed_rows, "theme_mode": theme_mode, "model_name": "ECMWF+7Timer"}
                
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(viewport={"width": 850, "height": 800}, device_scale_factor=3)
                    page = await context.new_page()
                    await page.set_content(Template(self._load_template()).render(**render_data))
                    await asyncio.sleep(1.5); save_path = os.path.abspath(f"data/plugin_data/astrbot_plugin_astroassist/forecast_final.png")
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    await page.screenshot(path=save_path, full_page=True); await browser.close()
                yield event.chain_result([Comp.Image(file=save_path)]); event.stop_event()
        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报异常: {str(e)}")

    async def terminate(self): pass
