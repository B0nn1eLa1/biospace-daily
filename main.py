"""
Biospace 每日新聞翻譯機器人 - compbio 學生求職導向版
v1.4: 加入 Featured Stories 自動抓取

寄送邏輯：
- RSS：固定 MAX_RSS_ARTICLES 篇（每天保證有貨）
- Featured Stories：抓取「今天 + 昨天」的精選文章（排除 sponsored），無數量保證
- URL 去重：若 RSS 與 Featured 撞到，Featured 優先（保留 ⭐ 標記）
                並從 RSS 補一篇遞補，確保 RSS 數量不被擠掉
- 上限保護：Featured 最多 MAX_FEATURED 篇，避免 quota 爆掉

每篇信件版面：
1. ⚡ TL;DR (3 句重點)
2. 📖 完整中文翻譯
3. 📄 完整英文原文
4. 💡 Industry Insight (compbio 視角)
5. 📚 單字表
"""

import os
import re
import json
import time
import smtplib
import feedparser
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from datetime import datetime, timedelta
from google import genai

# ---------- 設定 ----------
RSS_URL = "https://www.biospace.com/all-news.rss"
HOME_URL = "https://www.biospace.com/"

MAX_RSS_ARTICLES = 3       # RSS 固定寄幾篇
MAX_FEATURED = 5           # Featured 最多寄幾篇 (防 quota 爆)
FEATURED_DAYS_BACK = 1     # Featured 算到幾天前 (1 = 今天+昨天)

MODEL = "gemini-2.5-flash"
VOCAB_COUNT = 10
MAX_RETRIES = 3
RETRY_DELAY_SEC = 10
FETCH_DELAY_SEC = 2
REQUEST_TIMEOUT = 15

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
# 所有收件人（包括你自己），逗號分隔
BCC_RECIPIENTS = [
    e.strip()
    for e in os.environ["RECIPIENT_EMAIL"].split(",")
    if e.strip()
]

client = genai.Client(api_key=GEMINI_API_KEY)


# ---------- 抓 RSS ----------
def fetch_rss_articles(limit: int):
    """從 RSS 抓最新 limit 篇文章。"""
    feed = feedparser.parse(RSS_URL)
    articles = []
    for entry in feed.entries[:limit]:
        articles.append({
            "title": entry.title,
            "link": entry.link.split("?")[0].rstrip("/"),  # 標準化 URL 方便去重
            "summary": BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(strip=True),
            "is_featured": False,
            "category": "",
        })
    return articles


# ---------- 抓 Featured Stories ----------
def fetch_featured_articles():
    """從首頁抓 Featured Stories，過濾 sponsored 與舊文章。"""
    try:
        resp = requests.get(HOME_URL, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"      ⚠️ Failed to fetch homepage: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # 用 id="featured-stories" 定位
    anchor = soup.find(id="featured-stories")
    container = anchor.find_parent("bsp-list-loadmore") if anchor else None
    if not container:
        # Fallback: class 找
        container = soup.find("bsp-list-loadmore", class_=re.compile(r"PageListStandardB"))
    if not container:
        print(f"      ⚠️ Featured Stories container not found on homepage")
        return []

    today = datetime.now().date()
    cutoff = today - timedelta(days=FEATURED_DAYS_BACK)
    items = container.find_all("div", class_="PagePromo")

    featured = []
    for item in items:
        article = _parse_pagepromo(item)
        if not article:
            continue

        # 過濾 sponsored
        if "sponsor" in article["category"].lower():
            continue

        # 過濾舊文章
        date_obj = _parse_date(article.get("date_raw"))
        if not date_obj or date_obj < cutoff:
            continue

        article["is_featured"] = True
        article["date"] = date_obj
        featured.append(article)

    # 上限保護
    return featured[:MAX_FEATURED]


def _parse_pagepromo(promo):
    """解析單個 PagePromo 卡片。"""
    link_tag = promo.find("a", attrs={"aria-label": True}) or promo.find("a", href=True)
    if not link_tag:
        return None
    link = link_tag.get("href", "")
    if link.startswith("/"):
        link = "https://www.biospace.com" + link
    link = link.split("?")[0].rstrip("/")

    title = link_tag.get("aria-label", "").strip()
    if not title:
        h = promo.find(["h1", "h2", "h3"])
        if h:
            title = h.get_text(strip=True)
    if not title or len(title) < 10:
        return None

    desc = ""
    desc_tag = promo.find(class_=re.compile(r"description", re.I))
    if desc_tag:
        desc = desc_tag.get_text(" ", strip=True)

    date_raw = None
    for time_tag in promo.find_all("time"):
        date_raw = time_tag.get("datetime") or time_tag.get_text(strip=True)
        if date_raw:
            break
    if not date_raw:
        text = promo.get_text(" ", strip=True)
        m = re.search(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4}",
            text,
        )
        if m:
            date_raw = m.group(0)

    category = ""
    cat_tag = promo.find(class_=re.compile(r"eyebrow|category|kicker", re.I))
    if cat_tag:
        category = cat_tag.get_text(strip=True)

    return {
        "title": title,
        "link": link,
        "summary": desc,
        "date_raw": date_raw,
        "category": category,
    }


