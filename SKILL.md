---
name: org-health-qa14
description: 内部组织健康诊断工具 QA14。基于盖洛普 Q12 官方 12 题员工调研问卷（5 分制，四层：基本需求/管理支持/团队归属/成长发展），从员工明细数据自动生成三个管理者视角的 HTML 看板报告（CEO看板 / VP看板 / 经理人看板）及统一入口总览页。This skill should be used when the user asks to 分析组织健康调研数据、生成敬业度报告、员工问卷诊断、组织健康得分卡, or mentions the internal 12-question engagement survey / QA14.
agent_created: true
---

# 组织健康诊断 (Org Health Diagnosis)

基于内部 12 题组织健康调研问卷，仿盖洛普员工敬业度得分卡的分析逻辑，生成三个管理者视角的看板报告（CEO看板 / VP看板 / 经理人看板）。

## 分析框架（源自盖洛普得分卡逻辑）

- **敬业阶梯**：基本需求(Q1-2) → 管理支持(Q3-6) → 团队归属(Q7-10) → 成长发展(Q11-12)，低层不稳则高层无效
- **逐题得分卡**：当前均值 / 上期均值 / 变化 / 与上级单位对比 / 低分占比 / 评级
- **内部百分位**：单位综合得分在同级单位中的排名（替代盖洛普外部数据库百分位）
- **数据抑制**：参与人数 < 4 的单位不显示分数（保护匿名性），数据仍计入上级汇总
- **最高/最低题项**：自动识别每个单位的优势项与短板项

## 工作流程

### 第 1 步：确认输入数据

要求用户提供员工明细表（CSV 或 XLSX）。**列名无需固定**，脚本按表头文字自动识别：

- **组织列**：表头含「一级」「三级」（及可选「二级」）的列，自动识别为 一级事业部 / 二级 / 三级部门
- **题项列**：Q1~Q12 按题面关键词自动识别；缺失题自动跳过，维度只聚合存在的题
- 其余列（开放文本题、员工 ID 等）自动忽略

题目全文与维度映射见 `references/questionnaire.md`。个别题缺答允许（留空），整行无效数据会被跳过并记录警告。

### 第 2 步：运行分析脚本

依赖：`openpyxl`（仅 XLSX 需要）。安装到隔离环境：`<managed-python-venv>/pip install openpyxl`

```bash
python scripts/analyze_survey.py --input 明细表.xlsx --output analysis.json --period "2026 H1"
# 有上期数据时（上期运行产生的 analysis.json）：
python scripts/analyze_survey.py --input 明细表.xlsx --output analysis.json --period "2026 H1" --history prev_analysis.json
# 可选 --min-n 调整数据抑制阈值（默认 4）
```

脚本输出 `analysis.json`，包含 公司/事业部/部门 三级的：逐题均值、维度均值、综合均值、评级、敬业/怠工占比、低分占比、内部百分位、Top/Bottom 题项、环比变化。检查 stdout 中的警告条数，若警告多要向用户反馈数据质量问题。

**提示用户妥善保存 analysis.json**——它就是下期调研做环比对比的历史文件。

### 第 3 步：撰写诊断洞察 insights.json

读取 `analysis.json`，为每份报告撰写洞察。撰写前必读：
- `references/interpretation_guide.md` — 评级标准、阶梯逻辑、洞察撰写要求
- `references/action_library.md` — 12 题逐题的低分归因与管理动作库

insights.json 结构：

```json
{
  "ceo":  {"summary": "总体判断(2-3句)", "findings": ["发现1", "..."], "actions": ["建议1", "..."]},
  "vp":       {"<一级事业部名>": {"summary": "...", "findings": ["..."], "actions": ["..."]}},
  "manager":  {"<一级事业部>/<二级>/<三级部门>": {
      "summary": "...", "findings": ["..."], "actions": ["..."],
      "root_causes": {"Q4": ["原因1", "原因2"]},
      "conversation": {"Q4": "1:1对话问题..."},
      "actions_30": ["30天行动1"], "actions_60": ["60天行动1"], "actions_90": ["90天行动1"]
  }}
}
```

