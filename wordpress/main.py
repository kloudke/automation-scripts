#!/usr/bin/env python3
import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# Load environment variables (mostly for local testing if needed, though GH Actions will inject these)
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Config
SOURCE_URL = os.environ.get("SOURCE_WP_URL", "").rstrip("/")
SOURCE_USER = os.environ.get("SOURCE_WP_USER", "")
SOURCE_PASS = os.environ.get("SOURCE_WP_APP_PASSWORD", "")

DEST_URL = os.environ.get("DEST_WP_URL", "").rstrip("/")
DEST_USER = os.environ.get("DEST_WP_USER", "")
DEST_PASS = os.environ.get("DEST_WP_APP_PASSWORD", "")

STATE_FILE = "migration_state.json"

if not all([SOURCE_URL, SOURCE_USER, SOURCE_PASS, DEST_URL, DEST_USER, DEST_PASS]):
    logger.error("Missing required environment variables. Please check SOURCE_WP_* and DEST_WP_* variables.")
    exit(1) # We must exit here to prevent the script from running with invalid credentials

# Global State Dictionary to avoid duplicates and map IDs
state = {
    "categories": {}, # source_id -> dest_id
    "tags": {},
    "users": {},
    "media": {},
    "posts": [] # List of successfully migrated source post IDs
}

# --- State Management ---
def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                loaded = json.load(f)
                state.update(loaded)
            logger.info("Loaded previous migration state.")
        except Exception as e:
            logger.error(f"Error loading state file: {e}")

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# --- API Clients ---
class WPClient:
    def __init__(self, url, username, password):
        self.base_url = f"{url}/wp-json/wp/v2"
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": "\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\", \"Google Chrome\";v=\"120\"",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": "\"Windows\"",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin"
        })

    def _request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            logger.error(f"API Request failed: {method} {url} - {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response Content: {e.response.text}")
            return None

    def get(self, endpoint, params=None):
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint, data=None, json=None, files=None, headers=None):
        return self._request("POST", endpoint, data=data, json=json, files=files, headers=headers)

    def get_all(self, endpoint, params=None):
        """Fetch all pages of results"""
        if params is None:
            params = {}
        params['per_page'] = 100
        params['page'] = 1
        
        all_results = []
        while True:
            response = self.get(endpoint, params=params)
            if not response:
                break
                
            data = response.json()
            if not data:
                break
                
            all_results.extend(data)
            
            total_pages = int(response.headers.get('X-WP-TotalPages', 1))
            if params['page'] >= total_pages:
                break
                
            params['page'] += 1
            
        return all_results

# Initialize clients lazily or globally
source_client = None
dest_client = None

# --- Migration Functions ---

def migrate_terms(taxonomy="categories"):
    """Migrates categories or tags"""
    logger.info(f"--- Migrating {taxonomy} ---")
    source_terms = source_client.get_all(taxonomy)
    logger.info(f"Found {len(source_terms)} {taxonomy} on source.")

    # Pre-fetch existing terms from Destination to avoid redundant POSTs and 400 errors
    logger.info(f"Checking existing {taxonomy} on destination...")
    dest_terms = dest_client.get_all(taxonomy)
    dest_terms_by_slug = {t['slug']: t['id'] for t in dest_terms}
    logger.info(f"Found {len(dest_terms)} existing {taxonomy} on destination.")

    for term in source_terms:
        source_id = str(term['id'])
        if source_id in state[taxonomy]:
            logger.debug(f"Skipping term '{term['name']}' (already migrated)")
            continue
            
        # Check if identical term already exists on the destination
        if term['slug'] in dest_terms_by_slug:
            existing_dest_id = dest_terms_by_slug[term['slug']]
            state[taxonomy][source_id] = existing_dest_id
            logger.info(f"Mapped existing {taxonomy}: {term['name']} -> ID {existing_dest_id}")
            save_state()
            continue

        payload = {
            "name": term['name'],
            "slug": term['slug'],
            "description": term['description']
        }
        
        # Parent handling for hierarchical taxonomies (like categories)
        if term.get('parent') and term['parent'] > 0:
            parent_source_id = str(term['parent'])
            if parent_source_id in state[taxonomy]:
                payload['parent'] = int(state[taxonomy][parent_source_id])
            else:
                logger.warning(f"Parent {parent_source_id} for term {term['name']} not yet migrated. Term structure might be flat temporarily.")

        response = dest_client.post(taxonomy, json=payload)
        
        if response and response.status_code == 201:
            dest_term = response.json()
            state[taxonomy][source_id] = dest_term['id']
            # Re-update our in-memory map in case children need to reference it immediately
            dest_terms_by_slug[term['slug']] = dest_term['id']
            logger.info(f"Created {taxonomy}: {term['name']} -> {dest_term['id']}")
            save_state()
        else:
            logger.error(f"Failed to create {taxonomy}: {term['name']}")

