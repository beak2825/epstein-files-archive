import requests
from bs4 import BeautifulSoup
import os
from urllib.parse import urlparse, unquote
from email.utils import parsedate_to_datetime
import re
import time
from datetime import datetime, timedelta

# Define headers and cookies
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.justice.gov/epstein/doj-disclosures",
    "Origin": "https://www.justice.gov",
    "Cookie": "justiceGovAgeVerified=true"
}

# List of wanted headers for file metadata
wanted_headers = [
    'Date', 'Cache-Control', 'Content-Length', 'Etag', 'Expires', 
    'Last-Modified', 'Pragma', 'Server', 'Quic-Version', 
    'Content-Type', 'Alt-Svc', 'Accept-Ranges'
]

# Global counter for files processed
file_counter = 0

# Function to update or append to HASHES.txt
def update_hashes_file(basename, last_mod, etag):
    hashes_file = 'HASHES.txt'
    lines = []
    updated = False
    
    if os.path.exists(hashes_file):
        with open(hashes_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    
    pattern = re.compile(rf'^{re.escape(basename)}:\s*(.*)$')
    for i, line in enumerate(lines):
        match = pattern.match(line.strip())
        if match:
            lines[i] = f"{basename}: {last_mod}, {etag}\n"
            updated = True
            break
    
    if not updated:
        # Append to file
        with open(hashes_file, 'a', encoding='utf-8') as h:
            h.write(f"{basename}: {last_mod}, {etag}\n")
    else:
        # Rewrite entire file with updated line
        with open(hashes_file, 'w', encoding='utf-8') as h:
            h.writelines(lines)

# Function to get existing hash data from HASHES.txt
def get_hash_data(basename):
    """Returns tuple (last_modified, etag) or (None, None) if not found"""
    hashes_file = 'HASHES.txt'
    if not os.path.exists(hashes_file):
        return None, None
    
    pattern = re.compile(rf'^{re.escape(basename)}:\s*(.*)$')
    with open(hashes_file, 'r', encoding='utf-8') as f:
        for line in f:
            match = pattern.match(line.strip())
            if match:
                # Parse the data: "Last-Modified, Etag"
                data = match.group(1)
                parts = data.split(',', 1)
                if len(parts) == 2:
                    return parts[0].strip(), parts[1].strip().strip('"')
    return None, None

# Function to save page link every 50 files
def save_page_link(dataset_num, page):
    with open('page_link.txt', 'w', encoding='utf-8') as f:
        f.write(f"dataset={dataset_num}\n")
        f.write(f"page={page}\n")

# Function to load saved page link
def load_page_link():
    if not os.path.exists('page_link.txt'):
        return None, None
    
    try:
        with open('page_link.txt', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            dataset = None
            page = None
            for line in lines:
                if line.startswith('dataset='):
                    dataset = int(line.split('=')[1].strip())
                elif line.startswith('page='):
                    page = int(line.split('=')[1].strip())
            return dataset, page
    except:
        return None, None

# Function to check if error already exists in deleted.txt
def is_in_deleted_file(error_line):
    if not os.path.exists('deleted.txt'):
        return False
    
    with open('deleted.txt', 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip() == error_line.strip():
                return True
    return False

# Function to append to deleted.txt if not already present
def append_to_deleted(error_line):
    if not is_in_deleted_file(error_line):
        with open('deleted.txt', 'a', encoding='utf-8') as f:
            f.write(error_line + '\n')

# Function to check if file was processed and when
def check_file_processed(basename, dataset_num, reprocess_enabled):
    """
    Returns tuple (processed, skip):
    - processed: True if file exists
    - skip: True if we should skip (exists and within 24h cooldown or reprocess disabled)
    """
    txt_path = f"EFTA/DataSet_{dataset_num}/{basename}.txt"
    
    if not os.path.exists(txt_path):
        return False, False
    
    # File exists, check if reprocessing is enabled
    if not reprocess_enabled:
        return True, True
    
    # Check the Date header to determine when it was last processed
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('Date:'):
                    date_str = line.split('Date:', 1)[1].strip()
                    processed_date = parsedate_to_datetime(date_str)
                    now = datetime.now(processed_date.tzinfo)
                    time_diff = now - processed_date
                    
                    # If less than 24 hours, skip
                    if time_diff < timedelta(hours=24):
                        return True, True
                    else:
                        # More than 24 hours, allow reprocessing
                        return True, False
    except:
        # If we can't read the date, allow reprocessing
        return True, False
    
    # No Date header found, allow reprocessing
    return True, False

# Function to check if important data has changed
def has_data_changed(basename, new_last_mod, new_etag):
    """Check if Etag or Last-Modified has changed"""
    old_last_mod, old_etag = get_hash_data(basename)
    
    if old_last_mod is None and old_etag is None:
        # No existing data
        return True
    
    # Compare Etag and Last-Modified
    if new_etag != old_etag or new_last_mod != old_last_mod:
        return True
    
    return False

# Interactive start
print("Welcome to the Epstein Files Archiver.")
print("Options:")
print("  'normal' - Start from dataset 1, page 0")
print("  'custom' - Specify dataset and page")
print("  'resume' - Resume from saved page_link.txt")
choice = input("Enter your choice: ").strip().lower()

# Ask about reprocessing
reprocess_input = input("Enable reprocessing after 24h cooldown? (yes/no, default: yes): ").strip().lower()
reprocess_enabled = True if reprocess_input in ['', 'yes', 'y'] else False

if reprocess_enabled:
    print("Reprocessing enabled: Files will be rechecked after 24 hours.")
else:
    print("Reprocessing disabled: Already processed files will be skipped permanently.")

if choice == 'normal':
    dataset_num = 1
    start_page = 0
    print("Starting normal run from dataset 1, page 0.")
elif choice == 'custom':
    try:
        dataset_num = int(input("Enter starting dataset number: "))
        start_page = int(input("Enter starting page number for that dataset: "))
        print(f"Starting from dataset {dataset_num}, page {start_page}.")
    except ValueError:
        print("Invalid input. Starting normal run from dataset 1.")
        dataset_num = 1
        start_page = 0
elif choice == 'resume':
    dataset_num, start_page = load_page_link()
    if dataset_num is None or start_page is None:
        print("No saved progress found in page_link.txt. Starting from dataset 1, page 0.")
        dataset_num = 1
        start_page = 0
    else:
        print(f"Resuming from saved progress: dataset {dataset_num}, page {start_page}.")
else:
    print("Invalid choice. Starting normal run from dataset 1.")
    dataset_num = 1
    start_page = 0

while True:
    base_url = f"https://www.justice.gov/epstein/doj-disclosures/data-set-{dataset_num}-files"
    
    # For the starting page if resuming
    page = start_page
    current_url = base_url if page == 0 else f"{base_url}?page={page}"
    
    # Fetch the first page (or resumed page)
    resp = requests.get(current_url, headers=headers, allow_redirects=False)
    
    if resp.status_code == 403:
        # Save the 403 response
        os.makedirs('unreleased-datasets', exist_ok=True)
        html_path = f"unreleased-datasets/data-set-{dataset_num}-files.html"
        txt_path = f"unreleased-datasets/data-set-{dataset_num}-files.txt"
        
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(resp.text)
        
        headers_txt = f"Status-Code: {resp.status_code}\n"
        sorted_resp_headers = sorted(resp.headers.items(), key=lambda x: x[0])
        for k, v in sorted_resp_headers:
            headers_txt += f"{k}: {v}\n"
        
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(headers_txt)
        
        # Go to next dataset
        dataset_num += 1
        # Reset page to 0 for next dataset
        start_page = 0
        continue
    
    elif resp.status_code == 404:
        # Save the 404 response with fixed name as specified
        os.makedirs('unreleased-datasets', exist_ok=True)
        html_path = "unreleased-datasets/404.html"
        txt_path = "unreleased-datasets/404.txt"
        
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(resp.text)
        
        headers_txt = f"Status-Code: {resp.status_code}\n"
        sorted_resp_headers = sorted(resp.headers.items(), key=lambda x: x[0])
        for k, v in sorted_resp_headers:
            headers_txt += f"{k}: {v}\n"
        
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(headers_txt)
        
        # Stop the script
        break
    
    elif resp.status_code != 200:
        # Unexpected status, log and stop
        print(f"Unexpected status {resp.status_code} for {current_url}. Stopping.")
        break
    
    # Now enter the page loop, starting from the fetched page
    while True:
        # Parse the current page
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Find all file links in views-field-title
        file_links = []
        for field in soup.find_all('div', class_='views-field views-field-title'):
            a = field.find('a')
            if a and 'href' in a.attrs:
                link = a['href']
                full_link = 'https://www.justice.gov' + link if link.startswith('/') else link
                file_links.append(full_link)
        
        # Track how many files were actually processed on this page
        files_processed_on_page = 0
        
        # Process each file link
        for link in file_links:
            # Extract basename first to check if already processed
            path = urlparse(link).path
            basename = unquote(path.split('/')[-1])
            
            # Check if file was already processed
            processed, skip = check_file_processed(basename, dataset_num, reprocess_enabled)
            
            if skip:
                print(f"Skipping {basename} (already processed within 24h or reprocess disabled)")
                continue
            
            # Use HEAD to get headers without downloading body
            file_resp = requests.head(link, headers=headers, allow_redirects=False)
            
            if 300 <= file_resp.status_code < 400:
                # Redirect detected: pause and log
                print(f"Redirect detected for {link} (status: {file_resp.status_code})")
                with open('redirects.log', 'a', encoding='utf-8') as f:
                    f.write(f"URL: {link}\n")
                    f.write(f"Status: {file_resp.status_code}\n")
                    f.write(f"Location: {file_resp.headers.get('Location', 'N/A')}\n")
                    f.write(f"Request Headers: {file_resp.request.headers}\n\n")
                input("Press Enter to continue...")
                continue  # Skip saving headers for redirects
            
            if file_resp.status_code != 200:
                # Log to deleted.txt with duplicate checking
                error_line = f"Failed to fetch headers for {link}: {file_resp.status_code}"
                print(error_line)
                append_to_deleted(error_line)
                continue
            
            # Extract metadata
            last_mod = file_resp.headers.get('Last-Modified')
            etag = file_resp.headers.get('Etag', 'N/A').strip('"')
            
            # If file was processed before, check if data changed
            if processed:
                if not has_data_changed(basename, last_mod, etag):
                    print(f"Skipping {basename} (no changes in Etag or Last-Modified)")
                    continue
                else:
                    print(f"Reprocessing {basename} (data changed)")
            
            # Create directory
            dir_path = f"EFTA/DataSet_{dataset_num}"
            os.makedirs(dir_path, exist_ok=True)
            
            # Path for txt file
            txt_path = os.path.join(dir_path, f"{basename}.txt")
            
            # Collect and sort wanted headers (only if present)
            present_headers = {k: file_resp.headers[k] for k in wanted_headers if k in file_resp.headers}
            sorted_headers = sorted(present_headers.items(), key=lambda x: x[0])
            
            # Write to txt
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"Status-Code: {file_resp.status_code}\n")
                for k, v in sorted_headers:
                    f.write(f"{k}: {v}\n")
            
            # Set file modified time based on Last-Modified (GMT to timestamp)
            if last_mod:
                dt = parsedate_to_datetime(last_mod)
                timestamp = dt.timestamp()
                os.utime(txt_path, (timestamp, timestamp))  # Sets mtime to GMT equivalent; system will display in EST
            
            # Update HASHES.txt (will update if exists, else append)
            update_hashes_file(basename, last_mod, f'"{etag}"')
            
            # Increment counters
            file_counter += 1
            files_processed_on_page += 1
            
            # Save page link every 50 files
            if file_counter % 50 == 0:
                save_page_link(dataset_num, page)
                print(f"Progress saved: {file_counter} files processed (Dataset {dataset_num}, Page {page})")
        
        # Check if we processed any files on this page
        if files_processed_on_page == 0:
            print(f"No new files to process on page {page}, moving to next page.")
        
        # Check for next page
        next_link = soup.find('a', class_='usa-pagination__next-page')
        if not next_link:
            break
        
        # Fetch next page
        page += 1
        next_url = f"{base_url}?page={page}"
        resp = requests.get(next_url, headers=headers, allow_redirects=False)
        if resp.status_code != 200:
            print(f"Failed to fetch page {page} for dataset {dataset_num}: {resp.status_code}")
            break
    
    # If we completed the dataset (or broke due to page fail), move to next
    dataset_num += 1
    # Reset page to 0 for next dataset
    start_page = 0
    page = 0

print("Script completed.")
