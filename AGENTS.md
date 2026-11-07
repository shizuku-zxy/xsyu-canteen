# AGENTS.md

## 项目概览

「西石大 · 今天吃啥」—— 西安石油大学（鄠邑校区）食堂推荐应用。源自 https://github.com/shizuku-zxy/xsyu-canteen ，完整保留原项目源码与数据，仅新增沙箱部署配置。

- 档口/菜品/价格/份量/标签/图片结构化数据，可浏览、可筛选、可自然语言提问
- 不填 LLM Key 时使用内置规则引擎推荐（NLU 槽位解析 + 规则打分/放宽/过敏安全门）
- 可选接入 OpenAI 兼容接口（豆包/DeepSeek 等），Key 由页面"模型设置"写入，不入库不入 git

## 技术栈

- **后端**：Python 3.10+ 标准库 `ThreadingHTTPServer`，零第三方依赖
- **前端**：单文件 SPA（原生 JS/CSS，移动端优先 max-width 480px）
- **数据**：SQLite（`04_数据库/数据/app.db`，已内置数据）

## 目录结构

```
02_前端/            # index.html（单文件 SPA，内联 TREE 即数据源）+ img/（档口、菜品、楼层图）+ fonts/
03_服务端/           # 后端
  app.py            # 入口：HTTP 服务 + 路由表 + 静态文件服务
  runtime_env.py    # 路径解析（源码/PyInstaller 双模式）+ ensure_db
  routers/          # 接口层：api_catalog / api_recommend / api_meta / api_llm / api_supabase / api_event
  services/         # 业务层：nlu / slots / recommend / lexicon / llm_* / exposure / persona / profile / sb_client
  dao/db.py         # 数据访问层（唯一写 SQL 的地方）
  tag_dishes.py     # 【2026-11】菜品标签：基线生成 / 子代理终稿合并 / tag 表 + FTS + shop 统计刷新
  export_tree.py    # 【2026-11】把 DB 数据重新生成到 index.html 的内联 TREE（前端浏览/搜索数据源）
  tagging/          # 【2026-11】6 个楼层子代理复核后的标签终稿 JSON（可审计、可重放）
  fix_huxian_menu.py       # 【2026-11】户县软面照片/缺价/加料修订（用户新价目表实拍）
  fill_missing_prices.py   # 【2026-11】其余 34 道缺价菜补齐（实拍读数 + 同类参考价）
  llm_config.example.json  # LLM 配置示例（真实配置 llm_config.json 已 gitignore）
04_数据库/数据/app.db # SQLite 数据库（128 档口、1888 道可推荐菜品、177 加料）
.coze               # 沙箱构建/运行配置（唯一启动依据）
```

## 账号画像推荐（2026-11 新增，"越吃越懂你"）

- **数据流**：前端把推荐结果里的菜品/窗口点击（`window.__sbEvent`，keepalive POST `/api/event`）
  和每次提问（`_log_user_ask`，含 `payload.shown`）上报到 Supabase `user_events` 表 →
  `services/profile.py` 现算画像（事件是唯一事实源，画像不落表）→ 推荐引擎加权
- **画像四维**：canteen_w 常去食堂 / shop_w 常点档口 / tags 口味标签（经 `dao.dish_tags` 反查，
  只有 dish_click 反映口味，ask 槽位不掺入）/ price_p 常消费价位；时间衰减半衰期 14 天；
  冷启动 < 3 条不启用；画像缓存 120s（`_CACHE`）
- **打分融合（services/recommend._score + routers/api_recommend._window_mode）**：
  菜品榜 `+8×canteen_w`、`+4×shop_w`、价位±2 元内 +3、`6×Σtags`（走 recommend() 预留的 pref 通道）；
  窗口榜 `+0.25×canteen_w`。**铁律：画像只加分永不过滤，显式槽位（点名食堂/预算/忌口）永远压过画像**
- **曝光按人隔离**：登录用户的"已推过"由 `user_penalty_map` 从自己近 3 天 ask 事件的
  `payload.shown` 推导（公式对齐 exposure.penalty：age<1 天=0），不再吃全局曝光表；
  `_bump_exposure(items, sid, uid)` 对登录用户跳过全局 bump
- **身份传递**：前端 ask/上报带 `Authorization: Bearer <token>`（`window.__sbToken`）→
  `sb_client.verify_token` 调 GoTrue `/auth/v1/user` 验证，进程内缓存 10 分钟；
  未登录/验证失败一律按匿名降级，行为与旧版完全一致
