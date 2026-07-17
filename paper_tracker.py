import os
import re
import json
import requests
from openai import OpenAI
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
# --- 配置区 (这些将配置在 GitHub Secrets 中) ---
S2_API_KEY = os.getenv("S2_API_KEY")
LLM_API_KEY = os.getenv("LLM_API_KEY")
SERVERCHAN_KEY = os.getenv("SERVERCHAN_KEY")

HISTORY_FILE = "config/seen_papers.txt"
BLACKLIST_FILE = "config/blacklisted_venues.txt"
RESEARCH_TOPIC_FILE = "config/research_topic.txt"
MAX_PAPERS_AQUIRED_FROM_S2 = 100

# --- 排序 / 分层参数 ---
DEFAULT_RESEARCH_TOPIC = "行人过街行为与过街意图预测（Pedestrian Crossing Behavior / Intention Prediction）"
CANDIDATE_POOL_SIZE = 25   # 送入大模型做相关度排序的候选池大小
NUM_DEEP_READ = 5          # 精读论文数量（高相关，做完整深度总结）
NUM_QUICK_READ = 8         # 速读论文数量（次相关，做一句话要点）

# --- LLM 接入配置（统一在此处维护，便于替换模型/服务商） ---
LLM_BASE_URL = "https://chat.cqjtu.edu.cn/ds/api/v1"
LLM_MODEL = "deepseek-v4pro"


def get_llm_client():
    """统一构造 LLM 客户端"""
    return OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)


def read_research_topic():
    """读取研究主题（忽略 # 注释行），取首个有效行作为排序锚点。"""
    lines = read_list(RESEARCH_TOPIC_FILE)
    for line in lines:
        if not line.startswith("#"):
            return line
    return DEFAULT_RESEARCH_TOPIC


def read_list(file_path):
    """读取文件列表，忽略空行"""
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def read_seed_papers(file_path):
    """从本地读取 CSV 格式的文献 ID 列表"""
    papers = []
    if not os.path.exists(file_path):
        print(f"Warning: 找不到文件 {file_path}")
        return papers

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            # 移除换行符和首尾空格
            line = line.strip()
            # 忽略空行
            if line:
                papers.append(line)
    return papers


def fetch_tldr_batch(papers, headers):
    """通过 Semantic Scholar Batch API 批量回补 TLDR 文本（一句话概括）。"""
    if not papers:
        return papers
    paper_ids = [p.get("paperId") for p in papers if p.get("paperId")]
    if not paper_ids:
        for p in papers:
            p.setdefault("tldrText", "")
        return papers

    time.sleep(1)
    batch_url = "https://api.semanticscholar.org/graph/v1/paper/batch"
    batch_params = {"fields": "paperId,tldr"}
    try:
        batch_res = requests.post(
            batch_url, json={"ids": paper_ids}, headers=headers, params=batch_params
        )
        if batch_res.status_code == 200:
            tldr_dict = {}
            for item in batch_res.json():
                if item and isinstance(item.get("tldr"), dict):
                    tldr_dict[item["paperId"]] = (item["tldr"].get("text") or "").strip()
            for p in papers:
                p["tldrText"] = tldr_dict.get(p.get("paperId"), "")
        else:
            print(f"警告: TLDR 批量请求失败: {batch_res.status_code} - {batch_res.text}")
            for p in papers:
                p.setdefault("tldrText", "")
    except Exception as e:
        print(f"警告: TLDR 批量请求异常: {e}")
        for p in papers:
            p.setdefault("tldrText", "")
    return papers


