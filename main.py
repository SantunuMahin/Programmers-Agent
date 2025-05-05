import os
import json
import random
import asyncio
import aiohttp
import discord
from datetime import datetime
from discord.ext import commands, tasks
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Tuple, Union, Any
from dataclasses import dataclass
import logging
import aiofiles
from dataclasses import asdict

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('leetcode_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Constants and Configurations ---
load_dotenv()

@dataclass
class Config:
    TOKEN: str = os.getenv("DISCORD_TOKEN")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY")
    CACHE_FILE: str = "leetcode_interview.json"
    REQUEST_TIMEOUT: int = 60
    MESSAGE_LIMIT: int = 3000
    PROBLEM_CACHE_TTL: int = 86400  # 24 hours
    COMMAND_COOLDOWN: int = 3
    MAX_PROBLEMS_PER_CATEGORY: int = 350
    MAX_AI_RESPONSE_LENGTH: int = 1900
    MAX_QUESTION_LENGTH: int = 500
    SCRAPE_RETRIES: int = 3
    SCRAPE_TIMEOUT: int = 15
    AI_MODEL: str = "deepseek/deepseek-r1:free"

# --- Data Models ---
@dataclass
class LeetCodeProblem:
    title: str
    url: str
    difficulty: str
    topics: List[str]
    description: Optional[str] = None
    solution_hint: Optional[str] = None
    premium: Optional[bool] = False

@dataclass
class BotStats:
    commands_processed: int = 0
    problems_served: int = 0
    ai_queries: int = 0
    errors_encountered: int = 0

# --- Utility Classes ---
class ProblemCacheManager:
    """Handles caching of LeetCode problems with TTL and validation"""
    _lock = asyncio.Lock()
    
    @staticmethod
    async def load_problems() -> Dict[str, List[LeetCodeProblem]]:
        """Load problems from cache or fallback to defaults with TTL check"""
        async with ProblemCacheManager._lock:
            try:
                if await ProblemCacheManager._is_cache_valid():
                    cached = await ProblemCacheManager._load_cache()
                    if cached:
                        logger.info("Loaded problems from cache")
                        return cached
            except Exception as e:
                logger.error(f"Cache loading error: {e}")
            
            logger.info("Fetching fresh problems from LeetCode")
            try:
                problems = await ProblemScraper.scrape_leetcode() or ProblemDefaults.get_default_problems()
                await ProblemCacheManager._update_cache(problems)
                return problems
            except Exception as e:
                logger.error(f"Failed to fetch problems: {e}")
                return ProblemDefaults.get_default_problems()

    @staticmethod
    async def _is_cache_valid() -> bool:
        """Check if cache exists and is fresh"""
        try:
            if not os.path.exists(Config.CACHE_FILE):
                return False
                
            mod_time = os.path.getmtime(Config.CACHE_FILE)
            return (datetime.now().timestamp() - mod_time) < Config.PROBLEM_CACHE_TTL
        except OSError:
            return False

    @staticmethod
    async def _load_cache() -> Optional[Dict[str, List[LeetCodeProblem]]]:
        """Load and validate cache"""
        try:
            async with aiofiles.open(Config.CACHE_FILE, 'r') as f:
                data = json.loads(await f.read())
                if not isinstance(data, dict):
                    return None
                    
                validated = {}
                for difficulty, problems in data.items():
                    if difficulty not in ['easy', 'medium', 'hard']:
                        continue
                    validated[difficulty] = [
                        LeetCodeProblem(**p) for p in problems 
                        if isinstance(p, dict) and ProblemCacheManager._validate_problem(p)
                    ]
                return validated
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Cache load failed: {e}")
            return None

    @staticmethod
    def _validate_problem(problem_data: dict) -> bool:
        """Validate problem data structure"""
        required = ['title', 'url', 'difficulty']
        return all(k in problem_data for k in required) and \
               isinstance(problem_data['title'], str) and \
               problem_data['difficulty'] in ['easy', 'medium', 'hard']

    @staticmethod
    async def _update_cache(problems: Dict[str, List[LeetCodeProblem]]) -> None:
        """Update cache atomically with error handling"""
        try:
            temp_file = f"{Config.CACHE_FILE}.tmp"
            async with aiofiles.open(temp_file, 'w') as f:
                await f.write(json.dumps(
                    {k: [asdict(p) for p in v] for k, v in problems.items()},
                    indent=2
                ))
            os.replace(temp_file, Config.CACHE_FILE)
            logger.info("Cache updated successfully")
        except Exception as e:
            logger.error(f"Failed to update cache: {e}")
            try:
                os.remove(temp_file)
            except OSError:
                pass

class ProblemScraper:
    """Handles scraping LeetCode problems using GraphQL API"""
    
    GRAPHQL_URL = "https://leetcode.com/graphql"
    PROBLEMS_QUERY = """
    query problemsetQuestionList($categorySlug: String, $limit: Int, $filters: QuestionListFilterInput) {
        problemsetQuestionList: questionList(
            categorySlug: $categorySlug
            limit: $limit
            filters: $filters
        ) {
            questions: data {
                title
                titleSlug
                difficulty
                isPaidOnly
                topicTags {
                    name
                }
            }
        }
    }
    """
    
    @staticmethod
    async def scrape_leetcode() -> Optional[Dict[str, List[LeetCodeProblem]]]:
        """Scrape LeetCode problems using GraphQL API"""
        try:
            async with aiohttp.ClientSession() as session:
                data = {
                    "operationName": "problemsetQuestionList",
                    "query": ProblemScraper.PROBLEMS_QUERY,
                    "variables": {
                        "categorySlug": "",
                        "limit": 3000,
                        "filters": {}
                    }
                }
                
                headers = {
                    "Content-Type": "application/json",
                    "Referer": "https://leetcode.com/problemset/all/",
                }
                
                async with session.post(
                    ProblemScraper.GRAPHQL_URL,
                    json=data,
                    headers=headers,
                    timeout=Config.SCRAPE_TIMEOUT
                ) as response:
                    if response.status != 200:
                        logger.error(f"API request failed with status {response.status}")
                        return None
                        
                    result = await response.json()
                    questions = result.get('data', {}).get('problemsetQuestionList', {}).get('questions', [])
                    
                    problems = []
                    for q in questions:
                        try:
                            problems.append(LeetCodeProblem(
                                title=q['title'],
                                url=f"https://leetcode.com/problems/{q['titleSlug']}/",
                                difficulty=q['difficulty'].lower(),
                                topics=[t['name'] for t in q.get('topicTags', [])],
                                premium=q['isPaidOnly']
                            ))
                        except KeyError as e:
                            logger.debug(f"Skipping invalid problem: {e}")
                            continue
                    
                    return ProblemScraper._organize_by_difficulty(problems)
        except Exception as e:
            logger.error(f"Scraping failed: {e}")
            return None

    @staticmethod
    def _organize_by_difficulty(problems: List[LeetCodeProblem]) -> Dict[str, List[LeetCodeProblem]]:
        """Organize problems by difficulty"""
        organized = {'easy': [], 'medium': [], 'hard': []}
        for problem in problems:
            if problem.difficulty in organized:
                organized[problem.difficulty].append(problem)
        return organized

    @staticmethod
    def _organize_by_difficulty(problems: List[LeetCodeProblem]) -> Dict[str, List[LeetCodeProblem]]:
        """Organize problems by difficulty with interview-specific handling"""
        organized = {'easy': [], 'medium': [], 'hard': [], 'interview': []}
        
        INTERVIEW_PROBLEMS = [
            'two sum', 'add two numbers', 'longest substring without repeating characters',
            'median of two sorted arrays', 'container with most water', '3sum',
            'valid parentheses', 'merge two sorted lists', 'merge k sorted lists',
            'search in rotated sorted array', 'combination sum', 'rotate image',
            'group anagrams', 'maximum subarray', 'spiral matrix', 'jump game',
            'merge intervals', 'unique paths', 'climbing stairs', 'word break',
            'product of array except self', 'maximum product subarray'
        ]
        
        for problem in problems:
            # Add to regular difficulty categories
            if problem.difficulty in organized:
                organized[problem.difficulty].append(problem)
            
            # Add to interview category if it's a common interview problem
            if problem.title.lower() in INTERVIEW_PROBLEMS:
                organized['interview'].append(problem)
        
        return organized

class ProblemDefaults:
    """Provides default problem sets when scraping fails"""
    
    @staticmethod
    def get_default_problems() -> Dict[str, List[LeetCodeProblem]]:
        """Return comprehensive default problem sets including interview questions"""
        # [Previous default problems implementation remains the same]
        # ... (keeping all the same problem definitions)
        return {
            'easy': [
              # Previous problems...
            LeetCodeProblem(
                title="Diameter of Binary Tree",
                url="https://leetcode.com/problems/diameter-of-binary-tree/",
                difficulty="easy",
                topics=["Tree", "DFS"],
                solution_hint="Track max diameter during depth calculation."
            )
        ],
            'medium': [
                LeetCodeProblem(
                    title="Add Two Numbers",
                    url="https://leetcode.com/problems/add-two-numbers/",
                    difficulty="medium",
                    topics=["Linked List", "Math"],
                    solution_hint="Simulate digit-by-digit addition with carry handling."
                ),
               
            ],
            'hard': [
                LeetCodeProblem(
                    title="Median of Two Sorted Arrays",
                    url="https://leetcode.com/problems/median-of-two-sorted-arrays/",
                    difficulty="hard",
                    topics=["Array", "Binary Search"],
                    solution_hint="Use binary search to partition both arrays optimally."
                ),
               
            ],
            'interview': [
                # ===== EASY =====
               
                LeetCodeProblem(
                    title="Trapping Rain Water",
                    url="https://leetcode.com/problems/trapping-rain-water/",
                    difficulty="hard",
                    topics=["Array", "Two Pointers", "Dynamic Programming"],
                    solution_hint="Track left_max and right_max for each bar."
                )
            ]
        }

class AIHelper:
    """Handles AI querying with response length limiting and error handling"""
    
    @staticmethod
    async def query_ai(question: str) -> Optional[str]:
        """
        Query AI services with proper fallback and length limiting
        Args:
            question: The user's question/query
        Returns:
            str: AI response or error message
        """
        try:
            response = await AIHelper._query_openrouter(question)
            if response:
                # Clean and truncate response
                response = response.strip()
                if len(response) > Config.MAX_AI_RESPONSE_LENGTH:
                    response = response[:Config.MAX_AI_RESPONSE_LENGTH] + "... [response truncated]"
                return response
        except Exception as e:
            logger.error(f"AI service error: {e}")
        
        return "Sorry, I couldn't process your question right now."

    @staticmethod
    async def _query_openrouter(question: str) -> Optional[str]:
        """
        Query OpenRouter API with improved response handling
        Args:
            question: The user's question
        Returns:
            str: API response content or error message
        """
        if not Config.OPENROUTER_API_KEY:
            logger.error("OpenRouter API key not configured")
            return "Error: API key not configured"
            
        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/SantunuMahin"
        }
        
        payload = {
            "model": Config.AI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an advanced AI coding assistant specialized in LeetCode problems, technical interview preparation, "
                        "software development best practices, and long-term career growth for developers.\n\n"

                        "Your user is **Santunu Kaysar**, a passionate and forward-thinking Computer Science student with a solid foundation in "
                        "back-end development, data structures, and algorithmic problem-solving. He is the founding engineer at ServerCodeSocity, "
                        "deeply involved in writing scalable, clean, and maintainable code.\n\n"

                        "Santunu is an active competitive programmer on platforms like [LeetCode](https://leetcode.com/u/shantanumahin/) and "
                        "[Codeforces](https://codeforces.com/profile/shantanumahin1), where he consistently challenges himself to improve speed, logic, and efficiency.\n\n"

                        "His open-source contributions and projects are available on GitHub: [github.com/SantunuMahin](https://github.com/SantunuMahin/SantunuMahin). "
                        "These include applications built with Python, Django, REST APIs, automation tools, and intelligent Discord bots. "
                        "He actively strives for clean architecture, practical problem-solving, and impactful systems.\n\n"

                        "Santunu is also inspired by personal development books like *The 7 Habits of Highly Effective People*, *Atomic Habits*, and *The Alchemist*, "
                        "which inform his disciplined mindset and values-based leadership. He dreams of becoming a Machine Learning Engineer and using technology to address pressing global challenges, "
                        "including climate change, air pollution, and social inequality.\n\n"

                        "You can learn more about his professional profile here: [LinkedIn - Santunu Kaysar Mahin](https://www.linkedin.com/in/santunu-kaysar-mahin).\n\n"

                        "Please provide thoughtful, respectful, and technically accurate responses. Tailor your answers to help Santunu grow as a back-end developer, competitive programmer, and future ML engineer. "
                        "Use clean formatting, highlight key concepts clearly, and provide code blocks where needed. Focus on clarity, relevance, and long-term learning."
                    )
                },
                {"role": "user", "content": question}
            ],
            "temperature": 0.7,
            "max_tokens": 3000,
            "stream": False
        }


        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT)
            ) as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['choices'][0]['message']['content']
                    error_msg = f"API Error: {response.status}"
                    logger.error(error_msg)
                    return error_msg
        except asyncio.TimeoutError:
            error_msg = "Request timed out"
            logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(error_msg)
            return error_msg

    @staticmethod
    def chunk_response(text: str, max_len: int = None) -> List[str]:
        """
        Split long responses into Discord-friendly chunks
        Args:
            text: The text to chunk
            max_len: Maximum length per chunk
        Returns:
            List[str]: List of text chunks
        """
        max_len = max_len or Config.MAX_AI_RESPONSE_LENGTH
        return [text[i:i+max_len] for i in range(0, len(text), max_len)]

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True