def migrate_users():
    """Migrates users/authors."""
    logger.info("--- Migrating Users ---")
    source_users = source_client.get_all("users")
    logger.info(f"Found {len(source_users)} users on source.")

    # Pre-fetch existing users on Destination
    logger.info("Checking existing users on destination...")
    dest_users = dest_client.get_all("users")
    dest_users_by_slug = {u['slug']: u['id'] for u in dest_users}
    logger.info(f"Found {len(dest_users)} existing users on destination.")

    for user in source_users:
        source_id = str(user['id'])
        if source_id in state['users']:
            continue
            
        # Check if identical user already exists on the destination
        if user['slug'] in dest_users_by_slug:
            existing_dest_id = dest_users_by_slug[user['slug']]
            state['users'][source_id] = existing_dest_id
            logger.info(f"Mapped existing user: {user['slug']} -> ID {existing_dest_id}")
            save_state()
            continue

        # Create user (Requires secure password, WP usually emails them)
        payload = {
            "username": user['slug'],
            "name": user['name'],
            "email": f"{user['slug']}@placeholder.domain" # If email is missing in API response
        }
        
        if 'email' in user:
             payload['email'] = user['email']

        response = dest_client.post("users", json=payload)
        if response and response.status_code == 201:
            dest_user = response.json()
            state['users'][source_id] = dest_user['id']
            dest_users_by_slug[user['slug']] = dest_user['id']
            logger.info(f"Created user: {user['slug']} -> {dest_user['id']}")
            save_state()
        else:
             logger.error(f"Failed to create user: {user['slug']}. Will default to primary admin for their posts.")


def upload_media(source_media_url, alt_text=""):
    """Downloads an image from source and uploads to destination. Returns the new Media ID."""
    
    if not source_media_url:
        return None
        
    filename = os.path.basename(urlparse(source_media_url).path)
    
    # Check if we already migrated this specific URL
    if source_media_url in state['media']:
        return state['media'][source_media_url]

    logger.debug(f"Downloading media: {source_media_url}")
    try:
        # Get the file from source
        img_response = requests.get(source_media_url, stream=True)
        img_response.raise_for_status()
        
        # Upload to destination
        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": img_response.headers.get("Content-Type", "image/jpeg")
        }
        
        # Use raw data upload
        response = dest_client.post(
            "media",
            data=img_response.content,
            headers=headers
        )
        
        if response and response.status_code == 201:
            media_data = response.json()
            new_id = media_data['id']
            new_url = media_data['source_url']
            
            # Update alternate text if provided
            if alt_text:
                 dest_client.post(f"media/{new_id}", json={"alt_text": alt_text})
                 
            state['media'][source_media_url] = {
                "id": new_id,
                "url": new_url
            }
            save_state()
            logger.info(f"Uploaded media: {filename} -> ID: {new_id}")
            return state['media'][source_media_url]
        else:
            logger.error(f"Failed to upload media: {source_media_url}")
            return None
            
    except Exception as e:
        logger.error(f"Error handling media {source_media_url}: {e}")
        return None

def process_content_images(content):
    """Parses HTML content, finds images, uploads them to destination, and rewrites the src."""
    if not content:
        return content
        
    soup = BeautifulSoup(content, 'html.parser')
    images = soup.find_all('img')
    
    for img in images:
        src = img.get('src')
        alt = img.get('alt', '')
        
        if not src:
            continue
            
        # Only process images that belong to the source URL domain
        # Adjust logic if images are hosted on a CDN
        if SOURCE_URL in src or src.startswith('/'):
            # Ensure absolute URL
            abs_src = urljoin(SOURCE_URL, src)
            
            media_info = upload_media(abs_src, alt)
            if media_info:
                # Rewrite the src attribute in the HTML
                img['src'] = media_info['url']
                
                # Try to clean up WordPress specific alignment/size classes if necessary
                # Or just let WordPress handle the new URL
                
    return str(soup)