def search_arxiv(keywords, max_results=30):
    """
    使用 arXiv 官方 API 搜索关键词列表，返回归一化后的论文列表。
    无需安装第三方库，只需 requests 和标准库 xml。
    """
    papers = []
    base_url = "http://export.arxiv.org/api/query"
    
    for kw in keywords:
        # 多词关键词可以加双引号、AND 等，这里原样传入
        query = f"all:{kw}"
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending"
        }
        try:
            resp = requests.get(base_url, params=params, timeout=30)
            if resp.status_code != 200:
                print(f"arXiv API 返回 {resp.status_code}: {resp.text[:200]}")
                time.sleep(1)
                continue
            
            # 解析 XML
            root = ET.fromstring(resp.text)
            # 命名空间处理（Atom 默认 ns）
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom"
            }
            for entry in root.findall("atom:entry", ns):
                title_el = entry.find("atom:title", ns)
                title = title_el.text.strip() if title_el is not None else ""
                
                summary_el = entry.find("atom:summary", ns)
                abstract = summary_el.text.strip() if summary_el is not None else ""
                
                # 作者
                authors = []
                for author_el in entry.findall("atom:author/atom:name", ns):
                    if author_el.text:
                        authors.append(author_el.text.strip())
                
                # 发表日期
                published_el = entry.find("atom:published", ns)
                pub_date = published_el.text.strip()[:10] if published_el is not None else ""
                
                # arXiv ID
                id_el = entry.find("atom:id", ns)
                full_id = id_el.text.strip() if id_el is not None else ""
                # 提取纯 ID，例如 http://arxiv.org/abs/2103.xxxxx -> 2103.xxxxx
                arxiv_id = full_id.split("/abs/")[-1] if "/abs/" in full_id else full_id
                
                # URL
                link_el = entry.find("atom:link[@title='pdf']", ns)
                if link_el is None:
                    link_el = entry.find("atom:link", ns)
                pdf_url = link_el.attrib.get("href", "") if link_el is not None else ""
                
                papers.append({
                    "paperId": None,
                    "title": title,
                    "abstract": abstract.replace("\n", " ").strip(),
                    "authors": authors,
                    "url": f"https://arxiv.org/abs/{arxiv_id}",  # 统一用摘要页
                    "venue": "arXiv",
                    "publicationDate": pub_date,
                    "year": int(pub_date[:4]) if pub_date and len(pub_date)>=4 else None,
                    "externalIds": {"ArXiv": arxiv_id},
                    "source": "arxiv",
                    "tldrText": ""
                })
            
            # arXiv 要求礼貌延迟（没有明确速率限制，建议 3 秒以上）
            time.sleep(3)
            
        except Exception as e:
            print(f"arXiv 搜索关键词 '{kw}' 时出错: {e}")
            time.sleep(3)
    
    return papers