class LeetCodeBot(commands.Bot):
    """Custom bot class with additional functionality"""
    
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            case_insensitive=True,
            help_command=None,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for !help"
            )
        )
        self.problems: Dict[str, List[LeetCodeProblem]] = {}
        self.start_time = datetime.now()
        self.stats = BotStats()
        self._last_cache_update = datetime.now()
        
    async def setup_hook(self) -> None:
        """Initialize bot resources"""
        self.problems = await ProblemCacheManager.load_problems()
        self.update_cache_loop.start()
        
    async def close(self) -> None:
        """Cleanup on bot shutdown"""
        self.update_cache_loop.cancel()
        await super().close()
        
    @tasks.loop(hours=6)
    async def update_cache_loop(self):
        """Periodically update problem cache"""
        try:
            scraped = await ProblemScraper.scrape_leetcode()
            if scraped:
                self.problems = scraped
                await ProblemCacheManager._update_cache(scraped)
                self._last_cache_update = datetime.now()
                logger.info("Problem cache updated successfully")
        except Exception as e:
            logger.error(f"Cache update failed: {e}")
            self.stats.errors_encountered += 1

bot = LeetCodeBot()

# --- Message Utilities ---
from datetime import datetime  # Add this at the top of your file

async def send_embed(
    ctx,
    *,
    title: str = None,
    description: str = None,
    color: int = 0x00FF00,
    fields: list = None,
    footer: str | dict = None,
    thumbnail: str = None,
    image: str = None,
    url: str = None,
    timestamp: datetime = None  # Fixed this line
):
    """Send a rich embed message to the channel."""
    try:
        embed = discord.Embed(color=color)
        
        if title is not None:
            embed.title = title
        if description is not None:
            embed.description = description
        if url is not None:
            embed.url = url
        if timestamp is not None:
            embed.timestamp = timestamp

        if fields:
            for field in fields:
                if isinstance(field, dict) and {"name", "value"}.issubset(field):
                    embed.add_field(
                        name=str(field["name"]),
                        value=str(field["value"]),
                        inline=field.get("inline", False)
                    )

        if footer:
            if isinstance(footer, dict):
                text = footer.get("text")
                icon_url = footer.get("icon_url")
                if text:
                    embed.set_footer(text=str(text), icon_url=icon_url)
            else:
                embed.set_footer(text=str(footer))

        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if image:
            embed.set_image(url=image)

        await ctx.send(embed=embed)
        return True

    except Exception as e:
        await ctx.send(f"❌ Failed to send embed: {str(e)}")
        return False
    
