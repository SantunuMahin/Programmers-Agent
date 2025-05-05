# Programmers-Agent
Programmers Agent is a Discord Bot


## 🔍 Overview

The Programmer's Agent is an AI-powered assistant designed to help developers master coding interviews, solve LeetCode problems, and understand complex algorithms efficiently. Unlike generic Discord bots, it acts as a personalized mentor, offering tailored problem recommendations, AI-generated hints, and detailed explanations.
🚀 Core Features
### 1️⃣ LeetCode Problem Management

    !leetcode [difficulty] – Fetches a random problem (Easy/Medium/Hard/Interview).

    !search <query> – Searches problems by topic or title (e.g., !search binary tree).

    !daily – Provides today’s LeetCode daily challenge.

    !hint <problem> – Gives a strategic hint without spoiling the solution.

### 2️⃣ AI-Powered Assistance

    !ask <question> – Answers coding questions with explanations (e.g., "How does Dijkstra’s algorithm work?").

    !explain <concept> – Breaks down programming concepts (e.g., "!explain dynamic programming").

    Personalized Responses – The AI is fine-tuned to match the user’s learning style (based on the developer’s preferences).

### 3️⃣ Analytics & Utility

    !stats – Shows bot performance, problem-solving stats, and uptime.

    !ping – Checks bot responsiveness.

    !invite – Generates an invite link for other servers.

## ⚙️ Technical Architecture
### 🔗 Data Flow

    Problem Fetching

        Scrapes LeetCode via GraphQL API.

        Caches results in leetcode_interview.json (auto-refreshes every 24h).

    AI Integration

        Uses OpenRouter API (deepseek/deepseek-r1:free model).

        Implements response chunking (Discord’s 2000-character limit).

    User Interaction

        Commands → Bot processes → AI generates response → Rich embed formatting.

## 🧠 Smart Features

✅ Adaptive Caching – Minimizes API calls with TTL-based refreshing.
✅ Thread-Safe Operations – Uses asyncio.Lock() to prevent race conditions.
✅ Error Resilience – Fallback to default problems if scraping fails.
✅ Rate Limiting – Prevents spam with cooldowns (Config.COMMAND_COOLDOWN).
📂 GitHub Repository Structure

/Programmers-Agent  
│  
├── 📜 main.py                # Entry point (Discord bot setup)  
├── 📜 leetcode_interview.json # Cached problems (auto-updated)  
├── 📜 .env                   # API keys (DISCORD_TOKEN, OPENROUTER_API_KEY)  
├── 📜 leetcode_bot.log       # Logs commands & errors  
└── 📜 README.md              # Setup & usage guide  

🔧 How to Deploy

    Install dependencies:
    sh

`pip install discord.py aiohttp beautifulsoup4 python-dotenv`

### Set up .env:
env

DISCORD_TOKEN=your_discord_bot_token
OPENROUTER_API_KEY=your_openrouter_key

Run the bot:
sh

    -> python main.py

### 💡 Why This Stands Out

    Not Just a Bot, But a Mentor – Provides structured learning paths for interview prep.

    Optimized for Speed – Async I/O ensures smooth performance.

    Personalized AI – Understands the developer’s background for better responses.

    Self-Healing – Falls back to default problems if LeetCode is unreachable.

### 🎯 Ideal For:

✔ Job Seekers – Crush coding interviews with curated problems.
✔ Competitive Programmers – Sharpen skills with AI hints.
✔ Self-Taught Devs – Learn algorithms interactively.

This Programmer’s Agent is more than a bot—it’s a 24/7 coding coach 🚀.