- **设置面板**：`auth-wipe`「清除口味记忆」→ `profile.clear_user` 删该用户全部事件
- **两处已修复的坑 [坑]**：
  - `app.py _params` 曾把 Authorization 头截断 512 字符（Supabase JWT ~790 → GoTrue bad_jwt），
    现上限 2048；改此处无需改其他
  - PostgREST URL 过滤时间戳必须 URL 安全：`_url_since` 用 `strftime('%Y-%m-%dT%H:%M:%SZ')`，
    `isoformat()` 的 `+00:00` 在 URL 里被读成空格 → 400
- **`services/sb_client.py`**：services 层唯一 Supabase 网络出口（credentials/service_key/
  verify_token/select/insert/delete_all，全带超时容错）；routers 不直连 Supabase

## 构建与运行

- 无构建步骤、无第三方依赖安装
- 启动命令（已由 .coze 定义）：`python3 03_服务端/app.py --port ${DEPLOY_RUN_PORT} --no-browser`
- 端口必须读 `DEPLOY_RUN_PORT` 环境变量，禁止硬编码
- 注意：`app.py` 在指定端口被占用时会自动顺延尝试 port+1 ~ port+10

## API 一览

| 路径 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 健康检查（含 db 存在性） |
| `/api/meta` | GET | 校区→食堂→楼层→档口树（叶子含 dishes/hidden）+ 标签元数据 |
| `/api/shops` | GET | 档口列表（campus/canteen/floor 过滤，q 搜索） |
| `/api/shop` | GET | 档口详情（参数 name） |
| `/api/dishes` | GET | 菜品结构化筛选（budget/tags/exclude 等） |
| `/api/search` | GET | 菜品全文检索（参数 q） |
| `/api/recommend` `/api/ask` | GET/POST | 一句话推荐（参数 q），规则引擎兜底 |
| `/api/llm_config` | GET/POST | LLM 配置状态 / 保存（写运行时目录） |
| `/api/supabase-config` | GET | 下发 Supabase url/anonKey 给前端登录模块 |
| `/api/auth/register` | POST | 用户名+密码注册（后端 admin API，自动确认邮箱） |
| `/api/event` | POST | 行为事件上报（需登录 Bearer token）：dish_click / window_click / ask 落库，`action=clear` 清口味记忆 |
| `/api/phone` | GET | 局域网访问信息 |
| `/`、`/app` | GET | 前端页面 |
| `/img/*`、`/fonts/*`、`/dishimg/*` | GET | 静态资源（有目录穿越防护） |

## 开发注意事项

- 分层纪律：routers 不写业务、services 不碰 SQL、dao 是唯一 SQL 层
- 推荐结果契约字段（has_result/total_count/items/reply_text/engine 等）与前端强绑定，改动需同步
- `llm_config.json`、`exposure.json`、`llm_cache.json` 为运行时生成，已 gitignore，勿提交
- 图片/字体等静态资源在 `02_前端/` 下，新增菜品图放 `02_前端/img/dish/`
- 前端为单文件 index.html（约 2000 行），修改时注意分段编辑

## 前端数据流与图片路径（2026-11 修复）

- **数据源 = 内联 TREE（重要，2026-11 修正认知）**：当前 index.html **没有** `/api/meta` 动态替换逻辑
  （源码里没有该 fetch），`const TREE = {...}` 就是浏览/搜索/筛选的唯一数据源，`ALL = flatten()` 由它展开。
  因此**改了 DB 的价格/标签/照片后必须重跑**：`python projects/03_服务端/export_tree.py --apply`
  （只重写 `const TREE = {...}` 这一段，JSON 压缩一行，字段结构与原快照一致：
  `campus→canteen→floor→[{name,cat,cat_raw,photo,hidden[],dishes[]}]`，
  dish 紧凑字段 `n/p/sp/i/t/cf`，hidden=加料/配菜）。改完 index.html 无需重启服务。
- **户县软面照片**：DB `shop.photo/thumb` = `img/shop-IMG_20261105_154413.jpg`（新价目表实拍 1400×1051），
  `export_tree.py` 里 `PHOTO_OVERRIDE` 同步前端快照 photo（详情页头图用它；列表卡走 SHOP_IMG/FOOD 映射）
- **meta() 字段约定**：tree 叶子节点 dishes 用紧凑字段 `n/p/sp/i/t/cf`（对齐旧内联格式），
  hidden=is_addon=1（加料配菜不参与推荐）；photo/sign_img 已统一补前导 `/`，dish img 已拼 `/dishimg/` 前缀
