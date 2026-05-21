import os
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

SCREENSHOTS_DIR = "screenshots"


def capture_screenshot(url: str, output_dir: str = SCREENSHOTS_DIR) -> str:
    os.makedirs(output_dir, exist_ok=True)

    hostname = urlparse(url).hostname or "page"
    safe_host = hostname.replace(".", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_host}_{timestamp}.png"
    file_path = os.path.join(output_dir, filename)

    with sync_playwright() as p:
        # Run in non-headless mode to avoid bot detection (ERR_HTTP2_PROTOCOL_ERROR)
        browser = p.chromium.launch(headless=False)
        
        # Add a realistic user agent
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        # Apply stealth to hide playwright automation footprints
        Stealth().apply_stealth_sync(page)
        
        # Use domcontentloaded and a sleep since some sites never reach networkidle
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000) # Give dynamic content time to load
        page.screenshot(path=file_path, full_page=False)
        browser.close()

    return file_path


if __name__ == "__main__":
    path = capture_screenshot("https://www.turkishairlines.com")
    print(f"Screenshot saved to: {path}")
