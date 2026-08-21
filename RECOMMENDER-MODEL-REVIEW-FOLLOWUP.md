# 推荐模型评审复核与整改记录

复核日期：2026-08-21  
原始报告：`RECOMMENDER-MODEL-REVIEW.md`  
复核基线：提交 `fc7c293` 及当前工作区

## 总结

原报告的核心判断基本成立，尤其是分数尺度错配、Discovery 候选截断、负面原因造成的训练/服务漂移、评估未覆盖实际服务排序、模型签名被未标注数据扰动等问题。报告也有一些结论过度扩张：模型状态并非完全不可见，旧模型 sigmoid 不影响排序，视觉中心化和隐式负反馈属于需要实验验证的模型设计选择，不能仅凭静态审查直接定为缺陷。

首轮优先修复确定性行为错误、数据泄漏、不可达分支和低风险运行问题；本次续作完成了原文“未完成与建议”的六项工作，并用可重复 benchmark 决定哪些模型改动应实施、哪些应拒绝。严格历史证据不足时仍不替换生产 fallback，也不把诊断结果伪装成上线证据。

## 逐项裁定

| 项目 | 裁定 | 处理 |
| --- | --- | --- |
| F1 概率多样化分支不可达 | 成立 | 模型和结果显式携带 `score_scale`；分流不再依赖不存在的 v1 版本字符串，并增加 v2 端到端分流测试。 |
| F2 评估没有测量实际服务顺序 | 成立，已完成 | 评估器调用 `recommend_page` 并显式注入该折 artifact；新增不可变 feature snapshot，严格模式只读取标签发生前的 tags/detail/embedding。旧数据不伪造历史，因此当前严格覆盖率为 0，gate 正确拒绝。 |
| F3 模型状态不可见、gate 不可测且永不过期 | 部分成立 | “完全不可见”不准确：页面已有 `Model` 按钮，会显示 `/api/model` 的完整 JSON。gate 的陈旧性成立，现已校验 model schema、feature schema、生成时间（30 天）、评估样本覆盖，并在加载缓存/磁盘 artifact 时重新检查。没有改成分类器式的进程内 gate，因为 F13 证明该分类器原先的交叉验证本身有泄漏，不能作为更可靠范例。 |
| F4 个性化模型测试面不足 | 成立 | 增加真实 v2 分流、概率探索、首个可训练模型校准、超过 100 个 Discovery 候选、签名稳定性、评估报告有效性和反馈原因约束测试。 |
| F5 negative reason 泄漏标签并造成 train/serve shift | 成立 | 删除全部 `1.35` 特征放大；API 拒绝非负反馈携带 negative reason，导入时清除这类非法组合。reason 仅保留为诊断元数据。 |
| F6 50-79 条标签时校准器缺失 | 成立，已完成 | calibration 改为独立的末段时间 holdout；`C` 只在更早的数据上选择，校准样本不再参与超参数选择。当前模型有 207 条独立校准样本。若 holdout 只有单一类别，仍会明确标记不可校准。 |
| F7 探索阈值和 freshness 使用旧分数单位 | 成立 | 概率模型探索使用候选分布的相对分位阈值；freshness 以受限 logit offset 写入 `rank_score`，不再丢失，也不再冒充校准概率。 |
| F8 Discovery 只从前 100 个候选采样 | 成立 | 内部推荐池允许到 10000；Discovery 对完整候选集构造稳定前缀后分页，offset 100 以上不再必然为空；coverage 从几乎恒为 0 的 `min` 改为候选兴趣频率。 |
| F9 旧累加器 ratchet、tag-count bias、概率饱和 | 混合 | tag-count bias 和特定使用方式下的单向累积成立，属于旧模型结构问题，未在本轮无基准替换。sigmoid 饱和不影响排序的反驳成立，但会误导 UI；旧模型现在返回 `like_probability: null`，卡片显示原始 score。 |
| F10 单次反馈/请求成本与并发 | 成立，已完成主要热路径 | `tag_corpus_strengths`、series index、related-feedback index 使用数据库指纹缓存；feedback confidence 从 N+1 改为一次顺序扫描；queue counts 增加轻量缓存。真实库二次调用分别加速约 8.0x、14.1x、20.4x、2778.5x。 |
| F11 未标注图库变化导致无效重训 | 成立 | 模型签名只哈希实际训练样本、标签、顺序和对应特征；新增未标注 gallery 不再改变签名。 |
| F12 bootstrap prior 位于校准之后 | 成立 | `like_probability` 保持模型校准概率；bootstrap 和 freshness 只影响 `rank_score`。评估同时报告服务排序和纯模型排序，prior 正负文案分别显示 boost/penalty。 |
| F13 continuing classifier 泄漏与隐藏项 | 成立部分已完成 | 每折独立拟合预处理器；新增 `random` 探索 holdout 与 `uncertainty` 主动学习样本。随机样本只做逆概率加权独立评估，不回流训练；没有至少 20 条且每类至少 5 条前 classifier 保持 shadow。独立评估只接纳 `full-library` sampling frame。 |
| F14 视觉方向未中心化 | 假设经 benchmark 支持，已局部实施 | 5 折诊断中，视觉中心化将 NDCG@20 从 0.837676 提升到 0.851989、P@10 从 0.88 提升到 0.92，因此应用到 gated personalized visual block。未在严格证据不足时改动当前 serving 的 visual-only/legacy 路径。 |
| F15 正向隐式信号与闭环查询 | 混合 | 未通过 gate 的个性化模型不再生成 learned query；H@H download signal 已加入设置/API，可设为 0，并在变化后重训。把高分 skip 自动当负样本与报告前文“skip 不是负反馈”的正确原则冲突，未采纳。下载完成语义和 query yield 反馈仍值得后续改进。 |
| F16 小项 | 部分成立，block 缩放建议被实测否决 | 统一按 block 平均范数缩放使 NDCG@20 从 0.837676 降到 0.813804、P@10 从 0.88 降到 0.84，因此不实施。mark 强标签语义仍保留；disagreement 与 impression 利用仍是独立产品/实验议题。 |

