# 自动学术论文追踪器

这是一个基于 `GitHub Actions` 的全自动化科研追踪工具。它利用 Semantic Scholar 的推荐 API以及Arxiv，根据你设定的**关键词**或者**种子论文**自动找到最新相关的研究工作，并利用大语言模型（如 DeepSeek）生成中文深度总结，最后每天准时将内容推送到你的微信上。

同时，项目集成了 **GitHub Pages 静态网页**，自动按年/月归档每日论文报告，提供在线浏览界面。


---

## 更新日志
- **2026-07-17**：新增**大模型主题相关度排序**，并将报告拆分为 **🎯 精读论文** 与 **⚡ 速读论文** 双模块。
- **2026-06-25**：新增 GitHub Pages 静态网页，按年/月归档每日论文报告，支持在线浏览。
- **2026-06-09**：新增根据关键词检索。
- **2026-03-31**：完成核心功能开发和测试，公开仓库。
- **2026-04-01**：增加出版商黑名单功能。

## 🌟 功能特性

- **高度贴合**：通过配置“正向(Positive)”和“负向(Negative)”种子论文或者关键词，让推荐算法越来越懂你的研究偏好。
- **过滤不感兴趣的出版商**：内置出版商黑名单功能，自动屏蔽来自特定会议或期刊（`config/publisher_blacklist.txt`）的论文，专注于你真正关心的研究。
- **🎯 主题相关度排序**：先按日期召回候选池，再由大模型结合你的研究主题（`config/research_topic.txt`）对候选论文逐篇打分（0-100）并重排序，让最贴合课题的论文排在最前。
- **📖 精读 / ⚡ 速读双模块**：高相关论文进入「精读」模块，自动提取 3 大核心要点（问题、方法、创新与效果）；次相关论文进入「速读」模块，用一句话中文要点快速扫读。每篇均带**相关度徽章**。
- **AI 智能读库**：对晦涩的英文摘要进行精读总结，自动提取3大核心要点（创新、方法、解决的问题），用通俗易懂的中文呈现。
- **仅推最新**：每次从海量推荐中智能筛选候选池，再经大模型排序后按相关度分层推送。
- **防止重复**：自动维护推送历史记录（`config/seen_papers.txt`），杜绝重复推送同一篇论文，不浪费你的微信通知。
- **两步抓取 TLDR**：首轮筛选最新且含摘要的推荐论文，随后利用 Batch API 精准回补 TLDR（一句话极简总结），丰富 AI 的分析上下文。
- **免服务器部署**：完全依托于 GitHub Actions 运行，零开销、零维护。
- **微信准时送达**：结合 Server 酱，把你每天需要在各个平台刷论文的时间省下来，早晨直接在微信查收日报。

---

## 🚀 快速开始教程

如果你想在自己的 GitHub 账号下运行这套系统，请按照以下步骤操作：

### 1. Fork 本仓库

点击页面右上角的 `Fork` 按钮，将当前代码仓库复制一份到你自己的账号下。

### 2. 获取必要的 API Keys (密钥)

你需要提前准备好以下三个服务的 API 密钥：

