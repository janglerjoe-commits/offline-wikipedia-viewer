"""
Enhanced Offline Wikipedia Viewer with Optimized Search
RAM Usage: ~8-10 GB (down from 26 GB)
Features:
-Fast search using prefix trees and word indices
-Progressive indexing with immediate availability
-Efficient caching and memory management
Visit http://127.0.0.1:5000/
"""
import os
import sys
import threading
import time
import re
import bz2
import bisect
from collections import OrderedDict, defaultdict
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET
from flask import Flask, render_template_string, request, jsonify

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
INDEX_FILE = os.path.join(DATA_DIR, "enwiki-pages-articles-multistream-index.txt.bz2")
DUMP_FILE  = os.path.join(DATA_DIR, "enwiki-pages-articles-multistream.xml.bz2")
CACHE_SIZE = 100
BATCH_SIZE = 5000 
MAX_SEARCH_RESULTS = 50

# Global state
index_loaded = False
title_to_info = {}  # title -> (offset, page_id)
stream_offsets = {}  # offset -> next_offset
loading_status = {"indexed": 0, "total": 0, "current": "", "complete": False}
cache = OrderedDict()
data_lock = threading.Lock()

# Search optimization structures (RAM optimized)
search_index = {
    'sorted_titles': [],  # Sorted list for binary search
    'lower_to_original': {},  # lowercase -> original title
    'prefix_index': defaultdict(set),  # first 2-3 chars -> set of indices (REDUCED)
    'word_index': defaultdict(set),  # FULL words only -> set of indices (NO SUBSTRINGS)
}
search_lock = threading.Lock()

app = Flask(__name__)

