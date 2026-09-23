# 前端

Vite + React 18 + TypeScript + Tailwind 3。约 3100 行。

## 设计基准

以 `UI原型图.html` 为设计基准 1:1 还原。
**CSS 变量与组件类是从原型图原样搬过来的**（`src/index.css`），
只把 Tailwind CDN 换成了真实构建。

改样式时优先动 token，别在组件里散落硬编码颜色：

```css
--bg --panel --panel2 --line --txt --sub --brand --ok --warn --err
```

组件类：`.card .card2 .btn .btn-p .btn-ok .badge .b-ok/.b-warn/.b-err/.b-info
.inp .step .dot .tab .tbl .msg .num .kbd`

`.num` 用等宽字体 + `tabular-nums`，所有数字都该带上它——
表格里的数字不对齐会显得很业余。

## 目录

```
src/
├── App.tsx            页面路由（四页切换）、模式偏好、health 轮询
├── api.ts             fetch 封装 + RequestError
├── types.ts           与 server/schemas.py 一一对应
├── download.ts        Blob 下载
├── components/
│   ├── Header.tsx     顶栏：模式切换 + 状态徽章 + 导航
│   ├── StepNav.tsx    左侧阶段导航 + stageStates() 状态推导
│   ├── CalcCard.tsx   阶段 3 计算卡片（含四种异常态）
│   ├── SidePanel.tsx  右侧：AI 助手 / 依据缓存
│   ├── ui.tsx         Badge / Alert / Spinner / 状态与置信度映射
│   ├── Modal.tsx      对话框壳
│   ├── BindDialog.tsx 账号绑定引导
│   ├── VerifyDialog.tsx 双源核验
│   └── PointsDialog.tsx 数据点补录
└── pages/
    ├── Home.tsx       项目中心：自然语言输入、物料入口、最近项目
    ├── Workbench.tsx  工作台：阶段 0~6
    ├── Knowledge.tsx  知识库：缺口清单 / 数据表 / 可达性矩阵
    └── Settings.tsx   设置：账号绑定、用量上限、环境信息
```

## 核心约定

### 1. 界面不做计算，只渲染 trace

阶段 3/4/5 的每一个数字都直接来自 `SelectionTrace`。
前端**不做任何算术**，也不维护第二份展示数据。

包括格式化：数值显示用后端给的 `value_display`，不在前端二次 `toFixed`——
两处格式化迟早会不一致。

### 2. 阶段状态由 trace 推导，不自己记账

`StepNav.stageStates(trace, procure)` 是唯一的状态来源。
界面不维护"我走到第几步了"这种本地状态。

### 3. 七种状态各有各的措辞

`ui.tsx` 的 `STATUS` 映射是措辞的单一来源。三组必须区分开的：

| | |
|---|---|
| `data_missing` ⛔ | "该工况点没有数据" → 去补知识库 |
| `no_solution` 🔧 | "该系列内无解" → **去改设计**，补数据没用 |
| `skipped` · | "未执行"——前面出错没跑到 |
| `not_applicable` – | "本分支不适用"——不是错误 |

`CalcCard` 对 `skipped` 和 `not_applicable` 直接不渲染卡片；
`text` 类型的步骤也不渲染（它是标签赋值，不是推导）。

### 4. 分支字段按条件显隐

```tsx
const applies = (i: InputDef) =>
  !i.depends_on || String(values[i.depends_on.field] ?? '') === String(i.depends_on.equals)
```

条件由**服务端**解析成 `depends_on` 传下来。
前端不实现表达式求值——同一套规则两处实现迟早会对不上。

隐藏了几项要明说：「另有 N 项参数属于其它工况分支，已按你的选择隐藏」。

### 5. AI 建议只填进表单，不直接生效

`onApplySuggestions` 把建议写进表单值，用户仍要按「开始计算」。
也就是说它走的是和手输完全一样的那条路。

## 不能退化的几处诚实性

这些是刻意做的，改界面时别顺手"优化"掉：

- **顶栏徽章**反映真实能力（绑定状态 ∧ 网络可达），不是装饰
- **置信度徽章**读自数据表真实 `verification_status`
- **校核区**把「未执行」与「不通过」分开列，并注明"未执行不等于通过"
- **知识库**对没有工作流的物料说"没检查"，而不是"没问题"
- **设置页**不放假的 API key 输入框
- **结果表**模板变量没产出时整行消失，不显示 `{form}`

## 开发

```bash
# 两个终端
.venv/Scripts/python.exe -m server --port 8756   # 后端
cd web && npm run dev                             # 前端 → :5173，自动代理 /api
```

构建：`npm run build` → `web/dist/`，由 FastAPI 直接伺服。

**改完前端一定要重新 build**，否则后端伺服的还是旧产物。
`index.html` 已设 `Cache-Control: no-cache`（它引用带哈希的 bundle 名，
缓存住等于把用户永久钉在旧版本上），但浏览器仍可能有强缓存，调试时用硬刷新。

## 已知未做

- 移动端只有断点隐藏侧栏，没有专门的移动布局
- 无浅色主题（token 已就位，加一组 `[data-theme]` 覆盖即可）
- 无英制单位换算
- 多方案对比视图（SKILL.md 边界情况提到"多解时列 2-3 个方案"，尚未实现）
