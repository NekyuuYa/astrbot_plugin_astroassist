from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain
import httpx
import datetime

@register("astrbot_plugin_astroassist", "NekyuuYa", "晴天钟助手 - 调用 Open-Meteo 获取 ECMWF 云量数据", "0.1.6")
class AstroAssist(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        pass

    def _get_storage_key(self, event: AstrMessageEvent):
        """根据是否为群聊返回对应的存储 Key"""
        group_id = event.message_obj.group_id
        if group_id:
            return f"location_group_{group_id}"
        else:
            return f"location_user_{event.get_sender_id()}"

    @filter.command("设置定位")
    async def set_location(self, event: AstrMessageEvent, lat: float, lon: float):
        """设置当前群组或私聊的观测点经纬度。"""
        key = self._get_storage_key(event)
        location_data = {"lat": lat, "lon": lon}
        await self.put_kv_data(key, location_data)
        
        target = "当前群组" if event.message_obj.group_id else "您"
        yield event.plain_result(f"📍 {target}的定位已设置成功：纬度 {lat}, 经度 {lon}")
        event.stop_event()

    @filter.command("云量预报")
    async def cloud_forecast(self, event: AstrMessageEvent):
        """获取当前绑定的 ECMWF 云量预报（从当前-2h开始，合并转发形式）。"""
        key = self._get_storage_key(event)
        location = await self.get_kv_data(key, None)
        
        if not location:
            yield event.plain_result("❌ 请先使用 /设置定位 [纬度] [经度] 设置位置。")
            event.stop_event()
            return

        lat = location["lat"]
        lon = location["lon"]

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
            "models": "ecmwf_ifs025",
            "forecast_days": 3,
            "timezone": "auto"
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                c_total = hourly.get("cloud_cover", [])
                c_low = hourly.get("cloud_cover_low", [])
                c_mid = hourly.get("cloud_cover_mid", [])
                c_high = hourly.get("cloud_cover_high", [])

                if not times:
                    yield event.plain_result("❌ 未能获取到有效的云量数据。")
                    event.stop_event()
                    return

                # 计算起始时间：当前时间 - 2小时
                now = datetime.datetime.now(datetime.timezone.utc)
                start_threshold = now - datetime.timedelta(hours=2)

                # 构建消息链，每一天的预报作为一个 Plain 节点，这通常会触发平台的合并转发
                chain = [Plain(f"☁️ ECMWF 云量预报 ({lat}, {lon})\n格式: 时间 | 总 | 低 | 中 | 高\n--------------------------")]
                
                current_day_text = ""
                current_day_str = ""
                
                for i in range(len(times)):
                    dt = datetime.datetime.fromisoformat(times[i])
                    # Open-Meteo 返回的时间通常是不带时区的，但 API 请求中用了 timezone=auto
                    # 简单起见，这里假设 dt 与 start_threshold 的比较逻辑
                    if dt.replace(tzinfo=datetime.timezone.utc) < start_threshold:
                        continue
                    
                    day_str = dt.strftime("%m-%d")
                    time_str = dt.strftime("%H")
                    
                    if day_str != current_day_str:
                        if current_day_text:
                            chain.append(Plain(current_day_text.strip()))
                        current_day_text = f"\n📅 {day_str}\n"
                        current_day_str = day_str
                    
                    line = f"{time_str}时 | {c_total[i]:>2}% | {c_low[i]:>2}% | {c_mid[i]:>2}% | {c_high[i]:>2}%"
                    current_day_text += line + "\n"

                if current_day_text:
                    chain.append(Plain(current_day_text.strip()))

                if len(chain) > 1:
                    yield event.chain_result(chain)
                else:
                    yield event.plain_result("❌ 没有可用的预报数据。")
                
                event.stop_event()

        except Exception as e:
            logger.error(f"获取天气数据失败: {e}")
            yield event.plain_result(f"❌ 获取预报失败: {str(e)}")
            event.stop_event()

    async def terminate(self):
        pass
