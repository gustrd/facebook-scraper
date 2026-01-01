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

    # Get existing files to check for duplicates
    def get_existing_photo_ids():
        """Get set of photo IDs that are already downloaded"""
        existing_ids = set()
        for filepath in output_folder.glob('*'):
            if filepath.is_file() and not filepath.name.startswith('.'):
                # Extract ID from filename (everything before the extension)
                photo_id = filepath.stem
                existing_ids.add(photo_id)
        return existing_ids

    existing_ids = get_existing_photo_ids()
    if existing_ids:
        print(f"📂 Found {len(existing_ids)} existing photos in output folder")
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
            photos_url = f"https://www.facebook.com/{username}/photos"
            print(f"Navigating to 'Photos by {username}'...")
        elif tab.lower() == "of":
            # Photos user is tagged in
            photos_url = f"https://www.facebook.com/{username}/photos_of"
            print(f"Navigating to 'Photos of {username}'...")
        else:
            print(f"Invalid tab option: {tab}. Using 'by' as default.")
            photos_url = f"https://www.facebook.com/{username}/photos"

        driver.get(photos_url)
        time.sleep(5)  # Give page time to load initially

        print(f"\n{'='*70}")
        print(f"Starting incremental scroll & download process")
        print(f"Max scroll iterations: {max_scrolls}")
        print(f"Strategy: Scroll slowly, download photos in between scrolls")
        print(f"This gives Facebook time to load all content")
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
                        # Extract photo ID from URL first to check if we already have it
                        photo_id = None
                        if 'fbid=' in photo_url:
                            match = re.search(r'fbid=(\d+)', photo_url)
                            if match:
                                photo_id = match.group(1)

                        # If we can't get ID from URL, we'll need to visit the page
                        if photo_id and photo_id in existing_ids:
                            print(f"\n  [{idx}/{new_photos_count}] ⏭️  Already downloaded: {photo_id}")
                            skipped_count += 1
                            continue

                        print(f"\n  [{idx}/{new_photos_count}] Processing photo...")

                        # Navigate to the photo page
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
                            # Go back to photo gallery
                            driver.get(photos_url)
                            time.sleep(2)
                            continue

                        # Get the image URL
                        img_url = full_img.get_attribute('src')

                        if not img_url or img_url.startswith('data:'):
                            print(f"    ❌ Invalid image URL")
                            failed_count += 1
                            # Go back to photo gallery
                            driver.get(photos_url)
                            time.sleep(2)
                            continue

                        # Extract photo ID - try from URL first, then from img_url
                        if not photo_id:
                            # Try to extract from img_url
                            match = re.search(r'/(\d{10,})_', img_url)
                            if match:
                                photo_id = match.group(1)
                            else:
                                photo_id = f"photo_{downloaded_count + 1:04d}"

                        # Determine file extension
                        ext = '.jpg'
                        if '.' in img_url.split('/')[-1].split('?')[0]:
                            url_ext = img_url.split('/')[-1].split('?')[0].split('.')[-1]
                            if url_ext.lower() in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                                ext = '.' + url_ext.lower()

                        filename = f"{photo_id}{ext}"
                        filepath = output_folder / filename

                        # Double-check if file already exists (in case ID wasn't in URL)
                        if filepath.exists():
                            print(f"    ⏭️  Already exists: {filename}")
                            skipped_count += 1
                            existing_ids.add(photo_id)
                            # Go back to photo gallery
                            driver.get(photos_url)
                            time.sleep(2)
                            continue

                        print(f"    📥 Downloading: {filename}")
                        print(f"       Size: {full_img.size['width']}x{full_img.size['height']} pixels")

                        # Download the image
                        response = requests.get(img_url, timeout=30)

                        if response.status_code == 200:
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
                                downloaded_count += 1
                                existing_ids.add(photo_id)  # Add to existing IDs

                            # Random delay to avoid rate limiting
                            time.sleep(random.uniform(1, 3))
                        else:
                            print(f"    ❌ Failed: HTTP {response.status_code}")
                            failed_count += 1

                        # Go back to photo gallery for next photo
                        driver.get(photos_url)
                        time.sleep(random.uniform(2, 4))

                    except Exception as e:
                        print(f"    ❌ Error: {e}")
                        failed_count += 1
                        # Try to go back to photo gallery
                        try:
                            driver.get(photos_url)
                            time.sleep(2)
                        except:
                            pass
                        continue

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
            print(f"\n📜 Scrolling down to load more photos...")

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
        print(f"Total photos in folder: {len(existing_ids)}")
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