async def send_paginated(
    ctx,
    content: str,
    prefix: str = "",
    suffix: str = "",
    color: int = 0x000000,
    contains_code: bool = False,
    header: dict = None,
    footer: dict = None,
    decoration: dict = None,
    interactive: dict = None
):
    """Send premium formatted paginated messages with widescreen effect"""
    # Create full-width separator
    #full_width_separator = "⎯" * 50  # Uses extended ASCII characters
    
    # Format content with full-width elements
    formatted_content = (
        f"{prefix}\n\n"
        f"{content}\n\n"
        f"{suffix}\n"
    )
    
    # Create embed with full-width appearance
    embed = discord.Embed(
        description=formatted_content,
        color=decoration.get('side_color', color) if decoration else color,
    )
    
    # Add header with full-width styling
    if header:
        embed.set_author(
            name=f"▬ {header.get('title', '')} ▬",
            icon_url=header.get('icon')
        )
    
    # Add decoration elements
    if decoration:
        if 'thumbnail' in decoration:
            embed.set_thumbnail(url=decoration['thumbnail'])
        if 'image' in decoration:  # Full-width image
            embed.set_image(url=decoration['image'])
        embed.colour = decoration.get('highlight', embed.colour)
    
    # Add footer with full-width styling
    if footer:
        embed.set_footer(
            text=f"▬ {footer.get('text', '')} ▬",
            icon_url=footer.get('icon')
        )
    
    # Send message
    message = await ctx.send(embed=embed)
    
    # Add interactive elements
    if interactive and 'reactions' in interactive:
        for emoji in interactive['reactions']:
            await message.add_reaction(emoji)
            
