#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组织健康诊断 - 报告渲染脚本
从 analysis.json (+可选 insights.json) 渲染三个管理者视角的 HTML 看板报告。

三种看板（同一份数据，三种决策视角）:
  - CEO看板   (--type ceo)    : 战略投资视角 —— 救谁？改什么政策？看结构不看细节
  - VP看板    (--type vp)     : 经营与人才培养视角 —— 谁要辅导？资源给谁？看对比找抓手
  - 经理人看板 (--type manager): 带教与行为改进视角 —— 我这季度做什么？看行为给动作

用法:
  python render_report.py --analysis analysis.json --type ceo    --outdir reports
  python render_report.py --analysis analysis.json --type vp     --target all --outdir reports
  python render_report.py --analysis analysis.json --type manager --target "个人事业部/国内运营部/国内用户增长" --outdir reports
  可选: --insights insights.json  (由 AI 撰写的诊断洞察与行动建议)

insights.json 结构:
{
  "ceo":     {"summary": "...", "findings": [...], "actions": [...]},
  "vp":      {"<一级事业部名>": {"summary": "...", "findings": [...], "actions": [...]}},
  "manager": {"<一级/二级/三级>": {"summary": "...", "findings": [...],
                "root_causes": {"Q4": ["原因1", ...]},          // 可选，缺省用内置库
                "conversation": {"Q4": "对话问题..."},           // 可选，缺省用内置库
                "actions_30": [...], "actions_60": [...], "actions_90": [...]}}
}
"""
import argparse
import html as _html
import json
import math
import re
from pathlib import Path
import statistics

BAND_COLOR = {"优势": "#1a7f37", "良好": "#57a05c", "关注": "#d4a72c", "预警": "#cf222e", None: "#8b949e"}
BAND_BG = {"优势": "#dcf2e3", "良好": "#eaf5eb", "关注": "#fcf3d7", "预警": "#fde8e9", None: "#f0f2f4"}
DIM_ORDER = ["成长发展", "团队归属", "管理支持", "基本需求"]  # 阶梯自上而下

# 默认行业常模（5 分制）。当数据/配置未提供常模时作为占位参考；用户可通过 meta["benchmark"] 覆盖。
DEFAULT_BENCHMARK = {"基本需求": 4.05, "管理支持": 3.85, "团队归属": 3.75, "成长发展": 3.65}
DIM_COLOR = {"基本需求": "#a7c0cb", "管理支持": "#bbbbe0", "团队归属": "#e2bcbc", "成长发展": "#e3cf9c"}

# 敬业/怠工行业基准（盖洛普全球常模 2023-2024）
INDUSTRY_BENCHMARK = {
    "global": {"engaged": 23, "disengaged": 18, "label": "全球均值"},
    "china":  {"engaged": 15, "disengaged": 25, "label": "中国均值"},
    "tech":   {"engaged": "20-30", "disengaged": "15-20", "label": "科技/互联网"},
}
INDUSTRY_BENCHMARK_SOURCE = "盖洛普全球常模（2023-2024）"
DEFAULT_BENCHMARK_REGION = "tech"  # 科技/互联网（公司所属行业）
SCALE_MAX = 5.0

# 经理人看板：题项三块分组（按"经理人能直接动什么"重组，而非问卷原始四维度）
BLOCKS = {
    "我的管理行为": {"questions": ["Q3", "Q4", "Q5", "Q6"],
                   "desc": "这四题直接反映你的日常管理实践，是你最能立刻改变的", "color": "#8a8aa6"},
    "团队氛围": {"questions": ["Q7", "Q8", "Q9", "Q10"],
               "desc": "团队凝聚力与心理安全感，需要你刻意营造", "color": "#c98a8a"},
    "基础保障": {"questions": ["Q1", "Q2", "Q11", "Q12"],
               "desc": "工作条件与发展机会，部分需向上争取资源", "color": "#6c8a96"},
}

# 经理人看板内置：低分题根因假设 / 1:1对话指南 / 行动提示（可被 insights 覆盖）
ROOT_CAUSES = {
    "Q1": ["干部没有讲清楚要解决什么问题、什么结果算做好", "多项任务同时推进，优先级不清晰", "业务变化后未及时重新对齐目标和标准"],
    "Q2": ["工具、权限、信息或协作资源没有跟上", "跨团队协作长期卡住", "遇到障碍时未能及时协调解决"],
    "Q3": ["员工长期在不擅长的工作上消耗", "分配任务只看谁有时间，没看谁更适合", "成员自己不清楚自己的优势"],
    "Q4": ["做得好被认为是应该的，只有出错时才获得反馈", "认可集中在少数人，默默做事的人被忽略", "干部只在绩效考核时集中反馈，平时很少交流"],
    "Q5": ["只谈任务、不谈困难，员工遇到问题不愿求助", "对成员状态了解不足，忙起来就忽略了个别关注", "缺乏日常连接，只在出问题时才沟通"],
    "Q6": ["只派任务、不做辅导，发展沟通只发生在年终", "成长机会集中在少数人", "发展路径不清晰，看不到上升通道"],
    "Q7": ["会上沉默、会下抱怨，员工认为'说了也没用'", "意见提了但没有闭环反馈", "干部在充分听取意见前就过早给出结论"],
    "Q8": ["目标拆解时没讲清'为什么做'", "成员只看到小目标看不到全局", "各自完成分工，缺少主动补位和共同担当"],
    "Q9": ["目标拆解时没讲清'为什么做'", "成员只看到小目标看不到全局", "使命传达停留在口号层"],
    "Q10": ["对问题睁一只眼闭一只眼，低标准行为被长期容忍", "协作流程有摩擦，信息不共享", "出现问题后相互推责，缺少互相兜底的文化"],
    "Q11": ["员工不知道自己取得了哪些进步、下一步提升什么", "干部只派任务，很少提供辅导和成长反馈", "发展沟通只发生在年终，主要围绕绩效结论"],
    "Q12": ["工作内容长期重复，优秀员工看不到新的挑战", "没有通过新项目、复杂任务创造成长机会", "成长只靠自学，缺乏指引和资源支持"],
}
CONVERSATION_GUIDE = {
    "Q1": "和成员逐一对齐：你认为'做好这份工作'的标准是什么？我写的标准你看得到吗？哪里对不上？",
    "Q2": "问：你最近干活卡在哪个工具/信息/权限上？我帮你拆掉哪个最堵的？",
    "Q3": "问：这周你做的事里哪件最让你来劲？我们能不能把这类活多排一点给你？",
    "Q4": "（自查+询问）过去7天我认可过谁？问成员：什么样的认可对你最有效——当众、私下、还是具体的事后？",
    "Q5": "聊点工作之外的：最近生活上有什么在消耗你精力的？我能怎么支持？",
    "Q6": "问：你未来1-2年想往哪个方向走？现在的工作安排能不能给你铺路？缺什么我来争取。",
    "Q7": "问：你觉得团队开会时你的意见被听进去了吗？哪次你觉得说了没用？为什么？",
    "Q8": "问：你觉得团队开会时你的意见被听进去了吗？哪次你觉得说了没用？为什么？",
    "Q9": "讲清楚这个季度的目标为什么重要、连到公司哪件事；问成员：你做的事你觉得自己在拼什么？",
    "Q10": "问：你觉得团队里谁的工作质量你最放心/最不放心？卡点通常在哪？",
    "Q11": "问：在团队里你有没有能说心里话的人？没有的话，我帮你搭一个这样的连接。",
    "Q12": "和成员做一次发展意愿沟通：过去一年你在哪些方面有进步？接下来想往哪个方向发展（专业/管理/跨领域）？我能通过什么项目或任务给你创造成长机会？",
}
ACTION_HINT = {
    "Q1": "每周明确三项最高优先级及交付标准，与成员逐一确认双方理解一致",
    "Q2": "主动识别并解决工具、权限、信息和协作障碍，明确哪些需要向上协调",
    "Q3": "根据能力和优势重新分配任务，通过业务项目让成员发挥长处",
    "Q4": "在项目复盘中具体认可关键行为和业务价值，说明谁做了什么、创造了什么价值",
    "Q5": "保持日常沟通，1:1中了解成员的状态、障碍和支持需求，不只谈任务",
    "Q6": "提供反馈和辅导，通过挑战性任务帮助成员看见成长方向",
    "Q7": "重要决策前固定征集不同意见，对员工建议给予明确回应（采纳或不采纳都讲清原因）",
    "Q8": "反复对齐共同目标，讲清'为什么做'，让成员看到全局而非只是小目标",
    "Q9": "讲清质量底线，及时纠偏，明确什么行为不可接受",
    "Q10": "建立信息共享和协作机制，复盘时先解决问题再划分责任",
    "Q11": "定期反馈成员的进步、差距和下一阶段重点，不只等年终评价",
    "Q12": "通过新项目、复杂任务和跨团队协作创造成长机会，在1对1中明确下一阶段能力重点",
}


def esc(s):
    return _html.escape(str(s)) if s is not None else ""


def fmt(v, suppressed=False):
    if suppressed or v is None:
        return "*"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def fmt_delta(d, suppressed=False):
    if suppressed or d is None:
        return ""
    color = "#1a7f37" if d > 0 else ("#cf222e" if d < 0 else "#57606a")
    sign = "+" if d > 0 else ""
    arrow = "▲" if d > 0.04 else ("▼" if d < -0.04 else "—")
    return f'<span style="color:{color};font-size:12px;white-space:nowrap">{arrow} {sign}{d:.2f}</span>'


def bar(mean, band_name, width=120, suppressed=False):
    if suppressed or mean is None:
        return '<span class="muted">*</span>'
    pct = max(0, min(100, mean / SCALE_MAX * 100))
    c = BAND_COLOR.get(band_name, "#8b949e")
    return (f'<div class="bar-wrap" style="width:{width}px">'
            f'<div class="bar" style="width:{pct:.0f}%;background:{c}"></div></div>')


def band_chip(band_name, suppressed=False):
    if suppressed or not band_name:
        return '<span class="muted">*</span>'
    return (f'<span class="chip" style="background:{BAND_BG[band_name]};'
            f'color:{BAND_COLOR[band_name]}">{band_name}</span>')


def distribution_bar(dist, n):
    """分数分布堆叠条 + 低/中/高计数。n<6 时不显示（保护匿名），只给提示"""
    if dist is None or n < 6:
        return '<span class="muted" style="font-size:11px">样本较小，仅显示均值</span>'
    total = dist["low"] + dist["mid"] + dist["high"]
    if total == 0:
        return ""
    lp, mp, hp = dist["low"] / total * 100, dist["mid"] / total * 100, dist["high"] / total * 100
    return (f'<div class="dist-stack" style="width:130px">'
            f'<div style="width:{lp:.0f}%;background:#cf222e"></div>'
            f'<div style="width:{mp:.0f}%;background:#d4a72c"></div>'
            f'<div style="width:{hp:.0f}%;background:#1a7f37"></div></div>'
            f'<span class="muted" style="font-size:11px;margin-left:6px">'
            f'低{dist["low"]}·中{dist["mid"]}·高{dist["high"]}</span>')


def polarization_flag(dist, n):
    """极性判断：两极分化 / 共识偏低 / 分布均匀"""
    if dist is None or n < 6:
        return ""
    total = dist["low"] + dist["mid"] + dist["high"]
    if total == 0:
        return ""
    lp, hp = dist["low"] / total, dist["high"] / total
    if lp >= 0.25 and hp >= 0.25:
        return '<span class="chip" style="background:#fde8e9;color:#cf222e">两极分化</span>'
    if lp >= 0.4:
        return '<span class="chip" style="background:#fcf3d7;color:#d4a72c">共识偏低</span>'
    return '<span class="chip" style="background:#eaf5eb;color:#1a7f37">分布均匀</span>'


def css():
    return """
