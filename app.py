from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import json
import requests
from bs4 import BeautifulSoup
import re
import logging
import hashlib
from io import BytesIO
import base64

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
BASE_URL = "https://t.me/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

def extract_chat_id_advanced(soup, username):
    """Extract chat ID from Telegram page"""
    chat_id = None
    html_str = str(soup)
    
    try:
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string:
                patterns = [
                    r'"id":(-?\d{9,15})',
                    r'"channel_id":(-?\d{9,15})',
                    r'"chat_id":(-?\d{9,15})',
                    r'"user_id":(-?\d{9,15})',
                    r'"tgme":.*?"id":(-?\d{9,15})',
                    r'"peer":.*?"id":(-?\d{9,15})',
                    r'"userId":(-?\d{9,15})',
                    r'"chatId":(-?\d{9,15})',
                    r'"group_id":(-?\d{9,15})',
                    r'"supergroup_id":(-?\d{9,15})',
                    r'"megagroup_id":(-?\d{9,15})',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, script.string)
                    for match in matches:
                        if match and len(str(match)) > 5:
                            chat_id = str(match)
                            if chat_id.startswith('-100'):
                                return chat_id, "supergroup_channel"
                            elif chat_id.startswith('-'):
                                return chat_id, "group"
                            else:
                                return chat_id, "user"
                    if chat_id:
                        break
                if chat_id:
                    break
        
        if not chat_id:
            meta_tags = soup.find_all("meta", attrs={"property": "og:url"})
            for meta in meta_tags:
                if meta.get("content"):
                    url_match = re.search(r'/(?:c/)?(-?\d+)$', meta["content"])
                    if url_match:
                        chat_id = url_match.group(1)
                        if chat_id.startswith('-100'):
                            return chat_id, "supergroup_channel"
                        elif chat_id.startswith('-'):
                            return chat_id, "group"
                        else:
                            return chat_id, "user"
        
        if not chat_id:
            supergroup_match = re.search(r'(-100\d{9,13})', html_str)
            if supergroup_match:
                return supergroup_match.group(1), "supergroup_channel"
            
            group_match = re.search(r'(-\d{9,13})', html_str)
            if group_match:
                return group_match.group(1), "group"
            
            user_match = re.search(r'(\d{9,11})', html_str)
            if user_match:
                return user_match.group(1), "user"
        
        if chat_id:
            clean_id = re.sub(r'[^\d-]', '', chat_id)
            if clean_id.startswith('-100'):
                return clean_id, "supergroup_channel"
            elif clean_id.startswith('-'):
                return clean_id, "group"
            else:
                return clean_id, "user"
        else:
            return None, None
            
    except Exception as e:
        logger.error(f"Chat ID extraction error: {e}")
        return None, None

def detect_profile_type(html):
    """Detect profile type from HTML"""
    html_lower = html.lower()
    
    if "tgme_channel_info" in html_lower:
        return "supergroup_channel"
    elif "tgme_group_info" in html_lower:
        if "megagroup" in html_lower or "supergroup" in html_lower:
            return "supergroup"
        return "group"
    elif "tgme_bio" in html_lower:
        return "user"
    else:
        if "members" in html_lower or "subscribers" in html_lower:
            if "channel" in html_lower:
                return "channel"
            return "group"
        return "user"

def scrape_members(soup):
    """Extract member count from page"""
    members_info = {
        "count": None,
        "type": None
    }
    
    counter = soup.find("div", class_="tgme_page_extra")
    if counter:
        text = counter.text.strip()
        members_info["count"] = text
        
        if "members" in text.lower():
            members_info["type"] = "members"
        elif "subscribers" in text.lower():
            members_info["type"] = "subscribers"
    
    if not members_info["count"]:
        all_divs = soup.find_all("div")
        for div in all_divs:
            text = div.text.strip()
            if re.search(r'[\d,]+\s+(members?|subscribers?|participants?)', text.lower()):
                members_info["count"] = text
                break
    
    return members_info

def get_profile_photo(soup):
    """Extract profile photo URL"""
    photo_selectors = [
        "img.tgme_page_photo_image",
        "img.tgme_channel_photo",
        "img.tgme_group_photo",
        "img[src*='telegram']"
    ]
    
    for selector in photo_selectors:
        photo = soup.select_one(selector)
        if photo and photo.get("src"):
            src = photo["src"]
            if src.startswith("//"):
                src = "https:" + src
            return src
    
    return None