# --- Bot Commands ---
@bot.command(name="help")
@commands.cooldown(1, Config.COMMAND_COOLDOWN, commands.BucketType.user)
async def help_command(ctx: commands.Context):
    """Show help message with all commands"""
    bot.stats.commands_processed += 1

    # Grouped table-like help content using code block formatting
    fields = [
        {
            "name": "📚 LeetCode Problems",
            "value": (
                "```"
                "Command               | Description\n"
                "----------------------|------------------------------\n"
                "!leetcode [easy]      | Random problem (or specific difficulty)\n"
                "!lc [medium]          | Alias for !leetcode\n"
                "!search <query>       | Search for problems by title/topic\n"
                "!daily                | Today's daily challenge"
                "```"
            ),
            "inline": False
        },
        {
            "name": "🤖 AI Assistance",
            "value": (
                "```"
                "Command               | Description\n"
                "----------------------|------------------------------\n"
                "!ask <question>       | Get coding help (code+explanation)\n"
                "!hint <problem>       | Get a solution hint\n"
                "!explain <concept>    | Detailed concept explanation\n"
                "```"
            ),
            "inline": False
        },
        {
            "name": "📊 Stats & Tracking",
            "value": (
                "```"
                "Command               | Description\n"
                "----------------------|------------------------------\n"
                "!stats                | Show your solving statistics\n"
                "!cache               | Show bot availability\n"

                
                "```"
            ),
            "inline": False
        },
        {
            "name": "⚙️ Utility",
            "value": (
                "```"
                "Command               | Description\n"
                "----------------------|------------------------------\n"
                "!ping                 | Check bot latency\n"
                "!invite               | Get bot invite link\n"
                "!help                 | Show this message"
                "```"
            ),
            "inline": False
        }
    ]

    await send_embed(
        ctx,
        title="🌟 Programming AI Assistant - Command Help 🌟",
        description=(
            "**The most advanced LeetCode assistant on Discord!**\n"
            "• More accurate than ChatGPT for coding problems\n"
            "• Detailed explanations with complexity analysis\n"
            "• Personalized learning tracking\n\n"
            f"Use `!help <command>` for detailed info about a specific command."
        ),
        color=0xF89F1B,
        fields=fields,
        footer={
            "text": f"Requested by {ctx.author.display_name} | {len(bot.commands)} commands available",
            "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
        }
    )
