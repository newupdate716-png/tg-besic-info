from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import json
import requests
from bs4 import BeautifulSoup
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def extract_chat_id_from_html(html, target_chat_id):
    """Verify if the page contains the expected chat_id and extract type"""
    soup = BeautifulSoup(html, "html.parser")
    html_str = str(soup)
    
    # Check if the given chat_id exists in the page
    if str(target_chat_id) not in html_str:
        return None, None
    
    # Detect profile type
    if "tgme_channel_info" in html_str.lower():
        ptype = "supergroup_channel"
    elif "tgme_group_info" in html_str.lower():
        ptype = "group"
    elif "tgme_bio" in html_str.lower():
        ptype = "user"
    else:
        ptype = "unknown"
    
    return target_chat_id, ptype

def scrape_by_chat_id(chat_id):
    """Scrape Telegram data using only chat_id (via public link)"""
    chat_id = str(chat_id).strip()
    
    # Determine URL format
    if chat_id.startswith('-100'):
        # Supergroup/channel
        clean_id = chat_id[4:]
        url = f"https://t.me/c/{clean_id}"
    elif chat_id.startswith('-'):
        # Old group
        clean_id = chat_id[1:]
        url = f"https://t.me/joinchat/AAAAAE{clean_id}"  # Won't work for most, but try
        # Actually groups without username can't be scraped, so return error
        return {"error": "Private/unnamed groups cannot be scraped via chat_id. Use a public channel/group username."}
    else:
        # Personal chat? Not possible via web
        return {"error": "User personal chat IDs cannot be scraped via web. Use a public channel/group."}
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
    except Exception as e:
        return {"error": f"Request failed: {str(e)}"}
    
    if r.status_code != 200:
        return {"error": f"Cannot access page (HTTP {r.status_code})"}
    
    # Verify chat_id matches
    found_id, ptype = extract_chat_id_from_html(r.text, chat_id)
    if not found_id:
        return {"error": f"Chat ID {chat_id} not found in the page. It might be invalid or private."}
    
    soup = BeautifulSoup(r.text, "html.parser")
    
    # Extract name
    name = None
    title_tag = soup.select_one("div.tgme_page_title")
    if title_tag:
        name = title_tag.text.strip()
    if not name:
        h1 = soup.find("h1")
        if h1:
            name = h1.text.strip()
    
    # Extract bio/description
    bio = None
    desc = soup.select_one("div.tgme_page_description")
    if desc:
        bio = desc.text.strip()
    if not bio:
        desc2 = soup.select_one("div.tgme_channel_info_description")
        if desc2:
            bio = desc2.text.strip()
    
    # Extract photo URL
    photo_url = None
    photo = soup.select_one("img.tgme_page_photo_image")
    if photo and photo.get("src"):
        photo_url = photo.get("src")
        if photo_url.startswith("//"):
            photo_url = "https:" + photo_url
    
    # Extract member count
    member_count_raw = None
    member_count_numeric = None
    counter = soup.find("div", class_="tgme_page_extra")
    if counter:
        member_count_raw = counter.text.strip()
        nums = re.findall(r'[\d,]+', member_count_raw)
        if nums:
            try:
                member_count_numeric = int(nums[0].replace(',', ''))
            except:
                pass
    
    # Verified / restricted
    verified = "verified" if "tgme_icon_verified" in r.text else "not_verified"
    restricted = "restricted" if "can't be displayed" in r.text else "public"
    
    return {
        "chat_id": chat_id,
        "name": name,
        "bio": bio,
        "photo_url": photo_url,
        "profile_type": ptype,
        "member_count_raw": member_count_raw,
        "member_count": member_count_numeric,
        "verified": verified,
        "restricted": restricted,
        "public_link": url
    }

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        if parsed.path == '/info' or parsed.path == '/api/info':
            chat_id = params.get('chat_id', [None])[0]
            
            if not chat_id:
                response = {
                    "error": "Missing 'chat_id' parameter",
                    "usage": "/info?chat_id=-1001234567890"
                }
                self.wfile.write(json.dumps(response, indent=2).encode())
                return
            
            result = scrape_by_chat_id(chat_id)
            self.wfile.write(json.dumps(result, indent=2).encode())
        
        elif parsed.path == '/' or parsed.path == '/api':
            response = {
                "name": "Telegram Chat ID Info API",
                "developer": "t.me/ab_devs",
                "endpoints": {
                    "/info": {
                        "description": "Get Telegram channel/group info using chat_id (public only)",
                        "parameters": {
                            "chat_id": "Numerical chat ID (e.g., -1001234567890)"
                        },
                        "example": "/info?chat_id=-1001234567890"
                    }
                },
                "note": "Only works for public channels/groups that have a t.me/c/ID link. Private groups/users are not accessible."
            }
            self.wfile.write(json.dumps(response, indent=2).encode())
        
        else:
            self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode())

# For local testing
if __name__ == '__main__':
    from http.server import HTTPServer
    server = HTTPServer(('0.0.0.0', 8000), handler)
    print("Server running on http://localhost:8000")
    server.serve_forever()