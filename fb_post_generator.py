import os
import re
import subprocess
import requests
import trafilatura

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv
from anthropic import Anthropic
from PIL import Image
from io import BytesIO

load_dotenv()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def clean_filename(text):
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    return text[:80] or "article"


def _extract_images_jwmagazine(soup, url):
    article = soup.find("main") or soup
    image_urls = []
    in_heading_section = False

    for tag in article.find_all(["h2", "h3", "figure", "img"]):
        if tag.name in ["h2", "h3"]:
            in_heading_section = True
        elif in_heading_section:
            src = None
            if tag.name == "figure":
                img = tag.find("img")
                if img:
                    src = img.get("data-lazy-src") or img.get("src", "")
            elif tag.name == "img":
                src = tag.get("data-lazy-src") or tag.get("src", "")

            if src and "uploads" in src and not src.startswith("data:"):
                img_url = urljoin(url, src)
                if img_url not in image_urls:
                    image_urls.append(img_url)
                    in_heading_section = False

        if len(image_urls) >= 6:
            break

    return image_urls


def _extract_images_fashionpress(soup, url):
    import json as _json

    image_urls = []
    seen = set()

    # Primary: parse JSON-LD NewsArticle schema (most reliable)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            if isinstance(data, list):
                data = next((d for d in data if d.get("@type") == "NewsArticle"), None)
            if not data or data.get("@type") != "NewsArticle":
                continue
            images = data.get("image", [])
            if isinstance(images, str):
                images = [images]
            for img_url in images:
                img_url = urljoin(url, img_url)
                if img_url not in seen:
                    seen.add(img_url)
                    image_urls.append(img_url)
                if len(image_urls) >= 6:
                    return image_urls
            if image_urls:
                return image_urls
        except Exception:
            continue

    # Fallback: body images inside div#news figures
    for img in soup.select("div#news figure img"):
        src = img.get("src", "")
        if not src or src.startswith("data:"):
            continue
        img_url = urljoin(url, src)
        if img_url not in seen:
            seen.add(img_url)
            image_urls.append(img_url)
        if len(image_urls) >= 6:
            return image_urls

    return image_urls


def _extract_images_prtimes(soup, url):
    import json as _json

    image_urls = []
    seen = set()

    # Primary: parse __NEXT_DATA__ JSON (most reliable — contains all release images in order)
    next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_data_tag:
        try:
            data = _json.loads(next_data_tag.string)
            release = data["props"]["pageProps"].get("pressRelease", {})
            company_id = release.get("companyId")
            release_id = release.get("releaseId")
            images = release.get("images", [])
            for img_info in images:
                s3_name = img_info.get("fileNameS3")
                if s3_name and company_id and release_id:
                    img_url = (
                        f"https://prcdn.freetls.fastly.net/release_image/"
                        f"{company_id}/{release_id}/{s3_name}"
                        "?format=jpeg&auto=webp&fit=bounds&width=1950&height=1350"
                    )
                    if img_url not in seen:
                        seen.add(img_url)
                        image_urls.append(img_url)
                if len(image_urls) >= 6:
                    return image_urls
            if image_urls:
                return image_urls
        except Exception:
            pass

    # Fallback: CSS selector targeting PR Times body image structure
    for img in soup.select("div.pr-img img, figure.pr-img__item--large img"):
        src = img.get("src", "")
        if not src or src.startswith("data:"):
            continue
        img_url = urljoin(url, src)
        if img_url not in seen:
            seen.add(img_url)
            image_urls.append(img_url)
        if len(image_urls) >= 6:
            return image_urls

    return image_urls


def fetch_article(url):
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25).text
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1")
    title = title.get_text(strip=True) if title else "Untitled"

    downloaded = trafilatura.fetch_url(url)
    body = trafilatura.extract(downloaded) if downloaded else ""
    if not body:
        paragraphs = soup.find_all("p")
        body = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40)
    body = body[:8000]

    if "prtimes.jp" in url:
        image_urls = _extract_images_prtimes(soup, url)
    elif "fashion-press.net" in url:
        image_urls = _extract_images_fashionpress(soup, url)
    else:
        image_urls = _extract_images_jwmagazine(soup, url)

    return title, body, image_urls


