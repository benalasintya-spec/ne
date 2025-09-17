#!/usr/bin/env python3

import json
import logging
import time
import random
import sys
import os
from datetime import datetime
from urllib.parse import urljoin
from typing import List, Dict, Optional
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from jinja2 import Environment, FileSystemLoader
import google.generativeai as genai

# ===================================================================
# BAGIAN 1: KELAS SCRAPER (DIRANCANG ULANG UNTUK GOOGLE NEWS TOPICS)
# ===================================================================

class GoogleNewsScraper:
    def __init__(self, verbose=False):
        self.ua = UserAgent()
        self.base_url = "https://news.google.com"
        logging_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=logging_level, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
    def make_request(self, url: str, max_retries: int = 3) -> Optional[requests.Response]:
        headers = {'User-Agent': self.ua.random}
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
                if response.status_code == 200:
                    return response
                self.logger.error(f"HTTP Error {response.status_code} for {url}")
            except requests.RequestException as e:
                self.logger.error(f"Request failed: {e}")
            time.sleep(random.uniform(1, 3))
        self.logger.error(f"Failed to fetch {url} after {max_retries} attempts")
        return None
    
    def scrape_category(self, category_name: str, topic_id: str, max_articles: int) -> List[Dict]:
        articles = []
        seen_urls = set()
        topic_url = f"{self.base_url}/topics/{topic_id}?hl=id&gl=ID"
        self.logger.info(f"Starting scrape for category: '{category_name}' from {topic_url}")
        
        response = self.make_request(topic_url)
        if not response:
            return []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Cari semua elemen 'article' yang merupakan kontainer berita
        for article_tag in soup.find_all('article', limit=max_articles * 2): # Ambil lebih banyak untuk jaga-jaga
            if len(articles) >= max_articles:
                break
            try:
                link_tag = article_tag.find('a', href=True)
                if not link_tag: continue

                # URL di Google News bersifat relatif, jadi kita gabungkan
                relative_url = link_tag['href']
                absolute_url = urljoin(self.base_url, relative_url)

                if absolute_url in seen_urls: continue
                
                title_tag = article_tag.find('h4') or article_tag.find('h3')
                title = title_tag.text if title_tag else "Judul tidak ditemukan"
                
                publisher_tag = article_tag.find('div', class_='gPFEn')
                publisher = publisher_tag.text if publisher_tag else "Sumber tidak diketahui"

                article_data = {
                    'category': category_name,
                    'url': absolute_url,
                    'title': title,
                    'publisher': publisher,
                    'scraped_at': datetime.now().isoformat()
                }
                articles.append(article_data)
                seen_urls.add(absolute_url)
                self.logger.debug(f"Scraped: {title[:60]}...")

            except Exception as e:
                self.logger.warning(f"Could not parse an article element: {e}")
                continue
        
        self.logger.info(f"Finished scrape for '{category_name}'. Found {len(articles)} articles.")
        return articles[:max_articles]

# ===================================================================
# BAGIAN 2: FUNGSI-FUNGSI HELPER (PROMPT GEMINI DISESUAIKAN)
# ===================================================================

def rewrite_with_gemini(article: Dict, api_key: str) -> Optional[Dict]:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-pro')
    
    # Prompt baru: Minta Gemini untuk mengembangkan berita berdasarkan judul
    prompt = f"""
    Anda adalah seorang jurnalis AI yang handal. Berdasarkan judul berita berikut, tuliskan sebuah ringkasan berita singkat (1-2 paragraf) yang berkualitas, unik, dan informatif. 
    Bayangkan poin-poin utama yang mungkin dibahas dalam artikel tersebut dan rangkai menjadi narasi yang koheren dan netral.
    
    Judul Berita: "{article['title']}"

    Ringkasan Berita:
    """
    
    try:
        logging.info(f"Rewriting article: {article['title'][:50]}...")
        response = model.generate_content(prompt)
        clean_text = response.text.replace('*', '').replace('#', '')
        article['rewritten_content'] = clean_text
        return article
    except Exception as e:
        logging.error(f"Failed to call Gemini API: {e}")
        article['rewritten_content'] = "Konten tidak dapat dibuat saat ini."
        return article

def generate_static_site(articles_by_category: List[Dict], output_dir: str):
    try:
        logging.info("Generating static site file...")
        env = Environment(loader=FileSystemLoader('.'))
        template = env.get_template('template.html')
        
        html_content = template.render(
            articles_by_category=articles_by_category,
            generated_at=datetime.now().strftime('%d %B %Y, %H:%M:%S UTC')
        )
        
        output_path = Path(output_dir) / "index.html"
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        logging.info(f"Site successfully generated at: {output_path}")
    except Exception as e:
        logging.error(f"Failed to generate static site: {e}")

def load_config(config_file: str = 'config.json') -> Dict:
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"Configuration file '{config_file}' not found.")
        sys.exit(1)
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from '{config_file}'.")
        sys.exit(1)

# ===================================================================
# BAGIAN 3: FUNGSI UTAMA (ALUR KERJA DISESUAIKAN)
# ===================================================================

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    config = load_config()
    categories = config.get('categories', [])
    posts_per_category = config.get('posts_per_category', 5)
    gemini_delay = config.get('gemini_api_delay_seconds', 2)
    output_dir = '.'

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY environment variable is not set! Exiting.")
        sys.exit(1)

    scraper = GoogleNewsScraper(verbose=True)
    
    articles_for_template = []
    total_articles_scraped = 0

    for category in categories:
        category_name = category.get('name')
        topic_id = category.get('topic_id')
        
        if not category_name or not topic_id:
            logging.warning(f"Skipping invalid category entry: {category}")
            continue
            
        # 1. Scrape artikel per kategori
        scraped_articles = scraper.scrape_category(category_name, topic_id, posts_per_category)
        if not scraped_articles:
            continue
        
        total_articles_scraped += len(scraped_articles)
        
        # 2. Rewrite setiap artikel
        rewritten_articles_for_category = []
        for article in scraped_articles:
            rewritten_article = rewrite_with_gemini(article, GEMINI_API_KEY)
            if rewritten_article:
                rewritten_articles_for_category.append(rewritten_article)
            
            logging.info(f"Waiting for {gemini_delay} seconds...")
            time.sleep(gemini_delay)
            
        articles_for_template.append({
            "name": category_name,
            "articles": rewritten_articles_for_category
        })

    if total_articles_scraped == 0:
        logging.warning("Scraping resulted in 0 articles across all categories. Generating an empty site.")
    
    # 3. Simpan data (opsional) dan generate situs
    with open(Path(output_dir) / 'data.json', 'w', encoding='utf-8') as f:
        json.dump(articles_for_template, f, indent=2, ensure_ascii=False)
        
    generate_static_site(articles_for_template, output_dir)

    logging.info("Process finished successfully.")

if __name__ == "__main__":
    main()
