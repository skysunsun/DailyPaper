import os
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
MAX_PAPERS_AQUIRED_FROM_S2 = 100


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
    print(f"合并去重后共收集 {len(papers_list)} 篇未读论文，取前 10 篇最新。")
    top_new_papers = papers_list[:10]
    # 批量获取 TLDR
    if top_new_papers:
        time.sleep(1)  # 与上一个搜索请求间隔
        paper_ids = [p["paperId"] for p in top_new_papers]
        batch_url = "https://api.semanticscholar.org/graph/v1/paper/batch"
        batch_params = {"fields": "paperId,tldr"}
        batch_res = requests.post(
            batch_url, json={"ids": paper_ids}, headers=headers, params=batch_params
        )
        if batch_res.status_code == 200:
            tldr_data = batch_res.json()
            tldr_dict = {}
            for item in tldr_data:
                if item and isinstance(item.get("tldr"), dict):
                    tldr_dict[item["paperId"]] = (item["tldr"].get("text") or "").strip()
            for p in top_new_papers:
                p["tldrText"] = tldr_dict.get(p["paperId"], "")
        else:
            print(f"警告: TLDR 批量请求失败: {batch_res.status_code} - {batch_res.text}")
            for p in top_new_papers:
                p["tldrText"] = ""
    # ===== 新增：调用 arXiv 并合并 =====
    print("正在从 arXiv 补充关键词搜索结果...")
    arxiv_papers = search_arxiv(keywords, max_results=15)   # 可根据需要调整
    # 将已有的 S2 论文的 ArXiv ID 提取出来，用于去重
    existing_arxiv_ids = set()
    for p in top_new_papers:
        ext_id = p.get("externalIds", {}).get("ArXiv")
        if ext_id:
            existing_arxiv_ids.add(ext_id.strip().lower())
    # 合并 arXiv 新论文（去重 + 避免与 S2 中的已读论文重复）
    for ap in arxiv_papers:
        arxiv_id = (ap.get("externalIds", {}).get("ArXiv") or "").lower()
        if arxiv_id in existing_arxiv_ids:
            continue
        if ap.get("paperId") and ap["paperId"] in seen_papers:   # seen_papers 来自历史文件
            continue
        top_new_papers.append(ap)
        existing_arxiv_ids.add(arxiv_id)
    # 重新按日期排序
    def get_date(p):
        pub_date = p.get("publicationDate")
        if pub_date:
            return pub_date
        year = p.get("year")
        if year:
            return f"{year}-12-31"
        return "1900-01-01"
    top_new_papers.sort(key=get_date, reverse=True)
    print(f"合并 arXiv 后共 {len(top_new_papers)} 篇，最终取前 10 篇。")
    return top_new_papers[:10]




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

    # print(f"筛选后剩余 {len(unseen_papers)} 篇未读论文，正在获取 TLDR...")

    # 只取前 10 篇最新的
    top_new_papers = unseen_papers[:10]

    # 步骤 2：调用 Batch API 批量查这 10 篇的 TLDR
    if top_new_papers:
        time.sleep(1)
        paper_ids = [p["paperId"] for p in top_new_papers]
        batch_url = "https://api.semanticscholar.org/graph/v1/paper/batch"
        batch_params = {"fields": "paperId,tldr"}

        batch_res = requests.post(
            batch_url, json={"ids": paper_ids}, headers=headers, params=batch_params
        )

        if batch_res.status_code == 200:
            tldr_data = batch_res.json()
            # 建立一个 { 'paperId': 'TLDR 文本' } 的映射字典
            tldr_dict = {}
            for item in tldr_data:
                if item and item.get("tldr") and isinstance(item.get("tldr"), dict):
                    tldr_dict[item["paperId"]] = (
                        item["tldr"].get("text") or ""
                    ).strip()

            # 将提取到的 tldr 文本塞回论文数据里
            for p in top_new_papers:
                p["tldrText"] = tldr_dict.get(p["paperId"], "")
        else:
            print(f"警告: TLDR 批量请求失败: {batch_res.text}")
            for p in top_new_papers:
                p["tldrText"] = ""

    return top_new_papers


def summarize_papers_with_llm(papers):
    """调用大模型进行总结"""
    client = OpenAI(
        api_key=LLM_API_KEY, base_url="https://chat.cqjtu.edu.cn/ds/api/v1"
    )  # deepseek
    # client = OpenAI(
    #     api_key=LLM_API_KEY, base_url="https://api.siliconflow.cn/v1"
    # )  # siliconflow

    report_content = ""
    for idx, paper in enumerate(papers):
        title = paper.get("title", "无标题")
        date = paper.get("publicationDate") or paper.get("year") or "未知日期"
        abstract_text = (paper.get("abstract") or "").strip()
        tldr_text = (paper.get("tldrText") or "").strip()

        # 优先使用 DOI 生成永久链接
        doi = ""
        external_ids = paper.get("externalIds")
        if isinstance(external_ids, dict):
            doi = (external_ids.get("DOI") or "").strip()
        url = f"https://doi.org/{doi}" if doi else paper.get("url", "")
        if url == "":
            url = f"https://www.semanticscholar.org/paper/{paper.get('paperId')}"

        venue_name = (paper.get("venue") or "").strip() or "未知会议/期刊"

        if not abstract_text and tldr_text:
            abstract_text = f"（原始摘要缺失，以下为TLDR）{tldr_text}"
        if not abstract_text:
            abstract_text = "无摘要"

        authors_list = paper.get("authors", [])
        if len(authors_list) > 4:
            author_names = [
                authors_list[0].get("name", "未知"),
                authors_list[1].get("name", "未知"),
                "...",
                authors_list[-2].get("name", "未知"),
                authors_list[-1].get("name", "未知"),
            ]
            authors = ", ".join(author_names)
        else:
            authors = ", ".join([author.get("name", "未知") for author in authors_list])

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

        response = client.chat.completions.create(
            model="deepseek-v4pro",
            # model="deepseek-ai/DeepSeek-V3.2",
            messages=[{"role": "user", "content": prompt}],
        )

        summary = response.choices[0].message.content
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
            f.write(p.get("paperId") + "\n")


def push_to_wechat(content):
    """通过 Server 酱 推送到微信"""
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = {"title": "📚 你的每日文献追踪晨报到了！", "desp": content}
    requests.post(url, data=data)


if __name__ == "__main__":
    print("正在寻找最新推荐...")
    # new_papers = get_paper_recommendations()
    new_papers = get_paper_recommendations_via_keywords()
    if new_papers:
        print(f"找到 {len(new_papers)} 篇最新论文，正在使用 LLM 总结...")
        report = summarize_papers_with_llm(new_papers)
        print("正在推送到微信...")
        push_to_wechat(report)
        print("更新历史记录...")
        update_history(new_papers)
        print("全部完成！")
    else:
        print("今天没有发现未读的最新相关文献。")