- `root_causes` / `conversation` / `actions_30-60-90` 为可选字段，缺省时经理人看板自动用内置根因库与对话模板填充（基于该团队最弱题项），AI 撰写可覆盖以更贴合实际
- 部门数量多时，优先为高风险部门（综合均值 < 4.0）撰写深度洞察，健康部门可写简版（summary + 1-2 条 actions）。被抑制的部门无需撰写

### 第 4 步：渲染 HTML 报告

```bash
python scripts/render_report.py --analysis analysis.json --insights insights.json --type ceo --outdir reports
python scripts/render_report.py --analysis analysis.json --insights insights.json --type vp --target all --outdir reports
python scripts/render_report.py --analysis analysis.json --insights insights.json --type manager --target all --outdir reports
# 单个目标: --type vp --target "智能硬件事业部" / --type manager --target "智能硬件事业部/研发中心/研发一部"
# 统一入口总览（卡片式 · CEO/VP/经理人/需求对比/更新日志 五大入口）:
python scripts/render_report.py --analysis analysis.json --insights insights.json --type unified --outdir reports
# 二级索引页（VP选事业部、经理人选部门）:
python scripts/render_report.py --analysis analysis.json --insights insights.json --type vp_index --outdir reports
python scripts/render_report.py --analysis analysis.json --insights insights.json --type manager_index --outdir reports
```

三种看板报告（同一份数据，三种决策视角，内容差异化而非简单缩放）：
1. **CEO看板**（战略投资视角 · 救谁？改什么政策？）：公司健康度仪表盘（SVG圆环）、敬业阶梯（动态色块宽度）、**干预优先级矩阵**（健康度×影响面四象限散点图，定先救谁）、**系统性vs局部问题诊断**（全司共性=政策级/局部=管理辅导，低饱和横向条形图）、**组织风险仪表盘**（怠工人数/预警部门/高怠工/环比下滑）、一级事业部横向热力对比、穿透「一级→二级→三级」的高风险部门清单（三级部门）
2. **VP看板**（经营与人才培养视角 · 谁要辅导？资源给谁？）：**本部定位**（公司内排名/百分位/引领者-中位-落后者，集成在Hero区）、二级+三级树状热力对比（**含主管效能标签列**：优秀标杆/稳健/需辅导）、**本部特质诊断**（与公司差异最大3题=VP自身风格影响）、**跨部门共性识别**（多部门同低=事业部级问题）、逐题得分卡（对比公司）
3. **经理人看板**（带教与行为改进视角 · 我这季度做什么？）：团队健康度仪表盘（SVG圆环）、**员工类型分布**（同级部门100%堆叠条形图：怠工/从业/激发/高效四分类，本团队高亮）、**根因假设+1:1对话指南**（针对薄弱题项，内置12题根因库与对话模板，可被insights覆盖）、**分数分布与极性标记**（共识偏低vs两极分化，n<6抑制）、参考性逐题对比（团队vs二级/一级/公司四重对照）、**30/60/90天行动清单**（按时间盒排优先级）

报告为自包含 HTML（无外部依赖），可直接浏览器打开或打印为 PDF。

### 第 5 步：交付

用 present_files 展示生成的报告（CEO 报告放首位）。提醒用户：
- 报告含敏感组织数据，分发时注意各负责人只看自己单位的报告
- 保存 analysis.json 供下期环比

## 注意事项

- 分数按中国习惯：报告中环比上升=绿色▲、下降=红色▼（非股票场景，遵循直觉配色）
- 若用户只有已汇总的均值表（无明细），无法计算敬业占比/低分占比/数据抑制，需说明局限并手工构造 analysis.json 或建议改用明细数据
- 不得在报告中出现任何可识别个人的信息；min_n 抑制规则不可关闭为 1
