#!/usr/bin/env python3

import json
import logging
import time
import random
import sys
import os
from datetime import datetime
from urllib.parse import quote_plus, unquote
from typing import List, Dict, Optional
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
import google.generativeai as genai

# Import pustaka baru untuk Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ===================================================================
# BAGIAN 1: KELAS SCRAPER (DIUPGRADE TOTAL DENGAN SELENIUM)
# ===================================================================

class GoogleNewsScraper:
    def __init__(self, verbose=False):
        self.base_search_url = "https://www.google.com/search"
        logging_level = logging.INFO
        logging.basicConfig(level=logging_level, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
        # Pengaturan untuk menjalankan browser Chrome secara headless (tanpa GUI)
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
        
        self.logger.info("Initializing Selenium WebDriver...")
        try:
            # Menginstall dan menjalankan driver Chrome secara otomatis
            self.driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        except Exception as e:
            self.logger.error(f"Failed to initialize WebDriver: {e}")
            self.driver = None

    def scrape_category(self, category_name: str, max_articles: int, gl_code: str, hl_code: str) -> List[Dict]:
        if not self.driver:
            self.logger.error("WebDriver not initialized. Skipping scrape.")
            return []

        articles = []
        seen_urls = set()
        
        search_url = f"{self.base_search_url}?q={quote_plus(category_name)}&tbm=nws&gl={gl_code}&hl={hl_code}"
        
        self.logger.info(f"Navigating to search results for category: '{category_name}'")
        
        try:
            self.driver.get(search_url)
            # Menunggu hingga hasil pencarian benar-benar muncul di halaman
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "search"))
            )
            
            # Mengambil HTML halaman setelah semua JavaScript selesai dijalankan
            page_html = self.driver.page_source
            soup = BeautifulSoup(page_html, 'html.parser')
            
            for link_element in soup.select('a[href^="/url?q="]'):
                if len(articles) >= max_articles: break

                heading = link_element.find('div', role='heading')
                if not heading: continue

                try:
                    url = unquote(link_element['href'].split('/url?q=')[1].split('&sa=U')[0])
                    if url in seen_urls: continue

                    title = heading.get_text()
                    
                    parent_div = link_element.find_parent('div')
                    publisher_tag = parent_div.find('span')
                    publisher = publisher_tag.text if publisher_tag else "Unknown Source"
                    
                    article_data = {
                        'category': category_name, 'url': url, 'title': title,
                        'publisher': publisher, 'scraped_at': datetime.now().isoformat()
                    }
                    articles.append(article_data)
                    seen_urls.add(url)
                    self.logger.debug(f"Scraped: {title[:60]}...")
                except Exception as e:
                    self.logger.warning(f"Could not parse an article element: {e}")
                    continue
        
        except Exception as e:
            self.logger.error(f"An error occurred during scraping for '{category_name}': {e}")

        self.logger.info(f"Finished scrape for '{category_name}'. Found {len(articles)} articles.")
        return articles[:max_articles]
    
    def close(self):
        """Fungsi untuk menutup browser setelah selesai."""
        if self.driver:
            self.logger.info("Closing Selenium WebDriver.")
            self.driver.quit()

# ===================================================================
# BAGIAN 2: FUNGSI-FUNGSI HELPER
# ===================================================================

def rewrite_with_gemini(article: Dict, api_key: str) -> Optional[Dict]:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-pro')
    prompt = f"You are an expert AI journalist. Based on the headline \"{article['title']}\", write a brief, high-quality news summary (1-2 paragraphs)."
    try:
        logging.info(f"Rewriting article: {article['title'][:50]}...")
        response = model.generate_content(prompt)
        article['rewritten_content'] = response.text.replace('*', '').replace('#', '')
        return article
    except Exception as e:
        logging.error(f"Failed to call Gemini API: {e}")
        article['rewritten_content'] = "Content could not be generated at this time."
        return article

def generate_static_site(articles_by_category: List[Dict], output_dir: str):
    try:
        logging.info("Generating static site file...")
        env = Environment(loader=FileSystemLoader('.'))
        template = env.get_template('template.html')
        html_content = template.render(articles_by_category=articles_by_category, generated_at=datetime.now().strftime('%d %B %Y, %H:%M:%S UTC'))
        output_path = Path(output_dir) / "index.html"
        with open(output_path, 'w', encoding='utf-8') as f: f.write(html_content)
        logging.info(f"Site successfully generated at: {output_path}")
    except Exception as e:
        logging.error(f"Failed to generate static site: {e}")

def load_config(config_file: str = 'config.json') -> Dict:
    try:
        with open(config_file, 'r', encoding='utf-8') as f: return json.load(f)
    except FileNotFoundError:
        logging.error(f"Configuration file '{config_file}' not found."); sys.exit(1)
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from '{config_file}'."); sys.exit(1)

# ===================================================================
# BAGIAN 3: FUNGSI UTAMA
# ===================================================================

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    config = load_config()
    
    region_config = config.get('target_region')
    if not region_config: logging.error("'target_region' not found in config.json. Exiting."); sys.exit(1)
        
    region_name, gl_code, hl_code = region_config.get('name', 'Unknown'), region_config.get('gl', 'US'), region_config.get('hl', 'en')
    logging.info(f"--- Starting News Aggregator for region: {region_name} (gl={gl_code}, hl={hl_code}) ---")
    
    categories, posts_per_category, gemini_delay = config.get('categories', []), config.get('posts_per_category', 5), config.get('gemini_api_delay_seconds', 2)
    
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY environment variable is not set! Exiting."); sys.exit(1)

    scraper = GoogleNewsScraper(verbose=True)
    
    articles_for_template, total_articles_scraped = [], 0

    try:
        for category in categories:
            category_name = category.get('name')
            if not category_name: logging.warning(f"Skipping invalid category entry: {category}"); continue
                
            scraped_articles = scraper.scrape_category(category_name, posts_per_category, gl_code, hl_code)
            if not scraped_articles: continue
            
            total_articles_scraped += len(scraped_articles)
            
            rewritten_articles_for_category = []
            for article in scraped_articles:
                rewritten_article = rewrite_with_gemini(article, GEMINI_API_KEY)
                if rewritten_article: rewritten_articles_for_category.append(rewritten_article)
                logging.info(f"Waiting for {gemini_delay} seconds..."); time.sleep(gemini_delay)
                
            articles_for_template.append({"name": category_name, "articles": rewritten_articles_for_category})
    finally:
        # Memastikan browser selalu ditutup, bahkan jika terjadi error
        scraper.close()

    if total_articles_scraped == 0: logging.warning("Scraping resulted in 0 articles. Generating an empty site.")
    
    with open(Path(output_dir) / 'data.json', 'w', encoding='utf-8') as f: json.dump(articles_for_template, f, indent=2, ensure_ascii=False)
        
    generate_static_site(articles_for_template, output_dir)
    logging.info("Process finished successfully.")

if __name__ == "__main__":
    main()