def _parse_date(date_raw: str):
    if not date_raw:
        return None
    s = date_raw.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
    for fmt in ["%B %d, %Y", "%b %d, %Y", "%b. %d, %Y"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------- 合併 Featured + RSS（去重）----------
def merge_articles(featured, rss):
    """Featured 優先放前面，RSS 去重後補滿到 MAX_RSS_ARTICLES 篇。"""
    seen_links = {a["link"] for a in featured}
    rss_unique = [a for a in rss if a["link"] not in seen_links]
    rss_kept = rss_unique[:MAX_RSS_ARTICLES]

    print(f"   📊 Featured: {len(featured)}, RSS deduped: {len(rss_unique)}, "
          f"RSS kept: {len(rss_kept)}")

    return featured + rss_kept


# ---------- 爬全文 ----------
def fetch_full_text(url: str) -> str:
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        candidates = [
            ("article", {}),
            ("div", {"class": re.compile(r"article-?body|story-?body|post-?content|article-?content", re.I)}),
            ("main", {}),
        ]
        for tag, attrs in candidates:
            node = soup.find(tag, attrs)
            if node:
                for junk in node.select("script, style, aside, nav, footer, .ad, .advertisement, .newsletter"):
                    junk.decompose()
                paragraphs = [p.get_text(" ", strip=True) for p in node.find_all("p")]
                text = "\n\n".join(p for p in paragraphs if len(p) > 20)
                if len(text) > 200:
                    return text

        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = "\n\n".join(p for p in paragraphs if len(p) > 20)
        return text if len(text) > 200 else ""
    except Exception as e:
        print(f"      ⚠️ Failed to fetch full text: {e}")
        return ""


# ---------- 呼叫 Gemini ----------
def _call_gemini_json(prompt: str) -> dict:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return json.loads(response.text)
        except Exception as e:
            last_error = e
            err_str = str(e)
            is_retryable = any(code in err_str for code in
                               ["503", "429", "UNAVAILABLE", "overloaded", "RESOURCE_EXHAUSTED"])
            if attempt < MAX_RETRIES and is_retryable:
                wait = RETRY_DELAY_SEC * attempt
                print(f"      ⏳ Attempt {attempt} failed, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise last_error


def _call_gemini_text(prompt: str) -> str:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return response.text.strip()
        except Exception as e:
            last_error = e
            err_str = str(e)
            is_retryable = any(code in err_str for code in
                               ["503", "429", "UNAVAILABLE", "overloaded", "RESOURCE_EXHAUSTED"])
            if attempt < MAX_RETRIES and is_retryable:
                wait = RETRY_DELAY_SEC * attempt
                print(f"      ⏳ Attempt {attempt} failed, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise last_error


def translate_with_vocab(title: str, content: str) -> dict:
    prompt = f"""你是專業的生技醫藥新聞翻譯員。請處理以下英文新聞：

標題：{title}

內文：
{content}

請執行以下四件事：

1. 寫一段「3 句話以內」的繁體中文 TL;DR 摘要，講清楚這篇新聞的核心：
   發生了什麼、誰是主角、為什麼重要。

2. 寫一段「3 句話以內」的英文 TL;DR，內容對應中文版本，但**不是逐字翻譯**，
   而是自然、簡潔、像 Endpoints News 或 Bloomberg 風格的英文新聞概要。
   每句結尾用句號。中英兩版資訊量要相當。

3. 把整篇內文翻譯成自然流暢的繁體中文（台灣用語，例如「資料」而非「数据」)。
   要翻譯完整內文，不要省略或摘要。保留段落結構，段落之間用 \\n\\n 分隔。

4. 從原文中挑選 {VOCAB_COUNT} 個生技/醫藥/商業領域中值得學習的單字或片語。
   優先選擇 B2 以上難度或專業術語，不要選 the, is, have 這類基礎字。

請以下列 JSON 格式回傳，不要加任何說明文字或 markdown 標記：
{{
  "title_zh": "翻譯後的標題",
  "tldr": "3 句話以內的繁中重點摘要",
  "tldr_en": "3 句話以內的英文重點摘要 (Endpoints / Bloomberg style)",
  "translation": "完整翻譯後的內文，段落間用 \\n\\n 分隔",
  "vocabulary": [
    {{
      "word": "英文單字或片語",
      "pos": "詞性 (n./v./adj./phr. 等)",
      "meaning": "繁體中文解釋",
      "example": "從原文擷取的例句"
    }}
  ]
}}"""
    return _call_gemini_json(prompt)


def generate_insight(title: str, content: str) -> str:
    """以資深 computational biologist 的視角分析新聞。"""
    prompt = f"""You are a senior computational biologist (Principal Scientist / Director level) at a top
biotech, pharma, or tech-bio company (e.g., Recursion, insitro, Genentech gRED, Schrödinger,
Isomorphic Labs, Generate Biomedicines). You've seen multiple cycles of "AI for drug discovery"
hype and have a sharp, well-informed view on what actually matters.

Write a deeply informative industry briefing in TRADITIONAL CHINESE (Taiwanese usage: 「資料」 not
「数据」; 「程式」 not 「代码」). The audience is a graduating computational biology student
entering industry, but this is a BRIEFING, not a personal letter.

文章標題：{title}

文章內容：
{content}

# 內容深度要求（最重要）

這份簡報的價值來自具體、紮實的領域知識。請主動補充以下類型的細節（凡是相關的都該寫）：

- **數字與規模**：靶點佔已上市藥物的比例、市場規模、臨床試驗階段、藥物銷售額、收購金額等
- **具體公司與藥物名**：不只說「大藥廠」，要說「Eli Lilly 收購 Radionetics Oncology」「Novartis 的 Pluvicto」
  「Novo Nordisk 的 Ozempic / Wegovy」等真實案例
- **機制比喻**：把專業概念用生動的比喻講清楚
  （例如「放射性藥物像導彈：胜肽是導彈頭鎖定病灶，放射性同位素是彈藥精準打擊」）
- **術語的括號定義**：在第一次出現時用括號簡短定義
  （例如「孤兒受體（orphan receptor，已知存在但其內源性配體或功能不明確的受體）」）
- **技術對比**：比較不同 modality 或方法的差異
- **競爭格局**：列出該領域真實在做的公司清單（3–6 家）

# 結構

用三個固定小標題（Markdown ## 格式）：

## 🔬 技術與科學
講清楚這個藥物、療法或平台的本質：什麼 modality、什麼靶點/機制、創新點在哪、
為什麼這件事重要、相關的成功案例或先例。要有具體數字、藥物名、機制細節。

## 🧮 Computational / AI 觀點
具體說明這件事跟 compbio 的連結：哪些計算方法在這條路徑上不可或缺
（結構預測 / 分子動力學 / 高通量資料分析 / 機器學習建模 / 生成式 AI 等）？
哪些公司在這條路線上走得遠？若新聞跟 compbio 關聯薄弱，誠實點出。

## 🎯 產業地景與求職訊號
競爭格局：列出該 modality 或 indication 領域的主要 player 名單。
市場規模或趨勢訊號。值得投履歷的公司類型。相關的核心技能組合。

# 語氣與格式規範

絕對禁止：
- ❌ 對話式開場：「嗨」「恭喜」「這篇新聞...」「讓我們...」
- ❌ 第二人稱「你」「妳」「您」
- ❌ 教學/說教口吻：「別被沖昏頭」「要記得」
- ❌ 勵志收尾：「保持開放心態」「擁抱挑戰」「拭目以待」
- ❌ 籠統總結段：「總之...」「綜合來看...」
- ❌ 「值得關注」「未來可期」這類沒資訊量的句子

採用：
- ✅ 像 Endpoints News、STAT News 的產業簡報
- ✅ 直接陳述事實與判斷，第三人稱
- ✅ 客觀句式：「值得追蹤的公司包含...」「核心技能涵蓋...」
- ✅ 公司名、藥物名、技術術語用 **粗體** (Markdown)
- ✅ 開頭直接切入技術或產業判斷

# 長度
總字數 450–650 字。內容深度永遠優先於簡潔。

只回傳簡報內容本身（含三個 ## 小標題），不要前後加任何額外說明。"""
    return _call_gemini_text(prompt)


# ---------- HTML 工具 ----------
def text_to_html_paragraphs(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    out = []
    for p in paragraphs:
        p_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
        if p_html.startswith("## "):
            heading = p_html[3:].strip()
            out.append(
                f'<h3 style="margin-top:20px; margin-bottom:8px; color:#0066cc; '
                f'font-size:15px;">{heading}</h3>'
            )
        else:
            out.append(f"<p>{p_html}</p>")
    return "\n".join(out)


# ---------- 組單篇信件 HTML ----------
def build_single_html(idx: int, total: int, item: dict) -> str:
    data = item["data"]
    today = datetime.now().strftime("%Y-%m-%d")
    is_featured = item.get("is_featured", False)

    vocab_rows = "".join(
        f"""<tr>
            <td style="padding:8px; border:1px solid #ddd;"><b>{v['word']}</b></td>
            <td style="padding:8px; border:1px solid #ddd; color:#666;">{v['pos']}</td>
            <td style="padding:8px; border:1px solid #ddd;">{v['meaning']}</td>
            <td style="padding:8px; border:1px solid #ddd; font-size:13px; color:#555;">{v['example']}</td>
        </tr>"""
        for v in data["vocabulary"]
    )

    zh_html = text_to_html_paragraphs(data["translation"])
    en_html = text_to_html_paragraphs(item["full_text"])
    insight_html = text_to_html_paragraphs(item["insight"])

    # Featured 專屬徽章
    featured_badge = ""
    if is_featured:
        cat = item.get("category", "")
        cat_text = f" · {cat}" if cat else ""
        featured_badge = (
            f'<div style="display:inline-block; background:#ffd700; color:#333; '
            f'padding:4px 12px; border-radius:12px; font-size:12px; font-weight:bold; '
            f'margin-bottom:12px;">⭐ FEATURED STORY{cat_text}</div>'
        )

    return f"""<html><body style="font-family: -apple-system, sans-serif; max-width:720px; margin:auto; color:#222; line-height:1.7;">

    <div style="color:#888; font-size:13px; margin-bottom:8px;">
        🧬 Biospace 每日新聞 · {today} · 第 {idx}/{total} 篇
    </div>

    {featured_badge}

    <h1 style="color:#0066cc; border-bottom:3px solid #0066cc; padding-bottom:8px;">
        {data['title_zh']}
    </h1>
    <p style="color:#888; font-size:13px;">
        Original: {item['original_title']} · 
        <a href="{item['link']}">Read on Biospace ↗</a>
    </p>

    <div style="margin-top:24px; background:#fffbe6; border-left:4px solid #f5c518; padding:14px 18px; border-radius:4px;">
        <div style="font-weight:bold; color:#8a6d00; margin-bottom:6px;">⚡ TL;DR</div>
        <div style="color:#444;">{data['tldr']}</div>
        <div style="font-weight:bold; color:#8a6d00; margin-top:12px; margin-bottom:6px;">⚡ TL;DR (EN)</div>
        <div style="color:#444; font-style:italic;">{data['tldr_en']}</div>
    </div>

    <h2 style="margin-top:32px; color:#0066cc;">📖 中文翻譯</h2>
    <div style="background:#f9f9fc; padding:16px 20px; border-radius:6px;">
        {zh_html}
    </div>

    <h2 style="margin-top:32px; color:#0066cc;">📄 English Original</h2>
    <div style="background:#fafafa; padding:16px 20px; border-radius:6px; color:#444;">
        {en_html}
    </div>

    <h2 style="margin-top:32px; color:#0066cc;">💡 Industry Insight by AI </h2>
    <div style="background:#f0f7ff; border-left:4px solid #0066cc; padding:16px 20px; border-radius:4px;">
        {insight_html}
    </div>

    <h2 style="margin-top:32px; color:#0066cc;">📚 單字表</h2>
    <table style="border-collapse:collapse; width:100%; font-size:14px;">
        <thead>
            <tr style="background:#0066cc; color:white;">
                <th style="padding:8px; text-align:left;">單字</th>
                <th style="padding:8px; text-align:left;">詞性</th>
                <th style="padding:8px; text-align:left;">中譯</th>
                <th style="padding:8px; text-align:left;">例句</th>
            </tr>
        </thead>
        <tbody>{vocab_rows}</tbody>
    </table>

    <p style="margin-top:32px; color:#aaa; font-size:12px; text-align:center;">
        — 由 Gemini 自動翻譯與評論 · 內容僅供學習與職涯參考 —
    </p>
    </body></html>"""


# ---------- 寄信（BCC 模式）----------
def send_email(subject: str, html_body: str, smtp_server):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Biospace 每日新聞", GMAIL_USER))
    msg["To"] = GMAIL_USER           # 顯示寄給機器人自己
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    # Bcc 不寫進 header，實際投遞到所有收件人
    smtp_server.send_message(msg, to_addrs=BCC_RECIPIENTS)


# ---------- 主流程 ----------
def main():
    print("=" * 60)
    print(f"🧬 Biospace Daily Digest · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Step 1: 抓 Featured Stories
    print(f"\n⭐ Fetching Featured Stories (today + last {FEATURED_DAYS_BACK} day)...")
    featured = fetch_featured_articles()
    print(f"   Found {len(featured)} qualifying featured articles")
    for f in featured:
        print(f"   · [{f.get('category', '')}] {f['title'][:60]}... ({f.get('date')})")

    # Step 2: 抓 RSS
    print(f"\n📡 Fetching top {MAX_RSS_ARTICLES + MAX_FEATURED} RSS articles (extras for dedup)...")
    rss = fetch_rss_articles(MAX_RSS_ARTICLES + MAX_FEATURED)
    print(f"   Got {len(rss)} from RSS")

    # Step 3: 合併去重
    print(f"\n🔀 Merging featured + RSS...")
    articles = merge_articles(featured, rss)
    print(f"   Total: {len(articles)} articles to process")

    if not articles:
        print("\n❌ No articles to process.")
        return

    # Step 4: 爬全文 + 翻譯 + insight
    results = []
    for art in articles:
        flag = "⭐" if art.get("is_featured") else "📰"
        print(f"\n{flag} {art['title'][:70]}")
        print(f"   📥 Fetching full text...")
        full_text = fetch_full_text(art["link"])
        if not full_text:
            print(f"   ℹ️ Full text unavailable, falling back to summary.")
            full_text = art["summary"]
        print(f"   📝 Got {len(full_text)} chars")

        try:
            print(f"   🔄 Translating + summarizing...")
            data = translate_with_vocab(art["title"], full_text)

            print(f"   🧠 Generating compbio insight...")
            insight = generate_insight(art["title"], full_text)

            results.append({
                "original_title": art["title"],
                "link": art["link"],
                "full_text": full_text,
                "insight": insight,
                "data": data,
                "is_featured": art.get("is_featured", False),
                "category": art.get("category", ""),
            })
            print(f"   ✅ Done")
        except Exception as e:
            print(f"   ⚠️ Failed: {e}")

        time.sleep(FETCH_DELAY_SEC)

    if not results:
        print("\n❌ No articles processed successfully.")
        return

    # Step 5: 一封一封寄
    today = datetime.now().strftime("%Y-%m-%d")
    total = len(results)
    print(f"\n📧 Sending {total} emails (BCC to {len(BCC_RECIPIENTS)} recipients)...")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        for i, item in enumerate(results, 1):
            star = "⭐ " if item.get("is_featured") else ""
            subject = f"🧬 Biospace {star}#{i}/{total} · {item['data']['title_zh']} · {today}"
            html = build_single_html(i, total, item)
            send_email(subject, html, smtp)
            print(f"   ✅ Sent #{i}/{total}: {star}{item['data']['title_zh'][:50]}")

    print(f"\n🎉 All done! {total} emails sent (BCC to {len(BCC_RECIPIENTS)} recipient(s))")


if __name__ == "__main__":
    main()