def download_images(image_urls, folder):
    os.makedirs(folder, exist_ok=True)
    saved = []

    for i, img_url in enumerate(image_urls, start=1):
        try:
            r = requests.get(img_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            img = Image.open(BytesIO(r.content))
            w, h = img.size
            if w < 300 or h < 200:
                continue

            ext = img_url.split("?")[0].split(".")[-1].lower()
            if ext not in ["jpg", "jpeg", "png", "webp"]:
                ext = "jpg"
            path = os.path.join(folder, f"image_{i}.{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            size_kb = os.path.getsize(path) // 1024
            print(f"  ✅ image_{i}.{ext} ({size_kb} KB)")
            saved.append(path)
        except Exception:
            print(f"  ❌ Failed: {img_url[-50:]}")

    return saved


FB_EVENT_BLOCK_INSTRUCTION = """
---
SUPPLEMENTARY EVENT INFO BLOCK:

After writing the caption body, check whether the article is about an event, exhibition, festival, pop-up, or limited-time activity.

If yes, insert this block after the caption ending line ("Tap the comments to read the full guide 👇") and BEFORE the hashtags:

Event Info:
Time: [dates/hours from article, or "Not provided"]
Location: [venue/address from article, or "Not provided"]
Access: [transportation info]

For the Access field:
- If the article explicitly states transportation, use that.
- If not, use your knowledge of the location to infer the most common public transport route (e.g. "Take JR Yamanote Line from Shinjuku to Harajuku Station, 3-min walk."). Only do this when you are confident the location is well-known and the route is accurate.
- If you cannot confirm a reliable route, write: "Please refer to the official website for directions."

If the content is clearly not event-related, omit this block entirely.
Do NOT rewrite or shorten the caption to fit the block — it is purely supplementary.
"""


def _prompt_facebook(title, body, source_url):
    if "prtimes.jp" in source_url:
        return f"""
You are an experienced social media manager for Japan Web Magazine.

Create a Facebook caption based on the PR Times press release below.

Requirements:
- Around 100 words, strictly under 110 words (excluding hashtags and the event info block)
- Natural American English
- Clear, concise, friendly tone — not corporate
- Strong first line hook + 1 emoji
- Avoid starting with: Discover, Explore, Looking for
- Highlight the key announcement (product launch, event, collaboration, etc.)
- Include dates, locations, or prices if mentioned in the article
- Do not invent facts
- End with exactly: Tap the comments to read the full guide 👇
- Add 5-7 lowercase hashtags, always include #japan
{FB_EVENT_BLOCK_INSTRUCTION}
Article title: {title}
Article text: {body}
"""
    else:
        return f"""
You are an experienced social media manager for Japan Web Magazine.

Create a Facebook caption based on the article below.

Requirements:
- Around 100 words, strictly under 110 words (excluding hashtags and the event info block)
- Natural American English
- Clear, concise, friendly
- Strong first line hook + 1 emoji
- Avoid starting with: Discover, Explore, Looking for
- Mention location, access, or key highlights if available
- Do not invent facts
- End with exactly: Tap the comments to read the full guide 👇
- Add 5-7 lowercase hashtags, always include #japantrip
{FB_EVENT_BLOCK_INSTRUCTION}
Reference style:
Cherry blossom season, but make it cozy 🌸
"Hanami at bills" brings indoor sakura vibes & limited spring desserts you don't want to miss. Perfect for rainy days or chill brunch plans.
Event Duration: March 24 (Tue) – April 21 (Tue), 2026
Locations: All 8 bills restaurants in Japan
Tap the comments to read the full guide 👇
#tokyotravel #japantrip #sakura2026 #hanami #japanesefood

Article title: {title}
Article text: {body}
"""


def _prompt_xiaohongshu(title, body):
    return f"""
你是一位长期住在日本的旅居博主，正在小红书上分享旅游资讯。

请根据以下文章，生成一篇适合小红书风格的旅游资讯帖文。

写作风格要求：
- 自然口语，像朋友分享，不像广告或新闻稿
- 不要出现"震惊""一定要冲""超绝""必打卡"等夸张词
- 不要编造原文没有的信息
- 适量使用 emoji，不要每句都加
- 输出简体中文
- 不要使用任何 Markdown 格式，包括 **加粗**、*斜体*、# 标题符号等，输出纯文字
- 如需小标题，使用【】格式，例如：【亮点】【交通】
- 内文（不含信息整理区块与hashtag）不超过800字，语言精炼干练自然，去掉一切废话

帖文结构：

第零部分 — 标题行
- 格式固定为：【标题】[标题内容]
- 标题内容包含标点符号在内不超过20字
- 带1个 emoji，放在标题末尾

第一部分 — 正文
- 第一行直接开始正文，不重复标题
- 简单介绍背景或来由，语气自然
- 如有多个亮点或品项，用 ①②③ 分点列出，每点以「品名或小标题」开头
- 可穿插【小标题】区分不同段落（如【店内空间】【限定款】等）
- 适合哪类人去、推荐理由，自然融入正文

第二部分 — 个人感受结尾
- 1-2句自然收尾，可以是期待感、推荐语或个人想法
- 不要太刻意，像朋友随口说的

第三部分 — 信息整理（放在个人感受之后，hashtag之前）
如文章含有以下信息则列出，没有的项目请写"未提及"，不要乱写：

📌 活动名称：
🗓 时间：
📍 地点：
🚃 交通：
（交通说明规则：若文章未提及，可根据地点名称推断常见交通方式，仅在地点明确且你对路线有把握时使用。若不确定，写"请参考官方网站"）

第四部分 — Hashtag
- 紧接在信息整理区块之后
- 5个相关 hashtag，格式：#标签

Article title: {title}
Article text: {body}
"""


def _prompt_pr_facebook(title, body):
    return f"""
You are an experienced social media manager for Japan Web Magazine.

Create a Facebook post based on the press release below. Write in natural, conversational American English — like a friend sharing something cool, not a press release rewrite.

Post structure:

1. Hook line
   One punchy, engaging sentence + 1 emoji (under 15 words). Avoid starting with: Discover, Explore, Looking for.

2. Body
   Briefly introduce what this is and why it matters. Keep it natural and concise.

3. Highlights
   Use ①②③ to list key products, features, or selling points. Lead each with the item name or a short label.

4. Personal closing
   1-2 sentences, casual and genuine — like a recommendation from a friend, not a call to action.

5. Event info block (only if the article contains event/date/location details):
   📌 Event:
   🗓 Date:
   📍 Location:
   🚃 Access: [use article info; if not stated, infer from well-known location only when confident; otherwise write "Please check the official website for directions."]
   If clearly not event-related, omit this block.

6. Hashtags
   5-7 lowercase hashtags, always include #japan. Place immediately after the info block (or after the closing if no info block).

Rules:
- Do not fabricate facts not in the article
- Body + highlights + closing: under 150 words total (excluding info block and hashtags)
- No markdown formatting

Article title: {title}
Article text: {body}
"""


def generate_caption(title, body, source_url="", platform="facebook_jw"):
    if platform == "xiaohongshu":
        prompt = _prompt_xiaohongshu(title, body)
        max_tokens = 1000
    elif platform == "facebook_pr":
        prompt = _prompt_pr_facebook(title, body)
        max_tokens = 900
    else:
        prompt = _prompt_facebook(title, body, source_url)
        max_tokens = 800

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text.strip()


def process_url(url, platform="facebook_jw"):

    print("\n🔍 Fetching article...")
    title, body, image_urls = fetch_article(url)
    print(f"   Title: {title}")
    print(f"   Images found: {len(image_urls)}")

    folder_name = clean_filename(title)
    platform_folder = "xiaohongshu" if platform == "xiaohongshu" else "facebook"
    output_folder = os.path.join("Social Content", platform_folder, folder_name)

    print("\n📸 Downloading images...")
    image_files = download_images(image_urls, output_folder)

    print("\n✍️  Generating caption...")
    caption = generate_caption(title, body, source_url=url, platform=platform)

    caption_with_url = caption + f"\n\n{url}"

    caption_path = os.path.join(output_folder, "caption.txt")
    with open(caption_path, "w", encoding="utf-8") as f:
        f.write(caption_with_url)

    import platform as _platform
    if _platform.system() == "Darwin":
        subprocess.run("pbcopy", input=caption_with_url.encode(), check=True)
        print("📋 Caption copied to clipboard! Open Metricool and press Cmd+V.")

    print(f"\n✅ Done! Folder: {output_folder}")
    print(f"   {len(image_files)} images + caption.txt")
    return caption_with_url, image_files


