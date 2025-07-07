from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import pandas as pd
import time
import os
from datetime import datetime

# === CONFIG ===
TEMP_CSV = "outlier_jobs_temp.csv"
FINAL_CSV = "outlier_jobs.csv"
URL = "https://app.outlier.ai/en/expert/opportunities?location=All&type=All"

# === CHROME SETUP ===
chrome_options = Options()
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--remote-debugging-port=9222")
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--window-size=1920,1080")  # Ensure proper rendering in headless mode
chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")  # Prevent detection as a bot

driver = webdriver.Chrome(options=chrome_options)
driver.get(URL)
time.sleep(5)
driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
time.sleep(3)

# === SCRAPE LIST VIEW ===
soup = BeautifulSoup(driver.page_source, 'html.parser')
type_headers = soup.find_all('div', class_='text-lg font-semibold pt-4')

scraping_date = datetime.utcnow().strftime("%d/%m/%Y")
scraping_time = datetime.utcnow().strftime("%H:%M")
jobs_temp = []

for header in type_headers:
    job_type = header.get_text(strip=True)
    parent_div = header.find_parent()
    job_divs = parent_div.find_all('div', class_='flex flex-col py-2 border-b border-neutral-200 cursor-pointer group')

    for job_div in job_divs:
        title_tag = job_div.find('div', class_='text-md')
        location_tag = job_div.find('div', class_='text-xs')
        if not title_tag or not location_tag:
            continue
        jobs_temp.append({
            'Job Title': title_tag.get_text(strip=True),
            'Location': location_tag.get_text(strip=True),
            'Type': job_type,
        })

jobs_temp_df = pd.DataFrame(jobs_temp)
jobs_temp_df.reset_index(inplace=True)
jobs_temp_df.rename(columns={"index": "Index"}, inplace=True)
jobs_temp_df.to_csv(TEMP_CSV, index=False)
print(f"✅ Saved {len(jobs_temp_df)} jobs to {TEMP_CSV}")

# === LOAD EXISTING FINAL CSV OR INIT NEW ===
final_columns = [
    'Scraping Date', 'Scraping Time', 'ID', 'Posted at', 'Deleted at', 'Reposted at',
    'Job Title', 'Workplace Type', 'Location', 'Type', 'Job Type',
    'Apply Link', 'Description', 'Requirements', 'Highlights'
]

if os.path.exists(FINAL_CSV):
    existing_df = pd.read_csv(FINAL_CSV)
else:
    existing_df = pd.DataFrame(columns=final_columns)

existing_df['ID'] = existing_df['ID'].astype(str)

# === Counters for summary ===
added_count = 0
reposted_count = 0
deleted_count = 0

# === Track jobs successfully scraped this run ===
found_ids = set()