- **图片路径分层**：dish.img 在 DB 存裸文件名；`/dishimg/` 前缀只在 **export_tree.py**、
  **dao.meta()** 与 **routers/api_recommend.py**（`_items_from_out`、window 分支）拼——
  services 层（recommend._dish_norm）必须保持裸名，否则出现 `/dishimg//dishimg/` 双前缀
- **导航**：bind() 的 `[data-shop]` 点击按优先级取路径：①元素带 `data-dpath`（JSON 绝对路径，
  菜名搜索结果行专用，flatten 时已带 campus/canteen/floor/shop）→ ②已在楼层页（path.length===3）
  且该楼层确有此档口则相对 push（**同名档口必须相对 push，否则 findShopPath 按名匹配第一个会跳错食堂**，
  东/西食堂有 4 个同名档口：全季轻食/江西小炒/秦镇米皮/营养快餐）→ ③`findShopPath(name)` 反查 TREE；
  renderLevel 顶部对 `nodeAt(path)==null` 兜底回首页
- **跳转属性勿混用 [坑]**：旧推荐卡片用 `data-jump="campus|canteen|floor|shop"`（**竖线分隔字符串**，
  由 bind() 里 `[data-jump]` 分支 `split('|')` 解析）；菜名搜索行用 `data-dpath='[...]'`（**JSON**）。
  二者是两套格式、两个独立 handler，**绝对不能给同一元素同时挂 data-shop+data-jump(JSON)**——
  `[data-jump]` 分支会后绑定覆盖 `[data-shop]` 的 onclick，把 JSON 当竖线串 split 出 undefined →
  nodeAt 坏路径 → 防御回退主页（曾导致"搜索点菜品回主页"回归）
- **findShopPath 须在首个 script 块定义**：index.html 有多个 `<script>` 块，跨块顶层 function 互不可见；
  bind()（约 1182 行）调用 findShopPath，故 findShopPath 必须与 bind() 同块（否则点击报
  `ReferenceError: findShopPath is not defined`，即"点窗口点不进去"的根因）
- **详情页头图**：sign_img（价目表实拍）+ photo（档口实拍）多图横滑（.detail-hd .imgs flex），
  都没有时回退 IMGFIX/SIGN/FOOD 静态映射或首张菜品图
- **搜索结果两层折叠（2026-11 重构）**：doSearch 统一走 `renderSearchSplit(hits, kw, hasCond)`，
  结果分「档口」「菜品」两个 `.ssec` 折叠层（点 `.ssec-hd` 切换 `.closed`）：
  - 档口层：纯字面搜索 fetch `/api/shops?q=` 档口名匹配；条件搜索则由菜品 hits 按 shop 反推（`_shopsFromHits`），
    卡片用 `data-jump` 竖线跳档口详情；`_fillShopLayer` 只手动绑 gcard 的 data-jump（不调 bind()，避免重复绑定）
  - 菜品层：菜名/条件匹配的菜名行（`dishSearchRow`，带 data-shop+data-dpath，点击跳档口+flashDish 高亮）
  - 旧的 searchShopsUI/_paintDishHits/renderDishSearch 已被 doSearch 弃用（renderResults 仍服务筛选面板 _showFilterResults，勿删）
- **空楼层折叠 [小吃街]**：小吃街 floor=''（16 店无楼层）。renderLevel dep===2 遇单楼层自动 goTo 进档口列表；
  renderCrumbs 跳过 v==='' 的空段（cur 标最后可见段，data-crumb 仍用原 path 索引）；_backPath 已跳过折叠楼层
- **搜索历史**：`xs_search_history`（localStorage，最多 8 条，最新在前），doSearch 里 pushHist；
  聚焦空搜索框 renderHist 显示 chips，输入/搜索/点外部 hideHist；点 chip 回填并 doSearch，「清空」删记录
- **可点击光标**：搜索结果里 `.dish[data-shop]`（菜品行）与 `.gcard`（档口卡）均 cursor:pointer；
  详情页纯展示菜品行（dishRow，无 data-shop）保持默认 text
- **档口卡封面图**：搜索档口卡 `_shopGcard` 封面**优先用 photo（档口实拍，干净）**，sign_img 仅兜底——
  sign_img 是「价目表/招牌合成图」，图上印有大字（如 sign-027 顶部 37% 是白色招牌字"米渣渣咖喱饭"），
  当封面会在图上部压半个白字（用户反馈"字在框上"）
