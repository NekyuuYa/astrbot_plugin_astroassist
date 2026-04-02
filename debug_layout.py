import os
from jinja2 import Template
import datetime

def generate_debug_html():
    # 1. 模拟 3 天数据
    rows = []
    start_dt = datetime.datetime(2026, 4, 3, 0, 0)
    
    # 模拟日出日落点
    transitions_times = [
        start_dt + datetime.timedelta(hours=6, minutes=53), # Day 1 日出
        start_dt + datetime.timedelta(hours=18, minutes=24), # Day 1 日落
        start_dt + datetime.timedelta(days=1, hours=6, minutes=52), # Day 2 日出
        start_dt + datetime.timedelta(days=1, hours=18, minutes=25), # Day 2 日落
        start_dt + datetime.timedelta(days=2, hours=6, minutes=51), # Day 3 日出
        start_dt + datetime.timedelta(days=2, hours=18, minutes=26), # Day 3 日落
    ]

    for d_idx in range(3):
        day_date = (start_dt + datetime.timedelta(days=d_idx)).strftime("%d")
        day_rows = []
        
        for h_idx in range(24):
            current_hour_dt = start_dt + datetime.timedelta(days=d_idx, hours=h_idx)
            
            # 插入交替行
            for t in transitions_times:
                if current_hour_dt <= t < current_hour_dt + datetime.timedelta(hours=1):
                    type_str = "日出" if t.hour < 12 else "日落"
                    day_rows.append({
                        "is_transition": True, 
                        "label": f"{type_str} {t.strftime('%H:%M')}", 
                        "day": day_date
                    })

            # 普通数据行
            day_rows.append({
                "is_transition": False, "day": day_date, "hour": f"{h_idx:02d}",
                "temp_val": 15 + h_idx % 10, "temp_color": "#A8C7FF", "temp_cls": "on-light",
                "dew_val": 5, "dew_color": "#f59e0b", "dew_cls": "on-light",
                "humi_val": 60, "humi_color": "#A8C7FF", "humi_cls": "on-light",
                "wind_val": 15, "wind_color": "#A8C7FF", "wind_cls": "on-light",
                "seeing_val": 4, "seeing_color": "#3b82f6", "seeing_cls": "on-dark",
                "trans_val": 3, "trans_color": "#10b981", "trans_cls": "on-dark",
                "total": 20, "low": 0, "mid": 10, "high": 5
            })

        # 设置该天第一行的 rowspan
        day_rows[0]["is_first_of_day"] = True
        day_rows[0]["day_rowspan"] = len(day_rows)
        rows.extend(day_rows)

    render_data = {
        "lat": 31.9, "lon": 118.8,
        "location_name": "南京市（模拟）",
        "ref_time": "2026-04-03 08:00",
        "rows": rows,
        "theme_mode": "light-mode",
        "model_name": "ECMWF DEBUG"
    }

    # 2. 读取模板并渲染
    template_path = "template.html"
    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    tmpl = Template(template_str)
    html_content = tmpl.render(**render_data)

    # 3. 输出文件
    output_path = "debug_layout_full.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"✅ 完整调试文件已生成: {os.path.abspath(output_path)}")

if __name__ == "__main__":
    generate_debug_html()
