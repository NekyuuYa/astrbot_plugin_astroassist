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
import re
import math

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 专业天文气象看板", "0.8.0")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.env_ready = False

    async def initialize(self):
        """环境检测与初始化"""
        is_env_ready = await self.get_kv_data("env_v077_ok", False)
        if is_env_ready:
            self.env_ready = True
            return
        
        # 尝试在后台检查/安装 Playwright
        asyncio.create_task(self._ensure_env())

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
        except:
            pass

    # --- 辅助工具：坐标转换 ---
    def _gcj02_to_wgs84(self, lng, lat):
        """高德/腾讯 (GCJ-02) 转 WGS-84"""
        a = 6378245.0
        ee = 0.00669342162296594323
        def transformlat(lng, lat):
            ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
            ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
            ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
            ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * lat * math.pi / 30.0) * 2.0 / 3.0
            return ret
        def transformlng(lng, lat):
            ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
            ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
            ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
            ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
            return ret
        dlat = transformlat(lng - 105.0, lat - 35.0)
        dlng = transformlng(lng - 105.0, lat - 35.0)
        radlat = lat / 180.0 * math.pi
        magic = math.sin(radlat)
        magic = 1 - ee * magic * magic
        sqrtmagic = math.sqrt(magic)
        dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
        dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
        return lng - dlng, lat - dlat

    def _bd09_to_gcj02(self, bd_lon, bd_lat):
        """百度 (BD-09) 转 高德 (GCJ-02)"""
        x_pi = 3.14159265358979324 * 3000.0 / 180.0
        x = bd_lon - 0.0065
        y = bd_lat - 0.006
        z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * x_pi)
        theta = math.atan2(y, x) - 0.000003 * math.cos(x * x_pi)
        return z * math.cos(theta), z * math.sin(theta)

    async def _amap_geocode(self, address):
        """高德地理编码"""
        key = self.config.get("amap_key")
        if not key: raise ValueError("未配置 amap_key")
        async with httpx.AsyncClient() as client:
            url = f"https://restapi.amap.com/v3/geocode/geo?address={address}&key={key}"
            res = await client.get(url)
            data = res.json()
            if data["status"] == "1" and data["geocodes"]:
                location = data["geocodes"][0]["location"] # "lng,lat"
                lng, lat = map(float, location.split(","))
                return self._gcj02_to_wgs84(lng, lat) # 转 WGS-84
            raise ValueError(f"高德地图未找到该地名: {address}")

    # --- 指令处理 ---

    @filter.command("设置位置")
    async def set_location(self, event: AstrMessageEvent, *args):
        """
        /设置位置 <地名>
        /设置位置 -c <纬度> <经度> [坐标系: wgs84/gcj02/bd09]
        """
        key = self._get_storage_key(event)
        
        if not args:
            yield event.plain_result("❌ 用法：/设置位置 <地名> 或 /设置位置 -c <纬度> <经度> [坐标系]")
            return

        try:
            if args[0].lower() in ["-c", "-C"]:
                if len(args) < 3:
                    yield event.plain_result("❌ 坐标设置格式：-c <纬度> <经度> [wgs84/gcj02/bd09]")
                    return
                lat, lon = float(args[1]), float(args[2])
                coord_type = args[3].lower() if len(args) > 3 else "wgs84"
                
                # 转换到 WGS-84
                final_lng, final_lat = lon, lat
                if coord_type == "bd09":
                    final_lng, final_lat = self._gcj02_to_wgs84(*self._bd09_to_gcj02(lon, lat))
                elif coord_type == "gcj02":
                    final_lng, final_lat = self._gcj02_to_wgs84(lon, lat)
                
                loc_data = {"lat": final_lat, "lon": final_lng, "name": f"坐标({lat},{lon})"}
            else:
                address = " ".join(args)
                lng, lat = await self._amap_geocode(address)
                loc_data = {"lat": lat, "lon": lng, "name": address}

            await self.put_kv_data(key, loc_data)
            yield event.plain_result(f"📍 位置已设置为：{loc_data['name']}\n(WGS-84: {loc_data['lat']:.4f}, {loc_data['lon']:.4f})")
        except Exception as e:
            yield event.plain_result(f"❌ 设置失败: {str(e)}")
        event.stop_event()

    @filter.command("晴天钟")
    async def cloud_forecast(self, event: AstrMessageEvent, *args):
        """
        /晴天钟 [-d 天数] [-n] [地名]
        示例：/晴天钟 -d 1 -n 北京
        """
        # 解析参数
        days = 3
        night_only = False
        target_place = None
        
        i = 0
        while i < len(args):
            if args[i] == "-d" and i + 1 < len(args):
                try: days = int(args[i+1]); i += 2; continue
                except: pass
            if args[i] == "-n":
                night_only = True; i += 1; continue
            target_place = " ".join(args[i:]); break
        
        # 获取基础位置
        if target_place:
            try:
                lng, lat = await self._amap_geocode(target_place)
                location = {"lat": lat, "lon": lng, "name": target_place}
            except Exception as e:
                yield event.plain_result(f"❌ 临时地名解析失败: {e}")
                return
        else:
            key = self._get_storage_key(event)
            location = await self.get_kv_data(key, None)
            if not location:
                yield event.plain_result("❌ 请先使用 /设置位置 设置默认位置，或直接输入地名查询。")
                return

        lat, lon = location["lat"], location["lon"]

        try:
            async with httpx.AsyncClient() as client:
                url = "https://api.open-meteo.com/v1/forecast"
                params = {
                    "latitude": lat, "longitude": lon,
                    "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m",
                    "daily": "sunrise,sunset", # 获取日出日落
                    "models": "ecmwf_ifs025", "forecast_days": days, "timezone": "auto"
                }
                response = await client.get(url, params=params, timeout=10.0)
                data = response.json()
                
                hourly = data["hourly"]
                daily = data["daily"]
                
                # 计算当前主题
                now = datetime.datetime.now()
                theme_mode = "light-mode"
                theme_label = "日间"
                
                if self.config.get("auto_theme", True):
                    # 获取今日日落和日出（简单判断法）
                    try:
                        # Open-Meteo 返回的是 ISO 字符串列表
                        today_sunset = datetime.datetime.fromisoformat(daily["sunset"][0])
                        today_sunrise = datetime.datetime.fromisoformat(daily["sunrise"][0])
                        # 如果当前时间在日落后或日出前，则为夜间
                        # 考虑到时区，直接对比 ISO 字符串
                        now_str = now.isoformat()
                        if now > today_sunset or now < today_sunrise:
                            theme_mode = "night-mode"
                            theme_label = "夜间"
                    except: pass

                # 处理数据行
                start_threshold = now - datetime.timedelta(hours=2)
                all_rows, day_counts = [], {}
                
                # 辅助：色阶逻辑
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

                for i in range(len(hourly["time"])):
                    dt = datetime.datetime.fromisoformat(hourly["time"][i])
                    if dt < start_threshold: continue
                    
                    # 夜间模式过滤：简单判断 18:00 - 06:00
                    if night_only:
                        if not (dt.hour >= 18 or dt.hour <= 6): continue
                    
                    day_key = dt.strftime("%d")
                    day_counts[day_key] = day_counts.get(day_key, 0) + 1
                    
                    t_v, d_v, w_v = hourly['temperature_2m'][i], hourly['dew_point_2m'][i], hourly['wind_speed_10m'][i]
                    all_rows.append({
                        "day": day_key, "hour": dt.strftime("%H"),
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

                if not all_rows:
                    yield event.plain_result("❌ 当前条件下无预报数据（可能是使用了 -n 过滤掉了白天的所有数据）。")
                    return

                # 渲染准备
                render_data = {
                    "lat": round(lat, 4), "lon": round(lon, 4), "location_name": location["name"],
                    "ref_time": now.strftime("%Y-%m-%d %H:%M"), "rows": all_rows,
                    "theme_mode": theme_mode, "theme_label": theme_label, "model_name": "ECMWF IFS"
                }
                
                template_path = os.path.join(os.path.dirname(__file__), "template.html")
                with open(template_path, "r", encoding="utf-8") as f:
                    template_str = f.read()
                
                html_content = Template(template_str).render(**render_data)
                save_path = os.path.abspath(f"data/plugin_data/astrbot_plugin_astroassist/forecast_v8.png")
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                # 本地渲染
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(viewport={"width": 1100, "height": 800}, device_scale_factor=3)
                    page = await context.new_page()
                    await page.set_content(html_content)
                    await asyncio.sleep(1.5)
                    await page.screenshot(path=save_path, full_page=True)
                    await browser.close()

                yield event.chain_result([Comp.Image(file=save_path)])
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报执行异常: {str(e)}")

    async def terminate(self): pass
