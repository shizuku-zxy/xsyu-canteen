# DESIGN.md

## 交互与组件规范

### 光标
- 可点击元素：cursor:pointer（手指）——搜索结果菜品行 `.dish[data-jump]`、档口卡 `.gcard`、折叠层标题 `.ssec-hd`、历史记录 chip
- 纯展示文本：默认光标——详情页菜品行 `.dish[data-dish]`（无 data-jump）、正文

### 反馈动效
- 搜索/问答点菜品跳档口详情后，目标菜品行黄色闪烁定位：`@keyframes dishFlash`（`#FFF4CC` 浅黄底 + `#FCD34D` 金边 inset，0.55s×2 次），由 `_flashDish(name, spec)` 触发，1.5s 自动移除；菜品行需带 `data-dish`/`data-spec` 属性供跳转后定位
- 输入框 `.sbox input`：cursor:text（I 型），且 `-webkit-appearance:none` 去掉 iOS 搜索框胶囊内建样式

### 组件
- **搜索结果两层折叠层 `.ssec`**：分「档口」「菜品」两层；标题栏为白底卡片（圆角 11px、1px 浅灰描边），右侧箭头随折叠旋转 -90°；点击标题栏折叠/展开内容区
- **搜索历史记录 `.shist`**：浅灰圆角胶囊 chip（`.sh-chip`），点击回填并搜索；左侧「最近搜索」小字标签，右侧「清空」小字按钮；存 localStorage，最多 8 条
- **面包屑 `.crumb`**：跳过空楼层段（如小吃街 floor=''），不渲染空白节点
