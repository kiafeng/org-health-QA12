# org-health-qa14

基于盖洛普 Q12 官方 12 题员工调研问卷（5 分制）的组织健康诊断工具。从员工明细数据自动生成三个管理者视角的 HTML 看板报告（CEO 看板 / VP 看板 / 经理人看板）及统一入口总览页。

## 核心特性

- **三视角差异化看板**：同一份数据，三种决策视角，内容差异化而非简单缩放
  - **CEO 看板**：战略投资视角 — 救谁？改什么政策？含健康度仪表盘、干预优先级矩阵、系统性 vs 局部问题诊断、风险仪表盘
  - **VP 看板**：经营与人才培养视角 — 谁要辅导？资源给谁？含二级/三级热力对比（含主管效能标签）、本部特质诊断、跨部门共性识别
  - **经理人看板**：带教与行为改进视角 — 我这季度做什么？含员工类型分布、根因假设 + 1:1 对话指南、30/60/90 天行动清单
- **自包含 HTML 报告**：无外部依赖，可直接浏览器打开或打印为 PDF
- **表头自动识别**：列名无需固定，按表头文字自动识别题项列与组织列
- **数据抑制**：参与人数 < 4 的单位不显示分数，保护员工匿名性
- **环比对比**：支持与上期数据对比，自动计算变化趋势

## 分析框架（源自盖洛普得分卡逻辑）

- **敬业阶梯**：基本需求(Q1-2) → 管理支持(Q3-6) → 团队归属(Q7-10) → 成长发展(Q11-12)，低层不稳则高层无效
- **5 分制评级**：≥4.5 优势 / 4.0-4.49 良好 / 3.5-3.99 关注 / <3.5 预警
- **敬业度三分类**：≥4.0 敬业 / 3.0-3.9 从业 / <3.0 怠工

## 文件结构

```
org-health-qa14/
├── SKILL.md                          # Skill 定义（WorkBuddy AI 助手使用）
├── README.md                         # 本文件
├── LICENSE                           # MIT 许可证
├── scripts/
│   ├── analyze_survey.py             # 数据分析脚本：明细表 → analysis.json
│   └── render_report.py              # 报告渲染脚本：analysis.json → HTML 看板
├── references/
│   ├── questionnaire.md              # 12 题问卷全文与维度映射
│   ├── interpretation_guide.md       # 评级标准、关键信号识别、洞察撰写指南
│   └── action_library.md             # 逐题低分归因与管理动作库
└── sample_data/
    └── sample_survey.csv             # 示例数据（便于快速体验）
```

## 快速开始

### 1. 环境准备

```bash
# 需要 Python 3.8+，安装依赖
pip install openpyxl   # 仅 XLSX 输入需要
```

### 2. 运行分析

```bash
# 从员工明细表生成 analysis.json
python scripts/analyze_survey.py --input 明细表.xlsx --output analysis.json --period "2026 H1"

# 有上期数据时（上期的 analysis.json）：
python scripts/analyze_survey.py --input 明细表.xlsx --output analysis.json --period "2026 H1" --history prev_analysis.json
```

### 3. 撰写诊断洞察（可选但推荐）

读取 `analysis.json`，参考 `references/interpretation_guide.md` 和 `references/action_library.md`，为各层级撰写 `insights.json`。格式见 SKILL.md 第 3 步。

### 4. 渲染 HTML 报告

```bash
# CEO 看板
python scripts/render_report.py --analysis analysis.json --insights insights.json --type ceo --outdir reports

# VP 看板（全部事业部）
python scripts/render_report.py --analysis analysis.json --insights insights.json --type vp --target all --outdir reports

# 经理人看板（全部部门）
python scripts/render_report.py --analysis analysis.json --insights insights.json --type manager --target all --outdir reports

# 统一入口总览页
python scripts/render_report.py --analysis analysis.json --insights insights.json --type unified --outdir reports

# 二级索引页
python scripts/render_report.py --analysis analysis.json --insights insights.json --type vp_index --outdir reports
python scripts/render_report.py --analysis analysis.json --insights insights.json --type manager_index --outdir reports
```

单个目标渲染：
```bash
python scripts/render_report.py --analysis analysis.json --type vp --target "智能硬件事业部" --outdir reports
python scripts/render_report.py --analysis analysis.json --type manager --target "智能硬件事业部/研发中心/研发一部" --outdir reports
```

### 5. 使用示例数据体验

```bash
python scripts/analyze_survey.py --input sample_data/sample_survey.csv --output analysis.json --period "Demo"
python scripts/render_report.py --analysis analysis.json --type ceo --outdir reports
python scripts/render_report.py --analysis analysis.json --type vp --target all --outdir reports
python scripts/render_report.py --analysis analysis.json --type manager --target all --outdir reports
python scripts/render_report.py --analysis analysis.json --type unified --outdir reports
python scripts/render_report.py --analysis analysis.json --type vp_index --outdir reports
python scripts/render_report.py --analysis analysis.json --type manager_index --outdir reports
```

## 输入数据格式

CSV（UTF-8）或 XLSX，第一行为表头。列名无需固定，脚本按表头文字自动识别：

- **组织列**：表头含「一级」「三级」（及可选「二级」）的列
- **题项列**：Q1~Q12 按题面关键词自动识别；也支持直接用 Q1、Q2... 作为列名
- **分数**：1-5 分制
- 其余列自动忽略

示例：
```
员工ID,一级事业部,二级,三级部门,Q1,Q2,Q3,Q4,Q5,Q6,Q7,Q8,Q9,Q10,Q11,Q12
E001,智能硬件事业部,研发中心,研发一部,4,4,4,3,3,4,4,4,4,3,3,4
```

## 在 WorkBuddy 中使用

本仓库同时是一个 WorkBuddy Skill。将仓库克隆到 `~/.workbuddy/skills/org-health-qa14/` 后，在 WorkBuddy 对话中直接说"分析组织健康调研数据"即可触发。

## 许可证

MIT License - 详见 [LICENSE](LICENSE)
