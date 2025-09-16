#!/usr/bin/env python3

import json
import logging
import time
import random
import re
import sys
import os
from datetime import datetime
from urllib.parse import quote_plus
from typing import List, Dict, Optional
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from jinja2 import Environment, FileSystemLoader
import google.generativeai as genai

# ===================================================================
# BAGIAN 1: KELAS SCRAPER
# ===================================================================

class GoogleNewsScraper:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.ua = UserAgent()
        
        logging_level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(
            level=logging_level,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
    def rotate_user_agent(self) -> str:
        return self.ua.random
    
    def make_request(self, url: str, max_retries: int = 3) -> Optional[requests.Response]:
        headers = {
            'User-Agent': self.rotate_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Request attempt {attempt + 1} for {url}")
                response = requests.get(url, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:
                    self.logger.warning("Rate limited. Waiting before retry...")
                    time.sleep(2 ** attempt)
                else:
                    self.logger.error(f"HTTP Error {response.status_code} for {url}")
                    
            except requests.RequestException as e:
                self.logger.error(f"Request failed: {e}")
            
            time.sleep(random.uniform(1, 3))
        
        self.logger.error(f"Failed to fetch {url} after {max_retries} attempts")
        return None
    
    def parse_article_date(self, date_str: str) -> Optional[str]:
        if not date_str:
            return None
        return date_str # Keep it simple, as parsing can be complex
    
    def scrape_articles(self, keyword: str, max_articles: int = 10) -> List[Dict]:
        articles = []
        seen_urls = set()
        page = 0
        
        self.logger.info(f"Starting scrape for keyword: {keyword}")
        
        while len(articles) < max_articles:
            start = page * 10
            search_url = f"https://www.google.com/search?q={quote_plus(keyword)}&tbm=nws&start={start}"
            
            self.logger.debug(f"Fetching page {page + 1} for '{keyword}'")
            response = self.make_request(search_url)
            
            if not response:
                self.logger.error(f"Failed to fetch results page {page + 1}")
                break
            
            soup = BeautifulSoup(response.text, 'html.parser')
            result_containers = soup.find_all('div', class_='SoaBEf')
            
            if not result_containers:
                self.logger.info("No more results found.")
                break
            
            for container in result_containers:
                if len(articles) >= max_articles:
                    break
                
                try:
                    link_element = container.find('a')
                    if not link_element: continue
                    
                    url = link_element.get('href')
                    if not url or url in seen_urls: continue
                    
                    title_div = link_element.find('div', role='heading')
                    title = title_div.get_text() if title_div else ''
                    
                    snippet_div = container.find('div', class_='n0jPhd')
                    snippet = snippet_div.get_text() if snippet_div else ''
                    
                    date_span = container.find('span', class_='OSrXXb')
                    date_published = self.parse_article_date(date_span.get_text() if date_span else '')
                    
                    article_data = {
                        'keyword': keyword,
                        'url': url,
                        'title': title,
                        'date_published': date_published,
                        'snippet': snippet,
                        'scraped_at': datetime.now().isoformat()
                    }
                    
                    articles.append(article_data)
                    seen_urls.add(url)
                    self.logger.debug(f"Scraped article: {title[:50]}...")
                    
                except Exception as e:
                    self.logger.error(f"Error parsing article: {e}")
                    continue
            
            page += 1
            time.sleep(random.uniform(1, 3))
        
        self.logger.info(f"Scraped {len(articles)} articles for '{keyword}'")
        return articles
    
    def save_to_json(self, articles: List[Dict], filename: str):
        if not articles:
            self.logger.warning("No articles to save to JSON")
            return
        
        try:
            with open(filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(articles, jsonfile, indent=2, ensure_ascii=False)
            self.logger.info(f"Saved {len(articles)} articles to {filename}")
        except Exception as e:
            self.logger.error(f"Error saving to JSON: {e}")

# ===================================================================
# BAGIAN 2: FUNGSI-FUNGSI HELPER
# ===================================================================

def rewrite_with_gemini(article: Dict, api_key: str) -> Optional[Dict]:
    """Minta Gemini untuk menulis ulang artikel berdasarkan judul dan snippet."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-pro')
    
    prompt = f"""
    Anda adalah seorang jurnalis. Tulis ulang konten berita berikut menjadi sebuah ringkasan yang menarik dan informatif dalam 2-3 paragraf.
    Gaya bahasa harus netral, jelas, dan mudah dipahami. Fokus pada informasi utama yang terkandung dalam judul dan cuplikan.
    
    Judul Asli: "{article['title']}"
    Cuplikan Asli: "{article['snippet']}"

    Ringkasan Berita:
    """
    
    try:
        logging.info(f"Rewriting article: {article['title'][:50]}...")
        response = model.generate_content(prompt)
        article['rewritten_content'] = response.text
        return article
    except Exception as e:
        logging.error(f"Failed to call Gemini API: {e}")
        return None

def generate_static_site(articles: List[Dict], output_dir: str):
    """Membuat file index.html dari daftar artikel menggunakan template Jinja2."""
    if not articles:
        logging.warning("No articles to generate site.")
        return

    try:
        logging.info("Generating static site file...")
        env = Environment(loader=FileSystemLoader('.'))
        template = env.get_template('template.html')
        
        html_content = template.render(
            articles=articles,
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        )
        
        output_path = Path(output_dir) / "index.html"
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        logging.info(f"Site successfully generated at: {output_path}")
    except Exception as e:
        logging.error(f"Failed to generate static site: {e}")

def load_config(config_file: str = 'config.json') -> Dict:
    """Memuat konfigurasi dari file JSON."""
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
# BAGIAN 3: FUNGSI UTAMA
# ===================================================================

def main():
    """Fungsi utama yang menjalankan seluruh proses secara otomatis."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    config = load_config()
    keywords = config.get('keywords', [])
    max_articles_per_keyword = config.get('max_articles_per_keyword', 5)
    output_dir = '.' # Output di direktori root proyek

    if not keywords:
        logging.error("No keywords found in config.json. Exiting.")
        sys.exit(1)

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY environment variable is not set! Exiting.")
        sys.exit(1)

    scraper = GoogleNewsScraper(verbose=True)

    all_articles_raw = []
    for keyword in keywords:
        articles = scraper.scrape_articles(keyword, max_articles=max_articles_per_keyword)
        all_articles_raw.extend(articles)

    rewritten_articles = []
    for article in all_articles_raw:
        rewritten_article = rewrite_with_gemini(article, GEMINI_API_KEY)
        if rewritten_article:
            rewritten_articles.append(rewritten_article)
        time.sleep(1) # Jeda 1 detik antar panggilan API untuk menghindari rate limit

    json_file = Path(output_dir) / "data.json"
    scraper.save_to_json(rewritten_articles, str(json_file))

    generate_static_site(rewritten_articles, output_dir)

    logging.info("Process finished successfully. data.json and index.html have been updated.")

if __name__ == "__main__":
    main()