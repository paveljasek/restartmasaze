import os
import re
import hashlib
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://restartmasaze.webnode.cz/"
DOMAIN = urlparse(BASE_URL).netloc
OUTPUT_DIR = "."
ASSETS_DIR = "assets"

os.makedirs(ASSETS_DIR, exist_ok=True)

visited_urls = set()
assets_map = {}  # clean_url -> local_filename

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

def get_asset_filename(asset_url):
    parsed = urlparse(asset_url)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    basename = parsed.path.split('/')[-1]
    if not basename:
        basename = "asset"
    basename = re.sub(r'[^a-zA-Z0-9_\.\-]', '', basename)
    url_hash = hashlib.md5(clean_url.encode('utf-8')).hexdigest()[:8]
    if '.' in basename:
        parts = basename.rsplit('.', 1)
        return f"{parts[0]}_{url_hash}.{parts[1]}"
    else:
        return f"{basename}_{url_hash}"

def download_asset(asset_url):
    parsed = urlparse(asset_url)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if clean_url in assets_map:
        return f"{ASSETS_DIR}/{assets_map[clean_url]}"
    
    filename = get_asset_filename(asset_url)
    assets_map[clean_url] = filename
    filepath = os.path.join(ASSETS_DIR, filename)
    
    if not os.path.exists(filepath):
        print(f"Downloading asset: {clean_url}")
        try:
            r = session.get(asset_url, timeout=15)
            if r.status_code == 200:
                content = r.content
                if filename.endswith('.css'):
                    content = process_css_content(content.decode('utf-8', errors='ignore'), asset_url).encode('utf-8')
                with open(filepath, 'wb') as f:
                    f.write(content)
        except Exception as e:
            print(f"Error downloading asset {asset_url}: {e}")
            
    return f"{ASSETS_DIR}/{filename}"

def process_css_content(css_text, css_url):
    pattern = re.compile(r'url\([\'"]?(.*?)[\'"]?\)')
    
    def repl(match):
        inner_url = match.group(1).strip()
        if inner_url.startswith('data:'):
            return match.group(0)
        abs_url = urljoin(css_url, inner_url)
        parsed = urlparse(abs_url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean_url in assets_map:
            local_fn = assets_map[clean_url]
        else:
            local_fn = get_asset_filename(abs_url)
            assets_map[clean_url] = local_fn
            download_asset(abs_url)
        return f"url('{local_fn}')"
        
    return pattern.sub(repl, css_text)

def get_local_filepath(url):
    parsed = urlparse(url)
    path = parsed.path.lstrip('/')
    if not path:
        return "index.html", 0
    if path.endswith('/'):
        return f"{path}index.html", path.count('/')
    if '.' in path.split('/')[-1]:
        return path, path.count('/')
    return f"{path}/index.html", path.count('/') + 1

def get_relative_internal_link(target_url, current_depth):
    parsed = urlparse(target_url)
    path = parsed.path.lstrip('/')
    rel_base = ("../" * current_depth)
    res = rel_base + path
    if not res:
        res = "./"
    if parsed.query:
        res += "?" + parsed.query
    if parsed.fragment:
        res += "#" + parsed.fragment
    return res

def process_srcset(srcset_str, page_url, depth):
    parts = srcset_str.split(',')
    new_parts = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        subparts = part.split()
        img_url = urljoin(page_url, subparts[0])
        local_asset_path = download_asset(img_url)
        rel_path = ("../" * depth) + local_asset_path
        if len(subparts) > 1:
            new_parts.append(f"{rel_path} {' '.join(subparts[1:])}")
        else:
            new_parts.append(rel_path)
    return ", ".join(new_parts)

def crawl_page(url):
    parsed = urlparse(url)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if clean_url in visited_urls:
        return
    visited_urls.add(clean_url)
    
    print(f"Crawling page: {clean_url}")
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            print(f"Failed to fetch {url} with status {r.status_code}")
            return
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return
        
    soup = BeautifulSoup(r.text, 'html.parser')
    filepath, depth = get_local_filepath(clean_url)
    full_outpath = os.path.join(OUTPUT_DIR, filepath)
    os.makedirs(os.path.dirname(full_outpath), exist_ok=True)
    
    internal_urls_to_crawl = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        abs_url = urljoin(url, href)
        target_parsed = urlparse(abs_url)
        if target_parsed.netloc == DOMAIN:
            if not any(target_parsed.path.endswith(ext) for ext in ['.pdf', '.jpg', '.png', '.zip']):
                internal_urls_to_crawl.append(abs_url)
            a['href'] = get_relative_internal_link(abs_url, depth)
            
    for link in soup.find_all('link', href=True):
        rel = link.get('rel', [])
        if isinstance(rel, str):
            rel = [rel]
        if any(r in rel for r in ['stylesheet', 'icon', 'apple-touch-icon', 'preload']):
            abs_url = urljoin(url, link['href'])
            local_path = download_asset(abs_url)
            link['href'] = ("../" * depth) + local_path
            
    for script in soup.find_all('script', src=True):
        abs_url = urljoin(url, script['src'])
        local_path = download_asset(abs_url)
        script['src'] = ("../" * depth) + local_path
        
    for img in soup.find_all('img', src=True):
        abs_url = urljoin(url, img['src'])
        local_path = download_asset(abs_url)
        img['src'] = ("../" * depth) + local_path
        if img.get('srcset'):
            img['srcset'] = process_srcset(img['srcset'], url, depth)
            
    for source in soup.find_all('source', srcset=True):
        source['srcset'] = process_srcset(source['srcset'], url, depth)
        
    for embed in soup.find_all('embed'):
        if embed.get('data-src'):
            abs_url = urljoin(url, embed['data-src'])
            local_path = download_asset(abs_url)
            embed['data-src'] = ("../" * depth) + local_path
        if embed.get('src'):
            abs_url = urljoin(url, embed['src'])
            local_path = download_asset(abs_url)
            embed['src'] = ("../" * depth) + local_path
            
    for tag in soup.find_all(style=True):
        style_text = tag['style']
        if 'url(' in style_text:
            def style_repl(m):
                inner = m.group(1).strip()
                if inner.startswith('data:'):
                    return m.group(0)
                abs_u = urljoin(url, inner)
                loc_p = download_asset(abs_u)
                return f"url('{('../' * depth) + loc_p}')"
            tag['style'] = re.sub(r'url\([\'"]?(.*?)[\'"]?\)', style_repl, style_text)
            
    wnd_stripe = soup.find('div', class_='wnd-free-stripe')
    if wnd_stripe:
        wnd_stripe.decompose()
        
    for sf in soup.find_all('span', class_='sf'):
        sf.decompose()
        
    generator_meta = soup.find('meta', attrs={"name": "generator"})
    if generator_meta:
        generator_meta.decompose()
        
    for script in soup.find_all('script'):
        if script.string and ('wnd.trackerConfig' in script.string or 'events.webnode.com' in script.string):
            script.decompose()
        
    with open(full_outpath, 'w', encoding='utf-8') as f:
        f.write(str(soup))
        
    print(f"Saved: {full_outpath}")
    
    for next_url in internal_urls_to_crawl:
        crawl_page(next_url)

if __name__ == "__main__":
    crawl_page(BASE_URL)
    print("Crawling completed successfully!")