def get_paper_recommendations_via_keywords():
    """通过关键词搜索 Semantic Scholar，获取新论文推荐"""
    url_search = "https://api.semanticscholar.org/graph/v1/paper/search"
    headers = {"x-api-key": S2_API_KEY}
    # 读取关键词
    keywords_path = "config/keywords.txt"
    try:
        with open(keywords_path, "r", encoding="utf-8") as f:
            keywords = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"错误：关键词文件 {keywords_path} 不存在。")
        return []
    if not keywords:
        print("错误：关键词列表为空。")
        return []
    print(f"使用 {len(keywords)} 个关键词进行搜索: {keywords}")
    # 用于存储所有搜到的论文（paperId -> paper 字典）
    collected = {}
    seen_papers = set(read_list(HISTORY_FILE))
    blacklisted_venues = [v.lower() for v in read_list(BLACKLIST_FILE)]
    # 每个关键词最多获取多少篇（根据你的总量和关键词数量动态分配）
    per_keyword_limit = max(1, MAX_PAPERS_AQUIRED_FROM_S2 // len(keywords))
    for kw in keywords:
        print(f"正在搜索关键词: {kw}")
        offset = 0
        batch_size = min(100, per_keyword_limit)  # API 每页最多 100
        while len(collected) < MAX_PAPERS_AQUIRED_FROM_S2 and offset < 1000:  # 设置总偏移上限避免无限循环
            params = {
                "query": kw,
                "limit": batch_size,
                "offset": offset,
                "fields": ",".join([
                    "paperId", "title", "abstract", "authors",
                    "url", "venue", "externalIds", "publicationDate", "year"
                ])
            }
            resp = requests.get(url_search, headers=headers, params=params)
            # 处理限速 / 常见错误
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else 2
                print(f"  触发限流，等待 {wait} 秒...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  搜索请求失败: {resp.status_code} - {resp.text}")
                break
            data = resp.json()
            papers = data.get("data", [])
            if not papers:
                break
            # 逐一处理并存入 collected（去重 + 基本过滤）
            for p in papers:
                pid = p.get("paperId")
                if not pid or pid in seen_papers or pid in collected:
                    continue
                venue = (p.get("venue") or "").lower()
                if blacklisted_venues and any(bv in venue for bv in blacklisted_venues):
                    continue
                abstract = (p.get("abstract") or "").strip()
                if not abstract:
                    continue
                paper = dict(p)
                collected[pid] = paper
            offset += batch_size
            # 每个请求后必须等待 1 秒（全端点限速 1 req/s）
            time.sleep(1)
        # 换下一个关键词前也等 1 秒，避免两次起始请求过快
        time.sleep(1)
    # 转换回列表，按发表日期倒序排列
    papers_list = list(collected.values())
    def get_date(p):
        pub_date = p.get("publicationDate")
        if pub_date:
            return pub_date
        year = p.get("year")
        if year:
            return f"{year}-12-31"
        return "1900-01-01"
    papers_list.sort(key=get_date, reverse=True)
    print(f"合并去重后共收集 {len(papers_list)} 篇未读论文，取前 {CANDIDATE_POOL_SIZE} 篇最新作为排序候选池。")
    # 扩大候选池，交由大模型按研究主题相关度重排序后再分层
    top_new_papers = papers_list[:CANDIDATE_POOL_SIZE]
    # 批量回补 TLDR，为后续排序与总结提供上下文
    fetch_tldr_batch(top_new_papers, headers)
    # ===== 新增：调用 arXiv 并合并 =====
    # print("正在从 arXiv 补充关键词搜索结果...")
    # arxiv_papers = search_arxiv(keywords, max_results=15)   # 可根据需要调整
    # # 将已有的 S2 论文的 ArXiv ID 提取出来，用于去重
    # existing_arxiv_ids = set()
    # for p in top_new_papers:
    #     ext_id = p.get("externalIds", {}).get("ArXiv")
    #     if ext_id:
    #         existing_arxiv_ids.add(ext_id.strip().lower())
    # # 合并 arXiv 新论文（去重 + 避免与 S2 中的已读论文重复）
    # for ap in arxiv_papers:
    #     arxiv_id = (ap.get("externalIds", {}).get("ArXiv") or "").lower()
    #     if arxiv_id in existing_arxiv_ids:
    #         continue
    #     if ap.get("paperId") and ap["paperId"] in seen_papers:   # seen_papers 来自历史文件
    #         continue
    #     top_new_papers.append(ap)
    #     existing_arxiv_ids.add(arxiv_id)
    # # 重新按日期排序
    # def get_date(p):
    #     pub_date = p.get("publicationDate")
    #     if pub_date:
    #         return pub_date
    #     year = p.get("year")
    #     if year:
    #         return f"{year}-12-31"
    #     return "1900-01-01"
    # top_new_papers.sort(key=get_date, reverse=True)
    # print(f"合并 arXiv 后共 {len(top_new_papers)} 篇，最终取前 20 篇。")
    return top_new_papers




def get_paper_recommendations():
    
    """通过 Semantic Scholar 寻找相关新论文"""
    url = "https://api.semanticscholar.org/recommendations/v1/papers"
    headers = {"x-api-key": S2_API_KEY}

    positive_papers = read_seed_papers("config/seed_paper_positive.csv")
    negative_papers = read_seed_papers("config/seed_paper_negative.csv")

    print(f"载入正向论文: {len(positive_papers)} 篇")
    print(f"载入负向论文: {len(negative_papers)} 篇")

    if not positive_papers:
        print("错误：推荐系统至少需要一篇 Positive 论文作为基准。")
        return []

    payload = {"positivePaperIds": positive_papers, "negativePaperIds": negative_papers}

    # 步骤 1：获取推荐（注意：这里去掉了 tldr，防止 400 报错）
    params = {
        "fields": "paperId,title,abstract,authors,url,venue,externalIds,publicationDate,year",
        "limit": MAX_PAPERS_AQUIRED_FROM_S2,
    }

    response = requests.post(url, json=payload, headers=headers, params=params)

    if response.status_code != 200:
        print(f"API 请求失败: {response.status_code} - {response.text}")
        return []

    raw_papers = response.json().get("recommendedPapers", [])
    seen_papers = set(read_list(HISTORY_FILE))
    blacklisted_venues = [v.lower() for v in read_list(BLACKLIST_FILE)]

    print(f"从推荐系统获取到 {len(raw_papers)} 篇论文，正在筛选最新论文...")

    # 过滤掉已经推送过的论文
    unseen_papers = []
    for p in raw_papers:
        if p.get("paperId") in seen_papers:
            continue

        venue = (p.get("venue") or "").lower()
        if blacklisted_venues and any(bv in venue for bv in blacklisted_venues):
            continue

        abstract_text = (p.get("abstract") or "").strip()
        # 由于第一步没有获取 tldr，这里只判断摘要是否存在
        if not abstract_text:
            continue

        paper = dict(p)
        unseen_papers.append(paper)

    # 按发表日期倒序排列（最新的在前面）
    def get_date(p):
        pub_date = p.get("publicationDate")
        if pub_date:
            return pub_date
        year = p.get("year")
        if year:
            return f"{year}-12-31"
        return "1900-01-01"

    unseen_papers.sort(key=get_date, reverse=True)

    # 扩大候选池，交由大模型按研究主题相关度重排序后再分层
    top_new_papers = unseen_papers[:CANDIDATE_POOL_SIZE]

    # 批量回补 TLDR
    fetch_tldr_batch(top_new_papers, headers)

    return top_new_papers



def format_authors(authors_list):
    """
    兼容:
    [{"name":"Tom"}]
    ["Tom"]
    """
    names = []
    for author in authors_list or []:
        if isinstance(author, dict):
            names.append(author.get("name", "未知"))
        elif isinstance(author, str):
            names.append(author)

    if len(names) > 4:
        names = [names[0], names[1], "...", names[-2], names[-1]]

    return ", ".join(names)


def request_with_retry(method, url, max_retry=5, **kwargs):
    import time
    for i in range(max_retry):
        resp = method(url, **kwargs)
        if resp.status_code != 429:
            return resp
        wait = 2 ** i
        print(f"触发限流，等待 {wait} 秒...")
        time.sleep(wait)
    return resp


def _paper_link(paper):
    """生成论文永久链接：优先 DOI，其次 API url，最后 Semantic Scholar 页面。"""
    doi = ""
    external_ids = paper.get("externalIds")
    if isinstance(external_ids, dict):
        doi = (external_ids.get("DOI") or "").strip()
    url = f"https://doi.org/{doi}" if doi else paper.get("url", "")
    if not url:
        url = f"https://www.semanticscholar.org/paper/{paper.get('paperId')}"
    return url


def _extract_json_array(text):
    """从大模型返回文本中稳健地抽取 JSON 数组。"""
    if not text:
        return None
    # 去掉 ```json ... ``` 代码围栏
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 兜底：截取第一个 [ 到最后一个 ] 之间的内容
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


def rank_papers_by_relevance(papers, topic):
    """用大模型对候选论文按与研究主题的相关度打分(0-100)并降序排序。

    为每篇论文写入 relevanceScore(int) 与 relevanceReason(str)。
    大模型调用失败时回退为按原顺序（发表日期）排序，并给出中性分数。
    """
    if not papers:
        return papers

    # 构造精简候选清单，控制 token 消耗
    entries = []
    for i, p in enumerate(papers):
        title = (p.get("title") or "").strip()
        tldr = (p.get("tldrText") or "").strip()
        abstract = (p.get("abstract") or "").strip().replace("\n", " ")[:280]
        entries.append(f"[{i}] 标题: {title}\n    TLDR: {tldr or '无'}\n    摘要: {abstract or '无'}")
    catalog = "\n\n".join(entries)

    prompt = f"""你是「{topic}」领域的资深审稿人。下面是今日候选论文清单，请评估每篇与该研究主题的相关程度。

评分标准（0-100 整数）：
- 90-100：直接研究行人过街意图/行为/轨迹预测
- 70-89：自动驾驶/智能交通中的行人检测、VRU(弱势道路使用者)交互、行人轨迹等强相关
- 40-69：通用轨迹/时序预测、因果推断、可解释性等方法层面相关
- 10-39：机器学习/计算机视觉通用方法，弱相关
- 0-9：基本无关

请严格只输出一个 JSON 数组，不要任何多余文字，每个元素形如：
{{"id": 0, "score": 85, "reason": "简短中文理由，不超过18字"}}

候选论文清单：
{catalog}
"""

    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _extract_json_array(response.choices[0].message.content)
    except Exception as e:
        print(f"警告: 相关度排序调用失败，回退为日期排序: {e}")
        result = None

    score_map = {}
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            idx = item.get("id")
            if isinstance(idx, int) and 0 <= idx < len(papers):
                try:
                    score = int(item.get("score", 0))
                except (TypeError, ValueError):
                    score = 0
                score = max(0, min(100, score))
                score_map[idx] = (score, (item.get("reason") or "").strip())

    for i, p in enumerate(papers):
        if i in score_map:
            p["relevanceScore"], p["relevanceReason"] = score_map[i]
        else:
            # 未被模型评分（或调用失败）：给中性分并保持原相对次序
            p["relevanceScore"] = p.get("relevanceScore", 50)
            p["relevanceReason"] = p.get("relevanceReason", "")

    papers.sort(key=lambda x: x.get("relevanceScore", 0), reverse=True)
    print("相关度排序完成，得分预览: " + ", ".join(
        f"{p.get('relevanceScore')}" for p in papers[:CANDIDATE_POOL_SIZE]
    ))
    return papers


def summarize_deep_paper(paper, client):
    """对单篇精读论文做完整深度总结（问题 / 方法 / 创新与效果）。"""
    title = paper.get("title", "无标题")
    abstract_text = (paper.get("abstract") or "").strip()
    tldr_text = (paper.get("tldrText") or "").strip()
    if not abstract_text and tldr_text:
        abstract_text = f"（原始摘要缺失，以下为TLDR）{tldr_text}"
    if not abstract_text:
        abstract_text = "无摘要"

    prompt = f"""
你是一个严谨的学术专家。请基于以下论文信息，提取核心内容并转化为中文。
要求：
1. 极其精简、具体，拒绝空泛的套话，保留专业术语。
2. 绝对不要输出任何诸如“好的，这是为您总结的论文”之类的客套话。
3. 请严格按照以下 Markdown 格式输出:
**[试图解决的问题]**：(用一句话概括该研究针对的痛点或背景)
**[核心方法]**：(具体使用了什么架构、算法、模型或机制)
**[创新与效果]**：(实现了什么指标提升，或解决了什么具体的限制)

标题: {title}
TLDR: {tldr_text or "无"}
摘要原文: {abstract_text}
"""
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"警告: 精读总结失败 [{title[:30]}]: {e}")
        return f"**[AI 总结生成失败]**：{e}"


