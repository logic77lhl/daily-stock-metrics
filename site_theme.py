# -*- coding: utf-8 -*-
"""站点设计系统 —— **唯一**的样式来源。

## 为什么要有这个文件

在这之前，样式分散在 6 个地方，各自定义一套：

| 页面族 | 容器宽 | 表格 td 内边距 | h1 |
|---|---|---|---|
| 个股页 | 1020px | 8px 9px | （无 h1） |
| 榜单 | 1040px | 9px 10px | 25px |
| 日期页 | 1400px | 10px | 24px |
| 顶层页 | 1180px | 7px 8px | 23px |
| 回测页 | 1500px | 10px | 24px |
| 价值页 | 960px | 8px 6px | 20px |

结果是「6 种方言共享一个调色板」：颜色对得上，字号、间距、圆角全对不上。
更糟的是处理方式也不统一 —— 有的原生深色、有的靠覆盖层打 `!important`、
有的在复制文件时注入。**打补丁永远补不出一致性**，只会补出更多分叉。

## 用法

    from site_theme import page_css
    html = f"...<style>{page_css()}</style>..."

需要页面专属规则时传 `extra`（只放该页特有的，比如榜单的固定列宽）：

    page_css(extra="table.g{...}")

## 规范值（改这里，全站跟着变）

* 容器 1080px —— 6 种宽度里取中位，表格 8 列时不挤、正文不超过 90 字符
* 正文 14.5px/1.65 —— 中文正文舒适区间
* h1 24 / h2 15 / h3 13 —— 三级够用，再多读者分不清层级
* 表格 th 10px、td 9px、字号 13px；数字用等宽字体
* 卡片圆角 12px、内边距 15px 17px
* 胶囊圆角 999px、内边距 5px 12px、字号 12px

这些数字不是审美偏好，是「同一类元素在所有页面必须一样」的约束。
"""

from __future__ import annotations

# ── 规范值 ────────────────────────────────────────────────────
THEME_MARK = "/* site-theme:v1 */"   # 下游靠它识别 theme 样式块

CONTAINER = 1080          # px —— 正文页（个股/榜单/首页/补充页）
CONTAINER_WIDE = 1400     # px —— 密集数据报告（12 列宽表，1080 会挤爆）
                          # 只有两档，不是六档：宽度是「内容密度的函数」，
                          # 不是每个页面各自拍一个数字。
BODY_SIZE = "14.5px"
BODY_LH = "1.65"
H1_SIZE = "24px"
H2_SIZE = "15px"
H3_SIZE = "13px"
TABLE_SIZE = "13px"
CARD_RADIUS = "12px"
CARD_PAD = "15px 17px"

TOKENS = """
:root{
  --bg:#0a0e15; --bg2:#0e141d; --panel:#131b26; --panel2:#17202d;
  --line:#22303f; --line2:#2d3d4f;
  --tx:#e8eef7; --dim:#8fa3ba; --dim2:#7b8fa5;
  --acc:#ffd21e; --acc2:#f7b500;
  --ok:#34d399; --warn:#fbbf24; --bad:#fb7185;
  --blue:#7dd3fc; --violet:#a78bfa;
  --mono:ui-monospace,"Cascadia Code",Consolas,"SF Mono",monospace;
  --r-card:12px; --r-sm:8px; --r-pill:999px;
}
"""

BASE = """
*{box-sizing:border-box;-webkit-text-size-adjust:100%}
::selection{background:rgba(255,210,30,.26);color:#fff}
html{scrollbar-color:var(--line2) transparent;background:var(--bg)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:var(--line2);border-radius:5px}
:focus-visible{outline:2px solid var(--acc);outline-offset:3px}
body{margin:0;color:var(--tx);padding-bottom:40px;
  background:
    radial-gradient(1100px 500px at 12% -10%,rgba(255,210,30,.07),transparent 60%),
    radial-gradient(900px 480px at 92% 0%,rgba(125,211,252,.06),transparent 60%),
    var(--bg);
  font:14.5px/1.65 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}

/* 容器：全站只有这一种宽度 */
.page,.wrap,.container,main{max-width:1080px;margin:0 auto;padding:0 18px 40px}
"""

