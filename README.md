# Dev News Daily (Discord Automation)

Automated daily **Developer/Software Engineering** news with **OpenAI web search** and **Discord embeds**.  
This project uses **GitHub Actions** to find the latest dev-related news every 24h, summarize it with OpenAI, and post a clean **digest to a Discord channel** via webhook.

---

## ✨ Features

- 🔎 Uses OpenAI **Responses API** with integrated **web search**  
- 🧑‍💻 Focuses on **programming languages**, **frameworks**, **tooling**, **releases/changelogs**, **DevOps/Cloud**, and **security advisories**  
- 📌 Posts to Discord using **rich embeds** (title, summary, bullet points, sources)  
- ⏰ Runs automatically every day at midnight (Paris time) via GitHub Actions  
- 🛠 No server or hosting required → **GitHub + Discord webhook** only  

---

## 📂 Repository structure
.
├── .github/workflows/dev-news.yml # GitHub Actions workflow (automation)  
├── dev_news.py # Main Python script  
├── requirements.txt # Python dependencies  
└── README.md # Project documentation  

---

## ⚙️ Setup

### 1) Create a Discord Webhook
- In your Discord server → Channel settings → *Integrations* → *Webhooks*  
- Create a webhook and copy the **URL**

### 2) Add GitHub Secrets
In your repository → **Settings → Secrets → Actions** → *New repository secret* :

- `OPENAI_API_KEY` → your [OpenAI API key](https://platform.openai.com/api-keys)  
- `DISCORD_WEBHOOK_URL` → the Discord webhook URL for your target channel  

### 3) Install dependencies (local test)
```bash
pip install -r requirements.txt
```

### 4) Run locally (optional)
```bash
python dev_news.py
```
### 5) Push to GitHub
- Commit & push → GitHub Actions will run the workflow automatically  
- Check the **Actions** tab for logs  
- The result should appear in your Discord channel 🎉

---

## 🕒 Schedule
By default, the workflow runs:
- Every day around **00:00 Paris time**  
- You can also trigger it manually in **Actions → Run workflow**

---

## 🔧 Configuration (optional)
Edit these environment variables in the workflow if needed:

- `MODEL` → default: `gpt-4.1-mini`  
- `HOURS` → time window for news (default: `24`)  
- `TIMEZONE` → e.g. `Europe/Paris`  
- `LOCALE` → output language (default: `fr-FR`)  
- `EMBED_COLOR` → Discord embed color (default: `15105570` = orange)

---

## 📸 Example Output
A daily post in Discord looks like this:

- **Embed title** → "Veille Dev — YYYY-MM-DD (dernières 24 h)"  
- **Summary** (2–3 sentences)  
- **Highlights** (bullet points: names, versions, breaking changes, vendors)  
- **Sources** (list of links with short titles & dates)

---

## 🧭 Scope (examples)
- **Languages:** Python, JavaScript/TypeScript, Go, Rust, Java, C#, etc.  
- **Frameworks/Libraries:** React, Vue, Angular, Svelte, Django, FastAPI, Spring, .NET, etc.  
- **Tooling:** package managers, linters, formatters, build tools, IDEs  
- **Releases/Changelogs:** major/minor releases, deprecations, breaking changes  
- **DevOps/Cloud/CI-CD:** containers, IaC, serverless, performance  
- **Security:** advisories (CVE), supply-chain (npm/pypi/crates), patches  
- **Standards/Specs:** TC39, WHATWG, W3C, database ecosystems

