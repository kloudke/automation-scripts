#!/usr/bin/env python3
import os
import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

URLS_FILE = "migrated_urls.txt"
SCOPES = ['https://www.googleapis.com/auth/indexing']

def get_indexing_service():
    """Initializes and returns the Google Indexing API service object."""
    try:
        if 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ:
            logger.error("GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
            return None
            
        credentials = service_account.Credentials.from_service_account_file(
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'], scopes=SCOPES)
        service = build('indexing', 'v3', credentials=credentials)
        return service
    except Exception as e:
        logger.error(f"Failed to initialize Google Indexing Client: {e}")
        return None

def main():
    logger.info("Starting Google Search de-indexing process...")
    
    if not os.path.exists(URLS_FILE):
        logger.info(f"No migrated URLs file found at {URLS_FILE}. Nothing to de-index.")
        return

    service = get_indexing_service()
    if not service:
        logger.error("Could not obtain Google Indexing service. Exiting.")
        return

    # Read URLs from file
    with open(URLS_FILE, 'r') as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        logger.info("URL list is empty. Nothing to de-index.")
        return

    logger.info(f"Found {len(urls)} URLs to de-index.")

    # Request removal for each URL
    for url in urls:
        logger.info(f"Requesting removal for {url}")
        try:
            body = {
                "url": url,
                "type": "URL_DELETED"
            }
            # Execute the API call
            response = service.urlNotifications().publish(body=body).execute()
            logger.info(f"Successfully requested removal for {url}. Response: {response}")
        except Exception as e:
            logger.error(f"Failed to request removal for {url}: {e}")
            
    logger.info("De-indexing process completed.")

if __name__ == "__main__":
    main()
