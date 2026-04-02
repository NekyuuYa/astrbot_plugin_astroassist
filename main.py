from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image
import httpx
import datetime
import os
from PIL import Image as PILImage, ImageDraw, ImageFont

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.3.0")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        pass

    def _get_storage_key(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        return f"location_group_{group_id}" if group_id else f"location_user_{event.get_sender_id()}"

    @filter.command("设置定位")
    async def set_location(self, event: AstrMessageEvent, lat: float, lon: float):
        """设置当前会话的经纬度定位。"""
        key = self._get_storage_key(event)
        await self.put_kv_data(key, {"lat": lat, "lon": lon})
        yield event.plain_result(f"📍 定位设置成功：{lat}, {lon}")
        event.stop_event()

    @filter.command("云量预报")
    async def cloud_forecast(self, event: AstrMessageEvent):
        """获取当前绑定的 ECMWF 云量预报图（使用 Pillow 引擎渲染）。"""
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
                times, c_total = hourly.get("time", []), hourly.get("cloud_cover", [])
                c_low, c_mid, c_high = hourly.get("cloud_cover_low", []), hourly.get("cloud_cover_mid", []), hourly.get("cloud_cover_high", [])

                if not times:
                    yield event.plain_result("❌ 数据获取为空。")
                    event.stop_event()
                    return

                # 时间过滤：当前-2小时
                now = datetime.datetime.now()
                start_threshold = now - datetime.timedelta(hours=2)
                
                rows_to_render = []
                for i in range(len(times)):
                    dt = datetime.datetime.fromisoformat(times[i])
                    if dt < start_threshold: continue
                    rows_to_render.append({
                        "dt": dt,
                        "total": c_total[i],
                        "low": c_low[i],
                        "mid": c_mid[i],
                        "high": c_high[i]
                    })

                if not rows_to_render:
                    yield event.plain_result("❌ 没有可用的预报数据。")
                    event.stop_event()
                    return

                # --- Pillow 绘图开始 ---
                WIDTH = 480
                ROW_HEIGHT = 40
                HEADER_HEIGHT = 80
                DAY_LABEL_HEIGHT = 30
                
                # 计算需要绘制的日期分隔线数量
                days_set = sorted(list(set(r["dt"].date() for r in rows_to_render)))
                total_height = HEADER_HEIGHT + (len(days_set) * DAY_LABEL_HEIGHT) + (len(rows_to_render) * ROW_HEIGHT) + 40
                
                img = PILImage.new("RGB", (WIDTH, total_height), "#FFFFFF")
                draw = ImageDraw.Draw(img)
                
                # 尝试加载中文字体，否则使用默认
                font_path = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc" # 常见的 Linux 中文字体
                if not os.path.exists(font_path):
                    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
                
                try:
                    title_font = ImageFont.truetype(font_path, 24)
                    label_font = ImageFont.truetype(font_path, 14)
                    data_font = ImageFont.truetype(font_path, 16)
                    day_font = ImageFont.truetype(font_path, 18)
                except:
                    title_font = label_font = data_font = day_font = ImageFont.load_default()

                # 绘制页眉
                draw.rectangle([0, 0, WIDTH, HEADER_HEIGHT], fill="#0f172a")
                draw.text((20, 15), "🔭 晴天钟预报 (ECMWF)", font=title_font, fill="#FFFFFF")
                draw.text((20, 50), f"📍 {lat}, {lon} | 生成时间: {now.strftime('%H:%M')}", font=label_font, fill="#94a3b8")

                y = HEADER_HEIGHT
                current_date = None
                
                for row in rows_to_render:
                    # 检查日期变化，绘制日期头
                    row_date = row["dt"].date()
                    if current_date != row_date:
                        current_date = row_date
                        draw.rectangle([0, y, WIDTH, y + DAY_LABEL_HEIGHT], fill="#f1f5f9")
                        draw.text((20, y + 5), f"📅 {current_date.strftime('%m月%d日')}", font=day_font, fill="#334155")
                        y += DAY_LABEL_HEIGHT
                        # 绘制表头
                        draw.text((20, y), "时间", font=label_font, fill="#94a3b8")
                        draw.text((100, y), "总云", font=label_font, fill="#94a3b8")
                        draw.text((180, y), "低", font=label_font, fill="#94a3b8")
                        draw.text((260, y), "中", font=label_font, fill="#94a3b8")
                        draw.text((340, y), "高", font=label_font, fill="#94a3b8")
                        y += 20
                    
                    # 绘制数据行
                    time_str = row["dt"].strftime("%H:00")
                    draw.text((20, y + 10), time_str, font=data_font, fill="#475569")
                    
                    # 总云量带颜色
                    total_val = row["total"]
                    color = "#ef4444" if total_val > 70 else ("#f59e0b" if total_val > 20 else "#10b981")
                    draw.text((100, y + 10), f"{total_val}%", font=data_font, fill=color)
                    # 绘制小进度条
                    draw.rectangle([100, y + 32, 160, y + 36], fill="#f1f5f9")
                    draw.rectangle([100, y + 32, 100 + (total_val * 0.6), y + 36], fill=color)
                    
                    draw.text((180, y + 10), f"{row['low']}%", font=data_font, fill="#334155")
                    draw.text((260, y + 10), f"{row['mid']}%", font=data_font, fill="#334155")
                    draw.text((340, y + 10), f"{row['high']}%", font=data_font, fill="#334155")
                    
                    y += ROW_HEIGHT
                    draw.line([20, y, WIDTH - 20, y], fill="#f8fafc")

                # 绘制页脚
                draw.text((WIDTH//2, y + 10), "AstroAssist 晴天钟助手", font=label_font, fill="#cbd5e1", anchor="mt")

                # 保存并发送图片
                save_path = f"data/plugin_data/astrbot_plugin_astroassist/temp_forecast.png"
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                img.save(save_path)
                
                yield event.image_result(save_path)
                event.stop_event()

        except Exception as e:
            logger.error(f"AstroAssist Error: {e}")
            yield event.plain_result(f"❌ 预报失败: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