## 关键实现

- `exh_rec/personalized.py`
  - 模型/特征 schema 升级为 `personalized-content-v4` / `content-features-v6`。
  - 删除 reason-based feature scaling，修正小样本 rolling split 和 calibration。
  - 支持严格时间快照训练、独立时间 holdout calibration 和视觉均值中心化。
  - 模型签名只覆盖实际训练数据；缓存和 artifact 加锁并重新检查 acceptance。
  - learned query 必须来自 `ready && accepted` 的模型。
- `exh_rec/recommender.py`
  - 使用 `score_scale` 区分 probability/additive 排序。
  - 分离 `like_probability` 与 `rank_score`，修复 diversity、exploration、freshness 和 bootstrap prior。
  - 修复 Discovery 候选池、稳定分页、classification override、reason sign gate 和空删除重训。
- `scripts/evaluate_recommender.py`
  - 排名指标改用真实 `recommend_page` 服务顺序。
  - 同时报告 `personalized`（服务排序）与 `personalized_model`（模型本身）。
  - 使用 5 个非重叠测试窗，报告 95% CI、legacy/random/recency/site-rating baseline、快照覆盖率和强化 acceptance。
- `scripts/benchmark_feature_variants.py`
  - 可重复比较 raw、视觉中心化、block 平衡及组合变体；输出仅标为诊断，不可直接通过生产 gate。
- `scripts/classification_sampling.py`
  - 建立随机评估/不确定性主动抽样队列，并支持按 sample id 写入人工标签。
- `exh_rec/classification.py`
  - 每个验证折独立拟合预处理器；训练集与随机 holdout 分离，随机 holdout 使用逆选择概率评估。
- `exh_rec/app.py`、`static/index.html`、`static/app.js`
  - 反馈前快照不训练即将失效的模型。
  - H@H download signal 可在 0-2 之间配置；旧模型卡片不再显示伪概率。
- `exh_rec/db.py`、`requirements.txt`
  - 增加 feature snapshot、classification sample 表及索引；增加 busy timeout、feedback 查询索引，并显式声明 numpy/scipy/joblib。

## 最终真实评估