HEADINGS = """
h1{font-size:24px;font-weight:700;letter-spacing:-.4px;margin:0 0 8px;
  display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
h1 small,.title .code,.h1 .unit{font-size:13px;font-weight:400;color:var(--dim2);
  letter-spacing:0}
h2{font-size:15px;font-weight:700;margin:26px 0 12px;display:flex;
  align-items:baseline;gap:9px;flex-wrap:wrap}
h2 .unit,h2 .hint{font-size:11.5px;font-weight:400;color:var(--dim2)}
h2 .hint{margin-left:auto}
h3{font-size:13px;font-weight:650;margin:22px 0 8px;color:var(--tx);
  display:flex;align-items:baseline;gap:8px}
h3 .cnt{font-weight:400;color:var(--dim2);font-size:11.5px}
.cnt{font:700 11px var(--mono);color:var(--acc);background:rgba(255,210,30,.1);
  border:1px solid rgba(255,210,30,.25);padding:3px 8px;border-radius:6px}
.section-title,.sec-title{font-size:15px;font-weight:700;margin:26px 0 12px}
"""

HEADER = """
header{position:sticky;top:0;z-index:20;backdrop-filter:blur(14px);
  background:rgba(10,14,21,.86);border-bottom:1px solid var(--line);
  padding:14px 0 12px;margin-bottom:20px}
header h1{color:var(--tx)}
header p{color:var(--dim2);font-size:12.5px;margin:6px 0 0}
.title{font-size:21px;font-weight:700;letter-spacing:-.2px;display:flex;
  align-items:baseline;gap:8px;flex-wrap:wrap}
.title .ind{font-size:11px;color:var(--dim);background:var(--panel);
  border:1px solid var(--line);padding:3px 9px;border-radius:var(--r-pill);
  font-weight:500}
.prow{display:flex;align-items:baseline;gap:12px;margin-top:7px;flex-wrap:wrap}
.prow .px{font:700 23px/1.1 var(--mono);color:var(--tx)}
.prow .chg{font:600 14px/1 var(--mono)}
.meta{font-size:11.5px;color:var(--dim2);letter-spacing:.2px}
.breadcrumb{display:inline-block;margin-top:12px;padding:6px 14px;
  border-radius:var(--r-pill);color:var(--dim);background:rgba(255,255,255,.06);
  border:1px solid var(--line);font-size:12px}
"""

CARDS = """
.card,.icard,.kpi,.mini-card,.summary-card,.panel,.legend,.tabs{
  background:linear-gradient(180deg,var(--panel),var(--bg2));
  border:1px solid var(--line);border-radius:var(--r-card);
  padding:15px 17px;margin-bottom:14px;box-shadow:none;color:var(--tx)}
.card-title,.icard-title,.mini-title,.section-sub{font-size:14px;font-weight:700;
  color:var(--tx);display:flex;align-items:center;gap:8px}
.icard-head{padding:12px 17px;border-bottom:1px solid var(--line);
  font-size:14px;font-weight:700;color:var(--tx)}
.icard-body{padding:14px 17px;font-size:13px}
.icard{margin-bottom:14px;min-width:0}
.icard-body .chip2{margin:2px 4px 2px 0}
.k-label{font-size:11px;color:var(--dim2);letter-spacing:.3px}
.k-value{font:800 17px/1.2 var(--mono);color:var(--acc);margin-top:6px}
.mini-note,.muted,.more{color:var(--dim2);font-size:11.5px}
.lede{margin:16px 0 0;font-size:15px;line-height:1.75;max-width:76ch;color:var(--tx)}
.lede .n{font-family:var(--mono);font-weight:700;color:var(--acc)}

/* KPI 汇总卡网格：报告页 / 回测页共用。
   曾经只写在 site_css 的 report extra 里，结果回测页的卡片竖着堆 ——
   同一类元素必须在 theme 里，页面 extra 只放该页独有的。 */
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:12px;margin-bottom:20px}
.summary-card .num{font-size:26px}
.summary-card .label{font-size:12px;color:var(--dim2);margin-top:4px}
.card-red .num{color:var(--bad)}.card-green .num{color:var(--ok)}
.card-orange .num{color:var(--warn)}.card-blue .num{color:var(--blue)}
.card-purple .num{color:var(--violet)}.card-gold .num{color:var(--acc)}
.card-gray .num{color:var(--dim)}
/* 警示条 */
.warn-strip{margin:14px 0;padding:12px 15px;border-radius:var(--r-sm);
  border:1px solid rgba(255,210,30,.28);border-left:3px solid var(--warn);
  background:rgba(255,210,30,.06);color:var(--dim);font-size:12px;line-height:1.8}
.warn-strip b{color:var(--tx)}

/* 信号卡片：5 张并排 */
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin:16px 0 0}
.stat{background:linear-gradient(180deg,var(--panel),var(--bg2));
  border:1px solid var(--line);border-radius:var(--r-card);padding:13px 14px;
  min-width:0}
.stat .k{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--dim2);
  letter-spacing:.4px;white-space:nowrap}
.stat .k .dot{width:6px;height:6px;border-radius:50%;flex:none}
.stat .v{font:800 21px/1.15 var(--mono);margin-top:9px;letter-spacing:-.5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat .n{font-size:11px;color:var(--dim);margin-top:6px;line-height:1.45;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.stat.ok .v{color:var(--ok)}
.stat.warn .v{color:var(--warn)}
.stat.bad .v{color:var(--bad)}
.stat.na .v{color:var(--dim2)}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}
.dot.bad{background:var(--bad)}.dot.na{background:var(--dim2)}

/* 结论条 */
.sum{margin-top:12px;padding:13px 16px;border-radius:var(--r-card);font-size:14px;
  font-weight:660;border:1px solid var(--line2);background:var(--panel2);
  display:flex;align-items:center;gap:10px}
.sum .mk{width:3px;align-self:stretch;border-radius:2px;background:var(--dim2)}
.sum.ok .mk{background:var(--ok)}
.sum.bad .mk{background:var(--bad)}
"""