def summarize_quick_papers(papers, client):
    """对速读论文批量生成中文一句话要点（单次调用，节省成本）。

    返回 {index: 一句话中文要点} 映射；失败时回退为 TLDR。
    """
    result_map = {}
    if not papers:
        return result_map

    entries = []
    for i, p in enumerate(papers):
        title = (p.get("title") or "").strip()
        tldr = (p.get("tldrText") or "").strip()
        abstract = (p.get("abstract") or "").strip().replace("\n", " ")[:220]
        entries.append(f"[{i}] 标题: {title}\n    TLDR: {tldr or '无'}\n    摘要: {abstract or '无'}")
    catalog = "\n\n".join(entries)

    prompt = f"""你是一个学术助手。请为下列每篇论文写一句话中文速读要点（点明做了什么、用了什么方法或有何亮点），每条不超过40字，保留专业术语，不要客套话。

请严格只输出一个 JSON 数组，每个元素形如：
{{"id": 0, "point": "一句话要点"}}

论文清单：
{catalog}
"""
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = _extract_json_array(response.choices[0].message.content)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and isinstance(item.get("id"), int):
                    result_map[item["id"]] = (item.get("point") or "").strip()
    except Exception as e:
        print(f"警告: 速读要点批量生成失败，回退为 TLDR: {e}")

    # 兜底：缺失的用 TLDR 填充
    for i, p in enumerate(papers):
        if not result_map.get(i):
            result_map[i] = (p.get("tldrText") or "").strip() or "（暂无要点）"
    return result_map


