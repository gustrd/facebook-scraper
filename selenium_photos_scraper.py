#!/usr/bin/env python3
"""
Alternative photo scraper using Selenium for desktop Facebook
Downloads full-resolution photos incrementally while scrolling

Requirements:
    pip install selenium webdriver-manager

Usage:
    # Download photos from "Photos by" tab (photos uploaded by the user)
    python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab "by"

    # Download photos from "Photos of" tab (photos user is tagged in)
    python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab "of"

    # Resume a previous download (skips already downloaded photos)
    python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab "by" --resume

    # Limit number of photos or scroll iterations
    python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab "by" --limit 50
    python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab "by" --scrolls 300
"""
import argparse
import time
import random
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
import requests
import re
import hashlib

def scrape_photos_selenium(username, output_folder, tab="by", max_scrolls=300, limit=None, resume=False):
    """
    Scrape full-resolution photos using Selenium from desktop Facebook

    This version scrolls slowly and downloads photos incrementally:
    1. Scroll a little bit
    2. Collect new photo URLs that appeared
    3. Download those photos
    4. Repeat

    This gives Facebook plenty of time to load content between scrolls.

    Args:
        username: Facebook username/profile name
        output_folder: Where to save photos
        tab: Which tab to scrape - "by" for Photos by user, "of" for Photos of user
        max_scrolls: How many scroll iterations (default: 300)
        limit: Maximum number of photos to download (None = no limit)
        resume: If True, skip photos that were already processed
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        print("ERROR: Please install required packages:")
        print("pip install selenium webdriver-manager")
        return

    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    # Track stats
    downloaded_count = 0
    failed_count = 0
    skipped_count = 0

    # Get existing hashes to check for duplicates
    def get_existing_hashes():
        """Get set of file hashes that are already downloaded"""
        hashes = set()
        for filepath in output_folder.glob('*'):
            if filepath.is_file() and not filepath.name.startswith('.'):
                # We assume the filename is the hash
                hashes.add(filepath.stem)
        return hashes

    existing_hashes = get_existing_hashes()
    if existing_hashes:
        print(f"📂 Found {len(existing_hashes)} existing photos in output folder")
        if resume:
            print(f"   Will skip these photos and continue downloading new ones")

    # Setup Chrome driver
    options = webdriver.ChromeOptions()
    # Remove headless mode so you can see what's happening and login manually
    # options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--start-maximized')
    
    # Suppress noise and GCM errors
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-features=GCM')
    options.add_argument('--log-level=3')
    options.add_experimental_option('excludeSwitches', ['enable-logging'])

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 10)

    try:
        # Navigate to Facebook
        print("Opening Facebook...")
        driver.get("https://www.facebook.com")

        # Wait for manual login
        input("\n⚠️  Please log in to Facebook in the browser window, then press ENTER here to continue...\n")

        # Navigate to appropriate photos tab
        if tab.lower() == "by":
            # Photos uploaded by the user
            photos_url = f"https://www.facebook.com/{username}/photos_all"
            print(f"Navigating to 'Photos by {username}' (uploads)...")
        elif tab.lower() == "of":
            # Photos user is tagged in
            photos_url = f"https://www.facebook.com/{username}/photos_of"
            print(f"Navigating to 'Photos of {username}'...")
        else:
            print(f"Invalid tab option: {tab}. Using 'by' as default.")
            photos_url = f"https://www.facebook.com/{username}/photos"

        driver.get(photos_url)
        time.sleep(5)  # Give page time to load initially

        # Store the main gallery window handle
        gallery_handle = driver.current_window_handle
        
        # Create a dedicated worker window for downloads
        print("Opening secondary window for photo processing...")
        driver.execute_script("window.open('about:blank', 'download_window', 'width=1000,height=800');")
        # Identify handles
        all_handles = driver.window_handles
        download_handle = [h for h in all_handles if h != gallery_handle][0]
        
        # Switch back to gallery to start
        driver.switch_to.window(gallery_handle)

        print(f"\n{'='*70}")
        print(f"Starting incremental scroll & download process")
        print(f"Strategy: Main window stays on gallery, secondary window downloads photos")
        print(f"Max iterations: {max_scrolls}")
        print(f"{'='*70}\n")

        no_new_photos_count = 0
        total_photos_found = 0
        scroll_iteration = 0
        processed_urls = set()  # Track URLs we've checked in this session

        for scroll_iteration in range(max_scrolls):
            # Check if we've hit the limit
            if limit and downloaded_count >= limit:
                print(f"\n✓ Reached download limit of {limit} photos")
                break

            print(f"\n{'─'*70}")
            print(f"Scroll iteration {scroll_iteration + 1}/{max_scrolls}")
            print(f"{'─'*70}")

            # Find all photo links currently visible on page
            selectors = [
                "a[href*='/photos/']",
                "a[href*='/photo/']",
                "a[href*='fbid=']",
            ]

            current_photo_links = []
            for selector in selectors:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    try:
                        href = elem.get_attribute('href')
                        if href and ('/photos/' in href or '/photo/' in href or 'fbid=' in href):
                            # Skip album links, only get individual photos
                            if '/photos/a.' not in href and '/photos/albums/' not in href:
                                current_photo_links.append(href)
                    except:
                        continue

            # Remove duplicates while preserving order
            current_photo_links = list(dict.fromkeys(current_photo_links))

            # Filter out already processed photos in this session
            new_photos = [url for url in current_photo_links if url not in processed_urls]

            photos_on_page = len(current_photo_links)
            new_photos_count = len(new_photos)

            print(f"Photos visible on page: {photos_on_page}")
            print(f"New photos to check: {new_photos_count}")

            # Download new photos before scrolling more
            if new_photos:
                print(f"\n📥 Checking {new_photos_count} new photos...")
                no_new_photos_count = 0  # Reset counter when we find new photos

                for idx, photo_url in enumerate(new_photos, 1):
                    # Mark as processed
                    processed_urls.add(photo_url)

                    # Check limit again
                    if limit and downloaded_count >= limit:
                        print(f"\n✓ Reached download limit of {limit} photos")
                        break

                    try:
                        # If we can extract an ID from URL, we can still skip BEFORE visiting if we've seen it 
                        # but since we're using hashes for filenames now, we'll mainly rely on content hash.
                        # However, to save time, let's keep a session-based skip if the URL is identical.
                        # (processed_urls already handles this)

                        print(f"\n  [{idx}/{new_photos_count}] Processing photo...")

                        # Switch to download window and load photo
                        driver.switch_to.window(download_handle)
                        driver.get(photo_url)
                        time.sleep(random.uniform(2, 4))

                        # Try to find the full-resolution image
                        img_selectors = [
                            "img[data-visualcompletion='media-vc-image']",
                            "img.x1ey2m1c",  # Common class for full-size images
                            "img[style*='max-height']",
                            "div[role='dialog'] img",  # Image in photo viewer dialog
                            "div[data-pagelet*='MediaViewer'] img",
                        ]

                        full_img = None
                        for selector in img_selectors:
                            try:
                                imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                                # Get the largest image (by pixel dimensions)
                                largest = None
                                largest_size = 0

                                for img in imgs:
                                    try:
                                        width = img.get_attribute('width') or img.size['width']
                                        height = img.get_attribute('height') or img.size['height']
                                        width = int(width) if width else 0
                                        height = int(height) if height else 0
                                        size = width * height

                                        if size > largest_size:
                                            largest_size = size
                                            largest = img
                                    except:
                                        continue

                                if largest and largest_size > 40000:  # Minimum 200x200 pixels
                                    full_img = largest
                                    break
                            except:
                                continue

                        if not full_img:
                            print(f"    ❌ Could not find full-size image")
                            failed_count += 1
                            continue

                        # Get the image URL
                        img_url = full_img.get_attribute('src')

                        if not img_url or img_url.startswith('data:'):
                            print(f"    ❌ Invalid image URL")
                            failed_count += 1
                            continue

                        # Download the image to memory first
                        response = requests.get(img_url, timeout=30)

                        if response.status_code == 200:
                            # Calculate hash of content
                            content_hash = hashlib.md5(response.content).hexdigest()
                            
                            # Determine file extension
                            ext = '.jpg'
                            if '.' in img_url.split('/')[-1].split('?')[0]:
                                url_ext = img_url.split('/')[-1].split('?')[0].split('.')[-1]
                                if url_ext.lower() in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                                    ext = '.' + url_ext.lower()

                            filename = f"{content_hash}{ext}"
                            filepath = output_folder / filename

                            # Check if hash already exists
                            if filepath.exists() or content_hash in existing_hashes:
                                print(f"    ⏭️  Already exists (duplicate content): {filename}")
                                skipped_count += 1
                                existing_hashes.add(content_hash)
                                continue

                            with open(filepath, 'wb') as f:
                                f.write(response.content)

                            # Verify file size
                            file_size = filepath.stat().st_size
                            if file_size < 1000:  # Less than 1KB is likely an error
                                print(f"    ⚠️  File too small ({file_size} bytes), might be invalid")
                                filepath.unlink()
                                failed_count += 1
                            else:
                                print(f"    ✅ Downloaded ({file_size:,} bytes)")
                                print(f"       Hash: {content_hash}")
                                downloaded_count += 1
                                existing_hashes.add(content_hash)

                            # Random delay to avoid rate limiting
                            time.sleep(random.uniform(1, 3))
                        else:
                            print(f"    ❌ Failed: HTTP {response.status_code}")
                            failed_count += 1

                    except Exception as e:
                        print(f"    ❌ Error: {e}")
                        failed_count += 1
                    finally:
                        # ALWAYS return focus to gallery window
                        try:
                            driver.switch_to.window(gallery_handle)
                        except:
                            pass

                print(f"\n✓ Finished downloading batch")
                print(f"  Downloaded this session: {downloaded_count}")
                print(f"  Skipped (already downloaded): {skipped_count}")
                print(f"  Failed: {failed_count}")

            else:
                no_new_photos_count += 1
                print(f"\n⚠️  No new photos found (attempt {no_new_photos_count}/10)")

                if no_new_photos_count >= 10:
                    print(f"\n✓ No new photos after 10 scroll iterations - reached end of gallery")
                    break

            # Now scroll down slowly to load more photos
            print(f"\nScroll complete: Found {photos_on_page} total, {new_photos_count} new.")
            print(f"📜 Scrolling gallery to load more...")

            # Ensure gallery window is focused for scrolling
            driver.switch_to.window(gallery_handle)
            
            viewport_height = driver.execute_script("return window.innerHeight;")
            scroll_increment = int(viewport_height * 0.5)  # Scroll 50% of viewport (slower)

            # Do 3 small scrolls
            for j in range(3):
                driver.execute_script(f"window.scrollBy(0, {scroll_increment});")
                time.sleep(1.0)  # Pause between small scrolls

            # Wait for content to load (Facebook can be slow)
            wait_time = random.uniform(3, 6)
            print(f"   Waiting {wait_time:.1f}s for content to load...")
            time.sleep(wait_time)

        print(f"\n{'='*70}")
        print(f"✅ Download complete!")
        print(f"{'='*70}")
        print(f"Downloaded this session: {downloaded_count}")
        print(f"Skipped (already exist): {skipped_count}")
        print(f"Failed: {failed_count}")
        print(f"Total unique photos in folder: {len(existing_hashes)}")
        print(f"Saved to: {output_folder.absolute()}")
        print(f"\nYou can run this again with --resume to continue (skips existing files)")
        print(f"{'='*70}")

    finally:
        print("\nClosing browser in 5 seconds...")
        time.sleep(5)
        driver.quit()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Scrape Facebook photos using Selenium (full resolution, incremental download)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download photos uploaded by user
  python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab by

  # Download photos user is tagged in
  python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab of

  # Resume a previous download
  python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab by --resume

  # Download first 50 photos only
  python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab by --limit 50

  # Use more scroll iterations for large galleries
  python selenium_photos_scraper.py --username "user.profile" --output ./photos --tab by --scrolls 500
        """
    )

    parser.add_argument('--username', required=True, help='Facebook username or profile name')
    parser.add_argument('--output', required=True, help='Output folder for photos')
    parser.add_argument('--tab', choices=['by', 'of'], default='by',
                        help='Which tab to scrape: "by" = Photos by user (uploaded), "of" = Photos of user (tagged in)')
    parser.add_argument('--scrolls', type=int, default=300,
                        help='Number of scroll iterations (default: 300, will auto-stop when no more photos)')
    parser.add_argument('--limit', type=int, default=None,
                        help='Maximum number of photos to download (default: no limit)')
    parser.add_argument('--resume', action='store_true',
                        help='Resume from previous download (skips already processed photos)')

    args = parser.parse_args()

    scrape_photos_selenium(args.username, args.output, args.tab, args.scrolls, args.limit, args.resume)