leetcode_lock = asyncio.Lock()

#Leet commands
@bot.command(name="leetcode", aliases=["lc", "leet", "problem"])
@commands.cooldown(2, Config.COMMAND_COOLDOWN, commands.BucketType.user)
async def leetcode_command(ctx: commands.Context, difficulty: str = "random"):
    """Fetch a LeetCode problem by difficulty (only one at a time globally)."""
    async with leetcode_lock:
        try:
            bot.stats.commands_processed += 1

            difficulty = difficulty.lower()
            valid_difficulties = ['easy', 'medium', 'hard', 'interview', 'random']
            if difficulty not in valid_difficulties:
                await send_embed(
                    ctx,
                    title="❌ Invalid Difficulty",
                    description=f"Please choose from: {', '.join(f'`{d}`' for d in valid_difficulties)}",
                    color=discord.Color.red()
                )
                return

            if difficulty == "random":
                difficulty = random.choice(['easy', 'medium', 'hard', 'interview'])

            problems = bot.problems.get(difficulty, [])
            if not problems:
                await send_embed(
                    ctx,
                    title="⚠️ No Problems Available",
                    description=f"No `{difficulty}` problems in the database. Try again later.",
                    color=discord.Color.orange()
                )
                return

            problem = random.choice(problems)
            bot.stats.problems_served += 1

            display_difficulty = "Interview" if difficulty == "interview" else problem.difficulty.capitalize()

            fields = [
                {"name": "Difficulty", "value": display_difficulty, "inline": True},
                {"name": "Acceptance Rate", "value": str(getattr(problem, 'acceptance_rate', 'N/A')), "inline": True},
                {"name": "Frequency", "value": str(getattr(problem, 'frequency', 'N/A')), "inline": True}
            ]

            await send_embed(
                ctx,
                title=f"📝 {problem.title}",
                description=(
                    f"**Here's your {'random ' if difficulty == 'random' else ''}"
                    f"{display_difficulty} problem to solve!**\n\n"
                    f"🔗 [View on LeetCode]({problem.url})\n"
                ),
                color=0xF89F1B,
                fields=fields,
                url=problem.url,
                footer={
                    "text": f"Requested by {ctx.author.display_name}",
                    "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
                },
                thumbnail="https://leetcode.com/static/images/LeetCode_logo_rvs.png",
                image=getattr(problem, 'preview_image', None)
            )
        except Exception as e:
            await send_embed(
                ctx,
                title="❌ Error",
                description="An error occurred while fetching the problem.",
                color=discord.Color.red()
            )
            print(f"Error in leetcode command: {e}")