def build_report(deep_papers, quick_papers, topic):
    """生成两段式 Markdown 报告：🎯 精读论文 + ⚡ 速读论文。"""
    client = get_llm_client()
    report = f"> **研究主题**：{topic}\n\n"

    # ===== 精读论文 =====
    report += "## 🎯 精读论文（高相关 · 深度解析）\n\n"
    if deep_papers:
        for idx, paper in enumerate(deep_papers):
            title = paper.get("title", "无标题")
            date = paper.get("publicationDate") or paper.get("year") or "未知日期"
            venue_name = (paper.get("venue") or "").strip() or "未知会议/期刊"
            authors = format_authors(paper.get("authors", []))
            url = _paper_link(paper)
            tldr_text = (paper.get("tldrText") or "").strip()
            abstract_text = (paper.get("abstract") or "").strip() or "无摘要"
            score = paper.get("relevanceScore", "-")
            reason = paper.get("relevanceReason", "")
            summary = summarize_deep_paper(paper, client)

            report += (
                f"### {idx + 1}. [{title}]({url})\n"
                f"`相关度 {score}` {('· ' + reason) if reason else ''}\n\n"
                f"*{venue_name}* | {authors} | {date}\n\n"
                f"**TLDR:** {tldr_text or '无'}\n\n"
                f"> {abstract_text}\n\n"
                f"{summary}\n\n---\n"
            )
    else:
        report += "_今日暂无高相关论文。_\n\n---\n"

    # ===== 速读论文 =====
    report += "\n## ⚡ 速读论文（泛读 · 一句话要点）\n\n"
    if quick_papers:
        points = summarize_quick_papers(quick_papers, client)
        for idx, paper in enumerate(quick_papers):
            title = paper.get("title", "无标题")
            date = paper.get("publicationDate") or paper.get("year") or "未知日期"
            venue_name = (paper.get("venue") or "").strip() or "未知会议/期刊"
            url = _paper_link(paper)
            score = paper.get("relevanceScore", "-")
            point = points.get(idx, "")
            report += (
                f"**{idx + 1}. [{title}]({url})** `相关度 {score}`\n\n"
                f"{point}\n\n"
                f"<sub>{venue_name} | {date}</sub>\n\n"
            )
    else:
        report += "_今日暂无速读论文。_\n\n"

    return report