TABLES = """
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;
  font-feature-settings:"tnum" 1;font-size:13px;margin-top:12px}
thead th{position:sticky;top:64px;background:var(--bg);z-index:1}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid var(--line);
  white-space:nowrap}
th{font-size:11.5px;font-weight:600;color:var(--dim2);letter-spacing:.3px;
  padding:10px;border-bottom:1px solid var(--line2)}
th.k,td.k{text-align:left;font-weight:500;color:var(--tx);position:sticky;left:0;
  background:var(--bg);z-index:2}
td{font-family:var(--mono);font-size:12.5px}
td.k{font-family:inherit;font-size:13px}
tbody tr:hover td{background:rgba(255,255,255,.018)}
tbody tr:last-child td{border-bottom:0}
/* 语义：pos/neg 是基本面好坏；up/down 是涨跌（A 股红涨绿跌） */
.pos{color:var(--ok)}
.neg{color:var(--bad)}
.up{color:var(--bad)}
.down{color:var(--ok)}
.na,.mut{color:var(--dim2)}
td.ind{color:var(--dim);font-size:12px;text-align:left;font-family:inherit}
td.why{color:var(--ok);font-weight:700;letter-spacing:2px;font-size:12px}
.tblwrap{overflow:auto;border:1px solid var(--line);border-radius:var(--r-sm)}
/* 宽表兜底：列多到装不下时横向滚动，而不是把右侧列裁掉 */
.icard-body,.opp-grid{overflow-x:auto}
.tblwrap table{margin-top:0}
/* 年度表趋势行 */
.trow td{background:rgba(255,210,30,.045);color:var(--dim);font-size:11.5px;
  font-weight:600;border-top:1px solid var(--line2);border-bottom:0}
.trow td.k{color:var(--dim2);background:rgba(255,210,30,.045)}
"""

PILLS = """
.chip,.chip2,.pill,.bt,.arch,.tag,.temp,.filter-btn{
  display:inline-flex;align-items:center;gap:5px;padding:5px 12px;
  border-radius:var(--r-pill);background:var(--panel);border:1px solid var(--line);
  color:var(--dim);font-size:12px;white-space:nowrap;line-height:1.4}
.chip2{font-size:11.5px;padding:3px 9px}
.tag{font:600 10.5px/1 var(--mono);color:var(--acc);background:rgba(255,210,30,.1);
  border-color:rgba(255,210,30,.25);padding:4px 8px;border-radius:6px}
.pill.hot{border-color:rgba(255,210,30,.35);background:rgba(255,210,30,.12);
  color:var(--acc)}
.filter-btn,.tab-btn{cursor:pointer;transition:.16s}
.filter-btn:hover,.tab-btn:hover{border-color:var(--line2);color:var(--tx)}
.filter-btn.active,.tab-btn.active{background:rgba(255,210,30,.14);color:var(--acc);
  border-color:rgba(255,210,30,.35)}
.tabs{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding:6px}
.tabs::-webkit-scrollbar{display:none}
.tab-btn{flex:1 0 auto;min-width:120px;border:1px solid transparent;
  background:transparent;font-size:13px;font-weight:600;color:var(--dim)}
.flag,.signal{display:inline-block;padding:2px 10px;border-radius:var(--r-pill);
  font-size:12px;font-weight:600;min-width:50px;font-family:inherit}
.flag.overbought,.signal.overbought_resonance{background:rgba(251,113,133,.14);color:var(--bad)}
.flag.oversold,.signal.oversold_resonance{background:rgba(52,211,153,.14);color:var(--ok)}
.flag.newlow,.signal.newlow_resonance{background:rgba(251,191,36,.14);color:var(--warn)}
.flag.normal,.signal.partial,.signal.insufficient{background:rgba(255,255,255,.06);color:var(--dim2)}
.signal.resonance_strong{background:rgba(125,211,252,.14);color:var(--blue)}
.signal.resonance_weak{background:rgba(167,139,250,.14);color:var(--violet)}
.signal.divergence_dw,.signal.divergence_wd{background:rgba(255,210,30,.14);color:var(--acc)}
"""