@bot.command(name="search")
@commands.cooldown(2, Config.COMMAND_COOLDOWN, commands.BucketType.user)
async def search_command(ctx: commands.Context, *, query: str):
    """Search for LeetCode problems by title or topic.
    
    Example:
    !search binary tree
    !search dynamic programming
    """
    try:
        bot.stats.commands_processed += 1
        
        # Validate query length
        query = query.strip()
        if len(query) < 3:
            await send_embed(
                ctx,
                title="❌ Search Error",
                description="Search query must be at least 3 characters long",
                color=discord.Color.red()
            )
            return
        
        # Flatten all problems from all difficulty categories
        all_problems = []
        for difficulty in bot.problems.values():
            all_problems.extend(difficulty)
        
        # Normalize query and search
        query_lower = query.lower()
        matches = []
        
        for problem in all_problems:
            # Check title match
            title_match = query_lower in problem.title.lower()
            
            # Check topic matches
            topic_matches = any(
                query_lower in topic.lower() 
                for topic in getattr(problem, 'topics', [])
            )
            
            if title_match or topic_matches:
                matches.append(problem)
                if len(matches) >= 10:  # Limit to 10 results
                    break
        
        if not matches:
            await send_embed(
                ctx,
                title="🔍 No Results Found",
                description=f"No problems matched your search: '{query}'",
                color=discord.Color.orange(),
                footer={"text": "Try different keywords"}
            )
            return
            
        if len(matches) == 1:
            # Single result - show detailed view
            problem = matches[0]
            fields = [
                {"name": "Difficulty", "value": problem.difficulty.capitalize(), "inline": True},
                {"name": "Acceptance", "value": getattr(problem, 'acceptance_rate', 'N/A'), "inline": True},
                {"name": "Topics", "value": ", ".join(getattr(problem, 'topics', ['Various'])), "inline": False}
            ]
            
            if getattr(problem, 'is_premium', False):
                fields.append({"name": "Premium", "value": "🔒 Premium Problem", "inline": True})
            
            await send_embed(
                ctx,
                title=f"🔍 Found: {problem.title}",
                description=(
                    f"**Here's your search result:**\n\n"
                    f"🔗 [View on LeetCode]({problem.url})\n"
                    f"📝 {getattr(problem, 'short_description', 'No description available')}"
                ),
                color=0x5865F2,
                fields=fields,
                url=problem.url,
                thumbnail="https://leetcode.com/static/images/LeetCode_logo_rvs.png",
                footer={
                    "text": f"Requested by {ctx.author.display_name}",
                    "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
                }
            )
        else:
            # Multiple results - show list
            description = "\n".join(
                f"{idx+1}. **[{p.title}]({p.url})** "
                f"({p.difficulty.capitalize()})"
                f"{' 🔒' if getattr(p, 'is_premium', False) else ''}"
                for idx, p in enumerate(matches)
            )
            
            await send_embed(
                ctx,
                title=f"🔍 Search Results for '{query}'",
                description=description,
                color=0x5865F2,
                footer={
                    "text": f"Showing {len(matches)} matches • Use !search <number> to select",
                    "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
                }
            )
            
    except Exception as e:
        await send_embed(
            ctx,
            title="❌ Search Failed",
            description=f"An error occurred while searching: {str(e)}",
            color=discord.Color.red()
        )
        print(f"Search error: {e}")

@bot.command(name="ask")
@commands.cooldown(3, Config.COMMAND_COOLDOWN, commands.BucketType.user)
async def ask_command(ctx: commands.Context, *, question: str):
    """Ask the AI a coding question"""
    bot.stats.commands_processed += 1
    bot.stats.ai_queries += 1

    if len(question) > Config.MAX_QUESTION_LENGTH:
        await ctx.send(f"❌ Your question is too long. Please limit to {Config.MAX_QUESTION_LENGTH} characters.")
        return

    await ctx.typing()

    response = await AIHelper.query_ai(question)
    if not response:
        await ctx.send("⚠️ Sorry, I couldn't process your question at the moment. Please try again later.")
        return

    # Format AI answer with headers
    header_text = (
        f"🧠 **Programmer Agent's Answer** – _Tailored for {ctx.author.display_name}_\n\n"
        f"❓ **Question:** `{question}`\n\n"
    )
    footer_text = "\n━━━━━━━━━━━━━━━━━━━━\n💎 _Need more depth? Try `!explain` or `!optimize`_"

    contains_code = "```" in response

    if not contains_code:
        response = f"### ✨ Answer:\n\n{response.strip()}"

    await send_paginated(
        ctx,
        content=f"{header_text}{response}{footer_text}",
        color=0x8A2BE2,  # Elegant purple-blue for AI answers
        contains_code=contains_code,
        prefix="",
        suffix="",
        header={
            "title": "🔮 Programming AI Assistant",
            "icon": "https://i.imgur.com/J5hZ5zP.png"
        },
        footer={
            "text": f"👤 Asked by {ctx.author.display_name}",
            "icon": str(ctx.author.avatar.url) if ctx.author.avatar else None
        },
        interactive={
            "reactions": ["💡", "🔧", "❓"] if not contains_code else ["📊", "🧪", "🛠️"],
            "reference_link": "https://leetcode.com"
        }
    )

