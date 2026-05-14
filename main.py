"""
Biospace 每日新聞翻譯機器人 - compbio 學生求職導向版
版面順序：
1. 三句中文 TL;DR
2. 完整中文翻譯
3. 完整英文原文
4. Industry Insight（compbio 視角：技術路線、平台、求職）
5. 單字表
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
from datetime import datetime
from google import genai

# ---------- 設定 ----------
RSS_URL = "https://www.biospace.com/all-news.rss"
MAX_ARTICLES = 3
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
RECIPIENT = os.environ["RECIPIENT_EMAIL"]

client = genai.Client(api_key=GEMINI_API_KEY)


# ---------- 抓 RSS ----------
def fetch_articles(limit: int):
    feed = feedparser.parse(RSS_URL)
    articles = []
    for entry in feed.entries[:limit]:
        articles.append({
            "title": entry.title,
            "link": entry.link,
            "summary": BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(strip=True),
        })
    return articles


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

請執行以下三件事：

1. 寫一段「3 句話以內」的繁體中文 TL;DR 摘要，講清楚這篇新聞的核心：
   發生了什麼、誰是主角、為什麼重要。

2. 把整篇內文翻譯成自然流暢的繁體中文（台灣用語，例如「資料」而非「数据」)。
   要翻譯完整內文，不要省略或摘要。保留段落結構，段落之間用 \\n\\n 分隔。

3. 從原文中挑選 {VOCAB_COUNT} 個生技/醫藥/商業領域中值得學習的單字或片語。
   優先選擇 B2 以上難度或專業術語，不要選 the, is, have 這類基礎字。

請以下列 JSON 格式回傳，不要加任何說明文字或 markdown 標記：
{{
  "title_zh": "翻譯後的標題",
  "tldr": "3 句話以內的繁中重點摘要",
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
  （例如「胜肽相較於小分子更特異、比抗體分子量更小」「結合篩選 vs 功能性篩選」）
- **競爭格局**：列出該領域真實在做的公司清單（3–6 家），不要只說「許多公司在做」

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
市場規模或趨勢訊號。值得投履歷的公司類型。相關的核心技能組合
（程式、ML、計算化學、特定生物學領域知識等）。

# 語氣與格式規範

絕對禁止：
- ❌ 對話式開場：「嗨」「恭喜」「這篇新聞...」「讓我們...」「我們可以...」
- ❌ 第二人稱「你」「妳」「您」任何形式
- ❌ 教學/說教口吻：「別被沖昏頭」「要記得」「這告訴我們」
- ❌ 勵志收尾：「保持開放心態」「擁抱挑戰」「佔據一席之地」「拭目以待」
- ❌ 籠統總結段：「總之...」「綜合來看...」「這條路...」
- ❌ 「值得關注」「未來可期」這類沒資訊量的句子

採用：
- ✅ 像 Endpoints News、STAT News、Nature Biotechnology 的產業簡報
- ✅ 直接陳述事實與判斷，第三人稱
- ✅ 涉及行動建議改成客觀句式：「值得追蹤的公司包含...」「核心技能涵蓋...」
  而非「你應該追蹤...」
- ✅ 公司名、藥物名、技術術語用 **粗體** (Markdown)
- ✅ 開頭直接切入技術或產業判斷，不要鋪陳

# 長度
總字數 450–650 字。內容深度永遠優先於簡潔——寧可資訊密集，不要泛泛而談。

只回傳簡報內容本身（含三個 ## 小標題），不要前後加任何額外說明。"""
    return _call_gemini_text(prompt)


# ---------- HTML 工具 ----------
def text_to_html_paragraphs(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    out = []
    for p in paragraphs:
        # 支援 **粗體**
        p_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
        # 支援 ## 小標題（用在 insight 的三段分節）
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

    return f"""<html><body style="font-family: -apple-system, sans-serif; max-width:720px; margin:auto; color:#222; line-height:1.7;">

    <div style="color:#888; font-size:13px; margin-bottom:8px;">
        🧬 Biospace 每日新聞 · {today} · 第 {idx}/{total} 篇
    </div>

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


# ---------- 寄信 ----------
def send_email(subject: str, html_body: str, smtp_server):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Biospace 每日新聞", GMAIL_USER))
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    smtp_server.send_message(msg)


# ---------- 主流程 ----------
def main():
    print(f"📡 Fetching top {MAX_ARTICLES} articles from RSS...")
    articles = fetch_articles(MAX_ARTICLES)

    results = []
    for art in articles:
        print(f"\n📰 {art['title'][:70]}")
        print(f"   📥 Fetching full text...")
        full_text = fetch_full_text(art["link"])
        if not full_text:
            print(f"   ℹ️ Full text unavailable, falling back to RSS summary.")
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
            })
            print(f"   ✅ All done for this article")
        except Exception as e:
            print(f"   ⚠️ Failed: {e}")

        time.sleep(FETCH_DELAY_SEC)

    if not results:
        print("\n❌ No articles processed. Skipping email.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    total = len(results)
    print(f"\n📧 Sending {total} emails...")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        for i, item in enumerate(results, 1):
            subject = f"🧬 Biospace #{i}/{total} · {item['data']['title_zh']} · {today}"
            html = build_single_html(i, total, item)
            send_email(subject, html, smtp)
            print(f"   ✅ Sent #{i}/{total}: {item['data']['title_zh'][:50]}")

    print(f"\n🎉 All done! {total} emails sent to {RECIPIENT}")


if __name__ == "__main__":
    main()