# === LOOP THROUGH JOBS TO EXTRACT AND PROCESS ===
for _, job in jobs_temp_df.iterrows():
    try:
        index = int(job['Index'])
        job_type = job['Type']

        job_cards = driver.find_elements(By.CLASS_NAME, "group")
        if index >= len(job_cards):
            continue

        driver.execute_script("arguments[0].scrollIntoView(true);", job_cards[index])
        time.sleep(0.5)
        job_cards[index].click()

        WebDriverWait(driver, 10).until(EC.url_contains("/opportunities/"))
        job_url = driver.current_url
        job_id = job_url.split("/opportunities/")[1].split("?")[0]
        job_id = str(job_id)
        found_ids.add(job_id)

        row_match = existing_df[existing_df['ID'] == job_id]
        if not row_match.empty:
            # Already seen, mark repost if deleted
            idx = row_match.index[0]
            deleted_at = existing_df.at[idx, 'Deleted at']
            reposted_at = existing_df.at[idx, 'Reposted at']

            if pd.notna(deleted_at):
                deleted_dt = datetime.strptime(deleted_at, "%d/%m/%Y")
                reposted_dt = datetime.strptime(reposted_at, "%d/%m/%Y") if pd.notna(reposted_at) else None
                if reposted_dt is None or deleted_dt > reposted_dt:
                    existing_df.at[idx, 'Reposted at'] = scraping_date
                    existing_df.to_csv(FINAL_CSV, index=False)
                    reposted_count += 1
                    print(f"🔁 Reposted job ID {job_id}")
            # Don't re-scrape details
            driver.get(URL)
            WebDriverWait(driver, 10).until(EC.presence_of_all_elements_located((By.CLASS_NAME, "group")))
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            continue

        # === SCRAPE NEW JOB DETAILS ===
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        h1 = soup.find('h1')
        title = h1.get_text(strip=True) if h1 else ""

        location_div = soup.find('div', class_="text-sm font-small text-neutral-700 py-4")
        raw_location = location_div.get_text(strip=True) if location_div else ""
        workplace_type = "Remote" if raw_location.startswith("Remote") else ""
        location = raw_location.replace("Remote - ", "").strip() if raw_location.startswith("Remote - ") else raw_location

        description_container = soup.find('div', class_="text-sm font-small")
        description = ""
        if description_container:
            inside_expect = False
            for tag in description_container.children:
                if tag.name == "p":
                    text = tag.get_text(strip=True)
                    if "What to expect" in text:
                        inside_expect = True
                        continue
                    if not inside_expect:
                        description += text + "\n"
                elif tag.name in ["ul", "ol"]:
                    if not inside_expect:
                        for li in tag.find_all("li"):
                            description += "- " + li.get_text(strip=True) + "\n"

        highlights_divs = soup.select('div.outlier-theme .bg-utility-offWhite')
        highlights = ""
        for h in highlights_divs:
            parts = [el.get_text(separator=' ', strip=True) for el in h.find_all(['div'])]
            highlights += "\n".join(parts) + "\n"

        new_row = pd.DataFrame([{
            'Scraping Date': scraping_date,
            'Scraping Time': scraping_time,
            'ID': job_id,
            'Posted at': scraping_date,
            'Deleted at': '',
            'Reposted at': '',
            'Job Title': title,
            'Workplace Type': workplace_type,
            'Location': location,
            'Type': job_type,
            'Job Type': '',
            'Apply Link': job_url,
            'Description': description.strip(),
            'Requirements': '',
            'Highlights': highlights.strip()
        }])

        existing_df = pd.concat([existing_df, new_row], ignore_index=True)
        existing_df.to_csv(FINAL_CSV, index=False)
        added_count += 1
        print(f"✅ Added job ID {job_id} ({job_type})")

        # Return to list page
        driver.get(URL)
        WebDriverWait(driver, 10).until(EC.presence_of_all_elements_located((By.CLASS_NAME, "group")))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

    except TimeoutException:
        print(f"❌ Timeout at index {index}, retrying...")
        driver.get(URL)
        time.sleep(3)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)


# === MARK MISSING JOBS AS DELETED OR RE-DELETED ===
for idx, row in existing_df.iterrows():
    job_id = row['ID']
    deleted_at = row['Deleted at']
    reposted_at = row['Reposted at']

    if job_id not in found_ids:
        if pd.isna(deleted_at):
            existing_df.at[idx, 'Deleted at'] = scraping_date
            deleted_count += 1
            print(f"🗑️ Marked job ID {job_id} as deleted.")
        else:
            deleted_dt = datetime.strptime(deleted_at, "%d/%m/%Y")
            reposted_dt = datetime.strptime(reposted_at, "%d/%m/%Y") if pd.notna(reposted_at) else None
            if reposted_dt and reposted_dt > deleted_dt:
                existing_df.at[idx, 'Deleted at'] = scraping_date
                deleted_count += 1
                print(f"🗑️ Re-deleted job ID {job_id}")

# === SAVE FINAL VERSION AND PRINT SUMMARY ===
driver.quit()
existing_df = existing_df[final_columns]
existing_df.to_csv(FINAL_CSV, index=False)

print("\n📊 Summary")
print(f"✅ New jobs added: {added_count}")
print(f"🔁 Jobs reposted: {reposted_count}")
print(f"🗑️ Jobs deleted or re-deleted: {deleted_count}")
print(f"📄 Final CSV saved with {len(existing_df)} jobs → {FINAL_CSV}")
