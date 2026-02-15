import urllib.request
import time

# Define the headers dictionary
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.justice.gov/epstein/doj-disclosures",
    "Origin": "https://www.justice.gov",
    "Cookie": "justiceGovAgeVerified=true"  # Using Cookie header as provided
}

def fetch_head(url):
    try:
        req = urllib.request.Request(url, method='HEAD')
        # Add all headers from the dictionary
        for key, value in headers.items():
            req.add_header(key, value)
        with urllib.request.urlopen(req) as response:
            headers_resp = response.headers  # Renamed to avoid conflict with 'headers' dict
            status = response.status
            last_modified = headers_resp.get('Last-Modified', 'N/A')
            date = headers_resp.get('Date', 'N/A')
            content_length = headers_resp.get('Content-Length', 'N/A')
            etag = headers_resp.get('ETag', 'N/A')
            content_type = headers_resp.get('Content-Type', 'N/A')
            return last_modified, date, content_length, etag, content_type, status
    except Exception as e:
        return 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 0  # 0 for error

# Initial fetch
datasets = range(1, 13)
last_modifieds = {}
last_etags = {}

with open('zip_hashes.txt', 'w') as f:
    f.write('Last-Modified, Date, Content-Length, Etag, Content-Type, Status-code\n')
    for i in datasets:
        url = f'https://www.justice.gov/epstein/files/DataSet%20{i}.zip'
        lm, dt, cl, et, ct, st = fetch_head(url)
        if st != 200:
            lm, dt, cl, et, ct = 'N/A', 'N/A', 'N/A', 'N/A', 'N/A'
        line = f'DataSet {i}: {lm}, {dt}, {cl}, {et}, {ct}, {st}\n'
        f.write(line)
        last_modifieds[i] = lm
        last_etags[i] = et

# Continuous checking
while True:
    time.sleep(300)  # 5 minutes
    with open('zip_hashes.txt', 'a') as f:
        for i in datasets:
            url = f'https://www.justice.gov/epstein/files/DataSet%20{i}.zip'
            lm, dt, cl, et, ct, st = fetch_head(url)
            if st != 200:
                lm, dt, cl, et, ct = 'N/A', 'N/A', 'N/A', 'N/A', 'N/A'
            if lm != last_modifieds[i] and et != last_etags[i]:
                line = f'DataSet {i}: {lm}, {dt}, {cl}, {et}, {ct}, {st}\n'
                f.write(line)
                last_modifieds[i] = lm
                last_etags[i] = et