def migrate_posts(limit=None, status="published"):
    """Migrates posts sequentially"""
    logger.info("--- Migrating Posts ---")
    
    params = {
        'per_page': 10, 
        'page': 1, 
        'orderby': 'date', 
        'order': 'desc',
        'status': status
    }
    migrated_count = 0
    
    while True:
        logger.info(f"Fetching posts page {params['page']}...")
        response = source_client.get("posts", params=params)
        
        if not response:
            break
            
        posts = response.json()
        if not posts:
            break
            
        for post in posts:
            source_id = post['id']
            
            if source_id in state['posts']:
                logger.info(f"Skipping Post {source_id} (already migrated)")
                continue
                
            logger.info(f"Processing Post {source_id}: {post['title']['rendered']}")
            
            # 1. Prepare Taxonomy IDs
            dest_categories = [state['categories'].get(str(cat_id)) for cat_id in post.get('categories', []) if str(cat_id) in state['categories']]
            dest_tags = [state['tags'].get(str(tag_id)) for tag_id in post.get('tags', []) if str(tag_id) in state['tags']]
            
            # Filter out None values
            dest_categories = [c for c in dest_categories if c]
            dest_tags = [t for t in dest_tags if t]

            # 2. Prepare Author ID
            source_author_id = str(post.get('author'))
            dest_author_id = state['users'].get(source_author_id, 1) # Default to admin (1) if mapping fails
            
            # 3. Handle Featured Image
            dest_featured_media_id = 0
            if post.get('featured_media'):
                media_info = source_client.get(f"media/{post['featured_media']}")
                if media_info and media_info.json():
                    media_url = media_info.json().get('source_url')
                    alt_text = media_info.json().get('alt_text', '')
                    uploaded_media = upload_media(media_url, alt_text)
                    if uploaded_media:
                        dest_featured_media_id = uploaded_media['id']

            # 4. Process Content (Rewrite inline image URLs)
            processed_content = process_content_images(post['content']['rendered'])
            
            # 5. Build Payload
            payload = {
                "title": post['title']['rendered'],
                "content": processed_content,
                "excerpt": post['excerpt']['rendered'],
                "status": post['status'], # Keep original publish status
                "date": post['date'],     # Keep original date
                "author": dest_author_id,
                "categories": dest_categories,
                "tags": dest_tags,
                "format": post['format'],
                "slug": post['slug']
            }
            
            if dest_featured_media_id > 0:
                payload['featured_media'] = dest_featured_media_id
                
            # 6. Push to Destination
            dest_post_resp = dest_client.post("posts", json=payload)
            
            if dest_post_resp and dest_post_resp.status_code == 201:
                logger.info(f"Successfully migrated post {source_id} -> {dest_post_resp.json()['id']}")
                state['posts'].append(source_id)
                save_state()
                
                # Save original URL for de-indexing
                old_url = post.get('link')
                if old_url:
                    with open("migrated_urls.txt", "a") as f:
                        f.write(old_url + "\n")
                        
                migrated_count += 1
                
                if limit and migrated_count >= limit:
                    logger.info(f"Reached limit of {limit} posts. Stopping.")
                    return
            else:
                 logger.error(f"Failed to migrate post {source_id}")
                 if dest_post_resp:
                     logger.error(f"Response: {dest_post_resp.text}")

        # Check pagination
        total_pages = int(response.headers.get('X-WP-TotalPages', 1))
        if params['page'] >= total_pages:
            break
            
        params['page'] += 1

def test_connection():
    """Tests the connection to both Source and Destination sites."""
    logger.info("--- Testing Connections ---")
    
    # Test Source
    logger.info(f"Connecting to Source: {SOURCE_URL}")
    if not source_client:
        logger.error("❌ Source client not initialized. Cannot test connection.")
        src_success = False
    else:
        src_resp = source_client.get("users/me")
        if src_resp and src_resp.status_code == 200:
            logger.info(f"✅ Source Connection Successful. Authenticated as: {src_resp.json().get('name')}")
            src_success = True
        else:
            logger.error("❌ Failed to authenticate with Source.")
            src_success = False
            
    # Test Destination
    logger.info(f"Connecting to Destination: {DEST_URL}")
    if not dest_client:
        logger.error("❌ Destination client not initialized. Cannot test connection.")
        dest_success = False
    else:
        dest_resp = dest_client.get("users/me")
        if dest_resp and dest_resp.status_code == 200:
            logger.info(f"✅ Destination Connection Successful. Authenticated as: {dest_resp.json().get('name')}")
            dest_success = True
        else:
            logger.error("❌ Failed to authenticate with Destination.")
            dest_success = False
            
    return src_success and dest_success
        
    return True

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate WordPress Articles")
    parser.add_argument("--limit", type=int, default=None, help="Number of posts to migrate limit")
    parser.add_argument("--status", type=str, default="publish", help="Post status to migrate (publish, draft, any, etc)")
    parser.add_argument("--dry-run", action="store_true", help="Test site connections without migrating")
    args = parser.parse_args()

    global source_client, dest_client
    try:
        source_client = WPClient(SOURCE_URL, SOURCE_USER, SOURCE_PASS)
        dest_client = WPClient(DEST_URL, DEST_USER, DEST_PASS)
    except Exception as e:
        logger.error(f"Failed to initialize clients: {e}")

    if args.dry_run:
        test_connection()
        logger.info("Dry run connection test complete. Exiting.")
        return

    if not source_client or not dest_client:
        logger.error("Clients not initialized. Cannot proceed.")
        return
        
    logger.info("Starting Migration Process...")
    load_state()
    
    migrate_terms("categories")
    migrate_terms("tags")
    migrate_users()
    
    migrate_posts(limit=args.limit, status=args.status)
    
    logger.info("Migration Run Completed.")

if __name__ == "__main__":
    main()