:root{--ink:#111827;--sub:#6b7280;--line:#e5e7eb;--bg:#f3f4f6;--card:#ffffff;--accent:#2563eb;--shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);--shadow-lg:0 4px 16px rgba(0,0,0,.08)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;color:var(--ink);background:var(--bg);line-height:1.5;font-size:14px}
.page{max-width:1120px;margin:0 auto;padding:18px 22px 44px}
.report-head{background:linear-gradient(135deg,#1e3a5f,#2563eb);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:18px;box-shadow:var(--shadow-lg)}
.report-head h1{font-size:23px;font-weight:700;margin-bottom:4px;letter-spacing:.5px}
.report-head .sub{opacity:.8;font-size:13px}
.report-head .tag{display:inline-block;background:rgba(255,255,255,.15);border-radius:20px;padding:3px 14px;font-size:12px;margin-right:8px;margin-top:12px;backdrop-filter:blur(4px)}
.report-head .chain{font-size:12px;color:#fff;opacity:.85;margin-top:8px}
.sec-head{display:flex;align-items:center;gap:10px;margin:20px 0 9px}
.sec-head .sec-num{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:8px;background:var(--accent);color:#fff;font-size:14px;font-weight:700;flex-shrink:0}
.sec-head h2{font-size:18px;font-weight:700;border:none;padding:0;margin:0}
.sec-head .sec-sub{font-size:12px;color:var(--sub);margin-left:4px}
h2{font-size:17px;margin:20px 0 9px;padding-left:12px;border-left:4px solid var(--accent);font-weight:700}
h3{font-size:14px;margin:16px 0 8px;color:var(--sub);font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 16px;box-shadow:var(--shadow);transition:box-shadow .2s}
.card:hover{box-shadow:var(--shadow-lg)}
.card .label{font-size:12px;color:var(--sub);margin-bottom:6px;font-weight:500}
.card .value{font-size:30px;font-weight:800;letter-spacing:-.5px}
.card .foot{font-size:12px;color:var(--sub);margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
th{background:#f9fafb;font-size:12px;color:var(--sub);font-weight:600;padding:7px 10px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--line)}
td{padding:7px 10px;border-top:1px solid #f3f4f6;font-size:12.5px;vertical-align:middle}
tr:hover td{background:#f9fafb}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.center{text-align:center}
.bar-wrap{height:8px;background:#e8ebef;border-radius:4px;overflow:hidden;display:inline-block;vertical-align:middle}
.bar{height:100%;border-radius:4px}
.chip{display:inline-block;border-radius:20px;padding:2px 12px;font-size:12px;font-weight:600;white-space:nowrap}
.muted{color:#9ca3af}
.qtext{color:var(--sub);font-size:12px}
.ladder{display:flex;flex-direction:column;align-items:center;gap:6px;margin:10px 0 4px}
.ladder .step{display:flex;align-items:center;gap:16px}
.ladder .block{color:#fff;border-radius:10px;padding:12px 20px;text-align:center;box-shadow:var(--shadow)}
.ladder .block .dn{font-size:14px;font-weight:600}
.ladder .block .dv{font-size:22px;font-weight:800}
.ladder .side{width:300px;font-size:12px;color:var(--sub)}
.heat-cell{text-align:center;font-weight:600;border-radius:6px;padding:5px 0;font-size:13px}
.insight{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:13px 17px;margin-bottom:10px;box-shadow:var(--shadow)}
.insight ul{margin:8px 0 0 18px}
.insight li{margin:5px 0}
.hlk{color:#b45309;font-weight:700}
.hl-bullet{color:var(--accent);font-weight:700;margin-right:4px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.vp-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0;align-items:start}
.vp-cell.vp-span{grid-column:1/-1}
.vp-cell{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 18px;box-shadow:var(--shadow);overflow:hidden}
.vp-cell .ct{font-size:15px;font-weight:700;color:#1f2937;margin:0 0 10px 0;display:flex;align-items:center;gap:7px}
.vp-cell .ct small{font-weight:400;font-size:11px;color:var(--sub)}
.vp-cell .ct .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:none}
.vp-cell .ladder{gap:10px 0;padding:4px 0;display:grid;grid-template-columns:1fr 210px;align-items:center}
.vp-cell .ladder .step{display:grid;grid-template-columns:1fr 210px;gap:16px;align-items:center;width:100%;grid-column:1/-1}
.vp-cell .ladder .side{width:210px;grid-column:2;text-align:left;font-size:12px;color:var(--sub);line-height:1.55;display:flex;flex-direction:column;justify-content:center}
.vp-cell .ladder .side b{color:#1f2937;font-size:13px;margin-bottom:3px}
.vp-cell .ladder .block{grid-column:1;justify-self:center;max-width:100%;width:auto;margin:0;padding:9px 14px;box-sizing:border-box}
.vp-cell .ladder .block .dn{font-size:13px}
.vp-cell .ladder .block .dv{font-size:20px}
.ladder--inline .step{display:flex;justify-content:center;margin:0}
.ladder--inline .block{display:flex;flex-direction:column;align-items:center;text-align:center;gap:1px;padding:10px 14px;max-width:100%;box-sizing:border-box;color:#2f3b45}
.ladder--inline .block .dn{color:#1f2937}
.ladder--inline .block .dv{color:#1f2937}
.ladder--inline .block .bcore{font-size:12px;font-weight:600;opacity:1;margin-top:3px;line-height:1.35;color:#374151}
.ladder--inline .block .bqs{font-size:11px;opacity:1;line-height:1.45;margin-top:1px;color:#52606d}
.ladder--inline .block .chip{background:rgba(255,255,255,.82);color:#1f2937;border:none;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.vp-cell .ladder--inline{gap:8px 0;display:block;padding:4px 0}
.vp-cell .ladder--inline .step{grid-template-columns:none;gap:0}
.vp-cell .ladder--inline .block{justify-self:center;grid-column:auto}
.vp-wide{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px;box-shadow:var(--shadow);margin:16px 0}
.vp-wide .ct{font-size:16px;font-weight:700;color:#1f2937;margin:0 0 14px 0;display:flex;align-items:center;gap:7px}
.vp-wide .ct .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:none}
.vp-actions{margin:0;padding-left:20px}
.vp-actions li{margin:8px 0;font-size:14px;line-height:1.65;color:#374151}
.vfold{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);margin:14px 0;overflow:hidden}
.vfold>summary{font-size:15px;font-weight:700;color:#1f2937;padding:14px 18px;cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between}
.vfold>summary::-webkit-details-marker{display:none}
.vfold>summary::after{content:"▾";color:var(--sub);transition:transform .2s}
.vfold[open]>summary::after{transform:rotate(180deg)}
.vfold>summary:hover{background:#f9fafb}
.vfold .vfold-body{padding:4px 18px 18px 18px}
.hl{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 16px;box-shadow:var(--shadow)}
.hl .ttl{font-size:13px;font-weight:700;margin-bottom:8px}
.footnote{margin-top:26px;padding-top:12px;border-top:1px solid var(--line);font-size:11px;color:#9ca3af;line-height:1.8}
.legend{font-size:12px;color:var(--sub);margin:6px 0 10px}
.legend .chip{margin-right:6px}
.matrix{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}
.qcell{border:1px solid var(--line);border-radius:10px;padding:13px 15px;min-height:84px;box-shadow:var(--shadow)}
.qcell h4{font-size:13px;font-weight:700;margin-bottom:8px}
.qcell .bu-chip{display:inline-block;background:#f9fafb;border:1px solid var(--line);border-radius:8px;padding:4px 10px;font-size:12px;margin:3px 4px 3px 0}
.axis-lbl{font-size:11px;color:var(--sub);text-align:center;margin:4px 0 6px}
.blocks{display:grid;grid-template-columns:1fr;gap:14px}
.block-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;border-left:4px solid var(--accent);box-shadow:var(--shadow)}
.block-card h4{font-size:14px;font-weight:700;margin-bottom:2px}
.block-card .bdesc{font-size:12px;color:var(--sub);margin-bottom:10px}
.block-card table{border:none;box-shadow:none}
.block-card td{border:none;border-bottom:1px solid #f3f4f6}
.timeline{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
.tl-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px;box-shadow:var(--shadow)}
.tl-card .tl-h{font-size:13px;font-weight:700;margin-bottom:8px}
.tl-card ul{margin-left:16px}
.tl-card li{margin:4px 0;font-size:13px}
.watermark{display:inline-block;background:#fef3c7;color:#92400e;border:1px dashed #f59e0b;border-radius:6px;padding:2px 10px;font-size:11px;margin-left:8px}
.dist-stack{display:inline-flex;height:14px;border-radius:4px;overflow:hidden;vertical-align:middle}
.dist-stack div{height:100%}
.pos-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin-bottom:10px;box-shadow:var(--shadow)}
.pos-card .posv{font-size:24px;font-weight:800}
.privacy-note{background:#fffbeb;border:1px solid #fcd34d;border-radius:10px;padding:12px 16px;font-size:12px;color:#92400e;margin:12px 0}
.entry-head{text-align:center;padding:36px 20px 8px;margin-bottom:8px}
.entry-head h1{font-size:28px;font-weight:800;letter-spacing:-.5px;color:var(--ink)}
.entry-head .sub{font-size:14px;color:var(--sub);margin-top:8px}
.entry-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:28px 0}
.entry-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 22px;box-shadow:var(--shadow);transition:all .2s;text-decoration:none;color:inherit;display:flex;flex-direction:column;min-height:170px;cursor:pointer}
.entry-card:hover{box-shadow:var(--shadow-lg);transform:translateY(-2px)}
.entry-icon{font-size:38px;line-height:1;margin-bottom:14px}
.entry-ctitle{font-size:16px;font-weight:700;margin-bottom:8px;color:var(--ink)}
.entry-cdesc{font-size:13px;color:var(--sub);line-height:1.7;flex:1;margin-bottom:16px}
.entry-tag{display:inline-block;border-radius:20px;padding:4px 14px;font-size:12px;font-weight:600;align-self:flex-start}
.entry-foot{text-align:center;margin-top:32px;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:var(--sub);line-height:1.8}
.entry-mini{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 22px;box-shadow:var(--shadow);transition:all .2s;text-decoration:none;color:inherit;display:block}
.entry-mini:hover{box-shadow:var(--shadow-lg);transform:translateY(-1px)}
.entry-mini .em-title{font-size:14px;font-weight:700;margin-bottom:4px}
.entry-mini .em-sub{font-size:12px;color:var(--sub)}
.entry-mini .em-stats{display:flex;gap:14px;margin-top:10px;font-size:12px;color:var(--sub)}
.entry-mini .em-stats span{font-weight:700;color:var(--ink)}
.entry-section{display:none;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;margin-top:12px;box-shadow:var(--shadow)}
.entry-section:target{display:block}
@media (max-width:780px){.entry-grid{grid-template-columns:1fr}.two-col{grid-template-columns:1fr}.vp-grid{grid-template-columns:1fr}.vp-cell .ladder .step{grid-template-columns:1fr;gap:6px}.vp-cell .ladder .side{width:auto;text-align:center;grid-column:auto}.vp-cell .ladder .block{justify-self:stretch;grid-column:auto}}
.hero-banner{display:grid;grid-template-columns:auto 1fr;gap:18px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 24px;box-shadow:var(--shadow-lg);margin-bottom:8px}
.hero-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:14px}
.hero-stat{text-align:left;position:relative}
.hero-stat .hs-tip{position:absolute;top:0;right:0;width:15px;height:15px;border-radius:50%;background:#e5e7eb;color:#6b7280;font-size:10px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;cursor:help}
.hero-stat .hs-tip-pop{position:absolute;top:20px;right:0;width:210px;background:#1f2937;color:#fff;font-size:11px;line-height:1.55;padding:8px 10px;border-radius:8px;white-space:normal;text-align:left;opacity:0;visibility:hidden;transition:opacity .12s;z-index:30;box-shadow:0 6px 18px rgba(0,0,0,.18)}
.hero-stat .hs-tip:hover .hs-tip-pop,.hero-stat .hs-tip:focus .hs-tip-pop{opacity:1;visibility:visible}
.hero-stat .hs-tip-pop::after{content:"";position:absolute;top:-5px;right:5px;border:5px solid transparent;border-bottom-color:#1f2937}
.hero-stat .hs-label{font-size:12px;color:var(--sub);margin-bottom:4px}
.hero-stat .hs-value{font-size:26px;font-weight:800;letter-spacing:-.5px}.hero-stat .hs-value.textual{font-size:18px;letter-spacing:0}
.hero-stat .hs-foot{font-size:11px;color:var(--sub);margin-top:2px}
.hero-source{font-size:11px;color:var(--sub);text-align:right;margin:2px 4px 10px 0}
.db-table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
.db-table th{background:#f8fafc;color:#6b7280;font-weight:600;padding:8px 10px;text-align:left;border-bottom:1px solid #e5e7eb}
.db-table td{padding:8px 10px;border-bottom:1px solid #f3f4f6;color:#374151}
.db-table tbody tr:hover td{background:#fafbfc}
.etype-chart{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow);margin:10px 0}
.etype-head{margin-bottom:16px}
.etype-title{font-size:16px;font-weight:700}
.etype-sub{font-size:12px;color:var(--sub);margin-top:2px}
.etype-row{display:grid;grid-template-columns:170px 1fr 60px;gap:12px;align-items:center;padding:7px 8px;border-bottom:1px solid #f3f4f6}
.etype-row:last-child{border-bottom:none}
.etype-row.current{background:#eff6ff;border-radius:8px}
.etype-name{font-size:13px;font-weight:600}
.etype-name .n{font-size:11px;color:var(--sub);font-weight:400;margin-left:6px}
.etype-bar-wrap{height:26px;border-radius:6px;overflow:hidden;display:flex;background:#f3f4f6}
.etype-seg{display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:600;white-space:nowrap;min-width:0}
.etype-seg span{padding:0 6px;overflow:hidden;text-overflow:ellipsis}
.etype-neg{text-align:right;font-size:14px;font-weight:700;color:var(--ink)}
.etype-neg.high{color:#dc2626}
.etype-legend{display:flex;flex-wrap:wrap;gap:16px;margin-top:16px;font-size:12px;color:var(--sub)}
.etype-legend span{display:inline-flex;align-items:center;gap:6px}
.etype-dot{width:12px;height:12px;border-radius:3px}
.qbar-row{display:grid;grid-template-columns:140px 1fr 50px 90px;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid #f3f4f6}
.qbar-row:last-child{border:none}
.qbar-label{font-size:13px;font-weight:500}
.qbar-track{height:24px;background:#f3f4f6;border-radius:6px;overflow:hidden;position:relative}
.qbar-fill{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:8px;color:#fff;font-size:11px;font-weight:600}
.qbar-score{font-size:14px;font-weight:700;text-align:right;font-variant-numeric:tabular-nums}
.radar-wrap{width:100%}
.radar-legend{display:flex;justify-content:center;gap:18px;margin-top:8px;font-size:12px;color:#4b5563;flex-wrap:wrap}
.radar-legend span{display:inline-flex;align-items:center;gap:6px}
.radar-legend i{display:inline-block;width:12px;height:12px;border-radius:3px;flex:none}
.radar-src{font-size:11px;color:#9ca3af;text-align:center;margin-top:6px;line-height:1.5}
@media print{body{background:#fff}.page{padding:10mm}.report-head,.hero-banner,.card,.insight,.hl,.qcell,.block-card,.tl-card,.pos-card{box-shadow:none!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
 *{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def header_html(title, subtitle, tags, chain=""):
    tag_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in tags if t)
    chain_html = f'<div class="chain">组织链路：{esc(chain)}</div>' if chain else ""
    return (f'<div class="report-head"><h1>{esc(title)}</h1>'
            f'<div class="sub">{esc(subtitle)}</div>{tag_html}{chain_html}</div>')


def summary_cards(unit, meta, extra_cards=()):
    sup = unit["suppressed"]
    cards = [
        ("综合健康指数", fmt(unit["grand_mean"], sup), meta_band_foot(unit, sup)),
        ("参与人数", str(unit["n"]), "有效问卷"),
    ]
    if unit.get("grand_mean_delta") is not None and not sup:
        d = unit["grand_mean_delta"]
        cards.append(("环比变化", f"{'+' if d > 0 else ''}{d:.2f}",
                      f"上期 {unit.get('grand_mean_prev', '-')}"))
    eng = unit["engagement"]
    if not sup:
        cards.append(("敬业员工占比", f"{eng['engaged_pct']:.0f}%", "个人均分 ≥ 4.0"))
        cards.append(("怠工员工占比", f"{eng['disengaged_pct']:.0f}%", "个人均分 < 3.0"))
    cards.extend(extra_cards)
    inner = "".join(
        f'<div class="card"><div class="label">{esc(l)}</div>'
        f'<div class="value">{v}</div><div class="foot">{f}</div></div>'
        for l, v, f in cards)
    return f'<div class="cards">{inner}</div>'


def meta_band_foot(unit, sup):
    if sup:
        return "样本不足，已抑制"
    return f'满分 5 分 · {band_chip(unit["grand_band"])}'


def ladder_html(unit, meta, inline_text=False):
    """盖洛普式四层阶梯金字塔。色块宽度随维度均值动态变化：分数越高色块越长。
    inline_text=True 时把核心问题/题目/评级放到色块内部，适合窄卡片。"""
    sup = unit["suppressed"]
    max_w = 500
    min_w = 120
    widths = {"成长发展": 200, "团队归属": 300, "管理支持": 400, "基本需求": 500}
    steps = []
    cls = "ladder ladder--inline" if inline_text else "ladder"
    for d in DIM_ORDER:
        if not meta["dimensions"][d]["questions"]:
            continue
        dv = unit["dimensions"][d]
        mean_s = fmt(dv["mean"], sup)
        delta_s = fmt_delta(dv.get("delta"), sup)
        core = meta["dimensions"][d]["core_question"]
        qs = "、".join(meta["dimensions"][d]["questions"])
        # 色块宽度按均值动态计算：mean/5 * max_w，最低 min_w；抑制时用固定宽度
        if sup or dv["mean"] is None:
            w = widths[d]
        else:
            w = max(min_w, int(dv["mean"] / 5.0 * max_w))
        if inline_text:
            steps.append(
                f'<div class="step">'
                f'<div class="block" style="width:{w}px;background:{DIM_COLOR[d]}">'
                f'<div class="btitle"><span class="dn">{esc(d)}</span> <span class="dv">{mean_s}</span> {delta_s}</div>'
                f'<div class="bcore">{esc(core)}</div>'
                f'<div class="bqs">{esc(qs)} · {band_chip(dv["band"], sup)}</div>'
                f'</div></div>')
        else:
            steps.append(
                f'<div class="step">'
                f'<div class="block" style="width:{w}px;background:{DIM_COLOR[d]}">'
                f'<span class="dn">{esc(d)}</span> <span class="dv">{mean_s}</span> {delta_s}</div>'
                f'<div class="side"><b>{esc(core)}</b><br>{esc(qs)} · {band_chip(dv["band"], sup)}</div>'
                f'</div>')
    return f'<div class="{cls}">{"".join(steps)}</div>'


def dimension_radar(unit, meta, compare_unit=None, compare_label=None, benchmark=None, benchmark_label="行业常模", benchmark_source=None):
    """四维度得分雷达图（SVG）。CEO 用于公司 vs 行业常模，经理人用于本部门 vs 公司。"""
    dims = ["基本需求", "成长发展", "团队归属", "管理支持"]
    sup = unit.get("suppressed", False)
    values = []
    for d in dims:
        dv = unit["dimensions"].get(d, {})
        values.append(None if sup or dv.get("mean") is None else dv["mean"])

    comp_values = []
    if compare_unit and not compare_unit.get("suppressed", False):
        for d in dims:
            dv = compare_unit["dimensions"].get(d, {})
            comp_values.append(dv.get("mean"))
    else:
        comp_values = [None] * 4

    bench_values = []
    if benchmark:
        for d in dims:
            bench_values.append(benchmark.get(d))
    else:
        bench_values = [None] * 4

    W, H = 520, 340
    cx, cy = W / 2, H / 2 + 6
    r = 110
    max_v = SCALE_MAX

    # 四个轴角度：上 / 右 / 下 / 左（对应 dims 顺序）
    angles = [-90, 0, 90, 180]

    def point(v, i):
        if v is None:
            return None
        a = angles[i] * 3.14159 / 180
        ratio = v / max_v
        return cx + ratio * r * __import__('math').cos(a), cy + ratio * r * __import__('math').sin(a)

    # 网格：同心菱形（1-5）
    grid = ""
    for level in range(1, 6):
        pts = []
        for i, a in enumerate(angles):
            rad = a * 3.14159 / 180
            x = cx + (level / max_v) * r * __import__('math').cos(rad)
            y = cy + (level / max_v) * r * __import__('math').sin(rad)
            pts.append(f"{x:.1f},{y:.1f}")
        grid += f'<polygon points="{" ".join(pts)}" fill="none" stroke="#e5e7eb" stroke-width="1"/>'
        if level == max_v:
            continue
        # 右侧小刻度标签
        grid += f'<text x="{cx+4:.1f}" y="{cy-(level/max_v)*r+4:.1f}" font-size="9" fill="#d1d5db">{level}</text>'

    # 轴线 + 维度标签
    axes = ""
    for i, d in enumerate(dims):
        rad = angles[i] * 3.14159 / 180
        x2 = cx + r * __import__('math').cos(rad)
        y2 = cy + r * __import__('math').sin(rad)
        axes += f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        lx = cx + (r + 22) * __import__('math').cos(rad)
        ly = cy + (r + 22) * __import__('math').sin(rad)
        anchor = "middle"
        dy = 0
        if i == 1:  # right
            anchor = "start"
        elif i == 3:  # left
            anchor = "end"
        if i == 0:
            dy = -4
        elif i == 2:
            dy = 4
        axes += f'<text x="{lx:.1f}" y="{ly+dy:.1f}" text-anchor="{anchor}" font-size="12" fill="#6b7280">{esc(d)}</text>'

    # 多边形
    def poly(vals, fill, stroke, stroke_dash=None):
        pts = []
        for i, v in enumerate(vals):
            p = point(v, i)
            if p is None:
                return "", ""
            pts.append(f"{p[0]:.1f},{p[1]:.1f}")
        dash = f' stroke-dasharray="{stroke_dash}"' if stroke_dash else ""
        path = f'<polygon points="{" ".join(pts)}" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>'
        dots = "".join(
            f'<circle cx="{point(v,i)[0]:.1f}" cy="{point(v,i)[1]:.1f}" r="3.5" fill="{stroke}"/>'
            for i, v in enumerate(vals) if v is not None)
        return path, dots

    bench_poly, bench_dots = poly(bench_values, "rgba(107,114,128,.12)", "#6b7280", "4 4") if any(v is not None for v in bench_values) else ("", "")
    comp_poly, comp_dots = poly(comp_values, "rgba(156,163,175,.18)", "#9ca3af", "5 3") if any(v is not None for v in comp_values) else ("", "")
    unit_poly, unit_dots = poly(values, "rgba(59,130,246,.18)", "#3b82f6")

    # 数值标签（仅本单位）
    labels = ""
    for i, (v, d) in enumerate(zip(values, dims)):
        if v is None:
            continue
        p = point(v, i)
        offset = 10
        lx = p[0]
        ly = p[1]
        if i == 0:  # top
            ly -= offset
        elif i == 2:  # bottom
            ly += offset + 4
        elif i == 1:  # right
            lx += offset
        else:  # left
            lx -= offset
        labels += f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#2563eb">{v:.2f}</text>'

    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;margin:0 auto;display:block">'
           f'{grid}{axes}{bench_poly}{bench_dots}{comp_poly}{comp_dots}{unit_poly}{unit_dots}{labels}</svg>')

    # 图例
    legend_items = [("#3b82f6", unit.get("name", "本单位"))]
    if compare_label and any(v is not None for v in comp_values):
        legend_items.append(("#9ca3af", compare_label))
    if benchmark_label and any(v is not None for v in bench_values):
        legend_items.append(("#6b7280", benchmark_label))

    legend = '<div class="radar-legend">'
    for col, lbl in legend_items:
        legend += f'<span><i style="background:{col}"></i>{esc(lbl)}</span>'
    legend += '</div>'
    src = f'<div class="radar-src">数据来源：{esc(benchmark_source)}</div>' if benchmark_source else ""
    return f'<div class="radar-wrap">{svg}{legend}{src}</div>'


def question_table(unit, meta, compare_units=None, compare_labels=None):
    """逐题得分卡。compare_units: 参照单位列表(如公司整体、事业部、二级)"""
    sup = unit["suppressed"]
    has_hist = any(unit["questions"][q].get("prev") is not None for q in meta["questions"])
    compare_units = compare_units or []
    compare_labels = compare_labels or []
    head = "<th>题项</th><th>得分</th><th class='num'>均值</th>"
    if has_hist:
        head += "<th class='num'>上期</th><th class='num'>变化</th>"
    for lbl in compare_labels:
        head += f"<th class='num'>{esc(lbl)}</th>"
    head += "<th class='num'>低分占比</th><th class='center'>评级</th>"
    rows = []
    for dim, dcfg in meta["dimensions"].items():
        colspan = 3 + (2 if has_hist else 0) + len(compare_labels) + 2
        rows.append(f'<tr><td colspan="{colspan}" style="background:#f0f3f7;font-weight:700;font-size:12px">'
                    f'{esc(dim)} · {esc(dcfg["core_question"])}</td></tr>')
        for q in dcfg["questions"]:
            qs = unit["questions"][q]
            cells = (f'<td><b>{q} {esc(meta["question_short"][q])}</b>'
                     f'<div class="qtext">{esc(meta["questions"][q])}</div></td>'
                     f'<td>{bar(qs["mean"], qs["band"], 120, sup)}</td>'
                     f'<td class="num"><b>{fmt(qs["mean"], sup)}</b></td>')
            if has_hist:
                cells += (f'<td class="num muted">{fmt(qs.get("prev"), sup)}</td>'
                          f'<td class="num">{fmt_delta(qs.get("delta"), sup)}</td>')
            for cu in compare_units:
                cm = cu["questions"][q]["mean"]
                diff = ""
                if not sup and cm is not None and qs["mean"] is not None:
                    gap = qs["mean"] - cm
                    if abs(gap) >= 0.3:
                        gc = "#1a7f37" if gap > 0 else "#cf222e"
                        diff = f' <span style="color:{gc};font-size:11px">({"+" if gap > 0 else ""}{gap:.1f})</span>'
                cells += f'<td class="num muted">{fmt(cm, cu["suppressed"])}{diff}</td>'
            low = qs.get("low_pct")
            low_s = "*" if sup or low is None else f"{low:.0f}%"
            low_style = 'style="color:#cf222e;font-weight:700"' if (not sup and low is not None and low >= 30) else ""
            cells += (f'<td class="num" {low_style}>{low_s}</td>'
                      f'<td class="center">{band_chip(qs["band"], sup)}</td>')
            rows.append(f"<tr>{cells}</tr>")
    return f'<table><tr>{head}</tr>{"".join(rows)}</table>'


def heat_table(rows_data, meta, row_label, extra_note="", show_tag=False):
    """单位 × 维度热力表。
    rows_data: [(名称, unit_dict, 百分位, 缩进层级=0, manager_tag_or_None)]
    show_tag: 是否显示"效能标签"列（仅三级部门有意义）
    """
    # 主管效能标签样式（低饱和同色系）
    tag_style = {"优秀标杆": ("#7faa90", "#e8f0eb"), "稳健": ("#57606a", "#f0f2f4"), "需辅导": ("#b86b6b", "#f3e8e8")}
    head = (f"<th>{esc(row_label)}</th><th class='num'>人数</th><th class='num'>综合</th>"
            + "".join(f"<th class='center'>{esc(d)}</th>"
                      for d in ["基本需求", "管理支持", "团队归属", "成长发展"])
            + "<th class='num'>敬业%</th><th class='num'>怠工%</th><th class='num'>内部百分位</th><th class='num'>环比</th>")
    if show_tag:
        head += "<th>效能标签</th>"
    body = []
    for item in rows_data:
        name, u, pctl = item[0], item[1], item[2]
        indent = item[3] if len(item) > 3 else 0
        tag_val = item[4] if len(item) > 4 else None
        sup = u["suppressed"]
        name_style = f'style="padding-left:{8 + indent * 22}px' + (';color:#57606a' if indent else '') + '"'
        cells = [f"<td {name_style}><b>{esc(name)}</b></td>", f"<td class='num'>{u['n']}</td>",
                 f"<td class='num'><b>{fmt(u['grand_mean'], sup)}</b></td>"]
        for d in ["基本需求", "管理支持", "团队归属", "成长发展"]:
            dv = u["dimensions"][d]
            if sup or dv["mean"] is None:
                cells.append("<td class='center muted'>*</td>")
            else:
                cells.append(f"<td><div class='heat-cell' style='background:{BAND_BG[dv['band']]};"
                             f"color:{BAND_COLOR[dv['band']]}'>{dv['mean']:.2f}</div></td>")
        eng = u["engagement"]
        cells.append(f"<td class='num'>{'*' if sup else f'{eng['engaged_pct']:.0f}%'}</td>")
        dis_style = ('style="color:#b86b6b;font-weight:700"'
                     if not sup and eng["disengaged_pct"] >= 20 else "")
        cells.append(f"<td class='num' {dis_style}>{'*' if sup else f'{eng['disengaged_pct']:.0f}%'}</td>")
        cells.append(f"<td class='num'>{'*' if sup or pctl is None else str(pctl)}</td>")
        cells.append(f"<td class='num'>{fmt_delta(u.get('grand_mean_delta'), sup) or '<span class=muted>—</span>'}</td>")
        if show_tag:
            if tag_val and not sup:
                ts = tag_style.get(tag_val, tag_style["稳健"])
                cells.append(f"<td><span class='chip' style='background:{ts[1]};color:{ts[0]}'>{esc(tag_val)}</span></td>")
            else:
                cells.append("<td class='muted center'>—</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    note = f'<div class="legend">{extra_note}</div>' if extra_note else ""
    return f'<table><tr>{head}</tr>{"".join(body)}</table>{note}'


def highlight_key(text):
    """诊断洞察中高亮关键数据：数字/百分比、「」关键词、Q题号，使用特殊颜色标记。"""
    s = esc(text)
    # 「关键词」高亮
    s = re.sub(r"「([^」]+)」", r'<span class="hlk">「\1」</span>', s)
    # Q题号高亮
    s = re.sub(r"(Q\d+)", r'<span class="hlk">\1</span>', s)
    # 百分比 / 小数高亮
    s = re.sub(r"(\d+\.\d+%?|\d+%)", r'<span class="hlk">\1</span>', s)
    return s


def insights_html(ins):
    if not ins:
        return ('<div class="insight muted">（诊断洞察未生成 — 可在 insights.json 中补充 '
                "summary / findings 后重新渲染）</div>")
    parts = []
    if ins.get("summary"):
        parts.append(f'<div class="insight"><b>总体判断</b><br>{highlight_key(ins["summary"])}</div>')
    if ins.get("findings"):
        # 关键发现最多 3 点
        for x in ins["findings"][:3]:
            parts.append(f'<div class="insight"><span class="hl-bullet">●</span> {highlight_key(x)}</div>')
    return "".join(parts)


def legend_html(meta):
    chips = "".join(f'{band_chip(b)} ' for b in ["优势", "良好", "关注", "预警"])
    return (f'<div class="legend">评级标准（5 分制）: {chips} · {esc(meta["band_rules"])}'
            f' · 低分占比 = 打 1-2 分人数比例，≥30% 标红</div>')


def footnote(meta, privacy=""):
    hist = f'对比上期: {esc(meta["prev_period"])} · ' if meta.get("has_history") and meta.get("prev_period") else ""
    scale_note = (f'本报告基于内部组织健康调研（{len(meta["questions"])} 题 / 5 分制）生成，'
                  f'评分框架参考盖洛普 Q12 敬业阶梯模型。')
    priv = f'<br>{privacy}' if privacy else ""
    return (f'<div class="footnote">* 为保护员工匿名性，参与人数少于 {meta["min_n"]} 人的单位不显示分数'
            f'（数据抑制），但其数据仍计入上级汇总。 · {hist}{scale_note}{priv}</div>')


def page(title, body):
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><style>{css()}</style></head>'
            f'<body><div class="page">{body}</div></body></html>')


# ---------- CEO看板专属组件 ----------

def sec_head(num, title, sub=""):
    """带编号的现代段落标题"""
    sub_html = f'<span class="sec-sub">{esc(sub)}</span>' if sub else ""
    return f'<div class="sec-head"><span class="sec-num">{num}</span><h2>{esc(title)}</h2>{sub_html}</div>'


def health_gauge(score, band_name, max_score=5.0):
    """SVG 圆环仪表盘：综合健康指数可视化"""
    if score is None:
        return '<div class="muted">无数据</div>'
    pct = max(0, min(1, score / max_score))
    r = 72
    circ = 2 * 3.14159 * r
    offset = circ * (1 - pct)
    color = BAND_COLOR.get(band_name, "#8b949e")
    return f'''
    <div style="display:flex;align-items:center;gap:20px">
      <svg width="180" height="180" viewBox="0 0 180 180">
        <circle cx="90" cy="90" r="{r}" fill="none" stroke="#e8ebef" stroke-width="14"/>
        <circle cx="90" cy="90" r="{r}" fill="none" stroke="{color}" stroke-width="14"
                stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.1f}"
                stroke-linecap="round" transform="rotate(-90 90 90)"/>
        <text x="90" y="88" text-anchor="middle" font-size="44" font-weight="800" fill="{color}">{score}</text>
        <text x="90" y="112" text-anchor="middle" font-size="13" fill="#9ca3af">/ {max_score:.0f} 分</text>
      </svg>
      <div>
        <div style="font-size:28px;font-weight:800;color:{color}">{esc(band_name)}</div>
        <div style="font-size:13px;color:var(--sub);margin-top:6px">综合健康指数</div>
        <div style="font-size:11px;color:#9ca3af;margin-top:2px">5 分制 · 四维度加权</div>
      </div>
    </div>'''


def _benchmark_note(meta, kind, n_respondents=None):
    """生成与行业基准的对比备注。
    kind: 'engaged' / 'disengaged' / 'total_pct'
    meta 中可设置 benchmark_region ('global'/'china'/'tech')。
    """
    region = meta.get("benchmark_region") or DEFAULT_BENCHMARK_REGION
    bm = INDUSTRY_BENCHMARK.get(region, INDUSTRY_BENCHMARK[DEFAULT_BENCHMARK_REGION])
    if kind == "engaged":
        return f"个人均分≥4.0 · vs {bm['label']} {bm['engaged']}%"
    if kind == "disengaged":
        return f"个人均分<3.0 · vs {bm['label']} {bm['disengaged']}%"
    if kind == "total_pct":
        total = meta.get("total_employees")
        if not total or total <= 0:
            total = n_respondents or meta.get("total_respondents", 0)
        n = n_respondents if n_respondents is not None else meta.get("total_respondents", 0)
        if total > 0:
            pct = round(n / total * 100, 1)
            return f"占员工总数 {pct}%"
        return "占员工总数 —"
    return ""


def _help_tip(text):
    """右上角小问号，hover / 聚焦时显示解释气泡。"""
    if not text:
        return ""
    return (f'<span class="hs-tip" tabindex="0">?'
            f'<span class="hs-tip-pop">{esc(text)}</span></span>')


def hero_section(comp, meta, bus):
    """CEO看板 Hero 区域：仪表盘 + 关键指标横排"""
    period = meta.get("period") or ""
    scale_tag = "5 分制"
    eng = comp["engagement"]
    delta = comp.get("grand_mean_delta")
    delta_html = ""
    if delta is not None:
        dc = "#10b981" if delta > 0 else ("#ef4444" if delta < 0 else "#9ca3af")
        da = "▲" if delta > 0.04 else ("▼" if delta < -0.04 else "—")
        delta_html = f'<span style="color:{dc};font-size:14px;font-weight:700;margin-left:8px">{da} {delta:+.2f}</span>'
    region = meta.get("benchmark_region") or DEFAULT_BENCHMARK_REGION
    bm = INDUSTRY_BENCHMARK.get(region, INDUSTRY_BENCHMARK[DEFAULT_BENCHMARK_REGION])
    stats = [
        ("受访人数", str(meta["total_respondents"]), _benchmark_note(meta, "total_pct", meta["total_respondents"]), "#2563eb", ""),
        ("一级事业部", str(len(bus)), "参与调研", "#7c3aed", ""),
        ("敬业员工", f'{eng["engaged_pct"]:.0f}%', f"vs {bm['label']} {bm['engaged']}%",
         "#10b981", "个人均分 ≥ 4.0 的员工视为敬业（盖洛普 Q12 口径）"),
        ("怠工员工", f'{eng["disengaged_pct"]:.0f}%', f"vs {bm['label']} {bm['disengaged']}%",
         "#ef4444" if eng["disengaged_pct"] >= 20 else "#f59e0b",
         "个人均分 < 3.0 的员工视为怠工；3.0–3.9 为中立（不敬业）"),
    ]
    stats_html = "".join(
        f'<div class="hero-stat">{_help_tip(tip)}'
        f'<div class="hs-label">{esc(l)}</div>'
        f'<div class="hs-value" style="color:{c}">{v}</div>'
        f'<div class="hs-foot">{esc(f)}</div></div>' for l, v, f, c, tip in stats)
    gauge = health_gauge(comp["grand_mean"], comp["grand_band"])
    return (f'<div class="hero-banner"><div>{gauge}{delta_html}</div>'
            f'<div class="hero-stats">{stats_html}</div></div>'
            f'<div class="hero-source">行业基准来源：{INDUSTRY_BENCHMARK_SOURCE}</div>')


def scatter_matrix(bus, meta):
    """干预优先级散点图：SVG 2D 定位各事业部"""
    items = [(n, u) for n, u in bus.items() if not u["suppressed"] and u["grand_mean"] is not None]
    if not items:
        return '<div class="muted">无可用数据</div>'
    ns = sorted(u["n"] for _, u in items)
    med_n = ns[len(ns) // 2] if ns else 1
    max_n = max(u["n"] for _, u in items)
    # 坐标系：x=健康度(3.5~6.0), y=人数(0~max_n*1.15)
    x_min, x_max = 3.5, 5.2
    y_max = max_n * 1.15
    W, H = 680, 340
    pad_l, pad_r, pad_t, pad_b = 56, 30, 30, 46
    pw = W - pad_l - pad_r  # 绘图区宽
    ph = H - pad_t - pad_b  # 绘图区高

    def x_pos(gm):
        return pad_l + (gm - x_min) / (x_max - x_min) * pw

    def y_pos(n):
        return pad_t + ph - (n / y_max) * ph

    # 象限分割线
    x_div = x_pos(4.5)
    y_div = y_pos(med_n)
    # 象限标签
    quad_labels = [
        (pad_l + 8, pad_t + 16, "紧急干预", "#ef4444", "低健康 · 高影响"),
        (x_div + 8, pad_t + 16, "保持优势", "#10b981", "高健康 · 高影响"),
        (pad_l + 8, y_div + 16, "重点干预", "#f59e0b", "低健康 · 低影响"),
        (x_div + 8, y_div + 16, "关注维持", "#2563eb", "高健康 · 低影响"),
    ]
    qlabels = "".join(
        f'<text x="{x}" y="{y}" font-size="13" font-weight="700" fill="{c}">{esc(t)}</text>'
        f'<text x="{x}" y="{y+14}" font-size="10" fill="#9ca3af">{esc(d)}</text>'
        for x, y, t, c, d in quad_labels)
    # 分割线
    dividers = (f'<line x1="{x_div:.1f}" y1="{pad_t}" x2="{x_div:.1f}" y2="{pad_t+ph}" '
                f'stroke="#d1d5db" stroke-width="1" stroke-dasharray="5 4"/>'
                f'<line x1="{pad_l}" y1="{y_div:.1f}" x2="{pad_l+pw}" y2="{y_div:.1f}" '
                f'stroke="#d1d5db" stroke-width="1" stroke-dasharray="5 4"/>')
    # 象限背景色
    quads_bg = (f'<rect x="{pad_l}" y="{pad_t}" width="{x_div-pad_l:.1f}" height="{y_div-pad_t:.1f}" fill="#fef2f2" opacity=".5"/>'
                f'<rect x="{x_div:.1f}" y="{pad_t}" width="{pad_l+pw-x_div:.1f}" height="{y_div-pad_t:.1f}" fill="#ecfdf5" opacity=".5"/>'
                f'<rect x="{pad_l}" y="{y_div:.1f}" width="{x_div-pad_l:.1f}" height="{pad_t+ph-y_div:.1f}" fill="#fffbeb" opacity=".5"/>'
                f'<rect x="{x_div:.1f}" y="{y_div:.1f}" width="{pad_l+pw-x_div:.1f}" height="{pad_t+ph-y_div:.1f}" fill="#eff6ff" opacity=".5"/>')
    # 坐标轴
    x_ticks = [3.5, 4.0, 4.5, 5.0]
    x_axis = "".join(
        f'<line x1="{x_pos(t):.1f}" y1="{pad_t+ph}" x2="{x_pos(t):.1f}" y2="{pad_t+ph+4}" stroke="#9ca3af"/>'
        f'<text x="{x_pos(t):.1f}" y="{pad_t+ph+18}" text-anchor="middle" font-size="11" fill="#6b7280">{t:.1f}</text>'
        for t in x_ticks)
    y_ticks = [0, max_n // 4, max_n // 2, max_n * 3 // 4, max_n] if max_n > 4 else [0, max_n]
    y_axis = "".join(
        f'<line x1="{pad_l-4}" y1="{y_pos(t):.1f}" x2="{pad_l}" y2="{y_pos(t):.1f}" stroke="#9ca3af"/>'
        f'<text x="{pad_l-8}" y="{y_pos(t)+4:.1f}" text-anchor="end" font-size="11" fill="#6b7280">{t}</text>'
        for t in y_ticks if t >= 0)
    # 轴标题
    axis_titles = (f'<text x="{pad_l+pw/2:.1f}" y="{H-8}" text-anchor="middle" font-size="12" fill="#6b7280">综合健康指数 →</text>'
                   f'<text x="14" y="{pad_t+ph/2:.1f}" text-anchor="middle" font-size="12" fill="#6b7280" transform="rotate(-90 14 {pad_t+ph/2:.1f})">↑ 影响面（人数）</text>')
    # 数据点
    dots = ""
    for n, u in items:
        cx, cy = x_pos(u["grand_mean"]), y_pos(u["n"])
        bnd = u["grand_band"]
        dc = BAND_COLOR.get(bnd, "#8b949e")
        rad = 12 + (u["n"] / max_n) * 14  # 气泡大小按人数
        dis = u["engagement"]["disengaged_pct"]
        dots += (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" fill="{dc}" fill-opacity=".75" stroke="#fff" stroke-width="2"/>'
                 f'<text x="{cx:.1f}" y="{cy+4:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">{u["n"]}</text>'
                 f'<text x="{cx:.1f}" y="{cy-rad-6:.1f}" text-anchor="middle" font-size="11" font-weight="600" fill="#374151">{esc(n)}</text>'
                 f'<text x="{cx:.1f}" y="{cy+rad+14:.1f}" text-anchor="middle" font-size="10" fill="#6b7280">{u["grand_mean"]} · 怠工{dis:.0f}%</text>')
    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;margin:0 auto;display:block">'
           f'{quads_bg}{dividers}{qlabels}{x_axis}{y_axis}{axis_titles}{dots}</svg>')
    return (f'{svg}'
            f'<div class="legend">气泡大小=人数 · 颜色=健康评级 · 象限按"健康度4.5 / 人数中位数{med_n}"划分</div>')


# ---------- VP 看板：经理人效能象限（依据《经理效能矩阵·离散数据计算标准》） ----------
# 指标：团队均分 = 所辖成员各题均分的平均（1-5 分制）；离散度 = 个人均分的样本标准差（n-1）
# 阈值：所有经理团队均分的中位数 / 离散度的中位数（文档要求用中位数而非均值，抗极端值）
# 四象限（文档 2.2 / 2.3）：
#   明星经理  = 均分>中位数 且 离散度<=中位数  → 保持标杆
#   危险经理  = 均分<=中位数 且 离散度>中位数  → 最高优先级，立即干预
#   老好人经理 = 均分<=中位数 且 离散度<=中位数 → 高优先级，团队一致不满、问题在经理本人
#   高压经理  = 均分>中位数 且 离散度>中位数  → 中高优先级，内部分化、关注嫡系分化
QUADRANT_CFG = {
    "明星经理": {
        "color": "#1a7f37", "bg": "#dcfce7", "icon": "🌟",
        "trait": "团队整体优 秀且意⻅⼀致",
        "subtext": "管理有方，团队凝聚⼒强",
        "action": "保持，作为标杆",
        "priority": "—",
    },
    "危险经理": {
        "color": "#dc2626", "bg": "#fee2e2", "icon": "🚨",
        "trait": "团队整体差 且意⻅分化严重",
        "subtext": "团队已经散了，需立即⼲预",
        "action": "立即介入、单独辅导",
        "priority": "最高",
    },
    "老好人经理": {
        "color": "#d97706", "bg": "#fef3c7", "icon": "⚠️",
        "trait": "团队整体差 但意⻅⼀致",
        "subtext": "⼤家⼀致不满，问题在经理本⼈",
        "action": "换经理 / 重点赋能",
        "priority": "高",
    },
    "高压经理": {
        "color": "#2563eb", "bg": "#dbeafe", "icon": "🔥",
        "trait": "团队整体好 但内部分化",
        "subtext": "有⼈很好有⼈很差，可能嫡系分化",
        "action": "关注分化、拉平体验",
        "priority": "中高",
    },
}
# 文档 6.2 离散度解读（6 分制经验值）；本看板用 1-5 分制均值，按比例缩放后供量级参考
STD_BANDS = [
    (0.0, 0.40, "团队意⻅高度⼀致"),
    (0.40, 0.80, "正常离散范围"),
    (0.80, 1.20, "团队内部存在明显分歧"),
    (1.20, 99.0, "团队严重分化，需⽴即关注"),
]


def classify_quadrant(mean, std, mean_med, std_med):
    """文档 2.2 象限判定：均分>中位数 且 离散度<=中位数 → 明星；其余依此类推。"""
    if mean > mean_med and std <= std_med:
        return "明星经理"
    if mean <= mean_med and std > std_med:
        return "危险经理"
    if mean <= mean_med and std <= std_med:
        return "老好人经理"
    return "高压经理"  # mean > mean_med and std > std_med


def manager_quadrant_html(data, bu_name, meta, part="all"):
    """二级部门负责人效能象限：本事业部下属二级部门。
    横轴=团队均分(1-5)，纵轴=人员间标准差；参考线为两条中位数；圆圈大小=团队人数；颜色=象限。
    阈值（中位数）默认取本事业部下属二级部门，二级部门数<3 时回退全公司二级部门中位数。
    """
    bu = data["business_units"][bu_name]

    # 收集本事业部下所有二级部门
    items = []
    for l2n, l2 in bu["l2_units"].items():
        if l2.get("suppressed") or l2.get("grand_mean") is None:
            continue
        items.append({
            "l2": l2n, "n": l2["n"],
            "mean": l2["grand_mean"],
            "std": l2.get("score_std") or 0,
            "band": l2.get("grand_band"),
        })
    if not items:
        return '<div class="muted">本事业部暂无可展示的二级部门数据</div>'

    # 中位数阈值：优先本事业部，不足 3 个二级部门回退全公司二级部门
    means = [it["mean"] for it in items]
    stds = [it["std"] for it in items]
    if len(items) >= 3:
        mean_med = statistics.median(means)
        std_med = statistics.median(stds)
        med_src = "本事业部下属二级部门"
    else:
        all_means, all_stds = [], []
        for bn, b in data["business_units"].items():
            for l2n, l2 in b["l2_units"].items():
                if l2.get("grand_mean") is not None:
                    all_means.append(l2["grand_mean"])
                    all_stds.append(l2.get("score_std") or 0)
        mean_med = statistics.median(all_means) if all_means else 0
        std_med = statistics.median(all_stds) if all_stds else 0
        med_src = "全公司二级部门"

    for it in items:
        it["quad"] = classify_quadrant(it["mean"], it["std"], mean_med, std_med)

    # 坐标范围（团队均分 1-5 分制；标准差 0 起）
    x_min = max(1.0, min(min(means), mean_med) - 0.3)
    x_max = min(5.0, max(max(means), mean_med) + 0.3)
    y_max = max(0.6, max(max(stds), std_med) * 1.2 + 0.1)

    W, H = 720, 440
    pad_l, pad_r, pad_t, pad_b = 70, 34, 40, 60
    pw = W - pad_l - pad_r
    ph = H - pad_t - pad_b

    def x_pos(v):
        return pad_l + (v - x_min) / (x_max - x_min) * pw

    def y_pos(v):
        return pad_t + ph - (v / y_max) * ph

    x_med = x_pos(mean_med)
    y_med = y_pos(std_med)

    # 象限背景（高均分在右、高离散在上 => 右上明星 / 右下高压 / 左上老好人 / 左下危险）
    quads_bg = (
        f'<rect x="{pad_l}" y="{pad_t}" width="{x_med-pad_l:.1f}" height="{y_med-pad_t:.1f}" fill="#fef3c7" opacity=".55"/>'
        f'<rect x="{x_med:.1f}" y="{pad_t}" width="{pad_l+pw-x_med:.1f}" height="{y_med-pad_t:.1f}" fill="#dcfce7" opacity=".55"/>'
        f'<rect x="{pad_l}" y="{y_med:.1f}" width="{x_med-pad_l:.1f}" height="{pad_t+ph-y_med:.1f}" fill="#fee2e2" opacity=".55"/>'
        f'<rect x="{x_med:.1f}" y="{y_med:.1f}" width="{pad_l+pw-x_med:.1f}" height="{pad_t+ph-y_med:.1f}" fill="#dbeafe" opacity=".55"/>'
    )
    # 象限标签
    qlabels = (
        f'<text x="{pad_l+12}" y="{pad_t+20}" font-size="13" font-weight="700" fill="#d97706">⚠️ 老好人经理</text>'
        f'<text x="{x_med+12}" y="{pad_t+20}" font-size="13" font-weight="700" fill="#1a7f37">🌟 明星经理</text>'
        f'<text x="{pad_l+12}" y="{y_med+22}" font-size="13" font-weight="700" fill="#dc2626">🚨 危险经理</text>'
        f'<text x="{x_med+12}" y="{y_med+22}" font-size="13" font-weight="700" fill="#2563eb">🔥 高压经理</text>'
    )
    # 参考线（中位数）
    dividers = (
        f'<line x1="{x_med:.1f}" y1="{pad_t}" x2="{x_med:.1f}" y2="{pad_t+ph}" stroke="#6b7280" stroke-width="1.5" stroke-dasharray="6 4"/>'
        f'<line x1="{pad_l}" y1="{y_med:.1f}" x2="{pad_l+pw}" y2="{y_med:.1f}" stroke="#6b7280" stroke-width="1.5" stroke-dasharray="6 4"/>'
    )
    # 坐标轴刻度
    x_ticks = [x_min, mean_med, x_max]
    x_axis = "".join(
        f'<line x1="{x_pos(t):.1f}" y1="{pad_t+ph}" x2="{x_pos(t):.1f}" y2="{pad_t+ph+4}" stroke="#9ca3af"/>'
        f'<text x="{x_pos(t):.1f}" y="{pad_t+ph+18}" text-anchor="middle" font-size="11" fill="#6b7280">{t:.2f}</text>'
        for t in x_ticks
    )
    y_ticks = [0, std_med, y_max]
    y_axis = "".join(
        f'<line x1="{pad_l-4}" y1="{y_pos(t):.1f}" x2="{pad_l}" y2="{y_pos(t):.1f}" stroke="#9ca3af"/>'
        f'<text x="{pad_l-8}" y="{y_pos(t)+4:.1f}" text-anchor="end" font-size="11" fill="#6b7280">{t:.2f}</text>'
        for t in y_ticks
    )
    axis_titles = (
        f'<text x="{pad_l+pw/2:.1f}" y="{H-10}" text-anchor="middle" font-size="12" fill="#6b7280">团队均分（横轴 → 越右越好）</text>'
        f'<text x="18" y="{pad_t+ph/2:.1f}" text-anchor="middle" font-size="12" fill="#6b7280" transform="rotate(-90 18 {pad_t+ph/2:.1f})">↑ 人员间标准差（离散度）</text>'
    )
    # 中位数标注
    med_notes = (
        f'<text x="{x_med:.1f}" y="{pad_t-8}" text-anchor="middle" font-size="10" fill="#6b7280">均分中位数 {mean_med:.2f}</text>'
        f'<text x="{pad_l-8}" y="{y_med-6:.1f}" text-anchor="end" font-size="10" fill="#6b7280">离散中位数 {std_med:.2f}</text>'
    )
    # 气泡
    max_n = max(it["n"] for it in items)
    dots = ""
    for it in items:
        cx, cy = x_pos(it["mean"]), y_pos(it["std"])
        cfg = QUADRANT_CFG[it["quad"]]
        rad = 9 + (it["n"] / max_n) * 18
        label_y = cy - rad - 6
        dots += (
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" fill="{cfg["color"]}" fill-opacity=".78" stroke="#fff" stroke-width="2"/>'
            f'<text x="{cx:.1f}" y="{cy+4:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">{it["n"]}</text>'
            f'<text x="{cx:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="11" font-weight="600" fill="#374151">{esc(it["l2"])}</text>'
            f'<text x="{cx:.1f}" y="{cy+rad+14:.1f}" text-anchor="middle" font-size="10" fill="#6b7280">{it["mean"]:.2f}分 · σ{it["std"]:.2f}</text>'
        )
    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;margin:0 auto;display:block">'
           f'{quads_bg}{dividers}{qlabels}{x_axis}{y_axis}{axis_titles}{med_notes}{dots}</svg>')

    # 四象限摘要卡片（按文档 2.3 画像：特征 / 潜台词 / 干预优先级）
    quad_cards = []
    order = ["明星经理", "危险经理", "老好人经理", "高压经理"]
    for name in order:
        cfg = QUADRANT_CFG[name]
        members = [it for it in items if it["quad"] == name]
        if members:
            member_lines = " · ".join(f"{it['l2']}（{it['n']}人）" for it in members[:3])
            if len(members) > 3:
                member_lines += f" 等{len(members)}个"
        else:
            member_lines = "暂无"
        quad_cards.append(
            f'<div style="flex:1;min-width:200px;background:{cfg["bg"]};border:1px solid {cfg["color"]}40;border-radius:12px;padding:14px">'
            f'<div style="font-size:15px;font-weight:700;color:{cfg["color"]};margin-bottom:4px">{cfg["icon"]} {name}</div>'
            f'<div style="font-size:12px;color:#4b5563;margin-bottom:6px">{cfg["trait"]}</div>'
            f'<div style="font-size:12px;color:#6b7280;margin-bottom:8px"><b>潜台词：</b>{cfg["subtext"]}<br/><b>干预优先级：</b>{cfg["priority"]}</div>'
            f'<div style="font-size:12px;color:#374151;line-height:1.5"><b>落位部门：</b>{esc(member_lines)}</div>'
            f'</div>'
        )
    cards_html = f'<div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:18px">{"".join(quad_cards)}</div>'

    # 三级部门象限解读表
    rows = []
    for it in sorted(items, key=lambda x: (-x["mean"], x["std"])):
        cfg = QUADRANT_CFG[it["quad"]]
        band_txt = ""
        for lo, hi, txt in STD_BANDS:
            if lo <= it["std"] < hi:
                band_txt = txt
                break
        rows.append(
            f'<tr>'
            f'<td style="padding:10px 12px;border-bottom:1px solid var(--line);font-weight:600">{esc(it["l2"])}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid var(--line);text-align:center">{it["n"]}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid var(--line);text-align:center">{it["mean"]:.2f}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid var(--line);text-align:center">{it["std"]:.2f}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid var(--line);text-align:center"><span style="color:{cfg["color"]};font-weight:700">{cfg["icon"]} {it["quad"]}</span></td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid var(--line);color:#4b5563;font-size:13px">{esc(band_txt)} · {esc(cfg["subtext"])}</td>'
            f'</tr>'
        )
    table_html = (
        f'<h4 style="margin:24px 0 12px 0;color:#1f2937;font-size:15px">二级部门象限解读</h4>'
        f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
        f'<thead><tr style="background:#f3f4f6">'
        f'<th style="padding:10px 12px;text-align:left;font-weight:700">二级部门</th>'
        f'<th style="padding:10px 12px;text-align:center;font-weight:700">人数</th>'
        f'<th style="padding:10px 12px;text-align:center;font-weight:700">团队均分</th>'
        f'<th style="padding:10px 12px;text-align:center;font-weight:700">标准差</th>'
        f'<th style="padding:10px 12px;text-align:center;font-weight:700">象限定位</th>'
        f'<th style="padding:10px 12px;text-align:left;font-weight:700">离散度解读与潜台词</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )

    chart = (
        f'{svg}'
        f'<div class="legend">仅展示本事业部下属二级部门 · 横轴=团队均分 · 纵轴=人员间标准差(σ) · 圆圈大小=团队人数 · '
        f'颜色=象限 · 两条虚线为<b>中位数阈值</b>（均分中位数 {mean_med:.2f} / 离散中位数 {std_med:.2f}，取自{med_src}）</div>'
    )
    if part == "chart":
        return chart
    if part == "cards":
        return cards_html + table_html
    return (
        f'<div style="background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;box-shadow:var(--shadow)">'
        f'{chart}{cards_html}{table_html}'
        f'</div>'
    )


def diagnosis_bars(company, meta):
    """系统性 vs 局部问题：12 题得分横向条形图（低分红 / 中蓝 / 高绿；最低 3 题标 ▲；预警线 4.0、优秀线 4.5）。"""
    qs = meta.get("questions", {})
    short = meta.get("question_short", {})
    items = []
    for qid in qs:
        qd = (company.get("questions") or {}).get(qid) or {}
        mean = qd.get("mean")
        if mean is None:
            continue
        items.append((qid, short.get(qid) or qs[qid], mean))
    if not items:
        return ""
    items.sort(key=lambda x: x[2])
    lowest = {it[0] for it in items[:3]}
    items.sort(key=lambda x: int(x[0][1:]))

    def col(v):
        if v < 4.0:
            return "#cf222e"
        if v < 4.5:
            return "#1f6feb"
        return "#1a7f37"

    grid = "display:grid;grid-template-columns:14px 32px 1fr 40% 42px;align-items:center;gap:6px;font-size:12px"
    rows = [f'<div style="{grid}">']
    for qid, label, mean in items:
        c = col(mean)
        w = mean / 5.0 * 100.0
        mark = "▲" if qid in lowest else ""
        rows.append(
            f'<span style="color:#cf222e;font-size:11px">{mark}</span>'
            f'<span style="color:#6b7280;font-weight:600">{esc(qid)}</span>'
            f'<span style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:#374151" title="{esc(label)}">{esc(label)}</span>'
            f'<span style="position:relative;height:14px;background:#f0f2f4;border-radius:7px">'
            f'<span style="position:absolute;left:0;top:0;bottom:0;width:{w:.1f}%;background:{c};border-radius:7px"></span>'
            f'<span style="position:absolute;top:-3px;bottom:-3px;left:80%;width:1px;background:#d4a72c"></span>'
            f'<span style="position:absolute;top:-3px;bottom:-3px;left:90%;width:1px;background:#1a7f37"></span>'
            f'</span>'
            f'<span style="text-align:right;font-weight:700;color:{c}">{mean:.2f}</span>'
        )
    # 刻度轴（与条形轨道同列对齐）
    rows.append('<span></span><span></span><span></span>')
    rows.append('<span style="position:relative;height:12px;font-size:10px;color:#9ca3af">'
                '<span style="position:absolute;left:0;transform:translateX(0)">0</span>'
                '<span style="position:absolute;left:80%;transform:translateX(-50%);color:#d4a72c">4.0</span>'
                '<span style="position:absolute;left:90%;transform:translateX(-50%);color:#1a7f37">4.5</span>'
                '<span style="position:absolute;left:100%;transform:translateX(-100%)">5.0</span>'
                '</span>')
    rows.append('<span style="text-align:right;color:#9ca3af">分</span>')
    # 图例（跨整行）
    rows.append('<span style="grid-column:1/-1;display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:11px;color:#6b7280">'
                '<span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#cf222e;margin-right:4px;vertical-align:middle"></i>预警 &lt;4.0</span>'
                '<span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#1f6feb;margin-right:4px;vertical-align:middle"></i>良好 4.0–4.5</span>'
                '<span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#1a7f37;margin-right:4px;vertical-align:middle"></i>优秀 ≥4.5</span>'
                '<span style="color:#cf222e">▲ 最低 3 题</span></span>')
    # 说明（跨整行）
    rows.append('<span style="grid-column:1/-1;margin-top:8px;font-size:11px;color:#6b7280;line-height:1.6">'
                '公司整体最低分题为<span style="color:#cf222e;font-weight:600">系统性短板</span>，需在政策 / 机制层面统一干预；'
                '若仅个别部门在某题显著偏低，则属<span style="color:#1f6feb;font-weight:600">局部问题</span>，请在经理人看板逐题下钻定位。</span>')
    rows.append('</div>')
    return "".join(rows)


def vp_hero(bu, meta, all_bus, bu_name, comp):
    """VP看板 Hero：小仪表盘 + 本部定位 + 关键指标"""
    eng = bu["engagement"]
    pctl = bu.get("percentile_vs_bus")
    n_l2 = len(bu["l2_units"])
    n_dept = sum(len(l2["departments"]) for l2 in bu["l2_units"].values())
    ranked = sorted(all_bus.items(), key=lambda kv: (kv[1]["grand_mean"] is None, -(kv[1]["grand_mean"] or 0)))
    rank = next((i for i, (n, u) in enumerate(ranked, 1) if u is bu), 0)
    total = len(ranked)
    if pctl is not None:
        if pctl >= 67: pos, pc = ("引领者", "#10b981")
        elif pctl >= 33: pos, pc = ("中位水平", "#f59e0b")
        else: pos, pc = ("落后者", "#ef4444")
        pos_html = f'<div style="font-size:22px;font-weight:800;color:{pc}">{pos}</div><div style="font-size:11px;color:var(--sub)">公司内排名 {rank}/{total} · 超过 {pctl}% 事业部</div>'
    else:
        pos_html = '<div class="muted">排名数据不可用</div>'
    score = bu["grand_mean"]
    bnd = bu["grand_band"]
    color = BAND_COLOR.get(bnd, "#8b949e")
    gauge = (f'<svg width="120" height="120" viewBox="0 0 120 120">'
             f'<circle cx="60" cy="60" r="48" fill="none" stroke="#e8ebef" stroke-width="10"/>'
             f'<circle cx="60" cy="60" r="48" fill="none" stroke="{color}" stroke-width="10" stroke-linecap="round"'
             f' stroke-dasharray="{2*3.14159*48:.1f}" stroke-dashoffset="{2*3.14159*48*(1-min(1,max(0,score/5))):.1f}" transform="rotate(-90 60 60)"/>'
             f'<text x="60" y="62" text-anchor="middle" font-size="28" font-weight="800" fill="{color}">{score}</text>'
             f'<text x="60" y="80" text-anchor="middle" font-size="11" fill="#9ca3af">{bnd}</text></svg>')
    ceng = comp["engagement"]
    total = meta.get("total_employees")
    if not total or total <= 0:
        total = meta.get("total_respondents", 0)
    n_pct = round(bu["n"] / total * 100, 1) if total > 0 else 0.0
    stats = [
        ("受访人数", str(bu["n"]), f"占员工总数 {n_pct}%", "#2563eb", ""),
        ("下辖结构", f"{n_l2}二级/{n_dept}三级", "", "#7c3aed", "textual"),
        ("敬业员工", f'{eng["engaged_pct"]:.0f}%', f'vs 公司 {ceng["engaged_pct"]:.0f}%',
         "#10b981", "个人均分 ≥ 4.0 的员工视为敬业（盖洛普 Q12 口径）"),
        ("怠工员工", f'{eng["disengaged_pct"]:.0f}%', f'vs 公司 {ceng["disengaged_pct"]:.0f}%',
         "#ef4444" if eng["disengaged_pct"] >= 20 else "#f59e0b",
         "个人均分 < 3.0 的员工视为怠工；3.0–3.9 为中立（不敬业）"),
    ]
    stats_html = "".join(
        f'<div class="hero-stat">{_help_tip(tip)}'
        f'<div class="hs-label">{esc(l)}</div>'
        f'<div class="hs-value" style="color:{c}">{v}</div>'
        f'<div class="hs-foot">{esc(f)}</div></div>' for l, v, f, c, tip in stats)
    return (f'<div class="hero-banner" style="grid-template-columns:auto 240px 1fr;gap:24px">'
            f'<div>{gauge}</div><div>{pos_html}</div>'
            f'<div class="hero-stats">{stats_html}</div></div>')


def manager_hero(dept, meta):
    """经理人看板 Hero：小仪表盘 + 关键指标"""
    eng = dept["engagement"]
    score = dept["grand_mean"]
    bnd = dept["grand_band"]
    color = BAND_COLOR.get(bnd, "#8b949e")
    gauge = (f'<svg width="120" height="120" viewBox="0 0 120 120">'
             f'<circle cx="60" cy="60" r="48" fill="none" stroke="#e8ebef" stroke-width="10"/>'
             f'<circle cx="60" cy="60" r="48" fill="none" stroke="{color}" stroke-width="10" stroke-linecap="round"'
             f' stroke-dasharray="{2*3.14159*48:.1f}" stroke-dashoffset="{2*3.14159*48*(1-min(1,max(0,score/5))):.1f}" transform="rotate(-90 60 60)"/>'
             f'<text x="60" y="62" text-anchor="middle" font-size="28" font-weight="800" fill="{color}">{score}</text>'
             f'<text x="60" y="80" text-anchor="middle" font-size="11" fill="#9ca3af">{bnd}</text></svg>')
    stats = [
        ("团队人数", str(dept["n"]), "参与调研", "#2563eb"),
        ("敬业员工", f'{eng["engaged_pct"]:.0f}%', "个人均分≥4.0", "#10b981"),
        ("从业员工", f'{eng["neutral_pct"]:.0f}%', "个人均分3.0~3.9", "#f59e0b"),
        ("怠工员工", f'{eng["disengaged_pct"]:.0f}%', "个人均分<3.0", "#ef4444" if eng["disengaged_pct"]>=20 else "#f59e0b"),
    ]
    stats_html = "".join(
        f'<div class="hero-stat"><div class="hs-label">{esc(l)}</div>'
        f'<div class="hs-value" style="color:{c}">{v}</div>'
        f'<div class="hs-foot">{esc(f)}</div></div>' for l, v, f, c in stats)
    return (f'<div class="hero-banner" style="grid-template-columns:auto 1fr">'
            f'<div>{gauge}</div><div class="hero-stats">{stats_html}</div></div>')


def manager_team_snapshot(dept, meta):
    """经理人看板：单个团队的四类员工分布快照（100% 堆叠条）"""
    eng = dept["engagement"]
    gm = dept["grand_mean"] or 0
    ratio = max(0.0, min(1.0, (gm - 4.0) / 1.0))
    high = eng["engaged_pct"] * ratio
    inspired = eng["engaged_pct"] - high
    neg, neu, ins, hi = eng["disengaged_pct"], eng["neutral_pct"], inspired, high
    total = neg + neu + ins + hi or 1
    neg, neu, ins, hi = [100 * v / total for v in (neg, neu, ins, hi)]
    colors = {"neg": "#e52529", "neu": "#9ca3af", "inspired": "#5eead4", "high": "#0d9488"}
    labels = {"neg": "消极", "neu": "中立", "inspired": "激发", "high": "高效"}
    segs = "".join(f'<div class="etype-seg" style="width:{v:.1f}%;background:{colors[k]}"></div>'
                   for k, v in (("neg", neg), ("neu", neu), ("inspired", ins), ("high", hi)) if v > 0)
    legend = "".join(f'<span><i class="etype-dot" style="background:{colors[k]}"></i>{labels[k]} {v:.0f}%</span>'
                     for k, v in (("neg", neg), ("neu", neu), ("inspired", ins), ("high", hi))
                     if v >= 0.5)
    neg_cls = " high" if neg >= 30 else ""
    return (f'<div class="etype-chart"><div class="etype-head"><div class="etype-title">团队员工类型</div>'
            f'<div class="etype-sub">按个人均分估算 · 消极≥30%标红</div></div>'
            f'<div class="etype-bar-wrap" style="height:30px">{segs}</div>'
            f'<div class="etype-neg{neg_cls}" style="margin-top:8px;font-size:13px">消极占比 {neg:.0f}%</div>'
            f'<div class="etype-legend" style="margin-top:8px">{legend}</div></div>')


def manager_signals(dept, meta):
    """经理人看板：团队关键信号一览（快速 KPI 卡）"""
    eng = dept["engagement"]
    gm = dept["grand_mean"]
    bnd = dept["grand_band"]
    delta = dept.get("grand_mean_delta")
    dlt = (f"{delta:+.2f}" if delta is not None else "—")
    dims = [(d, dept["dimensions"][d]["mean"]) for d in DIM_ORDER
            if meta["dimensions"][d]["questions"] and dept["dimensions"][d]["mean"] is not None]
    dims.sort(key=lambda x: x[1])
    weak = dims[0] if dims else ("—", None)
    weak_txt = weak[0] + ((f"（{weak[1]:.2f}）") if weak[1] is not None else "")
    dneg = eng["disengaged_pct"]
    neg_style = 'style="color:#dc2626;font-weight:700"' if dneg >= 30 else ""
    rows = [
        ("综合均值", f"{gm}（{bnd}）"),
        ("环比变化", dlt),
        ("敬业 / 怠工", f"{eng['engaged_pct']:.0f}% / <span {neg_style}>{dneg:.0f}%</span>"),
        ("最弱维度", weak_txt),
        ("样本人数", f"{dept['n']} 人"),
    ]
    lis = "".join(
        '<div class="qbar-row" style="grid-template-columns:1fr auto;gap:14px">'
        f'<div class="qbar-label">{esc(k)}</div>'
        f'<div class="qbar-score" style="font-size:15px">{v}</div></div>'
        for k, v in rows)
    return "<div>" + lis + "</div>"


# ---------- VP看板专属组件 ----------

def bu_trait_diagnosis(bu, company, meta):
    """本部特质：与公司差异最大的3题——往往反映VP自身管理风格"""
    diffs = []
    for q in meta["questions"]:
        bm = bu["questions"][q]["mean"]
        cm = company["questions"][q]["mean"]
        if bm is not None and cm is not None:
            diffs.append((q, bm, cm, bm - cm))
    if not diffs:
        return ""
    diffs.sort(key=lambda x: abs(x[3]), reverse=True)
    top3 = diffs[:3]
    rows = ""
    for q, bm, cm, gap in top3:
        gc = "#1a7f37" if gap > 0 else "#cf222e"
        direction = "高于" if gap > 0 else "低于"
        rows += (f"<tr><td><b>{q}</b> {esc(meta['question_short'][q])}</td>"
                 f"<td class='num'>{bm}</td><td class='num'>{cm}</td>"
                 f"<td class='num' style='color:{gc}'>{direction}公司 {abs(gap):.2f}</td></tr>")
    return ('<h2>本部特质诊断 <span style="font-weight:400;font-size:12px;color:var(--sub)">'
            '与公司差异最大的3题</span></h2>'
            '<div class="insight" style="border-left-color:#8250df"><b>解读</b>：'
            '差异最大的题目往往反映事业部负责人的管理风格影响——'
            '若多个部门在这几题上同向偏离，说明根子可能在VP层而非部门层。</div>'
            f'<table><tr><th>题项</th><th class="num">本部</th><th class="num">公司</th>'
            f'<th class="num">差异</th></tr>{rows}</table>')


def cross_dept_common(bu, meta):
    """跨部门共性：本部多个部门同时低分的题——可能是事业部级问题"""
    dept_list = [d for l2 in bu["l2_units"].values() for d in l2["departments"].values()
                 if not d["suppressed"]]
    if len(dept_list) < 2:
        return ""
    common = []
    for q in meta["questions"]:
        low_depts = [d for d in dept_list if d["questions"][q]["mean"] is not None and d["questions"][q]["mean"] < 4.0]
        if len(low_depts) >= 2:
            common.append((q, len(low_depts), len(dept_list)))
    if not common:
        return ""
    common.sort(key=lambda x: x[1], reverse=True)
    chips = "".join(
        f'<span class="bu-chip"><b>{q}</b> {esc(meta["question_short"][q])} — '
        f'{cnt}/{tot} 部门低于4.0</span>' for q, cnt, tot in common)
    return ('<h3>跨部门共性短板</h3>'
            '<div class="insight" style="border-left-color:#d4a72c"><b>注意</b>：'
            '以下题目在多个部门同时偏低，提示这是<b>事业部级问题</b>而非个别主管问题，'
            '建议从事业部机制/VP自身管理方式上找原因，而非只要求下属主管改善。</div>'
            f'<div style="margin-top:8px">{chips}</div>')


# ---------- 经理人看板专属组件 ----------

def rootcause_conversation(dept, meta, ins):
    """根因假设 + 1:1对话指南：针对本团队薄弱题项"""
    bots = [q for q in dept["bottom_questions"] if q in meta["questions"]]
    if not bots:
        return ""
    ins_rc = (ins or {}).get("root_causes", {})
    ins_cv = (ins or {}).get("conversation", {})
    parts = []
    for q in bots:
        rc = ins_rc.get(q) or ROOT_CAUSES.get(q, [])
        cv = ins_cv.get(q) or CONVERSATION_GUIDE.get(q, "")
        rc_li = "".join(f"<li>{esc(r)}</li>" for r in rc)
        parts.append(
            f'<div class="block-card" style="border-left-color:#cf222e">'
            f'<h4 style="color:#cf222e">{q} {esc(meta["question_short"][q])} '
            f'<span style="font-weight:400;font-size:12px">均值 {fmt(dept["questions"][q]["mean"])}'
            f' · {polarization_flag(dept["questions"][q].get("dist"), dept["n"])}</span></h4>'
            f'<div class="bdesc"><b>可能的根因</b>（自查哪条最像你的团队）:'
            f'<ul style="margin-left:18px">{rc_li}</ul></div>'
            f'<div style="background:#f6f8fa;border-radius:8px;padding:10px 14px;margin-top:8px">'
            f'<b>1:1 对话指南</b>：{esc(cv)}</div></div>')
    return '<div class="blocks">' + "".join(parts) + '</div>'


def action_timeline(ins, dept, meta):
    """30/60/90天行动清单：有AI撰写则用，否则按薄弱题项自动生成"""
    a30 = (ins or {}).get("actions_30")
    a60 = (ins or {}).get("actions_60")
    a90 = (ins or {}).get("actions_90")
    bots = [q for q in dept["bottom_questions"] if q in meta["questions"]]
    if not a30 and bots:
        a30 = [f"针对 {q} {meta['question_short'][q]}：{ACTION_HINT[q]}" for q in bots[:2]]
    if not a30:
        a30 = ["针对薄弱项制定具体改善动作"]
    if not a60:
        a60 = ["把30天动作嵌入日常：周会目标沟通、项目复盘、员工1对1中持续执行",
               "复盘30天动作落地情况，调整未生效的，把已验证的固化为团队惯例"]
    if not a90:
        a90 = ["回看约定动作是否持续执行，员工是否感受到变化",
               "通过1对1和团队会议校验改善效果，识别是否出现新问题"]
    def tl(title, items, color):
        li = "".join(f"<li>{esc(x)}</li>" for x in items)
        return (f'<div class="tl-card" style="border-top:3px solid {color}">'
                f'<div class="tl-h" style="color:{color}">{title}</div><ul>{li}</ul></div>')
    return '<div class="timeline">' + tl("30 天 · 立即行动", a30, "#cf222e") + \
           tl("60 天 · 巩固固化", a60, "#d4a72c") + tl("90 天 · 复盘放大", a90, "#1a7f37") + '</div>'


def employee_type_distribution(data, bu_name, meta):
    """员工类型分布：VP视角 · 本BU下所有三级部门 100% 堆叠条形图，按二级单位分组"""
    bu = data["business_units"].get(bu_name, {})
    rows = []
    for l2n, l2 in sorted(bu.get("l2_units", {}).items()):
        for dn, d in l2["departments"].items():
            if d["suppressed"]:
                continue
            eng = d["engagement"]
            gm = d["grand_mean"] or 0
            # 把敬业拆成 激发(4.0-4.5) / 高效(>=4.5)
            ratio = max(0.0, min(1.0, (gm - 4.0) / 1.0))
            high = eng["engaged_pct"] * ratio
            inspired = eng["engaged_pct"] - high
            neg = eng["disengaged_pct"]
            neu = eng["neutral_pct"]
            total = neg + neu + inspired + high
            if total <= 0:
                continue
            # 归一化到 100%，避免舍入误差
            neg, neu, inspired, high = [100 * v / total for v in [neg, neu, inspired, high]]
            rows.append({
                "name": f"{l2n} / {dn}", "n": d["n"], "mean": gm,
                "neg": neg, "neu": neu, "inspired": inspired, "high": high,
            })

    if len(rows) < 2:
        return ""

    # 按消极占比降序，相同则按均值降序
    rows.sort(key=lambda x: (-x["neg"], -x["mean"]))

    # 标题旁的小结论
    top_neg = rows[0]
    title_sub = f"消极型员工占比最高：{top_neg['name']}（{top_neg['neg']:.0f}%）"

    colors = {"neg": "#e52529", "neu": "#9ca3af", "inspired": "#5eead4", "high": "#0d9488"}
    labels = {"neg": "消极", "neu": "中立", "inspired": "激发", "high": "高效"}

    html_rows = []
    for r in rows:
        segs = []
        for key in ["neg", "neu", "inspired", "high"]:
            v = r[key]
            if v > 0:
                text = f"{v:.0f}%" if v >= 8 else ""
                segs.append(f'<div class="etype-seg" style="width:{v:.1f}%;background:{colors[key]}"><span>{text}</span></div>')
        bar = '<div class="etype-bar-wrap">' + "".join(segs) + '</div>'
        neg_cls = " high" if r["neg"] >= 30 else ""
        html_rows.append(
            f'<div class="etype-row">'
            f'<div class="etype-name">{esc(r["name"])}<span class="n">n={r["n"]}</span></div>'
            f'{bar}'
            f'<div class="etype-neg{neg_cls}">{r["neg"]:.0f}%</div>'
            f'</div>'
        )

    legend = "".join(
        f'<span><i class="etype-dot" style="background:{colors[k]}"></i>{labels[k]}</span>'
        for k in ["neg", "neu", "inspired", "high"]
    )

    return (
        f'<div class="etype-chart">'
        f'<div class="etype-head"><div class="etype-title">员工类型分布</div>'
        f'<div class="etype-sub">{esc(title_sub)} · 按个人均分估算 · 消极≥30%标红</div></div>'
        f'{"".join(html_rows)}'
        f'<div class="etype-legend">{legend}</div>'
        f'</div>'
    )


# ---------- 三种看板 ----------

def render_ceo(data, insights):
    meta, comp, bus = data["meta"], data["company"], data["business_units"]
    period = meta.get("period") or ""
    scale_tag = "5 分制"
    body = header_html("CEO看板 · 公司总览", f"CEO 视角 | 全公司 {len(meta['questions'])} 题组织健康调研（盖洛普 Q12 官方）",
                       [period, f"受访 {meta['total_respondents']} 人", f"{len(bus)} 个一级事业部", scale_tag])

    body += hero_section(comp, meta, bus)

    body += sec_head(1, "诊断洞察", "AI 基于数据的总体判断与建议")
    body += insights_html(insights)

    # 卡片网格：四维度得分与系统性诊断并排
    body += "<div class='vp-grid'>"
    benchmark = meta.get("benchmark")
    if benchmark:
        benchmark_source = meta.get("benchmark_source") or "用户提供行业常模"
    else:
        benchmark = DEFAULT_BENCHMARK
        benchmark_source = "缺省参考值（非真实行业调研 · 可在 meta[\"benchmark\"] 覆盖）"
    body += ("<div class='vp-cell'><div class='ct'><span class='dot'></span>四维度得分 "
             "<small>公司均值 vs 行业常模（5 分制）</small></div>" +
             dimension_radar(comp, meta, benchmark=benchmark, benchmark_label="行业常模",
                             benchmark_source=benchmark_source) + "</div>")
    body += ("<div class='vp-cell'><div class='ct'><span class='dot'></span>系统性 vs 局部问题 "
             "<small>12 题得分分布（5 分制）</small></div>" +
             diagnosis_bars(comp, meta) + "</div>")
    body += "</div>"

    # 通栏：一级事业部横向对比
    rows = sorted(bus.items(), key=lambda kv: (kv[1]["grand_mean"] is None, -(kv[1]["grand_mean"] or 0)))
    body += ("<div class='vp-wide'><div class='ct'><span class='dot'></span>一级事业部横向对比</div>"
             + heat_table([(n, u, u.get("percentile_vs_bus")) for n, u in rows], meta, "一级事业部")
             + legend_html(meta) + "</div>")

    body += footnote(meta)
    return page(f"CEO看板-公司总览{('-' + period) if period else ''}", body)


def vp_action_summary(bu, data, meta):
    """VP 行动建议：聚合维度差距、低分部门、共性短板，生成可复制的行动要点。"""
    comp = data["company"]
    pts = []
    # 维度差距（本事业部 vs 公司）
    dg = []
    for d in DIM_ORDER:
        if meta["dimensions"][d]["questions"]:
            bm = bu["dimensions"][d]["mean"]
            cm = comp["dimensions"][d]["mean"]
            if bm is not None and cm is not None:
                dg.append((d, bm, bm - cm))
    dg.sort(key=lambda x: x[2])
    if dg and dg[0][2] <= -0.05:
        d, m, g = dg[0]
        pts.append(f"最弱维度为「{d}」（{m:.2f} 分，低于公司 {abs(g):.2f} 分），建议作为首要补齐方向。")
    if dg and dg[-1][2] >= 0.05:
        d, m, g = dg[-1]
        pts.append(f"优势维度「{d}」（{m:.2f} 分，高于公司 {g:.2f} 分），可沉淀为可复制的标杆做法。")
    # 低于 4.0 的部门
    low_depts = []
    for l2n, l2 in bu["l2_units"].items():
        for dn, d in l2["departments"].items():
            if not d["suppressed"] and d["grand_mean"] is not None and d["grand_mean"] < 4.0:
                low_depts.append((dn, d["grand_mean"]))
    if low_depts:
        low_depts.sort(key=lambda x: x[1])
        names = "、".join(f"{dn}（{m:.2f}）" for dn, m in low_depts[:4])
        pts.append(f"综合均值低于 4.0 分的部门需优先辅导：{names}。")
    # 跨部门共性短板
    depts = [d for l2 in bu["l2_units"].values() for d in l2["departments"].values()
             if not d["suppressed"] and d["grand_mean"] is not None]
    common = []
    for q in meta["questions"]:
        low = [d for d in depts if d["questions"][q]["mean"] is not None and d["questions"][q]["mean"] < 4.0]
        if len(low) >= 2:
            common.append(q)
    if common:
        pts.append(f"共性短板题项：{'、'.join(common[:3])} 等，多个部门同低，宜从事业部机制层面统筹解决而非单点要求主管。")
    if not pts:
        pts.append("本事业部整体处于健康区间，维持现有管理动作并关注边际变化即可。")
    return "<ul class='vp-actions'>" + "".join(f"<li>{esc(p)}</li>" for p in pts) + "</ul>"


def vp_dimension_diagnosis(bu, comp, meta):
    """Gallup Q12 层次诊断（本事业部）：L4→L1 四维度得分，对比公司均值，含状态标签、层间差与诊断洞察。保持 5 分制。"""
    layers = [
        ("L4", "成长发展"),
        ("L3", "团队归属"),
        ("L2", "管理支持"),
        ("L1", "基本需求"),
    ]
    dim_order = [d for _, d in layers]
    sup = bu.get("suppressed", False)

    rows = []
    scores = {}
    comp_scores = {}
    status_list = []
    for level, dim in layers:
        dv = bu["dimensions"].get(dim, {})
        cv = comp["dimensions"].get(dim, {})
        score = None if sup else dv.get("mean")
        cscore = cv.get("mean")
        scores[dim] = score
        comp_scores[dim] = cscore
        if score is None:
            rows.append(
                f'<div style="display:flex;align-items:center;gap:12px;padding:14px 16px;background:#f9fafb;border-radius:10px;margin-bottom:10px">'
                f'<span style="width:36px;height:36px;border-radius:8px;background:#e5e7eb;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#6b7280">{level}</span>'
                f'<div style="flex:1"><div style="font-size:15px;font-weight:700;color:#374151">{esc(dim)}</div><div class="muted">数据不可用</div></div></div>')
            continue
        delta = (score - cscore) if cscore is not None else None
        if score >= 4.0 and (delta is None or delta >= -0.05):
            status, sc, bg = "健康", "#10b981", "#dcfce7"
        elif score >= 3.8:
            status, sc, bg = "关注", "#f59e0b", "#fef3c7"
        else:
            status, sc, bg = "关注", "#f59e0b", "#fef3c7"
        # 瓶颈：四个维度中最低且低于 4.0
        is_bottleneck = score < 4.0 and all(score <= (scores.get(d) or 5) for d in dim_order if scores.get(d) is not None)
        tag_html = (f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;background:{bg};color:{sc}">{status}</span>'
                    + (f'<span style="display:inline-block;margin-left:6px;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;background:#fee2e2;color:#ef4444">▲ 瓶颈</span>' if is_bottleneck else ''))
        delta_html = ""
        if delta is not None:
            dc = "#10b981" if delta > 0 else ("#ef4444" if delta < -0.05 else "#6b7280")
            da = "↑" if delta > 0 else ("↓" if delta < -0.05 else "—")
            delta_html = f'<span style="color:{dc};font-size:13px;font-weight:700;margin-left:8px">{da} {abs(delta):.2f}</span>'
        comp_html = f'<span style="font-size:13px;color:#6b7280;margin-left:10px">公司均值 {cscore:.2f}</span>' if cscore is not None else ''
        rows.append(
            f'<div style="display:flex;align-items:center;gap:12px;padding:14px 16px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;margin-bottom:10px">'
            f'<span style="width:36px;height:36px;border-radius:8px;background:{DIM_COLOR.get(dim,"#e5e7eb")};display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#374151">{level}</span>'
            f'<div style="flex:1"><div style="font-size:15px;font-weight:700;color:#374151">{esc(dim)}</div></div>'
            f'<div style="display:flex;align-items:center;gap:6px"><span style="font-size:26px;font-weight:800;color:#1f2937">{score:.2f}</span>{comp_html}{delta_html}</div>'
            f'<div>{tag_html}</div></div>')
        status_list.append((dim, score, status, is_bottleneck))

    # 层间差
    gaps = []
    gap_desc = []
    for i in range(len(layers) - 1):
        upper_l, upper_d = layers[i]
        lower_l, lower_d = layers[i + 1]
        us, ls = scores.get(upper_d), scores.get(lower_d)
        if us is not None and ls is not None:
            diff = ls - us
            color = "#ef4444" if diff > 0.5 else ("#f59e0b" if diff > 0.2 else "#6b7280")
            arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "—")
            gaps.append((upper_l, lower_l, diff, lower_d, upper_d))
            gap_desc.append(
                f'<span style="display:inline-block;margin-right:16px;font-size:13px;color:#374151">'
                f'<b>{upper_l}→{lower_l}</b>：<span style="color:{color};font-weight:700">{arrow} {abs(diff):.2f} 分</span></span>')
    gap_html = f'<div style="margin-top:8px">{ "".join(gap_desc) }</div>' if gap_desc else ""

    # 诊断洞察
    insights = []
    if status_list:
        # 最大瓶颈
        weak = [x for x in status_list if x[3]]
        if weak:
            names = "、".join(esc(x[0]) for x in weak)
            insights.append(f"<b>最大瓶颈</b>：{names} 处于瓶颈状态，得分低于 4.0，存在「需求断裂」风险，建议优先干预。")
        # 最大层间差
        if gaps:
            gaps_sorted = sorted(gaps, key=lambda x: -abs(x[2]))
            _, _, maxdiff, lower_d, upper_d = gaps_sorted[0]
            if maxdiff > 0:
                insights.append(f"<b>最大层间差</b>：{esc(upper_d)}→{esc(lower_d)} 差 {maxdiff:.2f} 分，低层次需求满足度高于高层次，员工在「{esc(upper_d)}」上的体验未跟上。")
            elif maxdiff < 0:
                insights.append(f"<b>最大层间差</b>：{esc(upper_d)}→{esc(lower_d)} 差 {abs(maxdiff):.2f} 分，呈现正常金字塔结构。")
        # 关键发现
        lowest = min(status_list, key=lambda x: x[1])
        insights.append(f"<b>关键发现</b>：{esc(lowest[0])}（{get_layer_label(lowest[0])}）得分最低（{lowest[1]:.2f}），是本事业部优先改善领域。")
        # 行动建议
        insights.append(f"<b>行动建议</b>：聚焦 <b>{esc(lowest[0])}</b> 维度，优先通过 manager 1:1、反馈与认可机制、资源支持等方式补强短板。")

    insight_html = (f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px">'
                    f'<div style="font-size:14px;font-weight:700;color:#1e40af;margin-bottom:10px">诊断洞察</div>'
                    f'{"".join(f"<div style=\"font-size:13px;color:#374151;line-height:1.7;margin-bottom:6px\">{x}</div>" for x in insights)}</div>')

    return (f"<div class='vp-wide'><div class='ct'><span class='dot'></span>Gallup Q12 层次诊断 "
            f"<small>本事业部 · L4→L1 · 对比公司均值（5 分制）</small></div>"
            f'<div style="display:grid;grid-template-columns:1.5fr 1fr;gap:18px;align-items:start">'
            f'<div>{"".join(rows)}{gap_html}</div>'
            f'<div>{insight_html}</div></div></div>')


def get_layer_label(dim):
    """根据维度名返回 Gallup 层级标签。"""
    return {"基本需求": "L1", "管理支持": "L2", "团队归属": "L3", "成长发展": "L4"}.get(dim, "")


def render_vp(data, bu_name, insights):
    meta, comp = data["meta"], data["company"]
    bu = data["business_units"][bu_name]
    period = meta.get("period") or ""
    n_l2 = len(bu["l2_units"])
    n_dept = sum(len(l2["departments"]) for l2 in bu["l2_units"].values())
    body = header_html(f"VP看板 · {bu_name}", "一级事业部负责人视角 · 经营与人才培养",
                       [period, f"受访 {bu['n']} 人", f"{n_l2} 个二级 / {n_dept} 个三级部门"])
    body += vp_hero(bu, meta, data["business_units"], bu_name, comp)
    # Gallup Q12 层次诊断（通栏：含 L4-L1、公司对比、诊断洞察）
    body += vp_dimension_diagnosis(bu, comp, meta)
    # 并排：二级部门负责人效能象限 + 二级部门维度数据对比
    body += "<div class='vp-grid'>"
    body += (f"<div class='vp-cell'><div class='ct'><span class='dot'></span>二级部门负责人效能象限 "
             f"<small>团队均分 vs 人员间标准差 · 中位数阈值</small></div>"
             f"{manager_quadrant_html(data, bu_name, meta, part='chart')}</div>")
    tree = [(f"{l2n}", l2, l2.get("percentile_vs_l2"), 0, None)
            for l2n, l2 in sorted(bu["l2_units"].items(),
                                  key=lambda kv: (kv[1]["grand_mean"] is None, -(kv[1]["grand_mean"] or 0)))]
    body += (f"<div class='vp-cell'><div class='ct'><span class='dot'></span>二级部门维度数据对比 "
             f"<small>含主管效能标签</small></div>"
             f"<div style='overflow-x:auto'>{heat_table(tree, meta, '二级部门')}</div></div>")
    body += "</div>"
    # 以下模块不再并排，单列通栏
    body += (f"<div class='vp-wide'><div class='ct'><span class='dot'></span>员工类型分布 "
             f"<small>消极/中立/激发/高效 · 按消极占比降序</small></div>"
             f"{employee_type_distribution(data, bu_name, meta)}</div>")
    body += vp_group_insights(bu, meta)
    body += vp_manager_risk(bu, meta)
    # 通栏：VP 行动建议
    body += ("<div class='vp-wide'><div class='ct'><span class='dot'></span>VP 行动建议</div>"
             + vp_action_summary(bu, data, meta) + "</div>")
    # 折叠：逐题得分卡 / 本部特质 / 跨部门共性
    body += ("<details class='vfold'><summary>展开：逐题得分卡 · 本部特质诊断 · 跨部门共性</summary>"
             "<div class='vfold-body'>"
             + bu_trait_diagnosis(bu, comp, meta)
             + cross_dept_common(bu, meta)
             + question_table(bu, meta, [comp], ["公司整体"]) + legend_html(meta)
             + "</div></details>")
    body += footnote(meta)
    return page(f"VP看板-{bu_name}{('-' + period) if period else ''}", body)


def render_manager(data, bu_name, l2_name, dept_name, insights):
    meta, comp = data["meta"], data["company"]
    bu = data["business_units"][bu_name]
    l2 = bu["l2_units"][l2_name]
    dept = l2["departments"][dept_name]
    period = meta.get("period") or ""
    chain = f"{bu_name} / {l2_name} / {dept_name}"
    body = header_html(f"经理人看板 · {dept_name}", "三级部门负责人视角 · 带教与行为改进",
                       [period, f"受访 {dept['n']} 人"], chain=chain)

    if dept["suppressed"]:
        body += ('<div class="insight" style="border-left-color:#f59e0b"><b>数据抑制说明</b><br>'
                 f'本团队参与人数少于 {meta["min_n"]} 人，为保护员工匿名性，不展示具体分数。'
                 "团队数据已计入上级汇总。建议下期提高参与率后查看完整得分卡。</div>")
        body += footnote(meta)
        return page(f"经理人看板-{dept_name}", body)

    body += manager_hero(dept, meta)
    body += ('<div class="privacy-note"><b>使用提示</b>：本看板的分数分布与根因假设用于'
             '<b>诊断团队状态、改进你自己的管理行为</b>，请勿用于追查具体个人。'
             '样本&lt;6人时不显示分布，仅显示均值。</div>')
    body += sec_head(1, "诊断洞察", "AI 基于数据的团队判断与建议")
    body += insights_html(insights)
    # 2×2 卡片网格
    body += "<div class='vp-grid'>"
    body += ("<div class='vp-cell'><div class='ct'><span class='dot'></span>团队健康快照 "
             "<small>四类员工分布</small></div>" + manager_team_snapshot(dept, meta) + "</div>")
    body += ("<div class='vp-cell'><div class='ct'><span class='dot'></span>优先改善项 "
             "<small>根因假设与 1:1 指南</small></div>" + rootcause_conversation(dept, meta, insights) + "</div>")
    body += ("<div class='vp-cell'><div class='ct'><span class='dot'></span>30 / 60 / 90 天行动清单 "
             "<small>按时间盒排优先级</small></div>" + action_timeline(insights, dept, meta) + "</div>")
    body += ("<div class='vp-cell'><div class='ct'><span class='dot'></span>四维度得分 "
             "<small>本部门 vs 公司（5 分制）</small></div>" +
             dimension_radar(dept, meta, compare_unit=comp, compare_label="公司均值") + "</div>")
    body += "</div>"
    # 通栏：逐题对比
    body += ("<div class='vp-wide'><div class='ct'><span class='dot'></span>参考：逐题对比 "
             "<small>团队 vs 二级 / 一级 / 公司 · 四重对照</small></div>"
             + question_table(dept, meta, [l2, bu, comp], [l2_name, bu_name, "公司整体"])
             + legend_html(meta) + "</div>")
    body += footnote(meta, "经理人看板仅供部门负责人本人使用，含主管效能诊断信息，请勿在团队内公开传阅。")
    return page(f"经理人看板-{dept_name}{('-' + period) if period else ''}", body)


def render_unified(data, insights):
    """统一入口页：3张卡片 · CEO/VP索引/经理人索引"""
    meta = data["meta"]
    period = meta.get("period") or ""
    period_suffix = f"-{period}" if period else ""
    body = (f'<div class="entry-head"><h1>组织健康度诊断</h1>'
            f'<div class="sub">基于 {meta["total_respondents"]} 人调研数据 · 三层管理看板统一入口</div></div>')
    ceo_file = f"CEO看板_公司总览{period_suffix}.html"
    vp_idx_file = f"VP索引_选择事业部{period_suffix}.html"
    mgr_idx_file = f"经理人索引_选择部门{period_suffix}.html"
    cards = [
        ("🏢", "CEO 全景报告", "公司级组织健康总览，含健康指数仪表盘、诊断洞察、四维度得分（公司 vs 行业常模）、系统性 vs 局部问题诊断、一级事业部横向对比", "CEO 视角", "#dbeafe", "#1e40af", ceo_file),
        ("📊", "VP 事业部报告", "各一级事业部横向对比，含二级及三级部门热力图（含主管效能标签）、员工类型分布、本部特质诊断、跨部门共性识别、逐题对比", "VP 视角", "#fce7f3", "#be185d", vp_idx_file),
        ("👥", "经理人团队报告", "各三级部门深度诊断，含根因 1:1 对话指南、逐题对比、30/60/90 天行动清单", "经理人视角", "#dcfce7", "#15803d", mgr_idx_file),
    ]
    grid = '<div class="entry-grid">'
    for icon, title, desc, tag, bg, col, href in cards:
        grid += (f'<a class="entry-card" href="{href}">'
                 f'<div class="entry-icon">{icon}</div>'
                 f'<div class="entry-ctitle">{esc(title)}</div>'
                 f'<div class="entry-cdesc">{esc(desc)}</div>'
                 f'<div class="entry-tag" style="background:{bg};color:{col}">{esc(tag)}</div>'
                 f'</a>')
    grid += '</div>'
    body += grid
    body += (f'<div class="entry-foot">'
             f'数据来源：{meta["total_respondents"]} 人调研明细 · 生成时间：{period or "本期"}'
             f' · 题数：{len(meta["questions"])} 题 · '
             f'评级：5 分制</div>')
    return page(f"组织健康诊断QA14-统一入口{period_suffix}", body)


def render_vp_index(data, insights):
    """VP索引页：卡片网格列出所有事业部"""
    meta = data["meta"]
    period = meta.get("period") or ""
    period_suffix = f"-{period}" if period else ""
    bus = data["business_units"]
    body = (f'<div class="entry-head"><h1>选择事业部 · VP 看板</h1>'
            f'<div class="sub">共 {len(bus)} 个一级事业部 · 点击卡片进入对应 VP 看板</div></div>')
    body += '<div class="entry-grid">'
    ranked = sorted(bus.items(), key=lambda kv: (kv[1]["grand_mean"] is None, -(kv[1]["grand_mean"] or 0)))
    for bn, bu in ranked:
        pctl = bu.get("percentile_vs_bus")
        n_l2 = len(bu["l2_units"])
        n_dept = sum(len(l2["departments"]) for l2 in bu["l2_units"].values())
        vp_file = f"VP看板_{safe_name(bn)}{period_suffix}.html"
        body += (f'<a class="entry-card" href="{vp_file}">'
                 f'<div class="entry-icon">📊</div>'
                 f'<div class="entry-ctitle">{esc(bn)}</div>'
                 f'<div class="entry-cdesc">'
                 f'综合均值 <b>{bu["grand_mean"]}</b>（{esc(bu["grand_band"])}）'
                 f' · 内部百分位 <b>{pctl if pctl is not None else "*"}</b><br>'
                 f'下辖 <b>{n_l2}</b> 个二级 / <b>{n_dept}</b> 个三级部门<br>'
                 f'敬业 {bu["engagement"]["engaged_pct"]:.0f}% · 怠工 {bu["engagement"]["disengaged_pct"]:.0f}%'
                 f'</div>'
                 f'<div class="entry-tag" style="background:#fce7f3;color:#be185d">VP 视角</div>'
                 f'</a>')
    body += '</div>'
    body += f'<div class="entry-foot"><a href="组织健康诊断QA14_统一入口总览{period_suffix}.html" style="color:var(--accent);text-decoration:none">← 返回统一入口</a></div>'
    return page(f"VP索引{period_suffix}", body)


def render_manager_index(data, insights):
    """经理人索引页：卡片网格列出所有三级部门"""
    meta = data["meta"]
    period = meta.get("period") or ""
    period_suffix = f"-{period}" if period else ""
    bus = data["business_units"]
    body = (f'<div class="entry-head"><h1>选择部门 · 经理人看板</h1>'
            f'<div class="sub">共 {sum(len(l2["departments"]) for bn, bu in bus.items() for l2 in bu["l2_units"].values())} 个三级部门'
            f' · 按一级事业部 → 二级 → 三级部门列出</div></div>')
    for bn, bu in bus.items():
        body += f'<h3 style="margin:24px 0 12px;font-size:14px;color:var(--sub)">{esc(bn)}</h3>'
        body += '<div class="entry-grid">'
        for l2n, l2 in bu["l2_units"].items():
            for dn, d in l2["departments"].items():
                tag = d.get("manager_tag", "")
                if d["suppressed"]:
                    stats = f'<b>{d["n"]}</b>人 · 数据抑制'
                    tag_color, tag_bg = "#9ca3af", "#f3f4f6"
                    mgr_file = ""
                else:
                    tc = {"优秀标杆": ("#15803d", "#dcfce7"), "需辅导": ("#b91c1c", "#fee2e2"),
                          "稳健": ("#4b5563", "#f3f4f6")}.get(tag, ("#6b7280", "#f3f4f6"))
                    tag_color, tag_bg = tc
                    stats = (f'均值 <b>{d["grand_mean"]}</b> · {esc(d["grand_band"])}<br>'
                             f'敬业 {d["engagement"]["engaged_pct"]:.0f}% · 怠工 {d["engagement"]["disengaged_pct"]:.0f}%')
                    mgr_file = f"经理人看板_{safe_name(bn)}_{safe_name(l2n)}_{safe_name(dn)}{period_suffix}.html"
                body += (f'<a class="entry-card" href="{mgr_file}" style="min-height:160px">'
                         f'<div class="entry-icon">👥</div>'
                         f'<div class="entry-ctitle">{esc(dn)}</div>'
                         f'<div class="entry-cdesc" style="font-size:12px">{esc(l2n)}<br>{stats}</div>'
                         f'<div class="entry-tag" style="background:{tag_bg};color:{tag_color}">'
                         f'{esc(tag) if not d["suppressed"] else "数据抑制"}</div>'
                         f'</a>')
        body += '</div>'
    body += f'<div class="entry-foot"><a href="组织健康诊断QA14_统一入口总览{period_suffix}.html" style="color:var(--accent);text-decoration:none">← 返回统一入口</a></div>'
    return page(f"经理人索引{period_suffix}", body)


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', "_", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--insights", help="AI 撰写的洞察 JSON (可选)")
    ap.add_argument("--type", required=True, choices=["ceo", "vp", "manager", "unified", "vp_index", "manager_index"],
                    help="ceo=CEO看板; vp=VP看板; manager=经理人看板; unified=统一入口; vp_index=VP索引(选事业部); manager_index=经理人索引(选部门)")
    ap.add_argument("--target", default="all",
                    help='vp: 一级事业部名或 all; manager: "一级事业部/二级/三级部门" 或 all')
    ap.add_argument("--outdir", default="reports")
    args = ap.parse_args()

    data = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    period = data["meta"].get("period") or ""
    ins_all = {}
    if args.insights and Path(args.insights).exists():
        ins_all = json.loads(Path(args.insights).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []

    if args.type == "ceo":
        p = outdir / f"CEO看板_公司总览{('-' + period) if period else ''}.html"
        p.write_text(render_ceo(data, ins_all.get("ceo")), encoding="utf-8")
        written.append(p)
    elif args.type == "vp":
        names = list(data["business_units"]) if args.target == "all" else [args.target]
        for n in names:
            if n not in data["business_units"]:
                raise SystemExit(f"未找到一级事业部: {n}")
            p = outdir / f"VP看板_{safe_name(n)}{('-' + period) if period else ''}.html"
            p.write_text(render_vp(data, n, (ins_all.get("vp") or {}).get(n)), encoding="utf-8")
            written.append(p)
    elif args.type == "unified":
        p = outdir / f"组织健康诊断QA14_统一入口总览{('-' + period) if period else ''}.html"
        p.write_text(render_unified(data, ins_all), encoding="utf-8")
        written.append(p)
    elif args.type == "vp_index":
        p = outdir / f"VP索引_选择事业部{('-' + period) if period else ''}.html"
        p.write_text(render_vp_index(data, ins_all), encoding="utf-8")
        written.append(p)
    elif args.type == "manager_index":
        p = outdir / f"经理人索引_选择部门{('-' + period) if period else ''}.html"
        p.write_text(render_manager_index(data, ins_all), encoding="utf-8")
        written.append(p)
    else:  # manager
        pairs = []
        if args.target == "all":
            for bn, bu in data["business_units"].items():
                for l2n, l2 in bu["l2_units"].items():
                    pairs += [(bn, l2n, dn) for dn in l2["departments"]]
        else:
            if args.target.count("/") != 2:
                raise SystemExit('manager 目标格式: "一级事业部/二级/三级部门"')
            parts = args.target.split("/")
            pairs = [tuple(parts)]
        for bn, l2n, dn in pairs:
            bu = data["business_units"].get(bn, {})
            if dn not in bu.get("l2_units", {}).get(l2n, {}).get("departments", {}):
                raise SystemExit(f"未找到三级部门: {bn}/{l2n}/{dn}")
            key = f"{bn}/{l2n}/{dn}"
            p = outdir / f"经理人看板_{safe_name(bn)}_{safe_name(l2n)}_{safe_name(dn)}{('-' + period) if period else ''}.html"
            p.write_text(render_manager(data, bn, l2n, dn, (ins_all.get("manager") or {}).get(key)),
                         encoding="utf-8")
            written.append(p)

    for p in written:
        print("OK", p)


# ---------- VP 看板新增模块：员工群体洞察 / 管理者流失风险 ----------

def _group_bar_chart(title, grp):
    """单一人口学维度的均分横向条形图（5 分制，与全篇统一）。"""
    items = sorted(((k, v) for k, v in grp.items()
                    if v.get("mean") is not None),
                   key=lambda kv: kv[1]["mean"])
    rows = []
    for k, v in items:
        score = v["mean"]
        c = "#ef4444" if score < 3.5 else ("#f59e0b" if score < 4.0 else "#10b981")
        w = max(score / 5 * 100, 1)
        rows.append(
            f'<div style="display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0">'
            f'<span style="width:88px;color:#374151;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{esc(k)}</span>'
            f'<span style="position:relative;flex:1;height:14px;background:#f0f2f4;border-radius:7px">'
            f'<span style="position:absolute;left:0;top:0;bottom:0;width:{w:.1f}%;background:{c};border-radius:7px"></span></span>'
            f'<span style="width:40px;text-align:right;color:{c};font-weight:700">{score:.2f}</span>'
            f'<span style="width:46px;color:#9ca3af">n={v.get("n")}</span></div>')
    return (f'<div style="margin:4px 0"><div style="font-size:13px;font-weight:600;color:#374151;'
            f'margin-bottom:5px">{esc(title)}</div>{"".join(rows)}</div>')


def _group_insight_text(demo):
    """跨维度找出均分最低的群体，生成关键洞察。"""
    name_map = {"gender": "性别", "level": "职级", "tenure": "司龄", "perf": "绩效"}
    lowest = []
    for key, g in demo.items():
        for k, v in g.items():
            score = v.get("mean")
            if score is not None:
                lowest.append((score, name_map.get(key, key), k, v.get("n")))
    if not lowest:
        return ""
    lowest.sort()
    score, dim, k, n = lowest[0]
    return (f'<div class="insight" style="border-left-color:#ef4444">均分最低的群体为'
            f'<b>「{esc(k)}」</b>（{esc(dim)}，均分 {score:.2f}，n={n}），'
            f'建议优先排查其管理支持与团队归属短板，针对性补强。</div>')


def vp_group_insights(bu, meta):
    """员工群体洞察：按司龄 / 职级 / 性别 / 绩效分组的健康度条形图 + 关键洞察。"""
    demo = bu.get("demographics") or {}
    blocks = []
    for title, key in (("按司龄", "tenure"), ("按职级", "level"),
                       ("按性别", "gender"), ("按绩效", "perf")):
        g = demo.get(key)
        if g:
            blocks.append(_group_bar_chart(title, g))
    if not blocks:
        return ""
    ins = _group_insight_text(demo)
    cells = "".join(f'<div class="vp-cell" style="padding:12px 16px">{b}</div>' for b in blocks)
    return (f"<div class='vp-wide'><div class='ct'><span class='dot'></span>本事业部员工群体洞察 "
            f"<small>各群体均分（5 分制 · 仅本事业部员工）</small></div>"
            f"<div class='vp-grid'>{cells}</div>{ins}</div>")


def vp_manager_risk(bu, meta):
    """管理者流失风险分析：仅展示本事业部 P7+ 管理者汇总指标，不展示个人信息。"""
    mgr = bu.get("managers") or {"summary": {}}
    s = mgr.get("summary") or {}
    total = s.get("total", 0)
    cards = [
        ("P7+ 管理者总数", total, "#2563eb"),
        ("高危管理者", s.get("high_risk", 0), "#ef4444"),
        ("需关注", s.get("watch", 0), "#f59e0b"),
        ("稳定", s.get("stable", 0), "#10b981"),
        ("管理者风险率", f"{s.get('risk_rate', 0):.0f}%", "#7c3aed"),
    ]
    cards_html = "".join(
        f'<div class="hero-stat"><div class="hs-label">{esc(l)}</div>'
        f'<div class="hs-value" style="color:{c}">{v}</div></div>' for l, v, c in cards)
    note = '<div class="muted" style="font-size:12px">仅汇总本事业部 P7+ 管理者风险分布，不展示具体员工信息。</div>'
    return (f"<div class='vp-wide'><div class='ct'><span class='dot'></span>管理者流失风险分析 "
            f"<small>本事业部 P7+ 管理者汇总 · 不展示个人信息</small></div>"
            f'<div class="hero-stats" style="margin:4px 0 12px 0">{cards_html}</div>{note}</div>')


if __name__ == "__main__":
    main()
