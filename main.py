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

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 专业天文气象看板", "0.8.3")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.env_ready = False

    async def initialize(self):
        is_env_ready = await self.get_kv_data("env_v077_ok", False)
        if is_env_ready: self.env_ready = True
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

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    def _load_template(self):
        curr_dir = os.path.dirname(__file__)
        template_path = os.path.join(curr_dir, "template.html")
        with open(template_path, "r", encoding="utf-8") as f: return f.read()

    @filter.command("设置位置")
    async def set_location(self, event: AstrMessageEvent, *args):
        key = self._get_storage_key(event)
        if not args:
            yield event.plain_result("❌ 用法：/设置位置 <地名> 或 /设置位置 -c <纬度> <经度>")
            return
        try:
            if args[0].lower() in ["-c", "-C"]:
                lat, lon = float(args[1]), float(args[2])
                loc_data = {"lat": lat, "lon": lon, "name": f"坐标({lat},{lon})"}
            else:
                key_amap = self.config.get("amap_key")
                async with httpx.AsyncClient() as client:
                    url = f"https://restapi.amap.com/v3/geocode/geo?address={' '.join(args)}&key={key_amap}"
                    res = (await client.get(url)).json()
                    if res["status"] == "1" and res["geocodes"]:
                        lng, lat = map(float, res["geocodes"][0]["location"].split(","))
                        loc_data = {"lat": lat, "lon": lng, "name": " ".join(args)}
                    else: raise ValueError("地名解析失败")
            await self.put_kv_data(key, loc_data)
            yield event.plain_result(f"📍 位置已设置为：{loc_data['name']}")
        except Exception as e: yield event.plain_result(f"❌ 失败: {e}")
        event.stop_event()

    @filter.command("晴天钟")
    async def cloud_forecast(self, event: AstrMessageEvent, *args):
        days, night_only, target_place = 3, False, None
        i = 0
        while i < len(args):
            if args[i] == "-d" and i+1 < len(args): days = int(args[i+1]); i += 2; continue
            if args[i] == "-n": night_only = True; i += 1; continue
            target_place = " ".join(args[i:]); break
        
        key = self._get_storage_key(event)
        location = await self.get_kv_data(key, None) if not target_place else None
        if target_place:
            key_amap = self.config.get("amap_key")
            async with httpx.AsyncClient() as client:
                url = f"https://restapi.amap.com/v3/geocode/geo?address={target_place}&key={key_amap}"
                res = (await client.get(url)).json()
                if res["status"] == "1" and res["geocodes"]:
                    lng, lat = map(float, res["geocodes"][0]["location"].split(","))
                    location = {"lat": lat, "lon": lng, "name": target_place}
        
        if not location: yield event.plain_result("❌ 请先设置位置。"); return
        lat, lon = location["lat"], location["lon"]

        try:
            async with httpx.AsyncClient() as client:
                # 1. 并行请求 Open-Meteo 和 7Timer
                meteo_url = "https://api.open-meteo.com/v1/forecast"
                meteo_params = {
                    "latitude": lat, "longitude": lon,
                    "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m",
                    "daily": "sunrise,sunset", "models": "ecmwf_ifs025", "forecast_days": days, "timezone": "auto"
                }
                timer_url = f"http://www.7timer.info/bin/api.pl?lon={lon}&lat={lat}&product=astro&output=json"
                
                meteo_task = client.get(meteo_url, params=meteo_params, timeout=10.0)
                timer_task = client.get(timer_url, timeout=10.0)
                
                meteo_res, timer_res = await asyncio.gather(meteo_task, timer_task)
                m_data = meteo_res.json()
                t_data = timer_res.json()
                
                hourly, daily = m_data["hourly"], m_data["daily"]
                timer_series = t_data.get("dataseries", [])
                
                # 计算昼夜交替
                transitions = sorted([datetime.datetime.fromisoformat(s) for s in daily.get("sunrise", []) + daily.get("sunset", [])])
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

                def get_astro_color(val):
                    # 7Timer 1-8 色阶 (1最好，8最差)
                    if val <= 2: return "#10b981", "on-dark" # 极佳
                    if val <= 4: return "#3b82f6", "on-dark" # 良好
                    if val <= 6: return "#f59e0b", "on-light" # 一般
                    return "#BA1A1A", "on-dark" # 差

                all_rows, day_counts = [], {}
                
                # 寻找 7Timer 的起始时间 (7Timer 默认是 UTC)
                init_time_str = t_data.get("init", "2024010100")
                init_dt = datetime.datetime.strptime(init_time_str, "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)

                for i in range(len(hourly["time"])):
                    dt = datetime.datetime.fromisoformat(hourly["time"][i]).replace(tzinfo=None)
                    if dt < start_threshold.replace(tzinfo=None): continue
                    if night_only and not (dt.hour >= 18 or dt.hour <= 6): continue
                    
                    # 匹配 7Timer 数据 (3小时一组)
                    astro_seeing, astro_trans = 5, 5 # 默认值
                    dt_utc = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))) # 假设本地东八
                    hours_diff = (dt_utc - init_dt).total_seconds() / 3600
                    timer_idx = int(hours_diff / 3)
                    if 0 <= timer_idx < len(timer_series):
                        astro_seeing = timer_series[timer_idx].get("seeing", 5)
                        astro_trans = timer_series[timer_idx].get("transparency", 5)

                    is_transition, trans_type = False, ""
                    for t in transitions:
                        if dt <= t.replace(tzinfo=None) < dt + datetime.timedelta(hours=1):
                            is_transition, trans_type = True, ("SUNRISE" if t in [datetime.datetime.fromisoformat(x) for x in daily["sunrise"]] else "SUNSET")
                            break

                    day_key = dt.strftime("%d")
                    day_counts[day_key] = day_counts.get(day_key, 0) + 1
                    t_v, d_v, w_v = hourly['temperature_2m'][i], hourly['dew_point_2m'][i], hourly['wind_speed_10m'][i]
                    
                    all_rows.append({
                        "day": day_key, "hour": dt.strftime("%H"),
                        "is_transition": is_transition, "transition_type": trans_type,
                        "temp_val": int(t_v), "temp_color": get_temp_color(t_v)[0], "temp_cls": get_temp_color(t_v)[1],
                        "dew_val": int(d_v), "dew_color": "#ef4444" if d_v < 2 else ("#f59e0b" if d_v <= 5 else "#10b981"),
                        "dew_cls": "on-dark" if d_v < 2 or d_v > 5 else "on-light",
                        "humi_val": int(hourly['relative_humidity_2m'][i]),
                        "wind_val": int(w_v), "wind_color": "#BA1A1A" if w_v > 35 else ("#FFECB3" if w_v > 20 else ("#A8C7FF" if w_v > 10 else "#C4E7CB")),
                        "wind_cls": "on-dark" if w_v > 35 else "on-light",
                        "seeing_val": astro_seeing, "seeing_color": get_astro_color(astro_seeing)[0], "seeing_cls": get_astro_color(astro_seeing)[1],
                        "trans_val": astro_trans, "trans_color": get_astro_color(astro_trans)[0], "trans_cls": get_astro_color(astro_trans)[1],
                        "total": hourly["cloud_cover"][i], "low": hourly["cloud_cover_low"][i], "mid": hourly["cloud_cover_mid"][i], "high": hourly["cloud_cover_high"][i],
                        "is_first_of_day": False
                    })

                seen_days = set()
                for row in all_rows:
                    if row["day"] not in seen_days:
                        row["is_first_of_day"], row["day_rowspan"] = True, day_counts[row["day"]]
                        seen_days.add(row["day"])

                theme_mode, theme_label = "light-mode", "日间"
                try:
                    today_sunset = datetime.datetime.fromisoformat(daily["sunset"][0]).replace(tzinfo=None)
                    today_sunrise = datetime.datetime.fromisoformat(daily["sunrise"][0]).replace(tzinfo=None)
                    if now.replace(tzinfo=None) > today_sunset or now.replace(tzinfo=None) < today_sunrise: theme_mode, theme_label = "night-mode", "夜间"
                except: pass

                render_data = {
                    "lat": round(lat, 4), "lon": round(lon, 4), "location_name": location["name"],
                    "ref_time": now.strftime("%Y-%m-%d %H:%M"), "rows": all_rows,
                    "theme_mode": theme_mode, "theme_label": theme_label, "model_name": "ECMWF+7Timer"
                }
                
                template_str = self._load_template()
                html_content = Template(template_str).render(**render_data)
                save_path = os.path.abspath(f"data/plugin_data/astrbot_plugin_astroassist/forecast_v83.png")
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(viewport={"width": 1250, "height": 800}, device_scale_factor=3)
                    page = await context.new_page()
                    await page.set_content(html_content)
                    await asyncio.sleep(1.5)
                    await page.screenshot(path=save_path, full_page=True)
                    await browser.close()

                yield event.chain_result([Comp.Image(file=save_path)])
                event.stop_event()
        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报异常: {str(e)}")

    async def terminate(self): pass