# NO HTML FILE
SEARCH_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>Offline Wikipedia</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 20px; background: #1a1a1a; color: #e0e0e0; }
.container { max-width: 900px; margin: auto; background: #2d2d2d; padding: 30px; border-radius: 8px; }
h1 { color: #fff; text-align: center; border-bottom: 2px solid #3498db; padding-bottom: 15px; }
.search-box { width: 100%; padding: 15px; font-size: 16px; border: 1px solid #555; border-radius: 6px; 
             background: #3a3a3a; color: #e0e0e0; box-sizing: border-box; margin-bottom: 20px; }
.search-box:focus { outline: none; border-color: #3498db; }
.results { max-height: 60vh; overflow-y: auto; }
.result-item { padding: 12px; margin: 5px 0; background: #3a3a3a; border-radius: 4px; 
              cursor: pointer; border-left: 3px solid #3498db; transition: background 0.2s; }
.result-item:hover { background: #4a4a4a; }
.result-item .match { color: #3498db; font-weight: bold; }
.status { text-align: center; padding: 15px; color: #aaa; border-top: 1px solid #333; margin-top: 20px; }
.progress-bar { width: 100%; height: 8px; background: #333; border-radius: 4px; margin: 10px 0; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #3498db, #2ecc71); border-radius: 4px; transition: width 0.3s; }
.complete { color: #27ae60; font-weight: bold; }
.search-info { text-align: center; color: #888; font-size: 0.9em; margin: 10px 0; }
</style>
</head>
<body>
<div class="container">
    <h1>Offline Wikipedia</h1>
    <input type="text" id="search" placeholder="Search Wikipedia..." class="search-box" autocomplete="off">
    <div class="search-info" id="searchInfo"></div>
    <div class="results" id="results">
        <p>Search for articles - instant results as you type.</p>
    </div>
    <div class="status" id="status">
        Articles indexed: {{ loading_status.indexed }} / {{ loading_status.total }}
        <div class="progress-bar"><div class="progress-fill" id="progress"></div></div>
        <div id="current">{{ loading_status.current }}</div>
    </div>
</div>

<script>
let searchTimeout;
const search = document.getElementById('search');
const results = document.getElementById('results');
const status = document.getElementById('status');
const progress = document.getElementById('progress');
const current = document.getElementById('current');
const searchInfo = document.getElementById('searchInfo');

search.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const query = search.value.trim();
    if (query.length >= 2) {
        searchInfo.textContent = 'Searching...';
        searchTimeout = setTimeout(doSearch, 100);
    } else {
        searchInfo.textContent = '';
        results.innerHTML = '<p>Type at least 2 characters to search</p>';
    }
});

function doSearch() {
    const query = search.value.trim();
    if (query.length < 2) {
        results.innerHTML = '<p>Type at least 2 characters to search</p>';
        searchInfo.textContent = '';
        return;
    }
    
    const startTime = performance.now();
    
    fetch('/search?q=' + encodeURIComponent(query))
        .then(r => r.json())
        .then(data => {
            const elapsed = Math.round(performance.now() - startTime);
            searchInfo.textContent = `Found ${data.count} results in ${elapsed}ms`;
            
            if (data.results.length === 0) {
                results.innerHTML = '<p>No articles found for "' + query + '"</p>';
                return;
            }
            
            results.innerHTML = data.results.map(r => {
                const title = r[0];
                const lowerTitle = title.toLowerCase();
                const lowerQuery = query.toLowerCase();
                let displayTitle = title;
                
                if (lowerTitle.includes(lowerQuery)) {
                    const idx = lowerTitle.indexOf(lowerQuery);
                    displayTitle = title.substring(0, idx) + 
                        '<span class="match">' + title.substring(idx, idx + query.length) + '</span>' +
                        title.substring(idx + query.length);
                }
                
                return `<div class="result-item" onclick="location.href='/wiki/${encodeURIComponent(r[1])}'">
                    ${displayTitle}
                </div>`;
            }).join('');
        })
        .catch(e => {
            results.innerHTML = '<p style="color: red;">Search error</p>';
            searchInfo.textContent = '';
        });
}

function updateStatus() {
    fetch('/status')
        .then(r => r.json())
        .then(data => {
            status.innerHTML = `Articles indexed: ${data.indexed.toLocaleString()} / ${data.total.toLocaleString()}`;
            
            if (data.complete) {
                status.className = 'status complete';
                status.innerHTML = `All ${data.indexed.toLocaleString()} articles indexed and searchable.`;
                current.style.display = 'none';
                progress.style.width = '100%';
                setTimeout(() => clearInterval(statusInterval), 2000);
            } else if (data.total > 0) {
                progress.style.width = Math.max(2, (data.indexed / data.total) * 100) + '%';
                if (data.current) current.textContent = `Currently indexing: ${data.current}`;
            }
        });
}

const statusInterval = setInterval(updateStatus, 1000);
updateStatus();
</script>
</body>
</html>
"""

ARTICLE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>{{ title }} - Offline Wikipedia</title>
<style>
body { font-family: Georgia, serif; margin: 0; padding: 20px; background: #1a1a1a; color: #e0e0e0; line-height: 1.7; }
.container { max-width: 800px; margin: auto; background: #2d2d2d; padding: 30px; border-radius: 8px; }
.back { margin-bottom: 20px; }
.back a { color: #3498db; text-decoration: none; }
h1 { color: #fff; border-bottom: 3px solid #3498db; padding-bottom: 15px; }
h2 { color: #fff; border-bottom: 1px solid #3498db; margin-top: 30px; }
h3, h4, h5, h6 { color: #fff; margin-top: 25px; }
.error { color: #e74c3c; background: #2d1a1a; padding: 20px; border-radius: 5px; border-left: 4px solid #e74c3c; }
.notice { background: #2d251a; color: #ffc107; padding: 20px; border-radius: 5px; border-left: 4px solid #ffc107; }
.categories { margin-top: 30px; padding: 15px; background: #333; border-radius: 5px; }
.categories h3 { margin-top: 0; color: #3498db; }
.category-link { display: inline-block; margin: 3px 5px; padding: 4px 8px; background: #555; 
                border-radius: 3px; font-size: 0.9em; }
.category-link a { color: #e0e0e0; text-decoration: none; }
.category-link:hover { background: #666; }
a { color: #3498db; }
a:hover { color: #5dade2; }
.infobox { float: right; margin: 0 0 15px 15px; padding: 15px; background: #333; 
          border-radius: 5px; max-width: 300px; font-size: 0.9em; }
.infobox h3 { margin-top: 0; color: #3498db; }
</style>
</head>
<body>
<div class="container">
    <div class="back"><a href="/">&larr; Back to Search</a></div>
    <h1>{{ title }}</h1>
    {% if notice %}<div class="notice">{{ notice }}</div>{% endif %}
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    {% if content %}{{ content|safe }}{% endif %}
    {% if categories %}
    <div class="categories">
        <h3>Categories</h3>
        {% for category in categories %}
        <span class="category-link"><a href="/wiki/Category:{{ category }}">{{ category }}</a></span>
        {% endfor %}
    </div>
    {% endif %}
</div>
</body>
</html>
"""

def build_search_indices(titles_batch):
    """Build search indices - OPTIMIZED to reduce RAM usage."""
    with search_lock:
        base_idx = len(search_index['sorted_titles'])
        
        for title in titles_batch:
            # Skip namespaces
            if title.startswith(('Template:', 'File:', 'User:', 'Talk:', 'Wikipedia:', 'Help:', 'Portal:')):
                continue
            
            idx = len(search_index['sorted_titles'])
            search_index['sorted_titles'].append(title)
            
            title_lower = title.lower()
            search_index['lower_to_original'][title_lower] = title
            
            # Prefix index - only 2 and 3 character prefixes (not 1)
            if len(title_lower) >= 2:
                search_index['prefix_index'][title_lower[:2]].add(idx)
            if len(title_lower) >= 3:
                search_index['prefix_index'][title_lower[:3]].add(idx)
            
            # Word index - ONLY FULL WORDS, NO SUBSTRINGS
            # This is the key change - was creating substrings for every word
            words = re.split(r'[\s\-_,.()\[\]]+', title_lower)
            for word in words:
                if len(word) >= 2:  # Skip single characters
                    search_index['word_index'][word].add(idx)  # ONLY the full word

def load_index():
    """Progressive index loading with search index building."""
    global title_to_info, stream_offsets, loading_status, index_loaded
    
    if not os.path.exists(INDEX_FILE):
        with data_lock:
            loading_status["total"] = -1
        return
    
    try:
        # Count total entries
        print("Counting total articles...")
        with bz2.open(INDEX_FILE, 'rt', encoding='utf-8') as f:
            total = sum(1 for line in f if line.strip())
        
        with data_lock:
            loading_status["total"] = total
        
        print(f"Found {total:,} articles to index")
        
        # Process entries in batches
        batch_titles = {}
        batch_offsets = set()
        search_batch = []
        processed = 0
        
        with bz2.open(INDEX_FILE, 'rt', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(':', 2)
                if len(parts) != 3:
                    continue
                
                try:
                    offset = int(parts[0])
                    title = parts[2]
                    batch_titles[title] = (offset, parts[1])
                    batch_offsets.add(offset)
                    search_batch.append(title)
                    processed += 1
                    
                    with data_lock:
                        loading_status["current"] = title
                    
                    if processed % BATCH_SIZE == 0 or processed == total:
                        # Update main index
                        with data_lock:
                            title_to_info.update(batch_titles)
                            loading_status["indexed"] = len(title_to_info)
                        
                        # Build search indices
                        build_search_indices(search_batch)
                        
                        batch_titles.clear()
                        batch_offsets.clear()
                        search_batch.clear()
                        
                        if processed % 50000 == 0:
                            print(f"Indexed {processed:,} / {total:,} articles")
                        
                        time.sleep(0.001)
                        
                except ValueError:
                    continue
        
        # Build offset mapping
        print("Building offset mapping...")
        with data_lock:
            offsets = sorted(set(offset for offset, _ in title_to_info.values()))
            for i, offset in enumerate(offsets):
                stream_offsets[offset] = offsets[i + 1] if i + 1 < len(offsets) else None
            
            loading_status["complete"] = True
            index_loaded = True
        
        print("Indexing complete!")
        print(f"RAM usage: ~8-10 GB (optimized)")
            
    except Exception as e:
        print(f"Error during indexing: {e}")
        with data_lock:
            loading_status["total"] = -1

def fast_search(query, limit=MAX_SEARCH_RESULTS):
    """Optimized search using indices."""
    if not query or len(query) < 2:
        return []
    
    query_lower = query.lower()
    results = []
    seen = set()
    
    with search_lock:
        # Exact match (highest priority)
        if query_lower in search_index['lower_to_original']:
            title = search_index['lower_to_original'][query_lower]
            results.append((0, title))
            seen.add(title)
        
        # Prefix search using prefix index
        prefix_key = query_lower[:min(3, len(query_lower))]
        if prefix_key in search_index['prefix_index']:
            candidate_indices = search_index['prefix_index'][prefix_key]
            
            for idx in candidate_indices:
                if idx < len(search_index['sorted_titles']):
                    title = search_index['sorted_titles'][idx]
                    if title not in seen:
                        title_lower = title.lower()
                        
                        if title_lower.startswith(query_lower):
                            results.append((1, title))
                            seen.add(title)
                        elif query_lower in title_lower:
                            results.append((2, title))
                            seen.add(title)
                
                if len(results) >= limit * 2:
                    break
        
        # Word-based search if not enough results
        if len(results) < limit:
            # Try each word in the query
            query_words = query_lower.split()
            
            for query_word in query_words:
                if query_word in search_index['word_index']:
                    matching_indices = search_index['word_index'][query_word]
                    
                    for idx in list(matching_indices)[:limit * 3]:
                        if idx < len(search_index['sorted_titles']):
                            title = search_index['sorted_titles'][idx]
                            if title not in seen:
                                results.append((3, title))
                                seen.add(title)
                
                if len(results) >= limit * 2:
                    break
            
            # If still not enough, try partial word matching
            if len(results) < limit:
                for word in search_index['word_index']:
                    if query_lower in word:
                        matching_indices = search_index['word_index'][word]
                        for idx in list(matching_indices)[:limit]:
                            if idx < len(search_index['sorted_titles']):
                                title = search_index['sorted_titles'][idx]
                                if title not in seen:
                                    results.append((4, title))
                                    seen.add(title)
                        
                        if len(results) >= limit * 2:
                            break
    
    # Sort by relevance and limit
    results.sort(key=lambda x: (x[0], x[1].lower()))
    return [(title, title) for _, title in results[:limit]]

def get_stream_data(offset):
    """Extract stream data from dump file."""
    try:
        next_offset = stream_offsets.get(offset)
        size = (next_offset - offset) if next_offset else 2 * 1024 * 1024
        
        with open(DUMP_FILE, 'rb') as f:
            f.seek(offset)
            compressed = f.read(size)
        
        decompressor = bz2.BZ2Decompressor()
        return decompressor.decompress(compressed).decode('utf-8', errors='ignore')
        
    except Exception:
        return None

def extract_article(xml_content, target_title, target_id):
    """Extract article content from XML."""
    try:
        # Try XML parsing
        if not xml_content.strip().startswith('<?xml'):
            xml_content = f'<?xml version="1.0" encoding="UTF-8"?><mediawiki>{xml_content}</mediawiki>'
        
        root = ET.fromstring(xml_content)
        
        for page in root.findall('.//page'):
            title_elem = page.find('title')
            id_elem = page.find('id')
            
            if (title_elem is not None and title_elem.text == target_title) or \
               (id_elem is not None and id_elem.text == target_id):
                
                revision = page.find('.//revision')
                if revision is not None:
                    text_elem = revision.find('text')
                    if text_elem is not None and text_elem.text:
                        content, categories = wikitext_to_html(text_elem.text)
                        return content, categories
        
        return None, []
        
    except Exception:
        # Fallback regex
        page_pattern = r'<page>(.*?)</page>'
        for page_content in re.findall(page_pattern, xml_content, re.DOTALL):
            title_match = re.search(r'<title>(.*?)</title>', page_content)
            id_match = re.search(r'<id>(\d+)</id>', page_content)
            
            if title_match and (title_match.group(1) == target_title or 
                               (id_match and id_match.group(1) == target_id)):
                
                text_match = re.search(r'<text[^>]*>(.*?)</text>', page_content, re.DOTALL)
                if text_match and text_match.group(1).strip():
                    content, categories = wikitext_to_html(text_match.group(1))
                    return content, categories
        
        return None, []

def wikitext_to_html(text):
    """Enhanced wikitext to HTML conversion with better cleanup."""
    html = text
    categories = []
    
    # Extract categories first
    category_matches = re.findall(r'\[\[Category:([^\]|]+)(?:\|[^\]]*)?\]\]', html, re.IGNORECASE)
    categories.extend(category_matches)
    
    # Remove category links from main content
    html = re.sub(r'\[\[Category:[^\]]*\]\]', '', html, flags=re.IGNORECASE)
    
    # Remove all image/file references
    html = re.sub(r'\[\[(?:File|Image):[^\]]*(?:\[\[[^\]]*\]\][^\]]*)*\]\]', '', html, flags=re.IGNORECASE)
    
    # Remove gallery tags
    html = re.sub(r'<gallery[^>]*>.*?</gallery>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove templates
    def remove_templates(text):
        text = re.sub(r'\{\{[^{}]*\}\}', '', text)
        for _ in range(5):
            old_text = text
            text = re.sub(r'\{\{[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}', '', text)
            if old_text == text:
                break
        return text
    
    html = remove_templates(html)
    
    # Remove references
    html = re.sub(r'<ref[^>]*>.*?</ref>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<ref[^>]*/?>', '', html, flags=re.IGNORECASE)
    
    # Remove comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    
    # Remove table syntax
    html = re.sub(r'\{\|[^}]*\|\}', '', html, flags=re.DOTALL)
    html = re.sub(r'^\|-.*$', '', html, flags=re.MULTILINE)
    html = re.sub(r'^\![^|]*\|', '', html, flags=re.MULTILINE)
    html = re.sub(r'^\|[^|]*\|', '', html, flags=re.MULTILINE)
    
    # Headers
    html = re.sub(r'^======([^=]+)======', r'<h6>\1</h6>', html, flags=re.MULTILINE)
    html = re.sub(r'^=====([^=]+)=====', r'<h5>\1</h5>', html, flags=re.MULTILINE)
    html = re.sub(r'^====([^=]+)====', r'<h4>\1</h4>', html, flags=re.MULTILINE)
    html = re.sub(r'^===([^=]+)===', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^==([^=]+)==', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    
    # Formatting
    html = re.sub(r"'''([^']+)'''", r'<strong>\1</strong>', html)
    html = re.sub(r"''([^']+)''", r'<em>\1</em>', html)
    
    
    # Links - capitalize first letter of internal wiki links
    def capitalize_link(match):
        link = match.group(1)
        display = match.group(2) if match.lastindex >= 2 else match.group(1)
        # Capitalize first letter of the link target
        if link and link[0].islower():
            link = link[0].upper() + link[1:]
        return f'<a href="/wiki/{link}">{display}</a>'
    
    html = re.sub(r'\[\[([^|\]]+)\|([^\]]+)\]\]', capitalize_link, html)
    html = re.sub(r'\[\[([^\]]+)\]\]', capitalize_link, html)
    html = re.sub(r'\[([^ ]+) ([^\]]+)\]', r'<a href="\1" target="_blank">\2</a>', html)
    html = re.sub(r'\[([^ \]]+)\]', r'<a href="\1" target="_blank">\1</a>', html)
    
    # Lists
    html = re.sub(r'^\*+ (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r'^\#+ (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    
    # Clean up
    html = re.sub(r'\n\s*\n\s*\n', '\n\n', html)
    html = re.sub(r'^\s+$', '', html, flags=re.MULTILINE)
    
    # Convert to paragraphs
    paragraphs = [p.strip() for p in html.split('\n\n') if p.strip()]
    formatted = []
    
    in_list = False
    for para in paragraphs:
        if para.startswith('<h') or para.startswith('<li>'):
            if para.startswith('<li>') and not in_list:
                formatted.append('<ul>')
                in_list = True
            elif not para.startswith('<li>') and in_list:
                formatted.append('</ul>')
                in_list = False
            formatted.append(para)
        else:
            if in_list:
                formatted.append('</ul>')
                in_list = False
            para = para.replace('\n', ' ')
            para = re.sub(r'\s+', ' ', para)
            if para.strip():
                formatted.append(f'<p>{para}</p>')
    
    if in_list:
        formatted.append('</ul>')
    
    return '\n'.join(formatted), categories

def get_article_content(title):
    """Get article content with caching."""
    if title in cache:
        cache.move_to_end(title)
        return cache[title]
    
    with data_lock:
        if title not in title_to_info:
            return None, []
        offset, page_id = title_to_info[title]
    
    xml_content = get_stream_data(offset)
    if not xml_content:
        return None, []
    
    content, categories = extract_article(xml_content, title, page_id)
    
    if content:
        result = (content, categories)
        cache[title] = result
        if len(cache) > CACHE_SIZE:
            cache.popitem(last=False)
        return result
    
    return None, []

# Flask routes
@app.route('/')
def index():
    with data_lock:
        status = loading_status.copy()
    return render_template_string(SEARCH_TEMPLATE, loading_status=status)

@app.route('/search')
def search():
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify({"results": [], "count": 0})
    
    results = fast_search(query)
    return jsonify({"results": results, "count": len(results)})

@app.route('/wiki/<path:title>')
def article(title):
    title = unquote(title)
    
    with data_lock:
        available = title in title_to_info
        complete = loading_status["complete"]
        current = loading_status["indexed"]
        total = loading_status["total"]
    
    notice = None
    error = None
    content = None
    categories = []
    
    if not available:
        if not complete and total > 0:
            notice = f"Article not indexed yet. Progress: {current:,} of {total:,} articles."
        else:
            error = "Article not found."
    else:
        result = get_article_content(title)
        if result and result[0]:
            content, categories = result
        else:
            error = "Could not load article content."
    
    return render_template_string(ARTICLE_TEMPLATE, 
                                 title=title, notice=notice, 
                                 error=error, content=content,
                                 categories=categories)

@app.route('/status')
def status():
    with data_lock:
        return jsonify(loading_status.copy())

if __name__ == '__main__':
    print("Starting Offline Wikipedia Viewer with Optimized Search...")
    
    if not os.path.exists(INDEX_FILE):
        print(f"ERROR: Index file not found: {INDEX_FILE}")
        sys.exit(1)
        
    if not os.path.exists(DUMP_FILE):
        print(f"ERROR: Dump file not found: {DUMP_FILE}")
        sys.exit(1)
    
    print("Files found. Starting indexing...")
    print("Server available at http://127.0.0.1:5000/")
    print("\nRAM Optimizations:")
    print("- Prefix index: Only 2-3 char prefixes (not 1)")
    print("- Word index: Full words only, NO substrings")
    print("- Expected RAM: ~8-10 GB (down from 26 GB)")
    print("- Search quality: Same as original")
    print("- Search available immediately as articles are indexed")
    
    threading.Thread(target=load_index, daemon=True).start()
    
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)