1. **Semantic Scholar API Key (S2_API_KEY)**
   - 官方虽然有无 Key 调用的额度，但为了保证推荐 API 稳定运行，建议去 [Semantic Scholar API](https://www.semanticscholar.org/product/api) 拉到最下面的表格，申请专属 Key。用教育邮箱申请后，大概10分钟就能拿到。
2. **LLM API Key (LLM_API_KEY)**
   - 代码默认接入的是性价比极高的 **DeepSeek**。你可以去 [DeepSeek 开放平台](https://platform.deepseek.com/) 注册并生成一个 API 密钥。*(如果你想使用其他平台，只需在 `paper_tracker.py` 中更改 `base_url` 并换成对应服务的 Key 即可)*。实测一次推送大概要0.02元人民币，如果不需要AI自动总结，可以关闭这个功能。
3. **Server酱 SendKey (SERVERCHAN_KEY)**
   - 用于微信推送。访问 [Server酱官网](https://sct.ftqq.com/login) 用你的微信扫码登录，获取你的 `SendKey`，并配置好微信推送通道。

### 3. 配置 GitHub Secrets

为了保护你的密钥不被泄露，请将上面的 Key 填入 GitHub 仓库的加密设置中：

1. 进入你 Fork 后的仓库，点击顶部的 `Settings` (设置)。
2. 在左侧边栏找到 `Secrets and variables` -> 点击 `Actions`。
3. 点击绿色的 `New repository secret` 按钮，依次添加以下 3 个环境变量：
   - 变量名：`S2_API_KEY` （填入 Semantic Scholar 密钥）
   - 变量名：`LLM_API_KEY` （填入 DeepSeek 密钥）
   - 变量名：`SERVERCHAN_KEY` （填入 Server酱 SendKey）

### 4. 设置你的“种子论文”

修改工作区 `config/` 目录下的文件以调教推荐与排序：
- **`config/research_topic.txt`** (必须)：填写你的研究主题（首个非 `#` 注释行生效）。大模型会据此对每日候选论文按相关度重排序并分层为精读/速读。可写一句话详细描述以提升排序精度。
- **`config/keywords.txt`** (必须)：放入你的关键词（每行一个）。
- **`config/seed_paper_positive.csv`** (必须)：放入你觉得**很有价值、希望推荐类似研究**的论文 ID（每行一个）。
- **`config/seed_paper_negative.csv`** (可选)：放入你觉得**不相关、不希望系统推荐**的论文 ID（每行一个）。

> **💡 提示**：系统会自动创建和维护 `config/seen_papers.txt` 文件来记录已推送过的论文，防止重复推送。你无需手动操作。

### 5. 设置出版商黑名单（可选）

修改 `config/publisher_blacklist.txt` 文件，添加你不想接收论文的出版商名称（每行一个）。请使用论文推荐 API 返回的 `venue` 字段中的名称（大小写不敏感），例如：

```
arXiv
bioRxiv
```

#### 支持的论文 ID 格式

以下是系统支持的论文 ID 格式，你可以从 Semantic Scholar、arXiv、DOI 等平台获取这些 ID：

- `DOI:<doi>` - a Digital Object Identifier, e.g. `DOI:10.18653/v1/N18-3011`
- `ARXIV:<id>` - arXiv.org, e.g. `ARXIV:2106.15928`
- `<sha>` - a Semantic Scholar ID, e.g. `649def34f8be52c8b66281af98ae884c09aef38b`
- `CorpusId:<id>` - a Semantic Scholar numerical ID, e.g. `CorpusId:215416146`
- `MAG:<id>` - Microsoft Academic Graph, e.g. `MAG:112218234`
- `ACL:<id>` - Association for Computational Linguistics, e.g. `ACL:W12-3903`
- `PMID:<id>` - PubMed/Medline, e.g. `PMID:19872477`
- `PMCID:<id>` - PubMed Central, e.g. `PMCID:2323736`
- `URL:<url>` - URL from one of the sites listed below, e.g. `URL:https://arxiv.org/abs/2106.15928v1`
  - semanticscholar.org
  - arxiv.org
  - aclweb.org
  - acm.org
  - biorxiv.org

### 5. 启动与测试运行

完成以上步骤后，你可以手动触发一次来测试是否配置成功：
1. 点击仓库顶部的 `Actions` 选项卡。
2. (可能需要) 点击绿色的 `I understand my workflows, go ahead and enable them` 启用 Actions 工作流。
3. 在左侧列表中选中 `Daily Paper Tracker`。
4. 点击右侧的 `Run workflow` -> `Run workflow`。
5. 等待 1~2 分钟，如果全部打勾为绿色，你的微信就会收到第一封文献晨报！

*(此外，系统每天北京时间早上 9:00 会自动运行一次，同时允许手动触发)*。

### 6. 开启 GitHub Pages 在线浏览（可选但推荐）

完成 Actions 运行后，每日报告会自动归档到 `docs/archive/YYYY/MM/YYYY-MM-DD.md`。开启 GitHub Pages 后即可获得一个在线网页，按年/月浏览所有历史报告：

1. 进入你 Fork 后的仓库，点击顶部的 `Settings` (设置)。
2. 在左侧边栏找到 `Pages`。
3. 在 `Build and deployment` 下的 `Source` 选择 **Deploy from a branch**。
4. `Branch` 选择 `main`，文件夹选择 `/docs`，点击 `Save`。
5. 等待 1~2 分钟，页面顶部会显示你的站点地址，形如 `https://<你的用户名>.github.io/DailyPaper/`。
6. 打开该地址即可看到论文追踪网页，左侧侧边栏按 **年 > 月 > 日** 分类导航，点击日期即可在右侧渲染当日 Markdown 报告。

> **💡 提示**：网页使用 `marked.js`（CDN 加载）在前端渲染 Markdown，无需构建步骤。`docs/.nojekyll` 文件已禁用 Jekyll，确保 `docs/` 下所有文件原样发布。

---

## 工作原理

### 防止重复推送

脚本每次运行前会读取 `config/seen_papers.txt` 中的论文ID(Semantic Scholar ID)历史记录，并自动过滤掉已推送过的论文。发送完毕后，新推送的论文ID会被追加到该文件中，GitHub Actions 机器人会自动将此变更提交并推送到你的仓库，保证下次运行时不会重复推送同一篇论文。

### 每日归档与 GitHub Pages

每次运行脚本时，除了推送到微信，还会执行以下操作：

- 在 `docs/archive/YYYY/MM/` 目录下生成 `YYYY-MM-DD.md` 文件，包含当日所有论文的 AI 总结。
- 更新 `docs/manifest.json`，记录所有已生成报告的日期与论文数量。
- GitHub Actions 机器人自动提交并推送这些变更到 `main` 分支。
- GitHub Pages 检测到 `docs/` 目录更新后，自动重新部署静态网页。

### 主题相关度排序与精读/速读分层

系统采用「**先召回、后重排、再分层**」的两阶段策略：

1. **召回候选池**：按关键词/种子论文检索并去重，先按 `publicationDate`（发表日期）倒序，取前 `CANDIDATE_POOL_SIZE`（默认 25）篇作为候选池，并批量回补 TLDR。日期缺失时用 `year` 兜底。
2. **大模型重排序**（`rank_papers_by_relevance`）：结合 `config/research_topic.txt` 的研究主题，用大模型对候选池逐篇打分（0-100）并附简短理由，按相关度降序排列。若模型调用失败会自动回退为日期排序，保证稳定。
3. **分层生成报告**（`build_report`）：
   - **🎯 精读论文**：相关度最高的前 `NUM_DEEP_READ`（默认 5）篇，逐篇做完整深度总结（试图解决的问题 / 核心方法 / 创新与效果）。
   - **⚡ 速读论文**：其后 `NUM_QUICK_READ`（默认 8）篇，一次批量调用生成中文一句话要点，供快速扫读。

> 相关分层数量与候选池大小可在 `paper_tracker.py` 顶部的 `CANDIDATE_POOL_SIZE / NUM_DEEP_READ / NUM_QUICK_READ` 常量中调整；LLM 模型与服务商在 `LLM_BASE_URL / LLM_MODEL` 常量中统一维护。

### 链接与摘要策略

系统会分两步走响应中提取 `externalIds.DOI`、`venue` 和 `tldr.text`：

- 第一步：使用推荐 API 初选包含 `abstract` 的候选论文。
- 第二步：通过 Batch API 回补这批论文的 `tldr` 文本（一句话概括）。
- 链接优先使用 `https://doi.org/<DOI>`，无 DOI 时回退到 API 返回的 `url`。
- 展示字段优先使用 `venue`，避免 publicationVenue 缺失导致“未知出版社”。

---

## 进阶定制

这个仓库基本通过 vibe coding 实现。欢迎根据自己的需求进行修改和优化：

- **更改运行时间**：编辑 `.github/workflows/daily_tracker.yml` 文件中的 `cron: '0 1 * * *'` (注意这是 UTC 时间)。
- **更改 AI 提示词**：直接修改 `paper_tracker.py` 中的 `prompt` 字段，以生成符合你排版和侧重点的报告。 
- **替换其他的大模型**：如果你想用诸如 Kimi、通义千问等，只要它们兼容 OpenAI SDK 格式，直接在 `paper_tracker.py` 修改 `base_url` 即可。

## License

MIT License