- **HTML 强防缓存**：`app.py _send` 对 `text/html` 发 `no-store/no-cache/must-revalidate + Pragma + Expires:0`，
  静态资源仍 no-cache。根因：线上=本地新版排版正常，但用户浏览器/WebView 启发式缓存了开发中间态旧版
  （全宽大卡+图溢出），服务器无 ETag/Last-Modified 验证器时 no-cache 拦不住。
  **改 app.py 必须重启 python 生效（无热更）；改 index.html 等静态文件即改即读、无需重启**
- **暗色模式 [坑]**：主题由 `html[data-theme=dark]`（localStorage `xs_theme`，设置面板切换）驱动。
  新增带浅色底的组件**必须同时补一条暗色覆盖**，否则出现"白底浅字看不清"：
  尤其 **ID 选择器**（如 `#cs-panel{background:#fff}`、`#cs-q`）会压过旧的类选择器覆盖，
  必须用 `html[data-theme=dark] #id` 同权重覆盖；气泡内元素还要用 `html[data-theme=dark] .cs-m .bb .x`
  这种更高权重（因为已有 `html[data-theme=dark] .cs-m .bb *{color:…}` 兜底把所有字刷成浅色）。
  2026-11 修复：小油对话面板/气泡/输入区/配置页原先只改了文字色没改底色 → 现由 `#dark-chat-fix` style 块统一覆盖

## 预算语义（2026-11 大改，前后端口径一致）

- `N块以下/以内/内`、`只有/最多/不超过N块` → **上限**（0, N]
- `N到M块` → **区间** [N, M]
- `N块左右/上下/大概`、**裸价格「12块钱」** → **价格点**（around）：窗口 ±max(2, N×15%)，
  由 `_around_window` 对齐全库真实售价索引（`_price_points`），窗口空则吸附最近真实价位；
  放宽档 ±max(3.5, N×30%)；打分按价差 |p−N| 分档 +5/+3/+1
- 前端搜索（parseCond/doSearch/bandsFor）与后端（nlu/recommend）同一口径，改一侧必须同步另一侧
- LLM 路径 prompt（llm_fallback.py）同样约定裸价格 → budget_around=true

## 推荐多样性（换一批）

- **非真随机**：`services/exposure.py` 用 `md5(问题+日期+batch+菜品id)` 生成 0~1 稳定伪随机抖动参与打分；
  同日同问题同 batch 结果幂等，batch+1 种子变 → 排序换；另有 3 天曝光惩罚把近期推过的压一压
- 前端每条 bot 气泡用 `data-q`/`data-batch` 记住自己的问题与批次，点「换一批」= `reroll(该气泡)`
  原地刷新（batch+1），不再重发问题、不新开对话；失败重试（data-retry）保持同 batch 原地重试
- 请求体 batch 字段由前端传入，后端 `make_seed(q, date, batch)` 折成种子

## 登录注册（2026-11 新增，Supabase Auth）

- 左上角设置面板内「账号」行展开 `#setauth` 面板（用户名+密码），替代原手机端二维码功能（二维码 DOM/CSS/JS 已全部移除）
- **用户名映射内部邮箱**：Supabase Auth 只认邮箱，用户名映射为 `{username}@xsyu-eat.local`（前端 `emailOf()` / 后端 `_EMAIL_SUFFIX` 一致，改一侧必须同步另一侧）
- **注册走后端**：`routers/api_supabase.py register()` 用 service_role_key 调 `/auth/v1/admin/users`，`email_confirm:True` 自动确认（假邮箱收不到验证邮件）；用户名正则 `^[一-龥A-Za-z0-9_]{2,20}$`、密码≥6 位；重复注册 422 → "该用户名已被注册"；5xx 自动重试一次（GoTrue 冷启动偶发）
- **登录/登出/会话走前端 SDK**：`02_前端/supabase-js.min.js`（v2 UMD 本地化，零 CDN 依赖）暴露全局 `supabase`；index.html 内 auth IIFE 从 `/api/supabase-config` 拉 url/anonKey（带重试）→ createClient → `signInWithPassword` / `getSession` / `onAuthStateChange`
- **凭证多来源**：env `COZE_SUPABASE_URL/ANON_KEY/SERVICE_ROLE_KEY` → coze_workload_identity SDK 回填；anonKey 可下发前端，service_role_key 严禁出现在前端
- 会话持久化在 localStorage（SDK 默认），刷新自动恢复；「退出登录」调 `signOut`

## 菜品标签体系（2026-11 大改，"标签越多，回应越接近"）