def summarize_papers_with_llm(papers):
    """[兼容旧接口] 直接对论文列表做完整总结，不做分层。"""
    client = get_llm_client()
    report_content = ""
    for idx, paper in enumerate(papers):
        title = paper.get("title", "无标题")
        date = paper.get("publicationDate") or paper.get("year") or "未知日期"
        venue_name = (paper.get("venue") or "").strip() or "未知会议/期刊"
        authors = format_authors(paper.get("authors", []))
        url = _paper_link(paper)
        tldr_text = (paper.get("tldrText") or "").strip()
        abstract_text = (paper.get("abstract") or "").strip() or "无摘要"
        summary = summarize_deep_paper(paper, client)
        report_content += (
            f"## {idx+1}\n[{title}]({url})\n*{venue_name}* | {authors} | {date}\n\n"
            f"**TLDR:** {tldr_text}\n\n"
            f"> {abstract_text}\n\n"
            f"{summary}\n\n---\n"
        )
    return report_content


def update_history(papers):
    """将已推送的论文 ID 追加到历史记录文件"""
    if not papers:
        return

    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)

    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        for p in papers:
            pid = p.get("paperId")
            if pid:
                f.write(pid + "\n")
            else:
                arxiv_id = p.get("externalIds", {}).get("ArXiv")
                if arxiv_id:
                    f.write(f"arxiv:{arxiv_id}\n")