MISC = """
details{margin-top:9px;border:1px solid var(--line);border-radius:var(--r-sm);
  padding:0 14px;background:rgba(255,255,255,.012)}
details summary{cursor:pointer;padding:11px 0;font-size:12.5px;color:var(--dim);
  list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";color:var(--dim2)}
details[open] summary::before{content:"▾ "}
details table{margin-bottom:12px}
.fn,.note{font-size:11px;color:var(--dim2);margin:9px 0 0;line-height:1.75}
.fn b,.note b{color:var(--dim)}
.ft,.footer{color:var(--dim2);font-size:11px;text-align:center;margin-top:34px;
  line-height:1.9}
.strip{margin:8px 0 0;font-size:12px;color:var(--dim);line-height:1.85;
  font-variant-numeric:tabular-nums}
.strip .strip-k{display:inline-block;min-width:44px;color:var(--dim2);font-size:10.5px;
  letter-spacing:.06em;margin-right:9px}
.strip .strip-d{color:var(--dim2);font-size:10.5px;margin-left:10px}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 6px}
.nav a{font-size:12px;color:var(--dim);border:1px solid var(--line);
  border-radius:var(--r-pill);padding:5px 13px;background:var(--panel);transition:.16s}
.nav a:hover{color:var(--acc);border-color:rgba(255,210,30,.35);text-decoration:none}
.nav a.on{color:var(--acc);border-color:rgba(255,210,30,.35);
  background:rgba(255,210,30,.1)}
.nav a.back{color:var(--tx);font-weight:600}
th[data-dir]:after{content:" ▲";font-size:8px;color:var(--acc)}
th[data-dir="desc"]:after{content:" ▼"}
.legend{margin:2px 0 16px;font-size:11.5px;color:var(--dim2);line-height:1.9}
.legend b{color:var(--dim);font-weight:600}
.verdict{border-left:3px solid var(--line2);padding:10px 0 10px 14px;
  margin-top:26px;font-size:13.5px}
.verdict.warn{border-left-color:var(--warn)}
.verdict.ok{border-left-color:var(--ok)}
"""

LAYOUT = """
/* 布局网格：首页/洞察页用到的分栏容器。都归 theme，页面不再自己定义。 */
.kpi-row,.stats{display:grid;gap:9px}
.kpi-row{grid-template-columns:repeat(4,1fr)}
.dsm-brd-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.opp-grid{display:grid;grid-template-columns:1fr;gap:14px}
/* 超跌/超买这两张表各有 10 列，2 列网格里每边只有 500px，右侧列会被截断。
   整宽堆叠才装得下。 */
.sec-row,.dsm-sec-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.dsm-sec-col,.v-item,.v-mkt,.mini-card{background:var(--panel);
  border:1px solid var(--line);border-radius:var(--r-card);padding:13px 15px}
.v-row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.dash{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:900px){
  .kpi-row{grid-template-columns:repeat(2,1fr)}
  .sec-row,.dsm-sec-cols,.dash,.v-row{grid-template-columns:1fr}
}
"""

RESPONSIVE = """
@media(max-width:900px){
  .stats{grid-template-columns:repeat(2,1fr)}
  .stat .v{font-size:18px}
  .page,.wrap,.container,main{padding:0 14px 30px}
  h1{font-size:21px}
  table{font-size:12px}
  th,td{padding:7px 6px}
  thead th{top:56px}
}
@media(max-width:560px){
  .stats{grid-template-columns:1fr 1fr}
  .tabs{flex-wrap:wrap}
  .tab-btn{flex:1 1 calc(50% - 6px);min-width:0}
}
"""