@bot.command(name="explain")
@commands.cooldown(3, Config.COMMAND_COOLDOWN, commands.BucketType.user)
async def explain_command(ctx: commands.Context, *, concept: str):
    """Get a detailed explanation of a programming concept
    
    Example:
    !explain binary search
    !explain dynamic programming
    """
    try:
        bot.stats.commands_processed += 1
        bot.stats.ai_queries += 1
        
        concept = concept.strip()
        if len(concept) > Config.MAX_QUESTION_LENGTH:
            await send_embed(
                ctx,
                title="❌ Concept Too Long",
                description=f"Maximum length is {Config.MAX_QUESTION_LENGTH} characters",
                color=discord.Color.red()
            )
            return
            
        prompt = (
            f"Explain the programming concept '{concept}' in detail with:\n"
            "1. Clear definition\n2. Common use cases\n3. Example code snippets\n"
            "4. Time/space complexity\n5. Related concepts\n\n"
            "Format with Markdown headings and code blocks."
        )
        
        async with ctx.typing():
            response = await AIHelper.query_ai(prompt)
            
        if not response:
            await send_embed(
                ctx,
                title="⚠️ Service Unavailable",
                description="AI service is currently unavailable",
                color=discord.Color.orange()
            )
            return
            
        await send_paginated(
            ctx, 
            response,
        )

    except Exception as e:
        await send_embed(
            ctx,
            title="❌ Explanation Failed",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        print(f"Explain error: {e}")


@bot.command(name="hint")
@commands.cooldown(2, Config.COMMAND_COOLDOWN, commands.BucketType.user)
async def hint_command(ctx: commands.Context, *, problem_name: str):
    """Get a hint for a specific LeetCode problem
    
    Example:
    !hint two sum
    !hint reverse linked list
    """
    try:
        bot.stats.commands_processed += 1
        bot.stats.ai_queries += 1
        
        problem_name = problem_name.strip()
        if len(problem_name) < 3:
            await send_embed(
                ctx,
                title="❌ Invalid Problem Name",
                description="Please provide at least 3 characters",
                color=discord.Color.red()
            )
            return

        # Search across all difficulties
        all_problems = []
        for difficulty in bot.problems.values():
            all_problems.extend(difficulty)
        
        # Find closest matching problem
        matches = [
            p for p in all_problems
            if problem_name.lower() in p.title.lower()
        ]
        
        if not matches:
            await send_embed(
                ctx,
                title="🔍 Problem Not Found",
                description=f"No problems matching '{problem_name}'\nTry !search first",
                color=discord.Color.orange()
            )
            return
            
        problem = matches[0]
        
        if getattr(problem, 'solution_hint', None):
            hint = problem.solution_hint
        else:
            async with ctx.typing():
                hint = await AIHelper.query_ai(
                    f"Provide a helpful but not complete hint for LeetCode problem '{problem.title}'. "
                    "Focus on the key insight needed to solve it."
                )
                if not hint:
                    hint = "No hint available for this problem."
                
        await send_embed(
            ctx,
            title=f"💡 Hint for {problem.title}",
            description=hint,
            color=0xF89F1B,
            url=problem.url,
            footer={
                "text": f"Requested by {ctx.author.display_name}",
                "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
            },
        )

    except Exception as e:
        await send_embed(
            ctx,
            title="❌ Hint Failed",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        print(f"Hint error: {e}")


@bot.command(name="stats")
async def stats_command(ctx: commands.Context):
    """Show bot usage statistics"""
    try:
        bot.stats.commands_processed += 1
        
        # Calculate uptime
        delta = datetime.now() - bot.start_time
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
        
        # Problem counts
        problem_counts = {k: len(v) for k, v in bot.problems.items()}
        
        fields = [
            {"name": "🕒 Uptime", "value": uptime_str, "inline": True},
            {"name": "📊 Commands", "value": f"{bot.stats.commands_processed:,}", "inline": True},
            {"name": "💡 Problems Served", "value": f"{bot.stats.problems_served:,}", "inline": True},
            {"name": "🤖 AI Queries", "value": f"{bot.stats.ai_queries:,}", "inline": True},
            {"name": "📚 Problem Database", 
             "value": (
                 f"• Easy: {problem_counts.get('easy', 0):,}\n"
                 f"• Medium: {problem_counts.get('medium', 0):,}\n"
                 f"• Hard: {problem_counts.get('hard', 0):,}\n"
                 f"• Interview: {problem_counts.get('interview', 0):,}"
             ), "inline": True},
            {"name": "🔄 Last Update", 
             "value": bot._last_cache_update.strftime("%Y-%m-%d %H:%M"), 
             "inline": True}
        ]
        
        await send_embed(
            ctx,
            title="📈 Bot Statistics",
            description="Current usage metrics and performance:",
            fields=fields,
            color=0x5865F2,
            thumbnail="https://cdn-icons-png.flaticon.com/512/3132/3132693.png",
            footer={
                "text": f"Requested by {ctx.author.display_name}",
                "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
            }
        )

    except Exception as e:
        await send_embed(
            ctx,
            title="❌ Stats Failed",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        print(f"Stats error: {e}")


@bot.command(name="cache")
async def cache_command(ctx: commands.Context):
    """Show problem cache status"""
    try:
        cache_exists = os.path.exists(Config.CACHE_FILE)
        cache_size = os.path.getsize(Config.CACHE_FILE) if cache_exists else 0
        cache_time = datetime.fromtimestamp(os.path.getmtime(Config.CACHE_FILE)) if cache_exists else None
        
        fields = [
            {"name": "Status", "value": "✅ Active" if cache_exists else "❌ Inactive", "inline": True},
            {"name": "Size", "value": f"{cache_size/1024:.1f} KB", "inline": True},
            {"name": "Last Modified", 
             "value": cache_time.strftime("%Y-%m-%d %H:%M") if cache_time else "N/A", 
             "inline": True},
            {"name": "Location", "value": f"`{Config.CACHE_FILE}`", "inline": False},
            {"name": "TTL", "value": f"{Config.PROBLEM_CACHE_TTL/3600:.1f} hours", "inline": True}
        ]
        
        await send_embed(
            ctx,
            title="🗃️ Cache Information",
            description="Problem cache status and details:",
            fields=fields,
            color=0x5865F2,
            footer={
                "text": f"Requested by {ctx.author.display_name}",
                "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
            }
        )

    except Exception as e:
        await send_embed(
            ctx,
            title="❌ Cache Info Failed",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        print(f"Cache error: {e}")


 # At the top of your file with other imports

@bot.command(name="ping")
async def ping_command(ctx: commands.Context):
    """Check bot latency and connection status"""
    try:
        latency = round(bot.latency * 1000, 2)
        status = "🟢 Excellent" if latency < 100 else "🟡 Good" if latency < 300 else "🔴 Slow"
        
        await send_embed(
            ctx,
            title="🏓 Pong!",
            description=f"**Latency:** {latency}ms\n**Status:** {status}",
            color=discord.Color.green(),
            footer={
                "text": f"Requested by {ctx.author.display_name}",
                "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
            }
        )

    except Exception as e:
        await send_embed(
            ctx,
            title="❌ Ping Failed",
            description="Could not measure bot latency",
            color=discord.Color.red()
        )
        print(f"Ping error: {e}")



@bot.command(name="invite")
async def invite_command(ctx: commands.Context):
    """Get the bot's invite link"""
    try:
        # Generate invite link with recommended permissions
        permissions = discord.Permissions(
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            add_reactions=True
        )
        invite_url = discord.utils.oauth_url(bot.user.id, permissions=permissions)
        
        await send_embed(
            ctx,
            title="🔗 Invite Me To Your Server!",
            description=f"[Click here to add me to your server]({invite_url})\n\n"
                       "**Required Permissions:**\n"
                       "• Read Messages\n• Send Messages\n• Embed Links\n"
                       "• Attach Files\n• Read Message History\n• Add Reactions",
            color=0x5865F2,  # Discord blurple
            thumbnail=bot.user.avatar.url if bot.user.avatar else None,
            footer={
                "text": "Thank you for using me!",
                "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
            }
        )
        
    except Exception as e:
        await send_embed(
            ctx,
            title="❌ Invite Failed",
            description="Couldn't generate invite link",
            color=discord.Color.red()
        )
        print(f"Invite error: {e}")



@bot.command(name="about")
async def about_command(ctx: commands.Context):
    """Show information about this bot"""
    try:
        fields = [
            {"name": "Version", "value": "1.0.5", "inline": True},
            {"name": "Creator", "value": "Santunu Kaysar", "inline": True},
            {"name": "GitHub", "value": "[SantunuMahin](https://github.com/santunumahin)", "inline": True},
            {"name": "SCS Website", "value": "[ServerCodeSocity](https://servercodesocity.vercel.app)", "inline": True},
            {
                "name": "Description",
                "value": (
                    "**LeetCode AI Assistant** helps you master coding interviews with:\n"
                    "• 💡 AI-Powered Explanations\n"
                    "• 📚 Vast Problem Database\n"
                    "• 🧠 Coding Hints & Concepts\n"
                    "• 🧪 Daily Challenges\n"
                    "• 📈 Performance Analytics\n"
                    "• 🔍 Interview Prep Toolkit"
                ),
                "inline": False
            }
        ]
        
        await send_embed(
            ctx,
            title="🤖 Programming AI Assistant",
            description="Your intelligent companion for coding interviews and algorithm mastery.",
            fields=fields,
            color=0xF89F1B,
            footer={
                "text": f"Requested by {ctx.author.display_name}",
                "icon_url": str(ctx.author.avatar.url) if ctx.author.avatar else None
            }
        )

    except Exception as e:
        await send_embed(
            ctx,
            title="❌ About Failed",
            description=f"An unexpected error occurred.\n```{str(e)}```",
            color=discord.Color.red()
        )
        print(f"About error: {e}")

# --- Error Handling ---

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Handle command errors gracefully"""
    bot.stats.errors_encountered += 1
    
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Command not found. Try `!help` for available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: {error.param.name}. Usage: `!{ctx.command.name} {ctx.command.signature}`")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Command on cooldown. Try again in {error.retry_after:.1f} seconds.")
    else:
        logger.error(f"Command error: {str(error)}")
        await ctx.send(f"⚠️ An error occurred: {str(error)}")

# --- Main Execution ---
async def main():
    async with bot:
        try:
            logger.info("Starting LeetCode Bot...")
            await bot.start(Config.TOKEN)
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            logger.info("Bot shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())