- **词库**：`services/lexicon.py` 是唯一标签词库 ——
  - `TAG_DIM`：全部合法标签及维度（品类/细分/特点/主料/口味/做法；细分是 2026-11 新增的 60+ 个具体食物标签）
  - `FAMILIES`：细分食物家族（菜名命中词 / 要打的标签 / 查询同义词 / 反义词组），如
    馒头家族同时打「馒头/馍/白馍/面食/主食/小吃饼类/蒸」，粥家族同时打「粥/稀饭/粥汤」
  - `MATERIAL_RULES / TASTE_RULES / METHOD_RULES`：菜名→主料/口味/做法（带 avoid 防误伤）
  - `CATEGORY_RULES`：dish.category 是**档口级**品类，只按菜名模式兜底补标（面馆的卤肉小菜不会被标「面」）
  - `COUNTER_TAGS`：反义词。查「稀饭/粥」自动把「米饭」加进排除，绝不拿米饭冒充稀饭
  - `QUERY_TAG_EXTRA / QUERY_KEYWORD_MAP`：口语→标签 / 口语→按菜名关键词（含「炸酱面/水煮肉片/番茄炒蛋」
    这类含单字的复合菜名保护，避免被拆成 炸+酱+面）
- **离线标注**：`03_服务端/tag_dishes.py`
  - `--baseline-dir _work/tags_baseline`：按 lexicon 生成各楼层基线 JSON（供子代理复核，实际已跑）
  - `--merge-dir 03_服务端/tagging --apply`：合并子代理终稿 → 写 dish.tags_str + tag 表 + 重建 dish_fts
    + 刷新 shop 的 dish_count/price_min/max/avg/tags/taste_profile（128 个档口）
  - 当前数据：2065 道菜、平均 6.5 个标签/道、空标签仅 2 条公告脏数据；`tag` 表 137 个标签
- **在线理解（nlu/slots）**：rule NLU 先用 lexicon 最长匹配扫食物词 → 标签 + 直接命中词（terms）+
  菜名关键词（keywords）；`slots.normalize_slots` 归一标签、追加反义排除、保留 terms；
  LLM 路径在 `api_recommend.handle` 里也会用 lexicon 补 terms/food_terms（LLM 只给标签）
- **推荐过滤/排序（services/recommend.py）**：
  - `_pass`：细分标签按**同义标签组**过滤（`lexicon.match_group`），组内任一命中即通过
  - `_score`：细分标签命中 +14（粗品类 +10）；用户原话食物词直接出现在菜名 +25，同义叫法 +10
  - 排序：`_sort_tier`（菜名与用户原词完全相等 -1 → 直接命中 0 → 同义 1 → 仅标签 2）
    → 关键词命中长度（土豆丝 > 土豆）→ 名字长度 → 综合分 `_v`；点名食物且候选≤16 时不再「每档口只出一道」，
    避免同档口的「花卷/肉花卷」被砍到只剩一道
- **前端同步**：`index.html` 里 `ALIAS`（口语→细分标签）、`TAG_ROWS`（筛选面板新增「细分」一行）、
  `tagsFromText`（**最长匹配优先 + 命中即消费**，修「想喝稀饭」同时命中 稀饭+米饭 的老坑）、
  `parseNegText` 的类别词表都跟 lexicon 口径一致；**改标签口径必须同时改前端 ALIAS**
- **回归自测**：`python projects/03_服务端/selftest_queries.py`（64 项：馒头/馍/稀饭/粥/米饭/细分主食/
  直接命中优先/忌口/预算/位置/窗口/闲聊/食材做法口味/换一批幂等），当前全 PASS

## 数据备注

- **缺价已清零（2026-11）**：`04_数据库` 里可推荐菜品不再有 `price IS NULL`。
  - 户县软面 4 道面（金汤肥牛/酸菜肉丝/酸辣臊子/三鲜臊子）按用户提供的新价目表实拍补 11 元，
    并新增「煎蛋/卤蛋 各 1.5 元」（`fix_huxian_menu.py`，幂等可重跑）
  - 其余 34 道由 `fill_missing_prices.py` 补齐：18 道档口价目表实拍读数（confidence=high），
    16 道同店/同类参考价（confidence=low，source 标注清楚）；每道菜的 `source` 字段可追溯
  - 剩余 2 条公告/选项类脏数据（D1919/D1965，`is_recommendable=0`）不参与推荐，保留不动
- 已知边界：`_cn_num` 不支持"百"字（"一百块钱"解析为空预算，降级为随便推荐）；`_CN_NUM_TOPK` 与
  `_CN_NUM` 是两份表，前者仅服务 top_k（1~5），勿互相覆盖
