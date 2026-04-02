from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
import httpx
import datetime
import os
import base64
from PIL import Image as PILImage, ImageDraw, ImageFont

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.5.3")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        pass

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    def _get_font(self, size):
        # 常见 Linux 路径
        paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ]
        for p in paths:
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()

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
                    yield event.plain_result("❌ 数据获取为空。")
                    event.stop_event()
                    return

                # --- Pillow 工业级渲染逻辑 (2倍高清) ---
                SCALE = 2
                WIDTH = 600 * SCALE
                HEADER_H = 120 * SCALE
                ROW_H = 50 * SCALE
                DAY_H = 40 * SCALE
                
                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                rows = []
                for i in range(len(times)):
                    dt = datetime.datetime.fromisoformat(times[i])
                    if dt < start_threshold: continue
                    rows.append({"dt": dt, "total": c_total[i], "low": c_low[i], "mid": c_mid[i], "high": c_high[i]})

                if not rows:
                    yield event.plain_result("❌ 暂无可用预报。")
                    event.stop_event()
                    return

                # 计算总高度
                days_count = len(set(r["dt"].date() for r in rows))
                total_h = HEADER_H + (days_count * DAY_H) + (len(rows) * ROW_H) + (20 * SCALE)
                
                img = PILImage.new("RGB", (WIDTH, total_h), "#FDFBFF")
                draw = ImageDraw.Draw(img)
                
                # 字体加载
                f_title = self._get_font(28 * SCALE)
                f_sub = self._get_font(14 * SCALE)
                f_day = self._get_font(18 * SCALE)
                f_val = self._get_font(16 * SCALE)
                
                # 绘制页眉 (M3 蓝)
                draw.rectangle([0, 0, WIDTH, HEADER_H], fill="#E8F0FF")
                draw.text((30*SCALE, 30*SCALE), "🔭 晴天钟预报", font=f_title, fill="#1A1C1E")
                draw.text((30*SCALE, 75*SCALE), f"LOC: {lat}, {lon} | REF: {now.strftime('%H:%M')} | ECMWF", font=f_sub, fill="#44474E")

                y = HEADER_H
                curr_date = None
                
                for r in rows:
                    d = r["dt"].date()
                    if curr_date != d:
                        curr_date = d
                        draw.rectangle([0, y, WIDTH, y + DAY_H], fill="#F1F5F9")
                        draw.text((30*SCALE, y + 8*SCALE), f"📅 {d.strftime('%m月%d日')}", font=f_day, fill="#1A73E8")
                        y += DAY_H
                        # 表头
                        th_y = y - 5*SCALE
                        draw.text((150*SCALE, th_y), "TOTAL", font=f_sub, fill="#94A3B8")
                        draw.text((260*SCALE, th_y), "LOW", font=f_sub, fill="#94A3B8")
                        draw.text((370*SCALE, th_y), "MID", font=f_sub, fill="#94A3B8")
                        draw.text((480*SCALE, th_y), "HIGH", font=f_sub, fill="#94A3B8")
                    
                    # 绘制行
                    draw.text((30*SCALE, y + 15*SCALE), r["dt"].strftime("%H:00"), font=f_val, fill="#475569")
                    
                    # 云量列渲染 (方框进度条)
                    for idx, key in enumerate(["total", "low", "mid", "high"]):
                        val = r[key]
                        x_base = 150*SCALE + (idx * 110 * SCALE)
                        color = "#BA1A1A" if val > 80 else ("#F59E0B" if val > 20 else "#10B981")
                        # 绘制背景框
                        box_w, box_h = 80*SCALE, 30*SCALE
                        draw.rounded_rectangle([x_base, y + 10*SCALE, x_base + box_w, y + 10*SCALE + box_h], radius=6*SCALE, fill="#F0F0F8")
                        # 绘制填充
                        fill_w = (val / 100) * box_w
                        if fill_w > 0:
                            draw.rounded_rectangle([x_base, y + 10*SCALE, x_base + fill_w, y + 10*SCALE + box_h], radius=6*SCALE, fill=color)
                        # 绘制文字 (居中)
                        t_color = "#FFFFFF" if val > 50 else "#1A1C1E"
                        txt = str(val)
                        # 简单的文字居中逻辑
                        draw.text((x_base + 25*SCALE, y + 14*SCALE), txt, font=f_val, fill=t_color)

                    y += ROW_H
                    draw.line([30*SCALE, y, WIDTH - 30*SCALE, y], fill="#F0F0F8")

                # 保存并 Base64 发送
                save_path = "data/plugin_data/astrbot_plugin_astroassist/final_v053.png"
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                img.save(save_path, "PNG")
                
                with open(save_path, "rb") as f:
                    img_base64 = base64.b64encode(f.read()).decode("utf-8")
                
                yield event.chain_result([Image.fromBase64(img_base64)])
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报生成失败: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
