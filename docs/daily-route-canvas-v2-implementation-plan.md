# 阵容路线 v2 实施计划

基线：`424b2ee`  
分支：`feat/daily-route-canvas-v2`  
工作区：`/tmp/qiubot-daily-route-canvas-v2`

## 交付边界

- 实现按每个 Run 下一次实际观测 Day 的连续分支图，非相邻观测使用跨日虚线标识。
- 同 Day、同全局节点合并，多条父边汇入；从汇流节点继续展开时，进入成员按 `run_id` 并集去重。
- 每个节点详情展示当天推荐阵容 Top 3 和关联牌 Top 8。
- 页面改为可拖动、缩放、按需展开的画布。
- 删除“查询相似真实阵容”跳转。
- 先部署隔离候选环境供用户验收；未经确认不替换生产。

## API 契约

### `GET /api/routes/daily/node`

参数：`version`、`hero`、`day`、`node_id`。

返回全局节点详情：

- 节点核心和全局样本；
- 推荐阵容 Top 3；
- 关联牌 Top 8；
- 推荐榜候选/分母说明。

### `POST /api/routes/daily/expand`

请求：

```json
{
  "version": "latest",
  "hero": "Vanessa",
  "paths": [
    [
      {"day": 3, "node_id": "node-a"},
      {"day": 4, "node_id": "node-c"}
    ],
    [
      {"day": 3, "node_id": "node-b"},
      {"day": 4, "node_id": "node-c"}
    ]
  ]
}
```

约束：

- 所有路径必须以同一 `hero/day/node_id` 结束；
- 路径内部 Day 严格连续；
- 服务端重建成员交集，不接受客户端提供 `run_id`；
- 多条入路成员并集后按 `run_id` 去重；
- 下一层仅查询 `current_day + 1`；
- 动态门槛：`min(15,max(3,ceil(path_observable_runs*3%)))`；
- 返回所有过门槛子节点，不限制前三名；
- 隐藏样本保留在原始可观测分母中。

## 里程碑

1. 后端纯函数与 API 测试 RED。
2. 下一次实际观测路径、汇流并集、门槛逻辑 GREEN。
3. 节点推荐阵容榜和关联牌榜 GREEN。
4. 画布状态合并、拖动缩放和详情侧栏。
5. 真实库只读校准、性能优化和独立审查。
6. 隔离 staging 候选地址，等待用户视觉/交互验收。