def page_css(extra: str = "") -> str:
    """返回完整样式表。

    `extra` 只放**该页特有**的规则（比如榜单的固定列宽）。
    通用规则一律不在这里重复 —— 重复就意味着以后会分叉。
    """
    # 稳定标记：让下游能可靠识别「这是 theme 产出的样式块」，
    # 而不必去匹配某个具体规则的字面文本（那会让样式重构变成测试失败）。
    parts = [TOKENS, BASE, HEADINGS, HEADER, CARDS, TABLES, PILLS, LAYOUT,
             MISC, RESPONSIVE]
    css = THEME_MARK + "\n" + "\n".join(p.strip() for p in parts)
    if extra:
        css += "\n\n/* 页面专属 */\n" + extra.strip()
    return css


if __name__ == "__main__":
    out = page_css()
    print(f"page_css() 共 {len(out)} 字符")
    print(f"容器 {CONTAINER}px · 正文 {BODY_SIZE} · h1 {H1_SIZE} · "
          f"h2 {H2_SIZE} · 表格 {TABLE_SIZE} · 卡片圆角 {CARD_RADIUS}")


# ── 统一导航 ──────────────────────────────────────────────────
# 为什么要有：之前每个子页面都没有回首页的路，用户点进去就出不来。
# 现在全站一套导航，`prefix` 用于从顶层页（docs/）指向 基本面/ 子目录。
_NAV = [
    ("{p}index.html", "买什么", "四信号榜单"),
    ("{p}行业.html", "看行业", "行业排行 + 成员"),
    ("{p}Q3财报季.html", "财报季", "本季横向对比"),
    ("{p}格雷厄姆.html", "看长期", "七条标准"),
    ("{p}定投固定股数.html", "定投", "每月 100 股的回测"),
    ("{p}index.html", "全部标的", "380 只可排序清单"),
]


def nav(prefix: str = "", home: str = "", current: str = "") -> str:
    """返回统一导航条。

    prefix —— 从当前页到 `基本面/` 的相对前缀（顶层页传 "../基本面/"）
    home   —— 回首页的相对路径（基本面/ 下传 "../index.html"）
    current—— 当前项的标签，用于高亮
    """
    out = ['<div class="nav">']
    if home:
        out.append(f'<a class="back" href="{home}">← 首页</a>')
    for href, label, tip in _NAV:
        h = href.format(p=prefix)
        cls = ' class="on"' if label == current else ""
        out.append(f'<a href="{h}" title="{tip}"{cls}>{label}</a>')
    out.append('</div>')
    return "".join(out)


# ── 可排序表格（客户端，~20 行，无依赖）────────────────────────
# 为什么现在才加：我最初把「零 JS」当原则，结果 380 行的表只能按生成器
# 决定的顺序看 —— 用户要按股息率排、按 PE 分位排，都做不到。
# 原则应该服从需求：静态文件 + 20 行原生 JS 仍然没有构建步骤、没有服务端。
SORT_JS = """<script>
(function(){
  document.querySelectorAll('table[data-sort]').forEach(function(t){
    var tb=t.tBodies[0], hd=t.tHead.rows[0];
    Array.prototype.forEach.call(hd.cells,function(th,i){
      if(th.hasAttribute('data-nosort'))return;
      th.style.cursor='pointer';
      th.addEventListener('click',function(){
        var d=th.getAttribute('data-dir')==='asc'?'desc':'asc';
        Array.prototype.forEach.call(hd.cells,function(x){x.removeAttribute('data-dir')});
        th.setAttribute('data-dir',d);
        var rs=Array.prototype.slice.call(tb.rows);
        rs.sort(function(a,b){
          var x=a.cells[i].getAttribute('data-v'),
              y=b.cells[i].getAttribute('data-v');
          var nx=parseFloat(x), ny=parseFloat(y);
          var v=(x!==null&&y!==null&&!isNaN(nx)&&!isNaN(ny))
                ? nx-ny
                : String(x||'').localeCompare(String(y||''),'zh');
          return d==='asc'?v:-v;
        });
        rs.forEach(function(r){tb.appendChild(r)});
      });
    });
  });
})();
</script>"""