def push_to_wechat(content):
    """通过 Server 酱 推送到微信"""
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = {"title": "📚 你的每日文献追踪晨报到了！", "desp": content}
    requests.post(url, data=data)


# ===== GitHub Pages 静态站点归档 =====
from datetime import datetime

DOCS_DIR = "docs"
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")
MANIFEST_FILE = os.path.join(DOCS_DIR, "manifest.json")


def save_daily_markdown(report_content, papers, topic=None, deep_count=None, quick_count=None):
    """将每日报告保存为 docs/archive/YYYY/MM/YYYY-MM-DD.md，并更新 manifest。"""
    today = datetime.now().strftime("%Y-%m-%d")
    year = datetime.now().strftime("%Y")
    month = datetime.now().strftime("%m")

    # 创建归档目录
    archive_path = os.path.join(ARCHIVE_DIR, year, month)
    os.makedirs(archive_path, exist_ok=True)

    # 生成带标题的 Markdown 内容
    paper_count = len(papers)
    title = f"# 📚 每日论文追踪 - {today}"

    if deep_count is not None and quick_count is not None:
        count_line = (
            f"> 共 **{paper_count}** 篇（🎯 精读 **{deep_count}** / ⚡ 速读 **{quick_count}**） | "
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )
    else:
        count_line = (
            f"> 共追踪到 **{paper_count}** 篇最新论文 | "
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

    header = f"{title}\n\n" + count_line + "---\n\n"

    full_content = header + report_content

    # 写入文件
    md_file_path = os.path.join(archive_path, f"{today}.md")
    with open(md_file_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    print(f"已生成每日归档: {md_file_path}")

    # 更新 manifest
    update_manifest(today, paper_count)
    return md_file_path


def update_manifest(date_str, paper_count):
    """更新 docs/manifest.json，记录所有已生成的日报。"""
    manifest = {"dates": []}

    # 读取现有 manifest
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, IOError):
            manifest = {"dates": []}

    # 确保 dates 列表存在
    if "dates" not in manifest:
        manifest["dates"] = []

    # 检查是否已存在该日期，避免重复
    existing_entry = None
    for entry in manifest["dates"]:
        if isinstance(entry, dict) and entry.get("date") == date_str:
            existing_entry = entry
            break

    if existing_entry:
        existing_entry["count"] = paper_count
    else:
        manifest["dates"].append({"date": date_str, "count": paper_count})

    # 按日期降序排序
    manifest["dates"].sort(key=lambda x: x["date"] if isinstance(x, dict) else x, reverse=True)
    manifest["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 写回 manifest
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"已更新 manifest: {MANIFEST_FILE}")


if __name__ == "__main__":
    topic = read_research_topic()
    print(f"研究主题: {topic}")
    print("正在寻找最新推荐...")
    # candidates = get_paper_recommendations()
    candidates = get_paper_recommendations_via_keywords()

    if candidates:
        print(f"找到 {len(candidates)} 篇候选论文，正在用大模型按主题相关度排序...")
        candidates = rank_papers_by_relevance(candidates, topic)

        # 按相关度分层：精读（高相关，深度总结） + 速读（次相关，一句话要点）
        deep_papers = candidates[:NUM_DEEP_READ]
        quick_papers = candidates[NUM_DEEP_READ:NUM_DEEP_READ + NUM_QUICK_READ]
        shown_papers = deep_papers + quick_papers
        print(f"分层完成：精读 {len(deep_papers)} 篇 / 速读 {len(quick_papers)} 篇。")

        print("正在生成两段式报告（精读 + 速读）...")
        report = build_report(deep_papers, quick_papers, topic)

        print("正在生成每日归档 Markdown...")
        save_daily_markdown(report, shown_papers, topic=topic,
                            deep_count=len(deep_papers), quick_count=len(quick_papers))
        print("正在推送到微信...")
        push_to_wechat(report)
        print("更新历史记录...")
        update_history(shown_papers)
        print("全部完成！")
    else:
        print("今天没有发现未读的最新相关文献。")
