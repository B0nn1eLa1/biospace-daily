# 🧬 Biospace Daily Digest

每天自動把 [BioSpace](https://www.biospace.com) 的英文生技新聞翻譯成繁體中文，附上 TL;DR、業界 insight 與單字表，透過 Gmail 寄送。專為準備進入業界的 computational biology 學生設計。

## ✨ 功能

每天定時抓取 BioSpace 最新 3 則新聞，每則新聞獨立寄一封信，內含：

- ⚡ **TL;DR** — 三句話講清楚這則新聞在幹嘛
- 📖 **完整中文翻譯** — 自然流暢的台灣繁中
- 📄 **完整英文原文** — 方便中英對照學習
- 💡 **Industry Insight** — 以資深 computational biologist 視角撰寫的產業簡報
  - 🔬 技術與科學（modality、機制、創新點）
  - 🧮 Computational / AI 觀點（與 compbio 的連結、領先公司）
  - 🎯 產業地景與求職訊號（競爭格局、相關技能、值得追蹤的公司）
- 📚 **單字表** — 10 個生技/醫藥/商業領域專業單字，含詞性、中譯、例句

## 🏗️ 架構

```
BioSpace RSS
    ↓
Python (feedparser + BeautifulSoup) 抓 RSS + 爬全文
    ↓
Google Gemini API 翻譯 + 產生 insight + 單字表
    ↓
Gmail SMTP 寄信（每篇一封，BCC 多人）
    ↓
GitHub Actions 每天定時觸發
```

## 🛠️ 使用技術

- **語言**：Python 3.12
- **AI**：Google Gemini 2.5 Flash（免費 tier）
- **排程**：GitHub Actions（免費）
- **寄信**：Gmail SMTP + 應用程式密碼
- **主要套件**：`google-genai`、`feedparser`、`requests`、`beautifulsoup4`

## 🚀 部署

### 1. 申請必要服務

- [Google AI Studio](https://aistudio.google.com) → Get API key
- [Gmail 應用程式密碼](https://myaccount.google.com/apppasswords)（需先啟用兩步驟驗證）

### 2. Fork 或 clone 此 repo

```bash
git clone https://github.com/b0nn1eLa1/biospace-daily.git
cd biospace-daily
```

### 3. 設定 GitHub Secrets

到 repo 的 **Settings → Secrets and variables → Actions**，新增以下 4 個 secret：

| Name | Value |
|------|-------|
| `GEMINI_API_KEY` | Google AI Studio 產生的 API key |
| `GMAIL_USER` | 寄件用的 Gmail 地址 |
| `GMAIL_APP_PASSWORD` | Gmail 應用程式密碼（16 碼） |
| `RECIPIENT_EMAIL` | 收件人 email，多人用逗號分隔 |

### 4. 啟用 GitHub Actions

到 **Actions** tab，啟用 workflow，可手動點 **Run workflow** 測試。

## ⏰ 排程時間

預設台灣時間每天晚上 21:00 自動執行。修改 `.github/workflows/daily.yml` 的 cron 即可調整：

```yaml
- cron: '0 13 * * *'   # UTC 13:00 = 台灣 21:00
```

換算公式：台灣時間 − 8 = UTC 時間。

## 🎨 客製化

### 換新聞分類

修改 `main.py` 的 `RSS_URL`，可選分類：

| 分類 | URL |
|------|-----|
| 全部新聞（預設） | `https://www.biospace.com/all-news.rss` |
| FDA | `https://www.biospace.com/FDA.rss` |
| 交易/併購 | `https://www.biospace.com/deals.rss` |
| 政策 | `https://www.biospace.com/policy.rss` |
| 藥物遞送 | `https://www.biospace.com/drug-delivery.rss` |

### 調整每日篇數

修改 `main.py`：

```python
MAX_ARTICLES = 3   # 想看更多改成 5、想省 quota 改成 2
```

### 調整 Insight 角度

`generate_insight()` 函式裡的 prompt 是為 compbio 學生設計的。如果你是其他背景（化學、臨床、商管等），改 prompt 裡的人設與面向即可。

## 💰 成本

- **Gemini API**：免費 tier（每天 ~20–50 次請求），3 篇文章 = 6 次請求，剛好夠用
- **GitHub Actions**：免費（公開 repo 無限額度，私人 repo 每月 2000 分鐘）
- **Gmail**：免費（每天最多 500 封寄信額度）

**總月費：$0**

## ⚠️ 注意事項

- Gemini 免費 tier 會用你的請求資料訓練模型，**請勿傳入機密內容**
- 翻譯內容由 AI 生成，**僅供學習與職涯參考，非投資建議**
- 爬取 BioSpace 全文僅限個人學習用途，請尊重來源網站

## 📄 License

MIT — 自由 fork、修改、使用。

---

Built with Claude 🧡 by Bonnie