def get_group_features(soup):
    """Extract group features"""
    features = []
    html_text = str(soup)
    
    if "public" in html_text.lower():
        features.append("public")
    if "private" in html_text.lower():
        features.append("private")
    if "verified" in html_text.lower() or "tgme_icon_verified" in html_text:
        features.append("verified")
    if "restricted" in html_text.lower() or "can't be displayed" in html_text:
        features.append("restricted")
    if "scam" in html_text.lower():
        features.append("scam")
    if "fake" in html_text.lower():
        features.append("fake")
    
    return features

def scrape_telegram(username):
    """Main scraping function"""
    username = username.replace('@', '').strip()
    url = BASE_URL + username
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}
    
    if r.status_code != 200:
        return {"error": f"Invalid username (HTTP {r.status_code})"}
    
    soup = BeautifulSoup(r.text, "html.parser")
    profile_type = detect_profile_type(r.text)
    
    # Extract title
    title_selectors = ["div.tgme_page_title", "h1.tgme_channel_info_header_title", "h1"]
    title = None
    for selector in title_selectors:
        title = soup.select_one(selector)
        if title:
            break
    
    # Extract bio
    bio_selectors = ["div.tgme_page_description", "div.tgme_channel_info_description", "div.tgme_group_description"]
    bio = None
    for selector in bio_selectors:
        bio = soup.select_one(selector)
        if bio:
            break
    
    photo_url = get_profile_photo(soup)
    
    # Extract chat ID
    chat_id_result, id_type = extract_chat_id_advanced(soup, username)
    
    members_info = scrape_members(soup)
    features = get_group_features(soup)
    
    verified = "verified" if "tgme_icon_verified" in r.text else "not_verified"
    restricted = "restricted" if "can't be displayed" in r.text else "public"
    
    # Parse member count to number if possible
    member_count_numeric = None
    if members_info["count"]:
        try:
            # Extract numbers from string like "1,234 members"
            numbers = re.findall(r'[\d,]+', members_info["count"])
            if numbers:
                member_count_numeric = int(numbers[0].replace(',', ''))
        except:
            pass
    
    return {
        "username": username,
        "name": title.text.strip() if title else f"@{username}",
        "bio": bio.text.strip() if bio else None,
        "photo_url": photo_url,
        "chat_id": chat_id_result,
        "chat_id_type": id_type,
        "profile_type": profile_type,
        "member_count_raw": members_info["count"],
        "member_count": member_count_numeric,
        "member_type": members_info["type"],
        "verified": verified,
        "restricted": restricted,
        "features": features,
        "public_link": url,
        "deep_link": f"tg://resolve?domain={username}"
    }

def get_info_by_chat_id(chat_id):
    """Get info using chat ID (this is a fallback - will try to resolve)"""
    # Note: This is limited as we can't directly get info from chat ID via web
    # You might need to use Telegram Bot API for this
    return {
        "error": "Direct chat ID lookup not supported via web scraping. Please use username instead.",
        "chat_id": chat_id
    }

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Parse query parameters
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        # Set CORS headers
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        # Check if it's the info endpoint
        if parsed_url.path == '/info' or parsed_url.path == '/api/info':
            # Get username or chat_id from query
            username = query_params.get('user', [None])[0]
            chat_id = query_params.get('chat_id', [None])[0]
            
            if not username and not chat_id:
                response = {
                    "error": "Missing parameter",
                    "usage": {
                        "by_username": "/info?user=username",
                        "by_chat_id": "/info?chat_id=123456789"
                    }
                }
                self.wfile.write(json.dumps(response, indent=2).encode())
                return
            
            # Process based on parameter
            if username:
                result = scrape_telegram(username)
            else:  # chat_id
                result = get_info_by_chat_id(chat_id)
            
            self.wfile.write(json.dumps(result, indent=2).encode())
        
        elif parsed_url.path == '/' or parsed_url.path == '/api':
            # Root endpoint with usage info
            response = {
                "name": "Telegram basic Info API",
                "Developer": "t.me/ab_devs",
            
                "endpoints": {
                    "/info": {
                        "description": "Get Telegram profile information",
                        "parameters": {
                            "user": "Telegram username (without @)",
                        },
                        "example": "/info?user=telegram"
                    }
                }
            }
            self.wfile.write(json.dumps(response, indent=2).encode())
        
        else:
            # 404 for other endpoints
            response = {"error": "Endpoint not found"}
            self.wfile.write(json.dumps(response).encode())

# For local testing
if __name__ == '__main__':
    from http.server import HTTPServer
    server = HTTPServer(('localhost', 8000), handler)
    print('Server started on http://localhost:8000')
    server.serve_forever()