严格报告位于 `data/recommendation-evaluation.json`。当前 973 个历史非中性反馈全部早于 2026-08-21 启用 feature snapshot，因此 `strict_snapshots: true`、覆盖率 `0.0`、0 个有效折，acceptance 必须为 `false`。这不是实现失败，而是拒绝把今天的 metadata 伪造成过去状态；从现在开始的新反馈/mark 会自动留下可评估快照。

历史诊断报告位于 `data/recommendation-evaluation-diagnostic.json`，明确标记 `strict_snapshots: false`，不能通过生产 gate。它使用 5 个互不重叠的 100 项测试窗：

| 指标 | Legacy | Random | Recency | Site rating | Personalized served | Model-only |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ROC-AUC | 0.616 | 0.466 | 0.623 | 0.584 | 0.778 | 0.818 |
| NDCG@20 | 0.515 | 0.309 | 0.403 | 0.495 | 0.820 | 0.848 |
| Precision@10 | 0.48 | 0.32 | 0.42 | 0.52 | 0.84 | 0.90 |
| ECE | 0.622 | 0.247 | 0.607 | 0.559 | 0.095 | 0.095 |

served NDCG@20 相对最佳 baseline 的 95% 差值区间为 `[0.192446, 0.417427]`，P@10 差值区间为 `[0.120118, 0.519882]`。除严格快照覆盖外其余 gate 均通过。因此结论是：个性化模型非常值得保留，但不能据此替换 legacy fallback；待未来严格快照覆盖达到 80% 且至少 3 折后再自动裁定。

当前 artifact 为 `personalized-content-v4-b475265303785041`，`ready: true`、独立 calibration 207 条、`accepted: false`。continuing classifier 的 55 条旧 override 全部是 `updates`，没有 `review`，目前保持 `insufficient-data`。数据库中有 60 条待标注样本：早期 30 条已标记为 `legacy-unknown`、不进入无偏评估；新的 `full-library` 30 条包含 9 条随机 holdout 与 21 条主动样本。

## 六项建议完成情况

1. **时间快照：完成。** 新 schema 和所有 metadata/visual/feedback/mark 写路径已接入；历史不做虚假回填。
2. **评估重构：完成。** 非重叠窗口、95% CI、四类 baseline、严格覆盖 gate 均已落地。
3. **独立 calibration：完成。** 超参数选择与校准 holdout 分离。
4. **legacy / 视觉 / block 决策：完成。** 保留 legacy fallback；采纳 personalized 视觉中心化；否决统一 block 平衡。证据保存在 `data/recommendation-feature-ablation.json`。
5. **classifier 主动标注：完成基础设施。** 已建立全库 sampling frame 的真实待标注集；随机 holdout 未满足数量前不允许 classifier 自动隐藏项目。生成与标注命令分别为 `python scripts/classification_sampling.py --sample 30` 和 `python scripts/classification_sampling.py --label <id> --classification review|updates`。
6. **热路径：完成。** 缓存实测：tag corpus 197.1ms -> 24.7ms，series 361.9ms -> 25.6ms，related index 533.7ms -> 26.1ms，queue counts 2175.3ms -> 0.8ms。

后续不再是代码欠账，而是数据积累：继续正常反馈以形成严格快照窗口，并人工标注 `gallery_classification_samples`，直到随机 holdout 满足类别与数量门槛。

## 验证

- 模型、排序、评估、分类器和 API 专项测试：237 个通过。
- 全量 Python 测试：344 个运行，340 个通过、1 个跳过、3 个失败。
- 3 个失败均来自本轮开始前已有未提交改动中的 H@H archive 测试，原因是 Windows 执行时生成 `\\` 路径，而测试固定期望 `/`；本轮未修改或回退该模块。
- `node.exe --check static/app.js` 通过。
- 严格报告、诊断报告和 feature ablation 均成功生成并通过 JSON 重解析。
- 数据库已迁移，创建 8472 条 `migration-current` 初始快照和 60 条 classifier 待标注样本，其中 30 条属于有效的 `full-library` frame。
- 数据库备份：`data/recommender.sqlite3.bak-20260821-followup`；评估报告也保留了对应时间戳备份。
