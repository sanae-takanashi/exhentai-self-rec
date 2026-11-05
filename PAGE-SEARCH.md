# 本地单页相似搜索

页面入口：`/static/page-search.html`。选择本地画廊及抽样页，按单页向量的余弦相似度寻找其他画廊中的实际匹配页。所有比较和图片读取均在本地完成，搜索不抓取网络内容、不运行模型、不修改评分或数据库。

范围仅为已索引的本地抽样页。分数不是喜欢概率，不能推算整本画廊的相关页面比例。排除查询画廊的所有分辨率、与查询图片相同的资产，并按图片 SHA-256 去重候选。只有返回页面的文件通过大小及摘要验证后才会返回结果；损坏或缺失图片导致请求失败，界面保留重试入口。

`GET /api/local-pages/search?key=<gallery-key>&position=<sample-position>&limit=12` 返回 `available`、`reason`、`query: {item,page}`、`items: [{item,page,cosine}]`、`candidate_count`、`indexed_pages`、`scope: indexed-local-samples`、`score_kind: cosine-similarity`。limit 范围为 1–30。候选数量为去重后的索引记录数量，不表示整本页面覆盖率。

旧索引仍可预览，但 `page_search.available` 为 false、reason 为 `rebuild_required`。构建器 `scripts/build_hath_page_library.py` 从已验证的离线实验保存已有单页向量；它不重新编码。新索引声明 `page_embedding_schema: local-page-embeddings-v1`、`page_embedding_dimension`、`encoder`，每页记录 `embedding` 和与 encoder 相同的 `embedding_version`。混合维数、错误版本、无效向量均拒绝加载。

升级时使用一个尚不存在的新输出目录运行构建器，核验输出后再由用户配置加载。构建器不会覆盖已有目录；此功能实现不重建或切换真实页面库。

合成验证：`.venv-rocm/Scripts/python.exe -B -m unittest discover -s tests -p test_local_pages.py`。
